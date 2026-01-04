from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import database as db
import utils 
from bitkub import BitkubClient 
import httpx
from bot_engine import BotEngine
from fastapi.staticfiles import StaticFiles
import os

BOT_PASSWORD = os.getenv("BOT_PASSWORD", "1234")

# เริ่มต้น DB (ถ้า init_db เป็น sync ให้เรียกตรงนี้ได้เลย)
db.init_db() 

app = FastAPI()

# --- Static Files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # วนลูปส่งข้อความ (ต้อง copy list เพื่อป้องกัน error เวลา list เปลี่ยนขนาดขณะวนลูป)
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(message)
            except:
                self.disconnect(connection)

ws_manager = ConnectionManager()
bot = BotEngine(ws_manager)

# --- Pydantic Models ---
class UpdateSymbolModel(BaseModel):
    status: str
    money_limit: float
    cost_st: float

# --- Pydantic Model สำหรับ Test Trade ---
class TestTradeModel(BaseModel):
    symbol: str   # เช่น BTC
    amount: float # เงินบาทที่จะซื้อ (BUY) หรือ จำนวนเหรียญที่จะขาย (SELL)
    rate: float   # ราคาที่ต้องการ (ถ้าใส่ 0 = Market Price แต่ Bitkub แนะนำให้ใส่ราคา)

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    token = request.cookies.get("access_token")
    if token == "logged_in_success":
        try:
            with open("dashboard.html", "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "Dashboard file not found."
    
    try:
        with open("login.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Login file not found."
    
@app.post("/login")
async def login(response: Response, password: str = Form(...)):
    if password == BOT_PASSWORD:
        content = {"message": "Login Success"}
        response = JSONResponse(content=content)
        response.set_cookie(key="access_token", value="logged_in_success", httponly=True)
        return response
    else:
        raise HTTPException(status_code=401, detail="Incorrect Password")
    
@app.post("/logout")
async def logout(response: Response):
    content = {"message": "Logout Success"}
    response = JSONResponse(content=content)
    response.delete_cookie(key="access_token")
    return response

@app.post("/start")
async def start_bot():
    if bot.running:
        return {"message": "Bot is already running"}
    asyncio.create_task(bot.run_loop())
    return {"message": "Bot start command received"}

@app.post("/stop")
async def stop_bot():
    bot.running = False
    return {"message": "Bot stopping..."}

# --- 🟢 ส่วนที่แก้ไขให้เป็น Async Database ---

@app.get("/symbols")
async def get_symbols(): # ต้องเป็น async
    return await db.get_symbols() # ต้องมี await

@app.post("/add_symbol")
async def add_symbol(request: Request):
    data = await request.json()
    
    # 1. ใช้ Utils แปลงชื่อเหรียญให้เป็น THB_BTC เสมอ
    raw_symbol = data.get("symbol", "")
    symbol = utils.normalize_symbol(raw_symbol, to_api=False)

    # 2. ดึงค่า Config (ต้องดึงจาก data มาก่อน)
    money_limit = float(data.get("money_limit", 1000))
    cost_st = float(data.get("cost_st", 100))

    # 3. เรียก DB แบบ Async
    success = await db.add_symbol(symbol, money_limit, cost_st)
    
    if success:
        return {"status": "success", "message": f"Added {symbol}"}
    else:
        return {"status": "error", "message": "Add failed (Duplicate or Error)"}

@app.delete("/delete_symbol/{symbol_id}")
async def delete_symbol(symbol_id: int): # ต้องเป็น async
    try:
        # เรียก DB แบบ Async (ต้องไปเพิ่มฟังก์ชันนี้ใน database.py ด้วยนะครับ)
        await db.delete_symbol_data(symbol_id) 
        return {"message": f"Deleted ID {symbol_id}"}
    except Exception as e:
        return {"error": str(e)}

@app.put("/update_symbol/{symbol_id}")
async def update_symbol(symbol_id: int, item: UpdateSymbolModel): # ต้องเป็น async
    try:
        data = {
            "status": item.status,
            "money_limit": item.money_limit,
            "cost_st": item.cost_st
        }
        # เรียก DB แบบ Async
        await db.update_symbol_data(symbol_id, data)
        return {"message": f"Updated ID {symbol_id}"}
    except Exception as e:
        return {"error": str(e)}
    
@app.get("/history")
async def history():
    return await db.get_orders()

@app.get("/open-orders")
async def read_open_orders(sym: str = "THB_BTC"):
    """
    ดึงรายการออเดอร์ที่ค้างอยู่ (Open Orders) ของเหรียญนั้นๆ
    การใช้งาน: http://localhost:8000/open-orders?sym=THB_BTC
    """
    async with httpx.AsyncClient() as client:
        bk = BitkubClient()
        # เรียกใช้ฟังก์ชันจาก Class BitkubClient
        response = await bk.get_open_orders(client, sym)
        return response

@app.post("/test/buy")
async def test_buy(order: TestTradeModel):
    api = BitkubClient()
    async with httpx.AsyncClient() as client:
        
        res = await api.place_order(
            client, 
            sym=order.symbol, 
            amt=order.amount, 
            rat=order.rate, 
            side='BUY', 
            type='limit'
        )
        return res

@app.post("/test/sell")
async def test_sell(order: TestTradeModel):
    api = BitkubClient()
    async with httpx.AsyncClient() as client:
        
        res = await api.place_order(
            client, 
            sym=order.symbol, 
            amt=order.amount, 
            rat=order.rate, 
            side='SELL', 
            type='limit'
        )
        return res
    
@app.get("/test/price/{symbol}")
async def check_current_price(symbol: str):
    """
    เช็คราคาล่าสุดก่อนกดซื้อ (จะได้กรอก Rate ถูก)
    """
    api = BitkubClient()
    async with httpx.AsyncClient() as client:
        # ใช้ get_candles เพื่อดูราคาปิดล่าสุด
        df = await api.get_candles(client, symbol)
        if df is not None:
            last_price = df.iloc[-1]["close"]
            return {"symbol": symbol, "last_price": last_price}
        return {"error": "Could not fetch price"}

# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

@app.on_event("startup")
async def startup_event():
    print("🎬 Application Startup: Launching Bot Loop...")
    # สร้าง Background Task เพื่อรันบอทโดยไม่ขัดขวาง Server
    asyncio.create_task(bot.run_loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)