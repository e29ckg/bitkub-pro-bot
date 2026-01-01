from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import database as db
from bot_engine import BotEngine
from fastapi.staticfiles import StaticFiles

import os

BOT_PASSWORD = os.getenv("BOT_PASSWORD", "1234")

app = FastAPI()

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
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                self.disconnect(connection)

ws_manager = ConnectionManager()
bot = BotEngine(ws_manager)

# --- Pydantic Models ---
class SymbolModel(BaseModel):
    symbol: str
    status: str = 'true'
    money_limit: float = 1000
    cost_st: float = 100
    # เพิ่ม field อื่นๆ ตามต้องการ

class UpdateSymbolModel(BaseModel):
    status: str
    money_limit: float
    cost_st: float


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # เช็คว่ามี Cookie ชื่อ "access_token" หรือไม่
    token = request.cookies.get("access_token")
    
    # ถ้ามี Token และถูกต้อง (ในที่นี้เราเช็คแค่มันมีค่าไหม ง่ายๆ)
    if token == "logged_in_success":
        try:
            with open("dashboard.html", "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "Dashboard file not found."
    
    # ถ้าไม่มี Token ให้ส่งหน้า Login กลับไปแทน
    try:
        with open("login.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Login file not found."
    
@app.post("/login")
async def login(response: Response, password: str = Form(...)):
    if password == BOT_PASSWORD:
        # ถ้ารหัสถูก ให้สร้าง Cookie ชื่อ access_token
        content = {"message": "Login Success"}
        response = JSONResponse(content=content)
        # ตั้ง Cookie (httponly เพื่อความปลอดภัย)
        response.set_cookie(key="access_token", value="logged_in_success", httponly=True)
        return response
    else:
        # ถ้ารหัสผิด ส่ง Error 401
        raise HTTPException(status_code=401, detail="Incorrect Password")
    
@app.post("/logout")
async def logout(response: Response):
    content = {"message": "Logout Success"}
    response = JSONResponse(content=content)
    # ลบ Cookie ทิ้ง
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

@app.get("/symbols")
def get_symbols():
    return db.get_symbols()

@app.post("/add_symbol")
def add_symbol(item: SymbolModel):
    with db.sqlite3.connect(db.DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO watched_symbols (symbol, status, money_limit, cost_st) VALUES (?, ?, ?, ?)",
                       (item.symbol, item.status, item.money_limit, item.cost_st))
        conn.commit()
    return {"message": "Added"}

@app.delete("/delete_symbol/{symbol_id}")
def delete_symbol(symbol_id: int):
    try:
        db.delete_symbol_data(symbol_id)
        return {"message": f"Deleted ID {symbol_id}"}
    except Exception as e:
        return {"error": str(e)}

@app.put("/update_symbol/{symbol_id}")
def update_symbol(symbol_id: int, item: UpdateSymbolModel):
    try:
        # แปลงเป็น dict เพื่อส่งให้ db
        data = {
            "status": item.status,
            "money_limit": item.money_limit,
            "cost_st": item.cost_st
        }
        db.update_symbol_data(symbol_id, data)
        return {"message": f"Updated ID {symbol_id}"}
    except Exception as e:
        return {"error": str(e)}

# --- WebSocket Endpoint ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)