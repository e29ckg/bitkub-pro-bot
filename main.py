import os
import asyncio
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
import utils 
from bitkub import BitkubClient 
from bot_engine import BotEngine

# --- Settings & Config ---
# ใช้รหัสผ่านจาก .env หรือค่า default
BOT_PASSWORD = os.getenv("BOT_PASSWORD", "1234")

# เริ่มต้น DB
db.init_db() 

app = FastAPI(
    docs_url=None,    # ❌ ปิด Swagger UI (/docs)
    redoc_url=None,   # ❌ ปิด ReDoc (/redoc)
    openapi_url=None  # ❌ ปิด JSON Schema (/openapi.json)
)

# --- Middlewares ---
# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Static Files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    strategy: int = 1 

class TestTradeModel(BaseModel):
    symbol: str 
    amount: float 
    rate: float

# =====================================================================
# --- 🔒 ระบบ Security / Gatekeeper ---
# =====================================================================
async def check_user(request: Request):
    # 🟢 ดึงค่าจาก Cookie
    token = request.cookies.get("access_token")
    if token != "logged_in_success":
        raise HTTPException(status_code=401, detail="Please login first")
    return token

# =====================================================================
# --- 🖥️ Web Pages (HTML Routes) ---
# =====================================================================

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # 🟢 เช็คการล็อกอินผ่าน Cookie
    token = request.cookies.get("access_token")
    if token == "logged_in_success":
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    try:
        with open("login.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Login file not found. Please create login.html"

# =====================================================================
# --- 🔑 Auth APIs ---
# =====================================================================

@app.post("/login")
async def login(password: str = Form(...)):
    if password == BOT_PASSWORD:  
        # 🟢 [แก้ไข] ส่งแค่ JSON กลับไปบอก JS ว่าสำเร็จ
        response = JSONResponse(content={"message": "Login Success"})
        # ฝัง Cookie เหมือนเดิม
        response.set_cookie(key="access_token", value="logged_in_success", httponly=True)
        return response
    else:
        # 🟢 [แก้ไข] ถ้ารหัสผิด ส่ง HTTP 401 เพื่อให้ JS โชว์ข้อความ Access Denied
        raise HTTPException(status_code=401, detail="Incorrect Password")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    # 🟢 หน้าเว็บต้องล็อกอิน
    token = request.cookies.get("access_token")
    if token != "logged_in_success":
        return RedirectResponse(url="/login", status_code=303)
        
    try:
        with open("dashboard.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Dashboard file not found. Please create dashboard.html"

# =====================================================================
# --- 🔑 Auth APIs ---
# =====================================================================

@app.post("/login")
async def login(password: str = Form(...)):
    if password == BOT_PASSWORD:  
        # ถ้าล็อกอินผ่าน ให้ Redirect ไป Dashboard
        response = RedirectResponse(url="/dashboard", status_code=303)
        # ฝัง Cookie ให้จำค่า
        response.set_cookie(key="access_token", value="logged_in_success", httponly=True)
        return response
    else:
        # ถ้ารหัสผิด เด้งแจ้งเตือนแล้วกลับหน้าล็อกอิน
        return HTMLResponse("<script>alert('Incorrect Password'); window.location.href='/login';</script>")
    
@app.post("/logout")
async def logout():
    # ฝั่ง JS ใน dashboard.html จะเรียกผ่าน API นี้
    response = JSONResponse(content={"message": "Logout Success"})
    response.delete_cookie(key="access_token")
    return response

@app.get("/logout-page") 
async def logout_page():
    # สำรองกรณีอยากใช้ลิงก์ธรรมดา <a href="/logout-page">
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="access_token")
    return response


# =====================================================================
# --- 🤖 Bot Control APIs (ต้อง Login ก่อน) ---
# =====================================================================

@app.get("/bot-status")
async def get_bot_status():
    return {"running": bot.running}

@app.post("/start-bot", dependencies=[Depends(check_user)])
async def start_bot():
    if bot.running:
        return {"message": "Bot is already running"}
    asyncio.create_task(bot.run_loop())
    return {"message": "Bot start command received"}

@app.post("/stop-bot", dependencies=[Depends(check_user)])
async def stop_bot():
    bot.running = False
    return {"message": "Bot stopping..."}


# =====================================================================
# --- 📊 Database & Trading APIs (ต้อง Login ก่อน) ---
# =====================================================================

@app.get("/symbols", dependencies=[Depends(check_user)])
async def read_symbols():
    return await db.get_all_symbols()

@app.post("/add_symbol", dependencies=[Depends(check_user)])
async def add_symbol(request: Request):
    data = await request.json()
    
    raw_symbol = data.get("symbol", "")
    symbol = utils.normalize_symbol(raw_symbol, to_api=False)

    money_limit = float(data.get("money_limit", 1000))
    cost_st = float(data.get("cost_st", 100))
    strategy = int(data.get("strategy", 1))

    success = await db.add_symbol(symbol, money_limit, cost_st,  strategy)
    
    if success:
        return {"status": "success", "message": f"Added {symbol}"}
    else:
        return {"status": "error", "message": "Add failed (Duplicate or Error)"}

@app.delete("/delete_symbol/{symbol_id}", dependencies=[Depends(check_user)])
async def delete_symbol(symbol_id: int): 
    try:
        await db.delete_symbol_data(symbol_id) 
        return {"message": f"Deleted ID {symbol_id}"}
    except Exception as e:
        return {"error": str(e)}

@app.put("/update_symbol/{symbol_id}", dependencies=[Depends(check_user)])
async def update_symbol(symbol_id: int, item: UpdateSymbolModel): 
    try:
        data = {
            "status": item.status,
            "money_limit": item.money_limit,
            "cost_st": item.cost_st,
            "strategy": item.strategy 
        }
        await db.update_symbol_data(symbol_id, data)
        return {"message": f"Updated ID {symbol_id}"}
    except Exception as e:
        return {"error": str(e)}
    
@app.get("/history", dependencies=[Depends(check_user)])
async def history():
    return await db.get_orders()

@app.get("/open-orders", dependencies=[Depends(check_user)])
async def read_open_orders(sym: str = "THB_BTC"):
    async with httpx.AsyncClient() as client:
        bk = BitkubClient()
        response = await bk.get_open_orders(client, sym)
        return response

# --- Test Endpoints (สำหรับ Dev/Test) ---
@app.post("/test/buy", dependencies=[Depends(check_user)])
async def test_buy(order: TestTradeModel):
    api = BitkubClient()
    async with httpx.AsyncClient() as client:
        res = await api.place_order(client, order.symbol, order.amount, order.rate, 'BUY', type='limit')
        return res

@app.post("/test/sell", dependencies=[Depends(check_user)])
async def test_sell(order: TestTradeModel):
    api = BitkubClient()
    async with httpx.AsyncClient() as client:
        res = await api.place_order(client, order.symbol, order.amount, order.rate, 'SELL', type='limit')
        return res
    
@app.get("/test/price/{symbol}", dependencies=[Depends(check_user)])
async def check_current_price(symbol: str):
    api = BitkubClient()
    async with httpx.AsyncClient() as client:
        df = await api.get_candles(client, symbol)
        if df is not None:
            last_price = df.iloc[-1]["close"]
            return {"symbol": symbol, "last_price": last_price}
        return {"error": "Could not fetch price"}

# =====================================================================
# --- 📡 WebSocket & Startup ---
# =====================================================================

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
    # เริ่มการทำงานของ Bot อัตโนมัติเมื่อเซิร์ฟเวอร์เปิด
    asyncio.create_task(bot.run_loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)