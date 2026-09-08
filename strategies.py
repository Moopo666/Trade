import pandas as pd
import MetaTrader5 as mt5
import config
from indicators import calculate_rsi, calculate_atr, calculate_ema
from abc import ABC, abstractmethod
import datetime
import pytz
from tools import get_current_spread

class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""
    def __init__(self, symbol: str = config.DEFAULT_SYMBOL):
        self.symbol = symbol

    @abstractmethod
    def get_signal(self) -> dict:
        """Returns a trade signal or None."""
        pass

class RSIReversionStrategy(BaseStrategy):
    """Strategy 100001: Buys when RSI < 30, Sells when RSI > 70."""
    def get_signal(self) -> dict:
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 0, 100)
        if rates is None: return None
        df = pd.DataFrame(rates)
        df['close'] = df['close'].astype(float)
        
        rsi = calculate_rsi(df['close'], period=14)
        current_rsi = rsi.iloc[-1]
        
        if current_rsi < 30:
            return {"strategy_name": "RSI_Reversion", "magic": 100001, "action": "BUY", "symbol": self.symbol, "reason": f"RSI is oversold at {current_rsi:.2f}"}
        elif current_rsi > 70:
            return {"strategy_name": "RSI_Reversion", "magic": 100001, "action": "SELL", "symbol": self.symbol, "reason": f"RSI is overbought at {current_rsi:.2f}"}
        
        return None

class GoldMomentumScalper(BaseStrategy):
    """
    Expert Advisor for XAUUSD M1 (Single-Chart):
    - Session-based filtering
    - M1 Trend analysis (EMA 100)
    - Dynamic ATR-based risk management
    """
    def __init__(self, symbol: str = config.DEFAULT_SYMBOL):
        super().__init__(symbol)
        self.ema_period = 100
        self.max_spread = 150  # 15 pips
        self.bkk_tz = pytz.timezone('Asia/Bangkok')

    def is_session_active(self) -> bool:
        now_bkk = datetime.datetime.now(self.bkk_tz)
        hour = now_bkk.hour
        is_london = (hour >= 15 and hour < 17)
        is_ny = (hour >= 19 and hour < 22)
        return is_london or is_ny

    def get_signal(self) -> dict:
        if not self.is_session_active():
            return None
            
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 200)
        if rates is None: return None
        df = pd.DataFrame(rates)
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        
        rsi = calculate_rsi(df["close"])
        ema = calculate_ema(df["close"], self.ema_period)
        atr = calculate_atr(df)
        
        m1_close = df["close"].iloc[-1]
        m1_rsi = rsi.iloc[-1]
        m1_ema = ema.iloc[-1]
        m1_atr = atr.iloc[-1]
        
        spread = get_current_spread(self.symbol)
        
        is_uptrend = m1_close > m1_ema
        is_downtrend = m1_close < m1_ema
        
        sl = m1_atr * 2.0
        tp = m1_atr * 4.0
        
        if spread <= self.max_spread:
            if is_uptrend and m1_rsi <= 40:
                return {"strategy_name": "Gold_Momentum_M1", "magic": 100003, "action": "BUY", "symbol": self.symbol, "sl": sl, "tp": tp}
            elif is_downtrend and m1_rsi >= 60:
                return {"strategy_name": "Gold_Momentum_M1", "magic": 100003, "action": "SELL", "symbol": self.symbol, "sl": sl, "tp": tp}
        
        return None
