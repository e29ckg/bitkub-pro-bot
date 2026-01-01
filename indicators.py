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