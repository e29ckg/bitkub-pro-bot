import httpx
import time
import json
import hashlib
import hmac
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL", "https://api.bitkub.com")

class BitkubClient:
    def __init__(self):
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-BTK-APIKEY": API_KEY,
        }

    def _sign_payload(self, payload):
        ts = int(time.time())
        payload["ts"] = ts
        payload["sig"] = hmac.new(
            API_SECRET.encode('utf-8'),
            json.dumps(payload).encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return payload

    async def get_candles(self, client: httpx.AsyncClient, symbol, resolution=15):
        try:
            current_time = int(time.time())
            from_time = current_time - (1440 * 60) # 24 hours
            url = f"{BASE_URL}/tradingview/history?symbol={symbol}&resolution={resolution}&from={from_time}&to={current_time}"
            response = await client.get(url, timeout=10.0)
            data = response.json()
            if data["s"] == "ok":
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
        # สร้าง payload สำหรับอ่าน wallet
        payload = {"ts": int(time.time())}
        payload["sig"] = hmac.new(
            API_SECRET.encode('utf-8'),
            json.dumps(payload).encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        try:
            response = await client.post(f"{BASE_URL}/api/market/wallet", json=payload, headers=self.headers)
            return response.json()
        except Exception as e:
            print(f"Wallet API Error: {e}")
            return {"error": 1}

    async def place_order(self, client: httpx.AsyncClient, sym, amt, rat, side, type='limit'):
        payload = {
            "sym": sym,
            "amt": amt,
            "rat": rat,
            "typ": type,
            "ts": int(time.time())
        }
        # Sign Payload
        payload["sig"] = hmac.new(
            API_SECRET.encode('utf-8'),
            json.dumps(payload).encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        endpoint = "/api/market/place-bid" if side == 'buy' else "/api/market/place-ask"
        try:
            response = await client.post(f"{BASE_URL}{endpoint}", json=payload, headers=self.headers)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    async def cancel_order(self, client: httpx.AsyncClient, sym, order_id, side):
         # ... (Implement Cancel Logic similar to place_order) ...
         pass