# # utils.py

# def normalize_symbol(symbol: str, to_api: bool = False) -> str:
#     """
#     แปลงชื่อเหรียญให้เป็นรูปแบบที่ต้องการ
#     Input: "btc", "THB_BTC", "BTC_THB"
    
#     to_api = False (Default): คืนค่า "THB_BTC" (สำหรับเก็บใน DB และ Internal Logic)
#     to_api = True           : คืนค่า "BTC_THB" (สำหรับส่งไป Bitkub API)
#     """
#     if not symbol: return ""
    
#     s = symbol.upper().strip()
    
#     # หาส่วนที่เป็นชื่อเหรียญเพียวๆ (Base Coin) เช่น BTC
#     if s.startswith("THB_"):
#         base_coin = s.split("_")[1]
#     elif s.endswith("_THB"):
#         base_coin = s.split("_")[0]
#     else:
#         base_coin = s # กรณีส่งมาแค่ BTC

#     # คืนค่าตาม format ที่ต้องการ
#     if to_api:
#         return f"{base_coin}_THB" # Format ของ Bitkub API
#     else:
#         return f"THB_{base_coin}" # Format ของ Database เรา
    
# utils.py

def normalize_symbol(symbol, to_api=False):
    """
    แปลงชื่อเหรียญให้เป็น Format ที่ต้องการ
    เช่น: 
    - input: "THB_BTC" -> api: "btc_thb"
    - input: "BTC_THB" -> api: "btc_thb"
    """
    # 1. ทำให้เป็นตัวพิมพ์ใหญ่หมดก่อน และลบช่องว่าง
    s = symbol.upper().strip()
    
    # 2. ถ้าต้องการส่งให้ API (to_api=True)
    if to_api:
        # กรณีที่ใส่มาเป็น THB_XXX (เช่น THB_BONK) ให้สลับเป็น BONK_THB
        if s.startswith("THB_"):
            # ตัด THB_ ข้างหน้าออก แล้วเอาไปต่อท้าย
            # "THB_BONK" -> split("_") -> ["THB", "BONK"]
            parts = s.split("_")
            if len(parts) == 2:
                s = f"{parts[1]}_THB"
        
        # กรณีไม่มี _ (เช่น BTCTHB) อาจจะต้องใส่ _ เพิ่ม (แล้วแต่ Logic เดิมของคุณ)
        # แต่ Bitkub V3 มักจะรับเป็นตัวพิมพ์เล็ก "btc_thb"
        return s.lower()

    # กรณีอื่นๆ (ไม่ได้ส่ง API)
    return s