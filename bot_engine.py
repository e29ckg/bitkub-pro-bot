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

    # 🟢 [แก้ไข] เพิ่มพารามิเตอร์ strategy_type เข้ามา
    def analyze_market(self, df, symbol, strategy_type=1):
        df["RSI"] = ind.calculate_rsi(df["close"])
        df["MACD"], df["Signal"] = ind.calculate_macd(df["close"])
        df["BB_Mid"], df["BB_Upper"], df["BB_Lower"] = ind.calculate_bollinger_bands(df["close"])
        
        last = df.iloc[-1]
        trend = "Downtrend" if last["MACD"] < last["Signal"] else "Uptrend"
        
        decisions = []
        signal = "HOLD"
        
        # ==========================================
        # 🟢 STRATEGY 1: Trend & Reversal (ดั้งเดิม/ปลอดภัย)
        # ==========================================
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

        # ==========================================
        # 🟢 STRATEGY 2: RSI Scalping (เล่นสั้น เน้นรอบไว)
        # ==========================================
        elif strategy_type == 2:
            if last["RSI"] < 35: 
                signal = "BUY"
                decisions.append(f"Scalp BUY (RSI {last['RSI']:.2f})")
            elif last["RSI"] > 65:
                signal = "SELL"
                decisions.append(f"Scalp SELL (RSI {last['RSI']:.2f})")

        # ==========================================
        # 🟢 STRATEGY 3: MACD Cross (ตามเทรนด์ เล่นรอบใหญ่)
        # ==========================================
        elif strategy_type == 3:
            prev = df.iloc[-2] 
            
            # Golden Cross (ตัดขึ้น)
            if prev["MACD"] <= prev["Signal"] and last["MACD"] > last["Signal"]:
                signal = "BUY"
                decisions.append("MACD Golden Cross")
                
            # Death Cross (ตัดลง)
            elif prev["MACD"] >= prev["Signal"] and last["MACD"] < last["Signal"]:
                signal = "SELL"
                decisions.append("MACD Death Cross")
                
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
            
            # 🟢 [แก้ไข] ดึงยอดเหรียญจริงในกระเป๋า ป้องกันปัญหาจำนวนเหรียญใน DB ไม่ตรงกับความจริง
            coin_name = sym.split('_')[1] # ตัด THB_BTC เอาแค่ BTC
            real_balance = float(wallet.get('result', {}).get(coin_name, 0))
            
            # ใช้จำนวนที่ "น้อยที่สุด" เพื่อไม่ให้ขายเกินตัว
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
                await self.log_and_broadcast(f"❌ {sym} SELL Error: {res.get('error')}")
                if res.get('error') == 18:
                    await db.update_cost_coin(s_id, 0, 0)
                    await self.log_and_broadcast(f"ℹ️ {sym}: Updated DB to 0 due to insufficient balance.")
    
    async def clear_pending_orders(self, bitkub_client, http_client, symbol):
        print(f"🧹 Checking pending orders for {symbol}...")
        
        orders_res = await bitkub_client.get_open_orders(http_client, symbol)
        
        if orders_res.get('error') != 0:
            await self.log_and_broadcast(f"❌ Failed to get open orders {symbol}: {orders_res}")
            return

        open_orders = orders_res.get('result', [])
        if not open_orders: return

        print(f"⚠️ {symbol}: Found {len(open_orders)} pending orders. Cancelling & Reverting DB...")

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
                print(f"   ✅ Cancelled {o_id} ({o_side}) success.")
                total_value = o_amt * o_rate
                log_reason = ""

                if o_side == 'buy':
                    current_cost = max(0, current_cost - o_amt)
                    current_coin = max(0, current_coin - o_rec)
                    log_reason = f"Cancelled BUY: Revert -{o_amt:.2f} THB, -{o_rec} Coin"
                elif o_side == 'sell':
                    current_cost = current_cost + o_rec
                    current_coin = current_coin + o_amt
                    log_reason = f"Cancelled SELL: Return +{o_amt} Coin, Cost restored +{o_rec:.2f}"

                await db.update_cost_coin(s_id, current_cost, current_coin)
                
                dummy_result = {
                    "id": o_id,
                    "amt": o_amt, 
                    "rat": o_rate,
                    "ts": int(time.time()),
                    "typ": "limit"
                }
                await db.save_order(symbol, dummy_result, log_reason)
                await self.log_and_broadcast(f"🧹 {symbol}: Cancelled {o_side.upper()} {o_id} & Reverted DB.")

            else:
                print(f"   ❌ Cancel failed {symbol} {o_id}: {cancel_res}")

    async def process_symbol(self, client, symbol_data):
        sym = symbol_data['symbol']
        status = symbol_data['status']
        # 🟢 ดึง strategy จาก DB (ค่า Default คือ 1)
        strategy_type = symbol_data.get('strategy', 1) 
        
        if status != 'true': return

        df = await self.api.get_candles(client, sym)
        if df is None: return

        # 🟢 ส่ง strategy_type เข้าไปเพื่อวิเคราะห์ให้ตรงกลยุทธ์
        signal, reason, last_close = self.analyze_market(df, sym, strategy_type)
        
        previous_signal = self.last_status.get(sym, "HOLD")
        
        log_message = f"🔍 {sym} (S{strategy_type}): {last_close} | {signal} | {reason}"
        await self.ws_manager.broadcast(log_message)

        if signal != previous_signal:
            await self.clear_pending_orders(self.api, client, sym)
            
            if signal in ["BUY", "SELL"]:
                msg = f"🚨 {sym} Status Changed!\nStrategy: {strategy_type}\nFrom: {previous_signal}\nTo: {signal}\nReason: {reason}\nPrice: {last_close}"
                await self.send_telegram(msg)

            self.last_status[sym] = signal
            
        # === กรณีสัญญาณสั่งซื้อ (BUY) ===
        if signal == "BUY":
            if sym in self.processing_coins:
                print(f"⏳ {sym} is already being processed. Skip.")
                return 
            
            if symbol_data['coin'] == 0:
                if symbol_data['cost'] + symbol_data['cost_st'] <= symbol_data['money_limit']:
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "BUY", last_close, reason)
                    finally:
                        self.processing_coins.remove(sym)
                else:
                     if previous_signal != "BUY":
                        msg = f"⚠️ {sym}: Signal BUY but Money Limit Exceeded ({symbol_data['cost']}/{symbol_data['money_limit']})"
                        await self.log_and_broadcast(msg)
            
            # 🟢 3.2 DCA (ซื้อถัว) - ปรับเป็น Smart DCA นิดหน่อย
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
                        pass # รอให้ราคาตกถึงเป้า DCA ก่อน

        # === กรณีสัญญาณสั่งขาย (SELL) ===
        elif signal == "SELL":
            if sym in self.processing_coins:
                print(f"⏳ {sym} is already being sold. Skip.")
                return 

            if symbol_data['coin'] > 0:
                avg_cost = symbol_data['cost'] / symbol_data['coin']
                current_pnl_pct = ((last_close - avg_cost) / avg_cost) * 100
                min_profit_pct = 1.0 + config.FEE_BUFFER 

                if current_pnl_pct >= min_profit_pct:
                    reason_tp = f"{reason} | 💰 TP (+{current_pnl_pct:.2f}%)"
                    
                    self.processing_coins.add(sym)
                    try:
                        await self.execute_trade(client, symbol_data, "SELL", last_close, reason_tp)
                    finally:
                        self.processing_coins.remove(sym)
                else:
                    pass

    async def run_loop(self):
        self.running = True
        await self.log_and_broadcast("🚀 Bot Started (Multi-Strategy & Market Order)")
        
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    start_time = asyncio.get_running_loop().time()
                    
                    is_server_ready = await self.check_server_health(client)
                    
                    if not is_server_ready:
                        print(f"💤 Server not ready. Waiting... ({self.last_server_msg})")
                        await asyncio.sleep(30)
                        continue 

                    symbols = await db.get_active_symbols() 
                    
                    for sym in symbols:
                        await self.process_symbol(client, sym)
                        await asyncio.sleep(0.2) 
                    
                    elapsed = asyncio.get_running_loop().time() - start_time
                    print(f"✅ Processed {len(symbols)} symbols in {elapsed:.2f} seconds. Sleeping...")
                                                                         
                    await asyncio.sleep(10)

                except Exception as e:
                    print(f"⚠️ Bot Loop Error: {e}")
                    await asyncio.sleep(5)