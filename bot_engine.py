import asyncio
import httpx
import logging
import os
import database as db
import indicators as ind
import config  # <--- เรียกใช้ค่า Config
import utils   # <--- (เผื่อเรียกใช้ในอนาคต)
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
    
    async def send_telegram(self, message):
        if not self.tg_token or not self.chat_id:
            return 
            
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.get(url, params={"chat_id": self.chat_id, "text": message})
        except Exception as e:
            print(f"Telegram Error: {e}")

    async def log_and_broadcast(self, message):
        print(message)
        logging.info(message)
        await self.ws_manager.broadcast(message)
        
        if "BUY" in message or "SELL" in message or "Error" in message or "Active" in message:
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

            res = await self.api.place_order(client, sym, cost_st, price, 'buy')
            if res.get('error') == 0:
                result = res['result']
                new_cost = cost + result['amt']
                new_coin = coin + result['rec']
                
                # --- ใช้ Async DB (ไม่ต้องมี to_thread) ---
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"BUY: {reason}")
                
                await self.log_and_broadcast(f"✅ {sym} BUY Success @ {price}")
            else:
                await self.log_and_broadcast(f"❌ {sym} BUY Error: {res.get('error')}")

        elif action == "SELL":
            if coin <= 0: return

            res = await self.api.place_order(client, sym, coin, price, 'sell')
            if res.get('error') == 0:
                result = res['result']
                new_cost = max(0, cost - result['rec'])
                new_coin = max(0, coin - result['amt'])
                
                # --- ใช้ Async DB ---
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"SELL: {reason}")
                
                await self.log_and_broadcast(f"✅ {sym} SELL Success @ {price}")
    
    async def clear_pending_orders(self, bitkub_client, http_client, symbol):
        """
        ฟังก์ชันนี้จะเช็คว่ามีออเดอร์ค้างไหม ถ้ามีจะยกเลิกให้หมด
        """
        print(f"🧹 Checking pending orders for {symbol}...")
        
        # 1. ดึงออเดอร์ที่ค้างอยู่
        orders_res = await bitkub_client.get_open_orders(http_client, symbol)
        
        if orders_res.get('error') != 0:
            print(f"❌ Failed to get open orders: {orders_res}")
            return

        open_orders = orders_res.get('result', [])
        
        if not open_orders:
            print(f"✅ No pending orders for {symbol}.")
            return

        # 2. วนลูปยกเลิกทุกตัว
        print(f"⚠️ Found {len(open_orders)} pending orders. Cancelling...")
        
        for order in open_orders:
            # โครงสร้าง result ของ open-orders: {'id': '...', 'side': 'buy', ...}
            o_id = order.get('id')
            o_side = order.get('side') # buy หรือ sell
            
            cancel_res = await bitkub_client.cancel_order(http_client, symbol, o_id, o_side)
            
            if cancel_res.get('error') == 0:
                print(f"   ✅ Cancelled {o_id} success.")
            else:
                print(f"   ❌ Cancel failed {o_id}: {cancel_res}")
                
        print("🧹 Clear pending orders done.")

    async def process_symbol(self, client, symbol_data):
        bk = BitkubClient()
        sym = symbol_data['symbol']
        status = symbol_data['status']
        
        if status != 'true': return

        # 1. ดึงกราฟ
        df = await self.api.get_candles(client, sym)
        if df is None: return

        # 2. วิเคราะห์
        signal, reason, last_close = self.analyze_market(df, sym)
        
        # --- เช็คสถานะเปลี่ยน (Telegram Alert) ---
        previous_signal = self.last_status.get(sym, "N/A")
        
        log_message = f"🔍 {sym}: {last_close} | {signal} | {reason}"
        await self.ws_manager.broadcast(log_message)

        if signal != previous_signal:
            await self.clear_pending_orders(bk, client, sym)
            if signal in ["BUY", "SELL"]:
                msg = f"🚨 {sym} Status Changed!\nFrom: {previous_signal}\nTo: {signal}\nReason: {reason}\nPrice: {last_close}"
                await self.send_telegram(msg)

            self.last_status[sym] = signal
            
        # 3. ตัดสินใจซื้อขาย (Trading Logic)
        
        # === กรณีสัญญาณสั่งซื้อ (BUY) ===
        if signal == "BUY":
            # 3.1 ยังไม่มีของ -> ซื้อไม้แรก
            if symbol_data['coin'] == 0:
                 if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                     await self.execute_trade(client, symbol_data, "BUY", last_close, reason)
                 else:
                     # 🔴 เพิ่มแจ้งเตือน: เงินเต็มงบ
                     msg = f"⚠️ {sym}: Signal BUY but Money Limit Exceeded ({symbol_data['cost']}/{symbol_data['money_limit']})"
                     await self.log_and_broadcast(msg)
            
            # 3.2 มีของอยู่แล้ว -> ทำ DCA
            else:
                if symbol_data['coin'] > 0:
                    avg_price = symbol_data['cost'] / symbol_data['coin']
                    dca_percentage = config.DCA_DROP_PCT / 100
                    target_dca_price = avg_price * (1 - dca_percentage)
                    
                    # เช็คราคา: ลงมาเยอะพอหรือยัง?
                    if last_close < target_dca_price:
                        # เช็คเงิน: พอให้ซื้อเพิ่มไหม?
                        if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                            reason_dca = f"{reason} (DCA: Price dropped > {config.DCA_DROP_PCT}%)"
                            await self.execute_trade(client, symbol_data, "BUY", last_close, reason_dca)
                        else:
                            # 🔴 เพิ่มแจ้งเตือน: จะ DCA แต่เงินเต็มงบ
                            msg = f"⚠️ {sym}: Want to DCA but Money Limit Exceeded"
                            await self.log_and_broadcast(msg)
                    else:
                        # 🔴 เพิ่มแจ้งเตือน: สัญญาณมา แต่ราคายังลงไม่ถึงเป้า DCA
                        # (อันนี้อาจจะไม่ต้องส่งเข้า Telegram ก็ได้ เพราะมันจะแจ้งบ่อย ถ้าอยากให้แจ้งก็เอา comment ออก)
                        msg = f"⏳ {sym}: Signal BUY but Waiting for DCA target (< {target_dca_price:.2f})"
                        await self.ws_manager.broadcast(msg) # ส่งเข้าเว็บอย่างเดียวพอ กันรำคาญ

        # === กรณีสัญญาณสั่งขาย (SELL) ===
        elif signal == "SELL":
            if symbol_data['coin'] > 0:
                avg_cost = symbol_data['cost'] / symbol_data['coin']
                
                # คำนวณเป้ากำไรจาก Config (กำไร + ค่าธรรมเนียม)
                target_pct = (config.TAKE_PROFIT_PCT + config.FEE_BUFFER) / 100
                target_price = avg_cost * (1 + target_pct)
                
                current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100

                if last_close >= target_price:
                    reason_tp = f"{reason} | 💰 Take Profit (+{current_pnl_pct:.2f}%)"
                    await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp)
                else:
                    msg = f"🛡️ {sym}: Signal SELL but Price ({last_close}) < Target ({target_price:.2f}). Holding... (PNL: {current_pnl_pct:.2f}%)"
                    await self.log_and_broadcast(msg)

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Async Engine v2 Refactored)")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    start_time = asyncio.get_running_loop().time()
                    
                    # --- ใช้ Async DB (ไม่ต้องมี to_thread) ---
                    symbols = await db.get_symbols()
                    
                    tasks = [self.process_symbol(client, sym) for sym in symbols]
                    await asyncio.gather(*tasks)
                    
                    elapsed = asyncio.get_running_loop().time() - start_time
                    await self.log_and_broadcast(f"⏱️ Loop finished in {elapsed:.2f}s. Waiting...")
                    
                    await asyncio.sleep(10)

                except Exception as e:
                    await self.log_and_broadcast(f"⚠️ Bot Loop Error: {e}")
                    await asyncio.sleep(5)