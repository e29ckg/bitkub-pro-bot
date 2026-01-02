import aiosqlite
import time
from config import DB_NAME

# ฟังก์ชันนี้ใช้ตอนเปิดโปรแกรมครั้งแรก (Sync ได้ ไม่เป็นไร)
def init_db():
    import sqlite3
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            money_limit REAL,
            cost_st REAL,
            cost REAL DEFAULT 0,
            coin REAL DEFAULT 0,
            status TEXT DEFAULT 'true'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            symbol TEXT,
            type TEXT,
            amount REAL,
            rate REAL,
            ts REAL,
            reason TEXT
        )
    """)
    conn.commit()
    conn.close()

# --- Async Functions ---

async def get_symbols():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM symbols WHERE status='true'") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def add_symbol(symbol, money_limit, cost_st):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT INTO symbols (symbol, money_limit, cost_st) VALUES (?, ?, ?)",
                (symbol, money_limit, cost_st)
            )
            await db.commit()
            return True
        except:
            return False

async def update_cost_coin(s_id, new_cost, new_coin):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE symbols SET cost=?, coin=? WHERE id=?",
            (new_cost, new_coin, s_id)
        )
        await db.commit()

async def save_order(order_data, reason):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO orders (order_id, symbol, type, amount, rate, ts, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(order_data.get('id', '')),
            order_data.get('sym', ''), 
            order_data.get('typ', ''),
            float(order_data.get('amt', 0)),
            float(order_data.get('rat', 0)),
            float(order_data.get('ts', time.time())),
            reason
        ))
        await db.commit()

# --- 👇 เพิ่ม 2 ฟังก์ชันนี้ เพื่อให้ Main.py ทำงานได้ครบถ้วน 👇 ---

async def delete_symbol_data(s_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM symbols WHERE id=?", (s_id,))
        await db.commit()

async def update_symbol_data(s_id, data):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE symbols SET status=?, money_limit=?, cost_st=? WHERE id=?",
            (data['status'], data['money_limit'], data['cost_st'], s_id)
        )
        await db.commit()

async def get_orders(limit=50):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # ดึงข้อมูล เรียงจากเวลาล่าสุด (ts DESC)
        async with db.execute(f"SELECT * FROM orders ORDER BY ts DESC LIMIT {limit}") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]