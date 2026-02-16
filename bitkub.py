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

    # --- 🟢 เพิ่มใน Class BitkubClient ---
    async def get_server_status(self, client: httpx.AsyncClient):
        """
        ดึงสถานะ Server (Non-secure และ Secure endpoints)
        """
        try:
            url = f"{self.base_url}/api/status"
            # ไม่ต้อง Sign signature เพราะเป็น Public endpoint
            response = await client.get(url, timeout=5.0)
            
            if response.status_code == 200:
                return response.json()
            else:
                return [{"name": "Error", "status": "error", "message": f"HTTP {response.status_code}"}]
        except Exception as e:
            print(f"Check Status Error: {e}")
            return [{"name": "Connection", "status": "error", "message": str(e)}]

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

    # 🟢 [แก้ไข] ไม่ต้องรับค่า resolution แล้ว ให้ดึงจาก config โดยตรง
    async def get_candles(self, client: httpx.AsyncClient, symbol):
        try:
            query_symbol = utils.normalize_symbol(symbol, to_api=True)
            current_time = int(time.time())
            
            # 🟢 [แก้ไข] ดึงค่า TIMEFRAME จาก config.py
            resolution = config.TIMEFRAME 
            
            # คำนวณเวลาย้อนหลัง: สมมติเอากราฟ 100 แท่งย้อนหลัง
            # (resolution เป็นนาที * 60 วินาที * 100 แท่ง)
            from_time = current_time - (resolution * 60 * 100) 
            
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

        # 🟢 1. ป้องกันเลข Scientific Notation (เช่น 4.7e-05) เปลี่ยนเป็นสติงเรียบๆ
        def num_to_str(n):
            s = f"{float(n):.8f}".rstrip('0').rstrip('.')
            return '0' if s == '' else s

        amt_str = num_to_str(amt)
        rat_str = num_to_str(rat)

        ts = await self.get_server_timestamp(client)

        # 🟢 2. สร้าง JSON String ด้วยตัวเองเพื่อบังคับฟอร์แมตตัวเลข และเรียงคีย์ให้ตรงเป๊ะ
        # คีย์ต้องเรียงตามลำดับตัวอักษร: amt, rat, sym, typ เพื่อให้ทำ Signature ผ่าน
        payload_str = f'{{"amt":{amt_str},"rat":{rat_str},"sym":"{query_symbol}","typ":"{type}"}}'
        
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
            
            if response.status_code != 200:
                print(f"❌ Bitkub API Error ({response.status_code}): {response.text}")
                print(f"   Payload Sent: {payload_str}")
                
            res_json = response.json()
            
            if res_json.get('error') == 0 and isinstance(res_json.get('result'), dict):
                res_json['result']['_req_rat'] = float(rat)
                res_json['result']['_req_amt'] = float(amt)
                
            return res_json
            
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
        
    # --- 🟢 (ใหม่) ดึงออเดอร์ที่ค้างอยู่ ---
    async def get_open_orders(self, client: httpx.AsyncClient, sym):
        endpoint = "/api/v3/market/my-open-orders"
        method = "GET" # 🟢 1. เปลี่ยนเป็น GET ตาม Document
        query_symbol = utils.normalize_symbol(sym, to_api=True).lower()
        
        ts = await self.get_server_timestamp(client)
        
        # 🟢 2. สำหรับ GET V3: Payload คือ Query String (เริ่มด้วย ?)
        # ไม่ต้องใช้ json.dumps แต่ใช้ string format ตรงๆ
        payload_str = f"?sym={query_symbol}" 
        
        # สร้าง Signature (Timestamp + Method + Endpoint + QueryString)
        sig = self._sign_v3(ts, method, endpoint, payload_str)
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
            "X-BTK-TIMESTAMP": str(ts),
            "X-BTK-SIGN": sig
        }
        
        try:
            # 🟢 3. ส่ง Request โดยต่อ URL + Query String
            full_url = f"{self.base_url}{endpoint}{payload_str}"
            response = await client.get(full_url, headers=headers)
            
            # Debug: เช็คว่าตอบอะไรกลับมา ถ้าไม่ใช่ 200
            if response.status_code != 200:
                print(f"❌ API Error {response.status_code}: {response.text}")

            return response.json()
            
        except Exception as e:
            print(f"Get Open Orders Error: {e}")
            return {"error": 999, "result": [], "message": str(e)}

    # --- 🟢 (ใหม่) ยกเลิกออเดอร์ ---
    async def cancel_order(self, client: httpx.AsyncClient, sym, order_id, side):
        endpoint = "/api/v3/market/cancel-order"
        method = "POST"
        query_symbol = utils.normalize_symbol(sym, to_api=True).lower()
        
        ts = await self.get_server_timestamp(client)
        
        # Bitkub V3 Cancel ต้องส่ง sym, id, sd (side)
        payload = {
            "sym": query_symbol,
            "id": str(order_id),
            "sd": side.lower() # 'buy' or 'sell'
        }
        
        payload_str = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        sig = self._sign_v3(ts, method, endpoint, payload_str)
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": self.api_key,
            "X-BTK-TIMESTAMP": str(ts),
            "X-BTK-SIGN": sig
        }
        
        try:
            print(f"🚫 Cancelling order {order_id} ({side})...")
            response = await client.post(f"{self.base_url}{endpoint}", headers=headers, data=payload_str)
            return response.json()
        except Exception as e:
            print(f"Cancel Order Error: {e}")
            return {"error": 999}