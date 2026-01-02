# utils.py

def normalize_symbol(symbol: str, to_api: bool = False) -> str:
    """
    แปลงชื่อเหรียญให้เป็นรูปแบบที่ต้องการ
    Input: "btc", "THB_BTC", "BTC_THB"
    
    to_api = False (Default): คืนค่า "THB_BTC" (สำหรับเก็บใน DB และ Internal Logic)
    to_api = True           : คืนค่า "BTC_THB" (สำหรับส่งไป Bitkub API)
    """
    if not symbol: return ""
    
    s = symbol.upper().strip()
    
    # หาส่วนที่เป็นชื่อเหรียญเพียวๆ (Base Coin) เช่น BTC
    if s.startswith("THB_"):
        base_coin = s.split("_")[1]
    elif s.endswith("_THB"):
        base_coin = s.split("_")[0]
    else:
        base_coin = s # กรณีส่งมาแค่ BTC

    # คืนค่าตาม format ที่ต้องการ
    if to_api:
        return f"{base_coin}_THB" # Format ของ Bitkub API
    else:
        return f"THB_{base_coin}" # Format ของ Database เรา