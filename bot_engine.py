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
        
        self.trailing_highs = {} 
        self.market_regimes = {} 
        # 🟢 [ใหม่] หน่วยความจำสำหรับล็อคกลยุทธ์ (ป้องกัน Open Position Clash)
        self.active_auto_strategies = {} 
    
    async def send_telegram(self, message):
        if not self.tg_token or not self.chat_id: return 
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
        try:
            async with httpx.AsyncClient() as client:
                await client.post(url, data=payload, timeout=10.0)
        except Exception as e: print(f"Telegram Error: {e}")

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
            log_msg = f"✅ Server is back online! ({current_msg})" if is_all_ok else f"⛔ Server Error! Paused.\nDetails: {current_msg}"
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

    # 🟢 เพิ่มการรับค่า coin_balance เข้ามาเพื่อใช้เช็ค Open Position
    def analyze_market(self, df, symbol, strategy_type, coin_balance):
        df["RSI"] = ind.calculate_rsi(df["close"])
        df["MACD"], df["Signal"] = ind.calculate_macd(df["close"])
        df["BB_Mid"], df["BB_Upper"], df["BB_Lower"] = ind.calculate_bollinger_bands(df["close"])
        
        df["EMA_20"] = ind.calculate_ema(df["close"], 20)
        df["EMA_50"] = ind.calculate_ema(df["close"], 50)
        df["ADX"] = ind.calculate_adx(df, 14)
        
        last = df.iloc[-1]
        trend = "Downtrend" if last["MACD"] < last["Signal"] else "Uptrend"
        
        # 🟢 [1. ระบบดักจับ Whipsaw] เช็คย้อนหลัง 3 แท่งเทียนเพื่อความชัวร์ 100%
        try:
            is_bullish = all(df["EMA_20"].iloc[-i] > df["EMA_50"].iloc[-i] for i in range(1, 4)) and all(df["ADX"].iloc[-i] >= 25 for i in range(1, 4))
            is_bearish = all(df["EMA_20"].iloc[-i] < df["EMA_50"].iloc[-i] for i in range(1, 4)) and all(df["ADX"].iloc[-i] >= 25 for i in range(1, 4))
        except IndexError:
            is_bullish, is_bearish = False, False # กราฟไม่พอ

        if is_bullish:
            regime = "🐂 Bullish"
            auto_strat = 3
        elif is_bearish:
            regime = "🐻 Bearish"
            auto_strat = 1
        else:
            # ถ้าตลาดไม่ชัดเจน ถือว่าเป็นไซด์เวย์ทั้งหมด
            regime = "🦀 Sideways"
            auto_strat = 2

        # 🟢 [2. ระบบป้องกัน Open Position Clash]
        actual_strat = strategy_type
        if strategy_type == 4: # ถ้าผู้ใช้เลือกโหมด Auto (Strategy 4)
            if coin_balance > 0:
                # ถ้ามีของในมือ ให้ "ดึงกลยุทธ์เดิมที่ใช้ซื้อ" มาใช้ขายเท่านั้น!
                actual_strat = self.active_auto_strategies.get(symbol, 1) # ถ้าบอทดับแล้วเปิดใหม่ให้ใช้ Strat 1 ขายทิ้งเพื่อความปลอดภัย
            else:
                # ถ้าพอร์ตว่าง ให้เปลี่ยนกลยุทธ์ตามตลาดได้อิสระ
                actual_strat = auto_strat
                self.active_auto_strategies[symbol] = actual_strat # อัปเดตความจำไว้เผื่อมีการซื้อ

        decisions = []
        signal = "HOLD"
        
        # 🟢 ใช้ actual_strat (กลยุทธ์ที่ประมวลผลแล้ว) ในการหาจุดเข้าออก
        if actual_strat == 1:
            if trend == "Downtrend" and last["RSI"] < config.RSI_OVERSOLD:
                signal, decisions = "BUY", [f"RSI Oversold ({last['RSI']:.2f})"]
            elif trend == "Downtrend" and last["close"] < last["BB_Lower"]:
                signal, decisions = "BUY", ["Price < BB Lower"]
            elif trend == "Uptrend" and last["RSI"] > config.RSI_OVERBOUGHT:
                signal, decisions = "SELL", [f"RSI Overbought ({last['RSI']:.2f})"]
            elif trend == "Uptrend" and last["close"] > last["BB_Upper"]:
                signal, decisions = "SELL", ["Price > BB Upper"]

        elif actual_strat == 2:
            if last["RSI"] < 35: 
                signal, decisions = "BUY", [f"Scalp BUY (RSI {last['RSI']:.2f})"]
            elif last["RSI"] > 65:
                signal, decisions = "SELL", [f"Scalp SELL (RSI {last['RSI']:.2f})"]

        elif actual_strat == 3:
            prev = df.iloc[-2] 
            if prev["MACD"] <= prev["Signal"] and last["MACD"] > last["Signal"]:
                signal, decisions = "BUY", ["MACD Golden Cross"]
            elif prev["MACD"] >= prev["Signal"] and last["MACD"] < last["Signal"]:
                signal, decisions = "SELL", ["MACD Death Cross"]
                
        # 🟢 คืนค่า regime และ actual_strat กลับไปให้หน้าเว็บด้วย
        return signal, ", ".join(decisions), last["close"], regime, actual_strat

    async def execute_trade(self, client, symbol_data, action, price, reason):
        s_id = symbol_data['id']
        sym = symbol_data['symbol']
        cost = symbol_data['cost']
        coin = symbol_data['coin']
        cost_st = symbol_data['cost_st']
        
        wallet = await self.api.get_wallet(client)
        
        if action == "BUY":
            thb_balance = wallet.get('result', {}).get('THB', 0)
            if thb_balance < cost_st: return
            res = await self.api.place_order(client, sym, cost_st, 0, 'buy', type='market')
            
            if res.get('error') == 0:
                result = res['result']
                received_coin = result.get('rec', 0)
                if received_coin == 0: received_coin = cost_st / price
                new_cost = cost + cost_st
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
                return

            res = await self.api.place_order(client, sym, sell_amount, 0, 'sell', type='market')
            
            if res.get('error') == 0:
                result = res['result']
                thb_rec = result.get('rec', 0)
                if thb_rec == 0: thb_rec = sell_amount * price
                new_cost = max(0, cost - thb_rec)
                
                result['rat'] = price 
                await db.update_cost_coin(s_id, new_cost, 0) # เซ็ต Coin เป็น 0
                await db.save_order(sym, result, f"SELL: {reason}")
                await self.log_and_broadcast(f"✅ {sym} SELL Market Success (Got: {thb_rec:.2f} THB)")
                
                # 🟢 [เคลียร์ความจำ] เมื่อขายเสร็จ ให้ล้างข้อมูลกลยุทธ์ของโหมด Auto ทิ้ง เพื่อให้รอบหน้าประเมินใหม่
                if sym in self.active_auto_strategies:
                    del self.active_auto_strategies[sym]

            else:                
                if res.get('error') == 18:
                    await db.update_cost_coin(s_id, 0, 0)
                    if sym in self.active_auto_strategies: del self.active_auto_strategies[sym]
    
    async def clear_pending_orders(self, bitkub_client, http_client, symbol):
        orders_res = await bitkub_client.get_open_orders(http_client, symbol)
        if orders_res.get('error') != 0: return
        open_orders = orders_res.get('result', [])
        if not open_orders: return

        current_db_data = await db.get_symbol_by_name(symbol)
        if not current_db_data: return
        current_cost, current_coin, s_id = current_db_data['cost'], current_db_data['coin'], current_db_data['id']

        for order in open_orders:
            o_id, o_side, o_amt, o_rate, o_rec = order.get('id'), order.get('side').lower(), float(order.get('amount', 0)), float(order.get('rate', 0)), float(order.get('receive', 0))
            cancel_res = await bitkub_client.cancel_order(http_client, symbol, o_id, o_side)
            if cancel_res.get('error') == 0:
                if o_side == 'buy':
                    current_cost = max(0, current_cost - o_amt)
                    current_coin = max(0, current_coin - o_rec)
                elif o_side == 'sell':
                    current_cost = current_cost + o_rec
                    current_coin = current_coin + o_amt
                await db.update_cost_coin(s_id, current_cost, current_coin)
                await db.save_order(symbol, {"id": o_id, "amt": o_amt, "rat": o_rate, "ts": int(time.time()), "typ": "limit"}, f"Cancelled {o_side.upper()}")

    async def process_symbol(self, client, symbol_data):
        sym = symbol_data['symbol']
        status = symbol_data['status']
        strategy_type = symbol_data.get('strategy', 1) 
        coin_balance = symbol_data['coin'] # 🟢 ส่ง coin ไปให้ analyze
        
        if status != 'true': return

        df = await self.api.get_candles(client, sym)
        if df is None: return

        # 🟢 รับค่าที่คำนวณแล้วกลับมา
        signal, reason, last_close, regime, actual_strat = self.analyze_market(df, sym, strategy_type, coin_balance)
        
        # 🟢 บันทึกสถานะส่งไปให้เว็บ (เช่น 🐂 Bullish (S3) )
        self.market_regimes[sym] = {"regime": regime, "active_strat": actual_strat}

        previous_signal = self.last_status.get(sym, "HOLD")
        
        log_message = f"🔍 {sym} [S{actual_strat}]: {last_close} | {signal}"
        await self.ws_manager.broadcast(log_message)

        if signal != previous_signal:
            await self.clear_pending_orders(self.api, client, sym)
            self.last_status[sym] = signal
            
        # ==============================================================
        # 🟢 1. ระบบ Trailing Take Profit (TTP)
        # ==============================================================
        if coin_balance > 0:
            avg_cost = symbol_data['cost'] / coin_balance
            current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100
            activation_target = getattr(config, 'TTP_ACTIVATION_PCT', 1.5) + config.FEE_BUFFER
            drop_limit = getattr(config, 'TTP_DROP_PCT', 0.5)

            if current_pnl_pct >= activation_target:
                if sym not in self.trailing_highs or last_close > self.trailing_highs[sym]:
                    self.trailing_highs[sym] = last_close
                    await self.ws_manager.broadcast(f"🚀 {sym}: TTP Activated! New High: {last_close}")

            if sym in self.trailing_highs:
                highest_price = self.trailing_highs[sym]
                drawdown_price = highest_price * (1 - (drop_limit / 100)) 

                if last_close <= drawdown_price:
                    reason_tp = f"🎯 Trailing TP | Drop from High {highest_price} | Sold at +{current_pnl_pct:.2f}%"
                    if sym not in self.processing_coins:
                        self.processing_coins.add(sym)
                        try:
                            await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp)
                            del self.trailing_highs[sym] 
                            return 
                        finally:
                            if sym in self.processing_coins: self.processing_coins.remove(sym)

        if coin_balance == 0 and sym in self.trailing_highs:
            del self.trailing_highs[sym]

        # ==============================================================
        # 🟢 2. ระบบ Strategy หลัก
        # ==============================================================
        if signal == "BUY":
            if sym in self.processing_coins: return 
            
            if coin_balance == 0:
                if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "BUY", last_close, reason)
                    finally:
                        self.processing_coins.remove(sym)
            else:
                if coin_balance > 0:
                    avg_price = symbol_data['cost'] / coin_balance
                    target_dca_price = avg_price * (1 - (config.DCA_DROP_PCT / 100))
                    
                    if last_close < target_dca_price:
                        if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                            await self.execute_trade(client, symbol_data, "BUY", last_close, f"{reason} (DCA)")

        elif signal == "SELL":
            if sym in self.processing_coins: return 

            if coin_balance > 0:
                avg_cost = symbol_data['cost'] / coin_balance
                current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100
                min_profit_pct = 1.0 + config.FEE_BUFFER 

                if current_pnl_pct >= min_profit_pct:
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "SELL", last_close, f"{reason} | Strat TP (+{current_pnl_pct:.2f}%)")
                    finally:
                        self.processing_coins.remove(sym)

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Auto-AI + TTP Ready)")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    start_time = asyncio.get_running_loop().time()
                    if not await self.check_server_health(client):
                        await asyncio.sleep(30); continue 

                    symbols = await db.get_active_symbols() 
                    for sym in symbols:
                        await self.process_symbol(client, sym)
                        await asyncio.sleep(0.2) 
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"⚠️ Bot Loop Error: {e}"); await asyncio.sleep(5)