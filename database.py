import sqlite3
import time

DB_NAME = "app.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS watched_symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                cost REAL DEFAULT 0,
                coin REAL DEFAULT 0,
                last_price REAL DEFAULT 0,
                status TEXT DEFAULT 'false',
                money_limit REAL DEFAULT 0,
                cost_st REAL DEFAULT 0,
                cost_ft REAL DEFAULT 0,
                cost_ft_price REAL DEFAULT 0,
                coin_ft REAL DEFAULT 0,
                coin_ft_price REAL DEFAULT 0
            )
        """)
        # สร้างตาราง orders สำหรับเก็บประวัติ (ตัวอย่างย่อ)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                amount REAL,
                rate REAL,
                timestamp INTEGER,
                comment TEXT
            )
        """)
        conn.commit()

def get_symbols():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM watched_symbols")
        return [dict(row) for row in cursor.fetchall()]

def update_cost_coin(symbol_id, new_cost, new_coin, last_price):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE watched_symbols 
            SET cost=?, coin=?, last_price=? 
            WHERE id=?
        """, (new_cost, new_coin, last_price, symbol_id))
        conn.commit()

def save_order(order_data, comment):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO orders (symbol, side, amount, rate, timestamp, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (order_data.get('sym'), order_data.get('side', 'unknown'), 
              order_data.get('amt'), order_data.get('rat'), 
              int(time.time()), comment))
        conn.commit()

def delete_symbol_data(id: int):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM watched_symbols WHERE id=?", (id,))
        conn.commit()

def update_symbol_data(id: int, data: dict):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # อัปเดตเฉพาะค่าที่เราอนุญาตให้แก้ (User Config)
        cursor.execute("""
            UPDATE watched_symbols 
            SET status=?, money_limit=?, cost_st=?
            WHERE id=?
        """, (data['status'], data['money_limit'], data['cost_st'], id))
        conn.commit()

# รันครั้งแรกเพื่อสร้างตาราง
init_db()