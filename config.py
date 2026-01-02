# config.py

# --- Trading Logic ---
TIMEFRAME = 15          # นาทีกราฟ (1, 5, 15, 60, 240, 1440)
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# --- Money Management ---
TAKE_PROFIT_PCT = 1.5   # % กำไรที่จะขาย (เช่น 1.5%)
DCA_DROP_PCT = 2.0      # % ราคาตกที่จะซื้อเพิ่ม (DCA)
FEE_BUFFER = 0.52       # % เผื่อค่าธรรมเนียม (0.25+0.25 + vat นิดหน่อย)

# --- System ---
DB_NAME = "bitkub_bot.db"