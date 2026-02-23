import pandas as pd
import numpy as np

def calculate_rsi(data, period=14):
    delta = data.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(window=period, min_periods=1).mean()
    avg_loss = pd.Series(loss).rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return pd.Series(rsi, index=data.index)

def calculate_macd(data, short_window=12, long_window=26, signal_window=9):
    ema_short = data.ewm(span=short_window, adjust=False).mean()
    ema_long = data.ewm(span=long_window, adjust=False).mean()
    macd = ema_short - ema_long
    signal = macd.ewm(span=signal_window, adjust=False).mean()
    return macd, signal

def calculate_bollinger_bands(data, period=20, num_std=2):
    ma = data.rolling(window=period).mean()
    std_dev = data.rolling(window=period).std()
    upper_band = ma + (num_std * std_dev)
    lower_band = ma - (num_std * std_dev)
    return ma, upper_band, lower_band

def calculate_stochastic(data, high, low, period=14):
    low_min = low.rolling(window=period).min()
    high_max = high.rolling(window=period).max()
    k = ((data - low_min) / (high_max - low_min)) * 100
    d = k.rolling(window=3).mean()
    return k, d

def calculate_ema(series, period):
    """คำนวณ Exponential Moving Average (EMA)"""
    return series.ewm(span=period, adjust=False).mean()

def calculate_adx(df, period=14):
    """คำนวณ Average Directional Index (ADX) วัดความแรงของเทรนด์"""
    high = df['high']
    low = df['low']
    close = df['close']

    plus_dm = high.diff()
    minus_dm = low.diff()
    
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr1 = pd.DataFrame(high - low)
    tr2 = pd.DataFrame(abs(high - close.shift(1)))
    tr3 = pd.DataFrame(abs(low - close.shift(1)))
    tr = pd.concat([tr1, tr2, tr3], axis=1, join='inner').max(axis=1)

    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/period, adjust=False).mean() / atr)
    
    dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    
    return adx.fillna(0)