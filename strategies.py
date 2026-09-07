import pandas as pd
import MetaTrader5 as mt5
import config

# --- EA 1: RSI Mean Reversion ---
def ea_rsi_reversion(symbol: str = config.DEFAULT_SYMBOL) -> dict:
    """Strategy 100001: Buys when RSI < 30, Sells when RSI > 70."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100)
    if rates is None: return None
    df = pd.DataFrame(rates)
    df['close'] = df['close'].astype(float)
    
    # Native RSI calculation
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    current_rsi = df['rsi'].iloc[-1]
    
    if current_rsi < 30:
        return {"strategy_name": "RSI_Reversion", "magic": 100001, "action": "BUY", "symbol": symbol, "reason": f"RSI is oversold at {current_rsi:.2f}"}
    elif current_rsi > 70:
        return {"strategy_name": "RSI_Reversion", "magic": 100001, "action": "SELL", "symbol": symbol, "reason": f"RSI is overbought at {current_rsi:.2f}"}
    
    return None # No signal

# --- EA 2: EMA Trend Crossover ---
def ea_ema_crossover(symbol: str = config.DEFAULT_SYMBOL) -> dict:
    """Strategy 100002: Buys when EMA 9 crosses above EMA 21."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100)
    if rates is None: return None
    df = pd.DataFrame(rates)
    df['close'] = df['close'].astype(float)
    
    # Native EMA calculation
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    
    ema9_current = df['ema9'].iloc[-1]
    ema21_current = df['ema21'].iloc[-1]
    ema9_prev = df['ema9'].iloc[-2]
    ema21_prev = df['ema21'].iloc[-2]
    
    # Bullish Cross
    if ema9_prev <= ema21_prev and ema9_current > ema21_current:
        return {"strategy_name": "EMA_Crossover", "magic": 100002, "action": "BUY", "symbol": symbol, "reason": "EMA 9 crossed above EMA 21"}
    # Bearish Cross
    elif ema9_prev >= ema21_prev and ema9_current < ema21_current:
        return {"strategy_name": "EMA_Crossover", "magic": 100002, "action": "SELL", "symbol": symbol, "reason": "EMA 9 crossed below EMA 21"}
        
    return None # No signal
