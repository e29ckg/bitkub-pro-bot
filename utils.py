def normalize_symbol(symbol: str, to_api: bool = False) -> str:
    """
    แปลงชื่อเหรียญให้เป็นรูปแบบที่ต้องการ (Universal)
    
    Examples:
    - normalize_symbol("THB_BONK", to_api=True)  -> "bonk_thb" (ส่ง API)
    - normalize_symbol("BONK_THB", to_api=False) -> "THB_BONK" (เก็บ DB)
    - normalize_symbol("BTC", to_api=False)      -> "THB_BTC"
    """
    if not symbol: return ""
    
    # 1. ทำความสะอาด Input เป็นตัวพิมพ์ใหญ่ก่อน
    s = symbol.upper().strip()
    
    # 2. สกัดหาชื่อเหรียญเพียวๆ (Base Coin) เช่น BONK, BTC
    base_coin = ""
    
    if s.startswith("THB_") and len(s.split("_")) == 2:
        # กรณี THB_BTC
        base_coin = s.split("_")[1]
    elif s.endswith("_THB") and len(s.split("_")) == 2:
        # กรณี BTC_THB
        base_coin = s.split("_")[0]
    else:
        # กรณีส่งมาแค่ BTC หรือ format อื่นๆ
        base_coin = s

    # 3. คืนค่าตามวัตถุประสงค์
    if to_api:
        # สำหรับส่ง Bitkub API V3: ต้องเป็น "xxx_thb" (ตัวพิมพ์เล็ก)
        return f"{base_coin}_THB".lower()
    else:
        # สำหรับเก็บใน DB หรือใช้ใน Bot: ใช้ "THB_XXX" (ตัวพิมพ์ใหญ่)
        return f"THB_{base_coin}"