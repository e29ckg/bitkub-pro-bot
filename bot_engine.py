import asyncio
import httpx
import logging
import os
import database as db
import indicators as ind
import config  
import utils   
import time
from bitkub import BitkubClient

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
        
        # 🟢 [ใหม่] หน่วยความจำสำหรับเก็บ "ราคาสูงสุด" ของแต่ละเหรียญที่กำลังทำกำไร
        self.trailing_highs = {} 
        # 🟢 [เพิ่มใหม่] ตัวแปรเก็บสภาวะตลาด (กระทิง/หมี/ไซด์เวย์) เพื่อส่งให้หน้าเว็บ
        self.market_regimes = {}
    
    async def send_telegram(self, message):
        if not self.tg_token or not self.chat_id:
            return 
            
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML" 
        }

        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, data=payload, timeout=10.0)
        except Exception as e:
            print(f"Telegram Error: {e}")

    async def check_server_health(self, client):
        status_data = await self.api.get_server_status(client)
        
        is_all_ok = True
        error_messages = []

        if isinstance(status_data, list):
            for item in status_data:
                name = item.get("name", "Unknown")
                status = item.get("status", "error")
                message = item.get("message", "")
                
                if status != "ok":
                    is_all_ok = False
                    error_messages.append(f"{name}: {status} ({message})")
        else:
            is_all_ok = False
            error_messages.append("Invalid Status Response")

        current_msg = "All Systems Operational" if is_all_ok else " | ".join(error_messages)

        if is_all_ok != self.server_status_ok:
            if is_all_ok:
                log_msg = f"✅ Server is back online! ({current_msg})"
                await self.log_and_broadcast(log_msg)
            else:
                log_msg = f"⛔ Server Maintenance/Error Detected! Bot Paused.\nDetails: {current_msg}"
                await self.log_and_broadcast(log_msg) 

            self.server_status_ok = is_all_ok
            self.last_server_msg = current_msg

        return is_all_ok

    async def log_and_broadcast(self, message):
        print(message)
        logging.info(message)
        await self.ws_manager.broadcast(message)
        
        if "BUY" in message or "SELL" in message or "Error" in message or "Active" in message or "Changed" in message:
            await self.send_telegram(message)

    def analyze_market(self, df, symbol, strategy_type=1):
        df["RSI"] = ind.calculate_rsi(df["close"])
        df["MACD"], df["Signal"] = ind.calculate_macd(df["close"])
        df["BB_Mid"], df["BB_Upper"], df["BB_Lower"] = ind.calculate_bollinger_bands(df["close"])
        
        # 🟢 [ใหม่] คำนวณ EMA และ ADX เพื่อวิเคราะห์สภาวะตลาด
        df["EMA_20"] = ind.calculate_ema(df["close"], 20)
        df["EMA_50"] = ind.calculate_ema(df["close"], 50)
        df["ADX"] = ind.calculate_adx(df, 14)
        
        last = df.iloc[-1]
        trend = "Downtrend" if last["MACD"] < last["Signal"] else "Uptrend"
        
        # 🟢 [ใหม่] แปะป้ายบอกสถานะตลาด (Market Regime)
        if last["ADX"] < 25:
            regime = "🦀 Sideways"
        elif last["EMA_20"] > last["EMA_50"]:
            regime = "🐂 Bullish"
        else:
            regime = "🐻 Bearish"
        
        decisions = []
        signal = "HOLD"
        
        if strategy_type == 1:
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

        elif strategy_type == 2:
            if last["RSI"] < 35: 
                signal = "BUY"
                decisions.append(f"Scalp BUY (RSI {last['RSI']:.2f})")
            elif last["RSI"] > 65:
                signal = "SELL"
                decisions.append(f"Scalp SELL (RSI {last['RSI']:.2f})")

        elif strategy_type == 3:
            prev = df.iloc[-2] 
            if prev["MACD"] <= prev["Signal"] and last["MACD"] > last["Signal"]:
                signal = "BUY"
                decisions.append("MACD Golden Cross")
            elif prev["MACD"] >= prev["Signal"] and last["MACD"] < last["Signal"]:
                signal = "SELL"
                decisions.append("MACD Death Cross")
                
        # 🟢 คืนค่า regime กลับไปด้วย
        return signal, ", ".join(decisions), last["close"], regime

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

            buy_volume = cost_st
            res = await self.api.place_order(client, sym, buy_volume, 0, 'buy', type='market')
            
            if res.get('error') == 0:
                result = res['result']
                received_coin = result.get('rec', 0)
                if received_coin == 0: received_coin = buy_volume / price

                new_cost = cost + buy_volume
                new_coin = coin + received_coin
                result['rat'] = price 
                
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"BUY: {reason}")
                await self.log_and_broadcast(f"✅ {sym} BUY Market Success (Got: {received_coin:.8f} Coin)")
            else:
                await self.log_and_broadcast(f"❌ {sym} BUY Error: {res.get('error')}")

        elif action == "SELL":
            if coin <= 0: return
            
            coin_name = sym.split('_')[1] 
            real_balance = float(wallet.get('result', {}).get(coin_name, 0))
            sell_amount = min(coin, real_balance)

            if (sell_amount * price) < 10:
                await db.update_cost_coin(s_id, 0, 0) 
                await self.log_and_broadcast(f"⚠️ {sym}: ตัดขาดทุน/เศษเหรียญ (<10 บาท) รีเซ็ต DB เป็น 0")
                return

            res = await self.api.place_order(client, sym, sell_amount, 0, 'sell', type='market')
            
            if res.get('error') == 0:
                result = res['result']
                thb_rec = result.get('rec', 0)
                if thb_rec == 0: thb_rec = sell_amount * price

                new_cost = max(0, cost - thb_rec)
                new_coin = 0
                result['rat'] = price 
                
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"SELL: {reason}")
                await self.log_and_broadcast(f"✅ {sym} SELL Market Success (Got: {thb_rec:.2f} THB)")
            else:                
                await self.log_and_broadcast(f"❌ {sym} SELL Error: {res.get('error')} | Response: {res.get('result')}")
                if res.get('error') == 18:
                    await db.update_cost_coin(s_id, 0, 0)
                    await self.log_and_broadcast(f"ℹ️ {sym}: Updated DB to 0 due to insufficient balance.")
    
    async def clear_pending_orders(self, bitkub_client, http_client, symbol):
        orders_res = await bitkub_client.get_open_orders(http_client, symbol)
        
        if orders_res.get('error') != 0:
            return

        open_orders = orders_res.get('result', [])
        if not open_orders: return

        current_db_data = await db.get_symbol_by_name(symbol)
        if not current_db_data: return

        current_cost = current_db_data['cost']
        current_coin = current_db_data['coin']
        s_id = current_db_data['id']

        for order in open_orders:
            o_id = order.get('id')
            o_side = order.get('side').lower()
            o_amt = float(order.get('amount', 0)) 
            o_rate = float(order.get('rate', 0))
            o_rec = float(order.get('receive', 0))  
            
            cancel_res = await bitkub_client.cancel_order(http_client, symbol, o_id, o_side)
            
            if cancel_res.get('error') == 0:
                log_reason = ""
                if o_side == 'buy':
                    current_cost = max(0, current_cost - o_amt)
                    current_coin = max(0, current_coin - o_rec)
                    log_reason = f"Cancelled BUY: Revert -{o_amt:.2f} THB"
                elif o_side == 'sell':
                    current_cost = current_cost + o_rec
                    current_coin = current_coin + o_amt
                    log_reason = f"Cancelled SELL: Return +{o_amt} Coin"

                await db.update_cost_coin(s_id, current_cost, current_coin)
                
                dummy_result = {"id": o_id, "amt": o_amt, "rat": o_rate, "ts": int(time.time()), "typ": "limit"}
                await db.save_order(symbol, dummy_result, log_reason)
                await self.log_and_broadcast(f"🧹 {symbol}: Cancelled {o_side.upper()} {o_id}")

    async def process_symbol(self, client, symbol_data):
        sym = symbol_data['symbol']
        status = symbol_data['status']
        strategy_type = symbol_data.get('strategy', 1) 
        
        if status != 'true': return

        df = await self.api.get_candles(client, sym)
        if df is None: return

        # signal, reason, last_close = self.analyze_market(df, sym, strategy_type)
        signal, reason, last_close, regime = self.analyze_market(df, sym, strategy_type)
        self.market_regimes[sym] = regime  # อัปเดตสภาวะตลาดในหน่วยความจำ

        previous_signal = self.last_status.get(sym, "HOLD")
        
        log_message = f"🔍 {sym} (S{strategy_type}): {last_close} | {signal}"
        await self.ws_manager.broadcast(log_message)

        if signal != previous_signal:
            await self.clear_pending_orders(self.api, client, sym)
            self.last_status[sym] = signal
            
        # ==============================================================
        # 🟢 1. ระบบ Trailing Take Profit (TTP) - ทำงานเป็นอิสระจาก Strategy
        # ==============================================================
        if symbol_data['coin'] > 0:
            avg_cost = symbol_data['cost'] / symbol_data['coin']
            current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100
            
            # ดึงค่าจาก config (ใช้ getattr เผื่อลืมเติมตัวแปรใน config.py จะได้ไม่ error)
            activation_target = getattr(config, 'TTP_ACTIVATION_PCT', 1.5) + config.FEE_BUFFER
            drop_limit = getattr(config, 'TTP_DROP_PCT', 0.5)

            # 1.1 ถ้ากำไรถึงเป้า -> เริ่มจำราคาสูงสุด (Activated)
            if current_pnl_pct >= activation_target:
                if sym not in self.trailing_highs or last_close > self.trailing_highs[sym]:
                    self.trailing_highs[sym] = last_close
                    await self.ws_manager.broadcast(f"🚀 {sym}: TTP Activated/Updated! New High: {last_close} (+{current_pnl_pct:.2f}%)")

            # 1.2 ถ้าโหมด TTP ทำงานอยู่ ให้จับตาดูว่าราคาตกจากยอดเขาหรือยัง
            if sym in self.trailing_highs:
                highest_price = self.trailing_highs[sym]
                drawdown_price = highest_price * (1 - (drop_limit / 100)) # คำนวณราคายอมถอย (เช่น ห่างยอด 0.5%)

                if last_close <= drawdown_price:
                    reason_tp = f"🎯 Trailing Take Profit | Drop from High {highest_price} | Sold at +{current_pnl_pct:.2f}%"
                    
                    if sym not in self.processing_coins:
                        self.processing_coins.add(sym)
                        try:
                            await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp)
                            del self.trailing_highs[sym] # ขายแล้ว ล้างความจำทิ้ง
                            return # จบรอบการทำงานทันที ไม่ต้องไปเช็ค Strategy
                        finally:
                            if sym in self.processing_coins:
                                self.processing_coins.remove(sym)

        # 🟢 ล้างความจำทิ้งถ้ายอดเหรียญเป็น 0 แล้ว (เช่น ขายตัดขาดทุนมือ)
        if symbol_data['coin'] == 0 and sym in self.trailing_highs:
            del self.trailing_highs[sym]


        # ==============================================================
        # 🟢 2. ระบบ Strategy หลัก (BUY / SELL ปกติ)
        # ==============================================================
        if signal == "BUY":
            if sym in self.processing_coins:
                return 
            
            if symbol_data['coin'] == 0:
                if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "BUY", last_close, reason)
                    finally:
                        self.processing_coins.remove(sym)
            else:
                if symbol_data['coin'] > 0:
                    avg_price = symbol_data['cost'] / symbol_data['coin']
                    dca_percentage = config.DCA_DROP_PCT / 100
                    target_dca_price = avg_price * (1 - dca_percentage)
                    
                    if last_close < target_dca_price:
                        if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                            reason_dca = f"{reason} (DCA: Price dropped > {config.DCA_DROP_PCT}%)"
                            await self.execute_trade(client, symbol_data, "BUY", last_close, reason_dca)

        elif signal == "SELL":
            if sym in self.processing_coins:
                return 

            if symbol_data['coin'] > 0:
                avg_cost = symbol_data['cost'] / symbol_data['coin']
                current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100
                
                # ถ้า Strategy สั่งให้ขาย จะขายต่อเมื่อกำไรอย่างน้อย 1% (ป้องกันโดนหลอกขายหมู)
                min_profit_pct = 1.0 + config.FEE_BUFFER 

                if current_pnl_pct >= min_profit_pct:
                    reason_tp = f"{reason} | Strategy TP (+{current_pnl_pct:.2f}%)"
                    
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp)
                    finally:
                        self.processing_coins.remove(sym)

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Trailing Take Profit + Multi-Strategy)")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    start_time = asyncio.get_running_loop().time()
                    is_server_ready = await self.check_server_health(client)
                    
                    if not is_server_ready:
                        await asyncio.sleep(30)
                        continue 

                    symbols = await db.get_active_symbols() 
                    
                    for sym in symbols:
                        await self.process_symbol(client, sym)
                        await asyncio.sleep(0.2) 
                    
                    elapsed = asyncio.get_running_loop().time() - start_time
                    # print(f"✅ Processed {len(symbols)} symbols in {elapsed:.2f} seconds.")
                                                                         
                    await asyncio.sleep(10)

                except Exception as e:
                    print(f"⚠️ Bot Loop Error: {e}")
                    await asyncio.sleep(5)