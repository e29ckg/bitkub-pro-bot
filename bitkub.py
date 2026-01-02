import httpx
import time
import json
import hashlib
import hmac
import os
import pandas as pd
from dotenv import load_dotenv
import utils 
import config

load_dotenv()

class BitkubClient:
    def __init__(self):
        self.api_key = os.getenv("API_KEY")
        self.api_secret = os.getenv("API_SECRET")
        self.base_url = os.getenv("BASE_URL", "https://api.bitkub.com")
        
        # Default Headers
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
        }

    # --- 🟢 (1) ขอเวลา Server เป็น Milliseconds (ตาม Doc V3) ---
    async def get_server_timestamp(self, client: httpx.AsyncClient):
        try:
            response = await client.get(f"{self.base_url}/api/v3/servertime")
            if response.status_code == 200:
                # Doc V3: Response คือตัวเลข timestamp (ms) เพียวๆ
                return int(response.text)
            else:
                print(f"⚠️ Get Server Time Failed: {response.text}")
                # Fallback: ใช้เวลาเครื่อง * 1000 ให้เป็น ms
                return int(time.time() * 1000)
        except Exception as e:
            print(f"⚠️ Get Server Time Error: {e}")
            return int(time.time() * 1000)

    # --- 🟢 (2) สร้าง Signature แบบ V3 ---
    # สูตร: HMAC_SHA256( Timestamp + Method + Endpoint + Payload )
    def _sign_v3(self, timestamp_ms, method, endpoint, payload_str):
        # รวม String ตามลำดับที่ Doc กำหนด
        sig_payload = f"{timestamp_ms}{method}{endpoint}{payload_str}"
        
        return hmac.new(
            self.api_secret.encode('utf-8'),
            sig_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    async def get_candles(self, client: httpx.AsyncClient, symbol, resolution=15):
        try:
            query_symbol = utils.normalize_symbol(symbol, to_api=True)
            current_time = int(time.time())
            from_time = current_time - (1440 * 60)
            
            url = f"{self.base_url}/tradingview/history?symbol={query_symbol}&resolution={resolution}&from={from_time}&to={current_time}"
            response = await client.get(url, timeout=10.0)
            data = response.json()
            
            if data.get("s") == "ok":
                df = pd.DataFrame({
                    "timestamp": pd.to_datetime(data["t"], unit="s"),
                    "close": data["c"],
                    "high": data["h"],
                    "low": data["l"]
                })
                return df
            return None
        except Exception as e:
            print(f"Error fetching candles for {symbol}: {e}")
            return None        
        
    async def get_wallet(self, client: httpx.AsyncClient):
        endpoint = "/api/v3/market/wallet"
        method = "POST"
        
        # 🟢 ขอเวลา (ms)
        ts = await self.get_server_timestamp(client)
        
        # Wallet V3 ไม่มี Parameter แต่เป็น POST จึงส่ง Empty JSON
        payload = {}
        payload_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # สร้าง Signature
        sig = self._sign_v3(ts, method, endpoint, payload_str)
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
            "X-BTK-TIMESTAMP": str(ts),
            "X-BTK-SIGN": sig
        }
        
        try:
            # ส่ง payload_str (ซึ่งคือ "{}")
            response = await client.post(f"{self.base_url}{endpoint}", headers=headers, data=payload_str)
            return response.json()
        except Exception as e:
            print(f"Wallet API Error: {e}")
            return {"error": 1}

    async def place_order(self, client: httpx.AsyncClient, sym, amt, rat, side, type='limit'):
        query_symbol = utils.normalize_symbol(sym, to_api=True).lower()

        if side.upper() == 'BUY':
            endpoint = "/api/v3/market/place-bid"
        elif side.upper() == 'SELL':
            endpoint = "/api/v3/market/place-ask"
        else:
            return {'error': 999, 'result': 'Invalid side'}
        
        method = "POST"

        # แปลงเป็น float เพื่อคำนวณ
        safe_amt = float(amt)
        safe_rat = float(rat)

        # --- 🟢 (NEW) ตรวจสอบขั้นต่ำ 10 บาท ก่อนยิง API ---
        # ป้องกัน Error 12 จากฝั่ง Client เลย ไม่ต้องรอ Server ตอบกลับ
        total_value = safe_amt * safe_rat
        if total_value < 10:
            print(f"⚠️ Order Rejected (Client-side): มูลค่ารวม {total_value} บาท (ต่ำกว่าขั้นต่ำ 10 บาท)")
            return {
                "error": 12, 
                "result": f"Amount too low. Total value: {total_value} THB (Min: 10 THB)"
            }
        # ------------------------------------------------

        # 🟢 ปรับปรุงการจัดการทศนิยม
        # Amount ควรมีทศนิยมได้เยอะ (เช่น 8 ตำแหน่ง) ส่วน Price (THB) เอา 2 ตำแหน่ง
        def clean_num(n, is_amt=False):
            if n == int(n): return int(n)
            if is_amt:
                # ถ้าเป็น Amount ให้ปัดไม่เกิน 8 ตำแหน่ง เพื่อป้องกัน 0.00001 กลายเป็น 0.00
                return round(float(n), 8) 
            return round(float(n), 2)

        # 🟢 ขอเวลา (ms)
        ts = await self.get_server_timestamp(client)

        # 🟢 Payload V3
        payload = {
            "sym": query_symbol,
            "amt": clean_num(safe_amt, is_amt=True), # ใช้ is_amt=True
            "rat": clean_num(safe_rat, is_amt=False),
            "typ": type
        }

        # แปลงเป็น JSON String ห้ามมีเว้นวรรค
        payload_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        
        # 🟢 สร้าง Signature (Timestamp + Method + Endpoint + Payload)
        sig = self._sign_v3(ts, method, endpoint, payload_str)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
            "X-BTK-TIMESTAMP": str(ts),
            "X-BTK-SIGN": sig
        }

        url = f"{self.base_url}{endpoint}"
        try:
            response = await client.post(url, headers=headers, data=payload_str)
            
            # Debug Error กรณีมีปัญหา
            if response.status_code != 200:
                print(f"❌ Bitkub API Error ({response.status_code}): {response.text}")
                print(f"   Payload Sent: {payload_str}")
                
            return response.json()
        except Exception as e:
            return {"error": -1, "result": str(e)}

    async def get_bids(self, client: httpx.AsyncClient, sym, limit=5):
        query_symbol = utils.normalize_symbol(sym, to_api=True)
        try:
            url = f"{self.base_url}/api/v3/market/bids?sym={query_symbol}&lmt={limit}"
            response = await client.get(url, headers=self.headers)
            return response.json()
        except Exception as e:
            print(f"Error fetching bids for {sym}: {e}")
            return {"error": 1, "result": []}