import asyncio
import httpx
import logging
import os
import database as db
import indicators as ind
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
            return # ถ้าไม่ได้ตั้งค่าไว้ ก็ข้ามไป
            
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        try:
            # ใช้ client ชั่วคราวส่งข้อความไวๆ
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

    # async def log_and_broadcast(self, message):
    #     print(message)
    #     logging.info(message)
    #     await self.ws_manager.broadcast(message)

    def analyze_market(self, df, symbol):
        # คำนวณ Indicators
        df["RSI"] = ind.calculate_rsi(df["close"])
        df["MACD"], df["Signal"] = ind.calculate_macd(df["close"])
        df["BB_Mid"], df["BB_Upper"], df["BB_Lower"] = ind.calculate_bollinger_bands(df["close"])
        
        last = df.iloc[-1]
        trend = "Downtrend" if last["MACD"] < last["Signal"] else "Uptrend"
        
        decisions = []
        signal = "HOLD"
        
        # Logic การตัดสินใจ (ตัวอย่างย่อจากโค้ดเดิม)
        if trend == "Downtrend":
            if last["RSI"] < 30:
                signal = "BUY"
                decisions.append(f"RSI Oversold ({last['RSI']:.2f})")
            elif last["close"] < last["BB_Lower"]:
                signal = "BUY"
                decisions.append("Price < BB Lower")
        elif trend == "Uptrend":
            if last["RSI"] > 70:
                signal = "SELL"
                decisions.append(f"RSI Overbought ({last['RSI']:.2f})")
            elif last["close"] > last["BB_Upper"]:
                signal = "SELL"
                decisions.append("Price > BB Upper")
                
        return signal, ", ".join(decisions), last["close"]

    async def execute_trade(self, client, symbol_data, action, price, reason):
        # แกะข้อมูล symbol (ปรับให้ตรงกับ dict ที่ return จาก database.py)
        # database.py return dict: {'id': 1, 'symbol': 'THB_BTC', ...}
        s_id = symbol_data['id']
        sym = symbol_data['symbol']
        cost = symbol_data['cost']
        coin = symbol_data['coin']
        cost_st = symbol_data['cost_st']
        
        wallet = await self.api.get_wallet(client) # เช็คเงินจริง
        
        if action == "BUY":
            # ตรวจสอบเงินบาทใน wallet (key คือ THB)
            thb_balance = wallet.get('result', {}).get('THB', 0)
            
            if thb_balance < cost_st:
                await self.log_and_broadcast(f"⚠️ {sym}: ไม่พอซื้อ (มี {thb_balance} บาท)")
                return

            res = await self.api.place_order(client, sym, cost_st, price, 'buy')
            if res.get('error') == 0:
                result = res['result']
                # อัปเดต DB
                new_cost = cost + result['amt'] # amt คือจำนวนเงินที่ใช้
                new_coin = coin + result['rec'] # rec คือเหรียญที่ได้
                
                # เรียกใช้ synchronous DB function ใน thread แยก
                await asyncio.to_thread(db.update_cost_coin, s_id, new_cost, new_coin, price)
                await asyncio.to_thread(db.save_order, result, f"BUY: {reason}")
                
                await self.log_and_broadcast(f"✅ {sym} BUY Success @ {price}")
            else:
                await self.log_and_broadcast(f"❌ {sym} BUY Error: {res.get('error')}")

        elif action == "SELL":
            if coin <= 0: return

            res = await self.api.place_order(client, sym, coin, price, 'sell')
            if res.get('error') == 0:
                result = res['result']
                new_cost = max(0, cost - result['rec']) # rec คือเงินบาทที่ได้
                new_coin = max(0, coin - result['amt']) # amt คือเหรียญที่ขาย
                
                await asyncio.to_thread(db.update_cost_coin, s_id, new_cost, new_coin, price)
                await asyncio.to_thread(db.save_order, result, f"SELL: {reason}")
                
                await self.log_and_broadcast(f"✅ {sym} SELL Success @ {price}")

    # ในไฟล์ bot_engine.py (เลื่อนลงไปหาฟังก์ชัน process_symbol)

    async def process_symbol(self, client, symbol_data):
        sym = symbol_data['symbol']
        status = symbol_data['status']
        
        if status != 'true': return

        # 1. ดึงกราฟ
        df = await self.api.get_candles(client, sym)
        if df is None: return

        # 2. วิเคราะห์
        signal, reason, last_close = self.analyze_market(df, sym)
        
        # --- [ส่วนที่แก้ไขใหม่] เช็คว่าสถานะเปลี่ยนไหม ---
        
        # ดึงสถานะเก่าออกมา (ถ้าไม่มีให้เป็น 'N/A')
        previous_signal = self.last_status.get(sym, "N/A")
        
        # สร้างข้อความ Log
        log_message = f"🔍 {sym}: {last_close} | {signal} | {reason}"
        
        # ส่ง WebSocket ไปหน้าเว็บตลอดเวลา (เพื่อให้กราฟขยับ)
        print(log_message)
        logging.info(log_message)
        await self.ws_manager.broadcast(log_message)

        # *** เงื่อนไขการส่ง TELEGRAM ***
        # ส่งเฉพาะเมื่อ: 
        # 1. สถานะเปลี่ยน (เช่น HOLD -> BUY)
        # 2. และต้องไม่ใช่สถานะ HOLD (ยกเว้นคุณอยากรู้ตอนมันกลับมาปกติ)
        if signal != previous_signal:
            if signal in ["BUY", "SELL"]:
                msg = f"🚨 {sym} Status Changed!\nFrom: {previous_signal}\nTo: {signal}\nReason: {reason}\nPrice: {last_close}"
                await self.send_telegram(msg)
            
            # อัปเดตความจำใหม่
            self.last_status[sym] = signal
            
        # ---------------------------------------------------

        # 3. ตัดสินใจซื้อขาย (Trading Logic) - ส่วนนี้เหมือนเดิม
        if signal == "BUY" and symbol_data['cost'] == 0:
             await self.execute_trade(client, symbol_data, "BUY", last_close, reason)
        
        elif signal == "SELL" and symbol_data['coin'] > 0:
             await self.execute_trade(client, symbol_data, "SELL", last_close, reason)

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Async Engine)")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    start_time = asyncio.get_running_loop().time()
                    
                    # อ่านข้อมูลจาก DB (รันใน thread แยก)
                    symbols = await asyncio.to_thread(db.get_symbols)
                    
                    # สร้าง Tasks เพื่อรันทุกเหรียญพร้อมกัน
                    tasks = [self.process_symbol(client, sym) for sym in symbols]
                    await asyncio.gather(*tasks)
                    
                    # คำนวณเวลาที่ใช้
                    elapsed = asyncio.get_running_loop().time() - start_time
                    await self.log_and_broadcast(f"⏱️ Loop finished in {elapsed:.2f}s. Waiting...")
                    
                    await asyncio.sleep(10) # พัก 10 วินาที

                except Exception as e:
                    await self.log_and_broadcast(f"⚠️ Bot Loop Error: {e}")
                    await asyncio.sleep(5)