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
        self.active_auto_strategies = {} 
        
        # 🟢 [ใหม่] หน่วยความจำสำหรับจดจำว่า "เหรียญไหนแบ่งขายไม้แรกไปแล้วบ้าง"
        self.partial_tp_tracker = set() 
    
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

    def analyze_market(self, df, symbol, strategy_type, coin_balance):
        df["RSI"] = ind.calculate_rsi(df["close"])
        df["MACD"], df["Signal"] = ind.calculate_macd(df["close"])
        df["BB_Mid"], df["BB_Upper"], df["BB_Lower"] = ind.calculate_bollinger_bands(df["close"])
        
        df["EMA_20"] = ind.calculate_ema(df["close"], 20)
        df["EMA_50"] = ind.calculate_ema(df["close"], 50)
        df["ADX"] = ind.calculate_adx(df, 14)
        
        last = df.iloc[-1]
        trend = "Downtrend" if last["MACD"] < last["Signal"] else "Uptrend"
        
        try:
            is_bullish = all(df["EMA_20"].iloc[-i] > df["EMA_50"].iloc[-i] for i in range(1, 4)) and all(df["ADX"].iloc[-i] >= 25 for i in range(1, 4))
            is_bearish = all(df["EMA_20"].iloc[-i] < df["EMA_50"].iloc[-i] for i in range(1, 4)) and all(df["ADX"].iloc[-i] >= 25 for i in range(1, 4))
        except IndexError:
            is_bullish, is_bearish = False, False 

        if is_bullish:
            regime = "🐂 Bullish"
            auto_strat = 3
        elif is_bearish:
            regime = "🐻 Bearish"
            auto_strat = 1
        else:
            regime = "🦀 Sideways"
            auto_strat = 2

        actual_strat = strategy_type
        if strategy_type == 4: 
            if coin_balance > 0:
                actual_strat = self.active_auto_strategies.get(symbol, 1) 
            else:
                actual_strat = auto_strat
                self.active_auto_strategies[symbol] = actual_strat 

        decisions = []
        signal = "HOLD"
        
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
                
        return signal, ", ".join(decisions), last["close"], regime, actual_strat

    # 🟢 เพิ่ม Parameter 'sell_ratio' เพื่อรองรับการแบ่งไม้ขาย
    async def execute_trade(self, client, symbol_data, action, price, reason, sell_ratio=1.0):
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
            
            # 🟢 นำ sell_ratio มาคำนวณจำนวนเหรียญที่จะขาย
            target_sell_coin = coin * sell_ratio
            sell_amount = min(target_sell_coin, real_balance)

            if (sell_amount * price) < 10:
                # ถ้ายอดขายน้อยกว่า 10 บาท ถือว่าเป็นเศษเหรียญ ให้ล้าง DB เป็น 0 ไปเลย
                await db.update_cost_coin(s_id, 0, 0) 
                return

            res = await self.api.place_order(client, sym, sell_amount, 0, 'sell', type='market')
            
            if res.get('error') == 0:
                result = res['result']
                thb_rec = result.get('rec', 0)
                if thb_rec == 0: thb_rec = sell_amount * price
                
                # 🟢 สมการอัปเดตต้นทุนแบบสัดส่วน เพื่อรักษา Avg Price ของเหรียญที่เหลือให้ตรงกับความเป็นจริง
                actual_sell_ratio = sell_amount / coin
                new_coin = coin - sell_amount
                new_cost = cost * (1 - actual_sell_ratio)
                
                # ถ้าขาย 100% หรือเหลือเศษเหรียญน้อยมากๆ ให้ปรับเป็น 0 
                if sell_ratio == 1.0 or new_coin < 1e-8:
                    new_coin = 0
                    new_cost = 0
                
                result['rat'] = price 
                await db.update_cost_coin(s_id, new_cost, new_coin)
                await db.save_order(sym, result, f"SELL: {reason}")
                await self.log_and_broadcast(f"✅ {sym} SELL Market Success (Got: {thb_rec:.2f} THB) | Ratio: {sell_ratio*100}%")
                
                if new_coin == 0:
                    if sym in self.active_auto_strategies: del self.active_auto_strategies[sym]

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
        coin_balance = symbol_data['coin'] 
        
        if status != 'true': return

        df = await self.api.get_candles(client, sym)
        if df is None: return

        signal, reason, last_close, regime, actual_strat = self.analyze_market(df, sym, strategy_type, coin_balance)
        self.market_regimes[sym] = {"regime": regime, "active_strat": actual_strat}

        previous_signal = self.last_status.get(sym, "HOLD")
        log_message = f"🔍 {sym} [S{actual_strat}]: {last_close} | {signal}"
        await self.ws_manager.broadcast(log_message)

        if signal != previous_signal:
            await self.clear_pending_orders(self.api, client, sym)
            self.last_status[sym] = signal
            
        # ==============================================================
        # 🟢 1. ระบบแบ่งขายล็อคกำไร (Scaling Out / Partial TP)
        # ==============================================================
        if coin_balance > 0 and getattr(config, 'PARTIAL_TP_ENABLE', False):
            avg_cost = symbol_data['cost'] / coin_balance
            current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100
            partial_target = getattr(config, 'PARTIAL_TP_TARGET', 3.0) + config.FEE_BUFFER

            if current_pnl_pct >= partial_target and sym not in self.partial_tp_tracker:
                reason_partial = f"💎 Partial TP | Locked Profit at +{current_pnl_pct:.2f}%"
                if sym not in self.processing_coins:
                    self.processing_coins.add(sym)
                    try:
                        sell_ratio = getattr(config, 'PARTIAL_TP_RATIO', 0.5)
                        await self.execute_trade(client, symbol_data, "SELL", last_close, reason_partial, sell_ratio=sell_ratio)
                        
                        # จดจำไว้ว่าเหรียญนี้ได้ทำการแบ่งขายไม้แรกไปเรียบร้อยแล้ว
                        self.partial_tp_tracker.add(sym) 
                        return # จบ Loop ทันทีเพื่อให้ระบบคำนวณฐานข้อมูลใหม่ในรอบถัดไป
                    finally:
                        if sym in self.processing_coins: self.processing_coins.remove(sym)

        # ==============================================================
        # 🟢 2. ระบบ Trailing Take Profit (TTP) - ทำงานกับเหรียญที่เหลือ
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
                    reason_tp = f"🎯 Trailing TP (100%) | Drop from High {highest_price} | Sold at +{current_pnl_pct:.2f}%"
                    if sym not in self.processing_coins:
                        self.processing_coins.add(sym)
                        try:
                            # ขายเหรียญที่เหลือ 100% 
                            await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp, sell_ratio=1.0)
                            return 
                        finally:
                            if sym in self.processing_coins: self.processing_coins.remove(sym)

        # 🟢 เคลียร์ความจำเมื่อขายเหรียญจนหมดพอร์ตแล้ว
        if coin_balance == 0:
            if sym in self.trailing_highs: del self.trailing_highs[sym]
            if sym in self.partial_tp_tracker: self.partial_tp_tracker.remove(sym)

        # ==============================================================
        # 🟢 3. ระบบ Strategy หลัก (BUY / SELL ปกติ)
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
                        # ขายตามสัญญาณ Strategy 100%
                        await self.execute_trade(client, symbol_data, "SELL", last_close, f"{reason} | Strat TP (+{current_pnl_pct:.2f}%)", sell_ratio=1.0)
                    finally:
                        self.processing_coins.remove(sym)

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Scaling Out + Auto-AI Ready)")
        
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