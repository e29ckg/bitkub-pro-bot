import asyncio
import httpx
import logging
import os
import database as db
import indicators as ind
import config  # <--- เรียกใช้ค่า Config
import utils   # <--- (เผื่อเรียกใช้ในอนาคต)
import time
from bitkub import BitkubClient

# ตั้งค่า Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class BotEngine:
    def __init__(self, ws_manager):
        self.running = False
        self.ws_manager = ws_manager
        self.api = BitkubClient()
        self.tg_token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("CHAT_ID")
        self.last_status = {}
        self.server_status_ok = True 
        self.last_server_msg = "All endpoints ok"
        self.processing_coins = set()
    
    async def send_telegram(self, message):
        if not self.tg_token or not self.chat_id:
            return 
            
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML" # เผื่ออยากจัดรูปแบบตัวหนา/เอียง
        }

        try:
            async with httpx.AsyncClient() as client:
                # 🟢 เปลี่ยนจาก .get เป็น .post
                # และเปลี่ยนจาก params=... เป็น data=... (หรือ json=...)
                await client.post(url, data=payload, timeout=10.0)
                
        except Exception as e:
            print(f"Telegram Error: {e}")

    # --- 🟢 เพิ่มเมธอดใหม่ใน BotEngine ---
    async def check_server_health(self, client):
        status_data = await self.api.get_server_status(client)
        
        is_all_ok = True
        error_messages = []

        # Loop เช็คทุก Endpoint (Non-secure และ Secure)
        if isinstance(status_data, list):
            for item in status_data:
                name = item.get("name", "Unknown")
                status = item.get("status", "error")
                message = item.get("message", "")
                
                if status != "ok":
                    is_all_ok = False
                    error_messages.append(f"{name}: {status} ({message})")
        else:
            # กรณี format ผิดหรือไม่ใช่ list
            is_all_ok = False
            error_messages.append("Invalid Status Response")

        # สรุปข้อความปัจจุบัน
        current_msg = "All Systems Operational" if is_all_ok else " | ".join(error_messages)

        # 🟢 ตรวจสอบการเปลี่ยนแปลงสถานะ (Change Detection)
        if is_all_ok != self.server_status_ok:
            
            if is_all_ok:
                # จากเสีย -> ดี
                log_msg = f"✅ Server is back online! ({current_msg})"
                await self.log_and_broadcast(log_msg)
            else:
                # จากดี -> เสีย
                log_msg = f"⛔ Server Maintenance/Error Detected! Bot Paused.\nDetails: {current_msg}"
                await self.log_and_broadcast(log_msg) # แจ้ง Telegram ทันที

            # อัปเดตสถานะล่าสุดเก็บไว้
            self.server_status_ok = is_all_ok
            self.last_server_msg = current_msg

        return is_all_ok

    async def log_and_broadcast(self, message):
        print(message)
        logging.info(message)
        await self.ws_manager.broadcast(message)
        
        if "BUY" in message or "SELL" in message or "Error" in message or "Active" in message or "Changed" in message:
            await self.send_telegram(message)

    def analyze_market(self, df, symbol):
        # คำนวณ Indicators
        df["RSI"] = ind.calculate_rsi(df["close"])
        df["MACD"], df["Signal"] = ind.calculate_macd(df["close"])
        df["BB_Mid"], df["BB_Upper"], df["BB_Lower"] = ind.calculate_bollinger_bands(df["close"])
        
        last = df.iloc[-1]
        trend = "Downtrend" if last["MACD"] < last["Signal"] else "Uptrend"
        
        decisions = []
        signal = "HOLD"
        
        # --- ใช้ค่าจาก config.py แทนการ Hardcode ---
        if trend == "Downtrend":
            if last["RSI"] < config.RSI_OVERSOLD:
                signal = "BUY"
                decisions.append(f"RSI Oversold ({last['RSI']:.2f})")
            elif last["close"] < last["BB_Lower"]:
                signal = "BUY"
                decisions.append("Price < BB Lower")
        elif trend == "Uptrend":
            if last["RSI"] > config.RSI_OVERBOUGHT:
                signal = "SELL"
                decisions.append(f"RSI Overbought ({last['RSI']:.2f})")
            elif last["close"] > last["BB_Upper"]:
                signal = "SELL"
                decisions.append("Price > BB Upper")
                
        return signal, ", ".join(decisions), last["close"]

    async def execute_trade(self, client, symbol_data, action, price, reason):
        s_id = symbol_data['id']
        sym = symbol_data['symbol']
        cost = symbol_data['cost']
        coin = symbol_data['coin']
        cost_st = symbol_data['cost_st']
        
        wallet = await self.api.get_wallet(client)
        
        if action == "BUY":
            thb_balance = wallet.get('result', {}).get('THB', 0)
            
            if thb_balance < cost_st:
                await self.log_and_broadcast(f"⚠️ {sym}: ไม่พอซื้อ (มี {thb_balance} บาท)")
                return

            # คำนวณจำนวนเหรียญสำหรับ Limit Order
            # buy_volume = cost_st / price
            buy_volume = cost_st
            
            # ส่ง type='limit'
            res = await self.api.place_order(client, sym, buy_volume, price, 'buy', type='limit')
            
            if res.get('error') == 0:
                result = res['result']
                
                # ถ้า Limit ยังไม่ Match ทันที result['rec'] อาจเป็น 0
                received_coin = result.get('rec', 0)
                if received_coin == 0: received_coin = buy_volume

                # Update DB: บวก Cost(บาท) และ Coin(เหรียญ)
                new_cost = cost + cost_st
                new_coin = coin + received_coin
                
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"BUY: {reason}")
                
                await self.log_and_broadcast(f"✅ {sym} BUY Success @ {price} (Vol: {buy_volume:.6f})")
            else:
                await self.log_and_broadcast(f"❌ {sym} BUY Error: {res.get('error')}")

        elif action == "SELL":
            if coin <= 0: return
            
            # เช็คขั้นต่ำ 10 บาท
            if (coin * price) < 10:
                await self.log_and_broadcast(f"⚠️ {sym}: มูลค่าขายน้อยกว่า 10 บาท (ข้าม)")
                return

            res = await self.api.place_order(client, sym, coin, price, 'sell', type='limit')
            
            if res.get('error') == 0:
                result = res['result']
                
                thb_rec = result.get('rec', 0)
                if thb_rec == 0: thb_rec = coin * price

                # Update DB: ลด Cost ลงตามเงินที่ได้คืน, Coin เหลือ 0
                new_cost = max(0, cost - thb_rec)
                new_coin = 0 # ขายหมด
                
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"SELL: {reason}")
                
                await self.log_and_broadcast(f"✅ {sym} SELL Success @ {price}")
            else:                
                await self.log_and_broadcast(f"❌ {sym} SELL Error: {res.get('error')}")
                if res.get('error') == 18:  # หากเกิดข้อผิดพลาด "ไม่พอขาย"
                    await db.update_cost_coin(s_id, 0, 0)  # ตั้ง cost Coin เป็น 0 เพื่อแก้ไขสถานะ
                    await self.log_and_broadcast(f"ℹ️ {sym}: Updated DB to 0 Cost/Coin due to insufficient balance.")
    
    async def clear_pending_orders(self, bitkub_client, http_client, symbol):
        """
        เคลียร์ออเดอร์ค้าง และคืนค่า Cost/Coin ใน Database
        """
        print(f"🧹 Checking pending orders for {symbol}...")
        
        # 1. ดึงออเดอร์ที่ค้างอยู่จาก Bitkub
        orders_res = await bitkub_client.get_open_orders(http_client, symbol)
        
        if orders_res.get('error') != 0:
            # ใช้ log_and_broadcast เพื่อให้เห็นทั้งใน Console และ Telegram (ถ้าเปิด)
            await self.log_and_broadcast(f"❌ Failed to get open orders {symbol}: {orders_res}")
            return

        open_orders = orders_res.get('result', [])
        
        if not open_orders:
            # print(f"✅ No pending orders for {symbol}.") 
            return

        print(f"⚠️ {symbol}: Found {len(open_orders)} pending orders. Cancelling & Reverting DB...")

        # 2. ดึงข้อมูลปัจจุบันจาก DB
        current_db_data = await db.get_symbol_by_name(symbol)
        
        if not current_db_data:
            print(f"❌ Database error: Symbol {symbol} not found.")
            return

        current_cost = current_db_data['cost']
        current_coin = current_db_data['coin']
        s_id = current_db_data['id']

        # 3. วนลูปยกเลิกทีละตัว
        for order in open_orders:
            o_id = order.get('id')
            o_side = order.get('side').lower()
            
            # 🔴 [แก้ไข] Open Orders API ใช้ key "amount" ไม่ใช่ "amt"
            o_amt = float(order.get('amount', 0)) 
            o_rate = float(order.get('rate', 0))
            o_rec = float(order.get('receive', 0))  
            
            # ยิง API ยกเลิก
            cancel_res = await bitkub_client.cancel_order(http_client, symbol, o_id, o_side)
            
            if cancel_res.get('error') == 0:
                print(f"   ✅ Cancelled {o_id} ({o_side}) success.")
                
                # --- 4. Logic คืนค่า (Revert DB) ---
                # สำหรับ Limit Order: Amount คือจำนวนเหรียญ, Rate คือราคาต่อหน่วย
                # ดังนั้น มูลค่ารวม (THB) = Amount * Rate
                total_value = o_amt * o_rate
                log_reason = ""

                if o_side == 'buy':
                    # ตอนซื้อ (Limit): เราบวก Cost (บาท) และ Coin (เหรียญ) ล่วงหน้า
                    # ยกเลิก: ต้องลบ Cost ออก และลบ Coin ออก
                    current_cost = max(0, current_cost - o_amt)
                    current_coin = max(0, current_coin - o_rec)
                    log_reason = f"Cancelled BUY: Revert -{o_amt:.2f} THB, -{o_rec} Coin"
                    
                elif o_side == 'sell':
                    # ตอนขาย: เราลบ Coin ออก และลบ Cost (Realize Profit/Loss)
                    # ยกเลิก: ต้องคืน Coin กลับมา และคืน Cost กลับมา (เสมือนว่ายังไม่ได้ขาย)
                    current_cost = current_cost + o_rec
                    current_coin = current_coin + o_amt
                    log_reason = f"Cancelled SELL: Return +{o_amt} Coin, Cost restored +{o_rec:.2f}"

                # อัปเดต DB
                await db.update_cost_coin(s_id, current_cost, current_coin)
                
                # บันทึกประวัติ
                dummy_result = {
                    "id": o_id,
                    "amt": o_amt, # ใน DB เราใช้ชื่อ field amt ก็ให้คงไว้แบบนี้ถูกแล้ว
                    "rat": o_rate,
                    "ts": int(time.time()),
                    "typ": "limit"
                }
                await db.save_order(symbol, dummy_result, log_reason)
                
                # print(f" ↪️ DB Updated: {log_reason}")
                await self.log_and_broadcast(f"🧹 {symbol}: Cancelled {o_side.upper()} {o_id} & Reverted DB.")

            else:
                print(f"   ❌ Cancel failed {symbol} {o_id}: {cancel_res}")
                
        print("🧹 Clear pending orders done.")

    async def process_symbol(self, client, symbol_data):
        sym = symbol_data['symbol']
        status = symbol_data['status']
        
        if status != 'true': return

        # 1. ดึงกราฟ
        df = await self.api.get_candles(client, sym)
        if df is None: return

        # 2. วิเคราะห์
        signal, reason, last_close = self.analyze_market(df, sym)
        
        # --- เช็คสถานะเปลี่ยน ---
        previous_signal = self.last_status.get(sym, "HOLD")
        
        log_message = f"🔍 {sym}: {last_close} | {signal} | {reason}"
        await self.ws_manager.broadcast(log_message)

        if signal != previous_signal:
            # 🟢 [FIXED] เรียกใช้ method ของ class ตัวเองให้ถูกต้อง cancle order
            await self.clear_pending_orders(self.api, client, sym)
            
            if signal in ["BUY", "SELL"]:
                msg = f"🚨 {sym} Status Changed!\nFrom: {previous_signal}\nTo: {signal}\nReason: {reason}\nPrice: {last_close}"
                await self.send_telegram(msg)

            self.last_status[sym] = signal
            
        # 3. ตัดสินใจซื้อขาย (Trading Logic)
        
        # === กรณีสัญญาณสั่งซื้อ (BUY) ===
        if signal == "BUY":
            # เช็คว่ากำลัง process เหรียญนี้อยู่ไหม?
            if sym in self.processing_coins:
                print(f"⏳ {sym} is already being processed. Skip.")
                return # ข้ามไปเลย
            
            # 3.1 ยังไม่มีของ -> ซื้อไม้แรก
            if symbol_data['coin'] == 0:
                if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                    # 🟢 ล็อกเหรียญก่อนสั่งซื้อ
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "BUY", last_close, reason)
                    finally:
                        # 🟢 ปลดล็อกเสมอ ไม่ว่าจะ error หรือสำเร็จ
                        # แต่! ถ้าซื้อสำเร็จ ใน DB จะมี Coin แล้ว Loop หน้าจะไม่เข้าเงื่อนไขนี้เอง
                        # ดังนั้นเราปลดล็อกได้เลยเพื่อให้ Loop หน้าเช็คจาก DB เอา
                        self.processing_coins.remove(sym)
                else:
                     if previous_signal != "BUY":
                        msg = f"⚠️ {sym}: Signal BUY but Money Limit Exceeded ({symbol_data['cost']}/{symbol_data['money_limit']})"
                        await self.log_and_broadcast(msg)
            
            # 3.2 มีของอยู่แล้ว -> ทำ DCA
            else:
                if symbol_data['coin'] > 0:
                    avg_price = symbol_data['cost'] / symbol_data['coin']
                    dca_percentage = config.DCA_DROP_PCT / 100
                    target_dca_price = avg_price * (1 - dca_percentage)
                    
                    if last_close < target_dca_price:
                        if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                            reason_dca = f"{reason} (DCA: Price dropped > {config.DCA_DROP_PCT}%)"
                            await self.execute_trade(client, symbol_data, "BUY", last_close, reason_dca)
                        else:
                            if previous_signal != "BUY":
                                msg = f"⚠️ {sym}: Want to DCA but Money Limit Exceeded"
                                await self.log_and_broadcast(msg)
                    else:
                        msg = f"⏳ {sym}: Signal BUY but Waiting for DCA target (< {target_dca_price:.2f})"
                        # await self.ws_manager.broadcast(msg)

        # === กรณีสัญญาณสั่งขาย (SELL) ===
        elif signal == "SELL":
            if symbol_data['coin'] > 0:
                avg_cost = symbol_data['cost'] / symbol_data['coin']
                
                target_pct = (config.TAKE_PROFIT_PCT + config.FEE_BUFFER) / 100
                target_price = avg_cost * (1 + target_pct)
                
                current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100

                if last_close >= target_price:
                    reason_tp = f"{reason} | 💰 TP (+{current_pnl_pct:.2f}%)"
                    await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp)
                else:
                    pass

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Async Engine v2)")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    start_time = asyncio.get_running_loop().time()
                    
                    # 🟢 1. ตรวจสอบสถานะ Server ก่อนทำอย่างอื่น
                    is_server_ready = await self.check_server_health(client)
                    
                    if not is_server_ready:
                        print(f"💤 Server not ready. Waiting... ({self.last_server_msg})")
                        await asyncio.sleep(30) # รอ 30 วินาทีค่อยเช็คใหม่
                        continue # ข้าม Loop นี้ไปเลย (ไม่เทรด)

                    # --- ถ้า Server OK ถึงจะทำงานต่อ ---
                    
                    # (แนะนำ) ให้ get_active_symbols กรองเฉพาะ status='true' มาเลยจะประหยัด loop
                    symbols = await db.get_active_symbols() 
                    
                    tasks = [self.process_symbol(client, sym) for sym in symbols]
                    await asyncio.gather(*tasks)
                    
                    elapsed = asyncio.get_running_loop().time() - start_time
                    
                    # 🟢 แก้ไข: ใช้ print เฉยๆ เพื่อไม่ให้รกหน้าเว็บ/Telegram
                    # print(f"✅ Processed {len(symbols)} symbols in {elapsed:.2f} seconds. Sleeping...")
                    
                    # ❌ ลบบรรทัดนี้ออก หรือใส่ await ถ้าจำเป็นจริงๆ
                    await self.log_and_broadcast(f"✅ Processed {len(symbols)} symbols in {elapsed:.2f} seconds. Sleeping...")
                                                             
                    await asyncio.sleep(10)

                except Exception as e:
                    # ตรงนี้ต้องมี await
                    await self.log_and_broadcast(f"⚠️ Bot Loop Error: {e}")
                    await asyncio.sleep(5)