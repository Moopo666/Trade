import requests
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
import time
import config
import csv
import sqlite3
import indicators

def get_unified_market_data(symbol: str) -> dict:
    """Fetches M1 and M5 data and calculates indicators using indicators module."""
    rates_m1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, 20)
    rates_m5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, 60)
    
    if rates_m1 is None or rates_m5 is None: return None

    # RSI on M1
    df_m1 = pd.DataFrame(rates_m1)
    df_m1["close"] = df_m1["close"].astype(float)
    rsi_m1 = indicators.calculate_rsi(df_m1["close"])
    
    # EMA/ATR on M5
    df_m5 = pd.DataFrame(rates_m5)
    df_m5["close"] = df_m5["close"].astype(float)
    df_m5["high"] = df_m5["high"].astype(float)
    df_m5["low"] = df_m5["low"].astype(float)
    
    ema_m5 = indicators.calculate_ema(df_m5["close"], 50)
    atr_m5 = indicators.calculate_atr(df_m5, 14)

    return {
        "prev_m1_rsi": rsi_m1.iloc[-2],
        "curr_m1_rsi": rsi_m1.iloc[-1],
        "m5_ema50": ema_m5.iloc[-1],
        "m5_atr": atr_m5.iloc[-1],
        "m5_close": df_m5["close"].iloc[-1]
    }


def manage_existing_positions(position, m5_atr):
    """Manages active position for Break-Even and Partial Close."""
    symbol_info = mt5.symbol_info(position.symbol)
    point = symbol_info.point
    
    # Calculate profit in points
    price_diff = (mt5.symbol_info_tick(position.symbol).bid - position.price_open) if position.type == mt5.ORDER_TYPE_BUY else (position.price_open - mt5.symbol_info_tick(position.symbol).ask)
    profit_points = price_diff / point
    
    # Break-Even: Profit >= 1.5 * ATR
    if profit_points >= (m5_atr * 1.5) and position.sl < position.price_open:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "sl": position.price_open,
            "tp": position.tp,
            "symbol": position.symbol
        }
        mt5.order_send(request)
        send_telegram_alert(f"✅ Break-even set for ticket {position.ticket}")

    # Partial Close: Profit >= 3.0 * ATR
    if profit_points >= (m5_atr * 3.0) and position.comment != "Partial Closed":
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": position.symbol,
            "volume": position.volume / 2.0,
            "type": mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "price": mt5.symbol_info_tick(position.symbol).bid if position.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(position.symbol).ask,
            "deviation": 5,
            "magic": position.magic,
            "comment": "Partial Closed"
        }
        mt5.order_send(request)
        send_telegram_alert(f"✅ Partial Close executed for ticket {position.ticket}")

# Initialize Database
def init_db():
    """Initializes SQLite database for trade logging."""
    conn = sqlite3.connect('trades.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            strategy TEXT,
            direction TEXT,
            spread REAL,
            atr REAL,
            trend TEXT,
            decision TEXT,
            reasoning TEXT,
            actual_price_1h_later REAL
        )
    ''')
    conn.commit()
    conn.close()

# Ensure DB is initialized
init_db()

def log_trade_decision(strategy, direction, spread, atr, trend, decision, reasoning):
    """Logs trade signals and AI decisions to SQLite."""
    conn = sqlite3.connect('trades.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trade_logs (timestamp, strategy, direction, spread, atr, trend, decision, reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now(), strategy, direction, spread, atr, trend, decision, reasoning))
    conn.commit()
    conn.close()

def send_telegram_alert(
    message: str,
    bot_token: str = config.TELEGRAM_BOT_TOKEN,
    chat_id: str = config.TELEGRAM_CHAT_ID,
):
    """Sends trade alerts and system health logs directly to your phone via Telegram with retries."""
    if not bot_token or not chat_id:
        print("[DEBUG] Telegram Alert skipped: Missing Bot Token or Chat ID.")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    
    # Retry mechanism for network instability
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print("[DEBUG] Telegram Alert sent successfully.")
                return
            else:
                print(f"[DEBUG] Telegram Alert failed with status {response.status_code}: {response.text}")
                return 
        except Exception as e:
            print(f"Failed to send alert (attempt {attempt+1}/3): {e}")
            time.sleep(2) # Brief pause before retry
            
    # If we reach here, all retries failed
    print(f"🚨 [CRITICAL] Telegram Alert Failed after 3 attempts: {message}")

def calculate_dynamic_lot_size(symbol: str, sl_points: int, risk_percent: float = 0.01) -> float:
    """Calculates lot size based on 1% risk of free margin using tick values."""
    account_info = mt5.account_info()
    if account_info is None: return 0.01
    
    risk_amount = account_info.equity * risk_percent
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None: return 0.01
    
    # Calculate stop loss distance in terms of price
    sl_price_distance = sl_points * symbol_info.point
    
    # Calculate number of ticks in the stop loss distance
    # Ensure tick_size is not zero to avoid division by zero
    tick_size = symbol_info.trade_tick_size if symbol_info.trade_tick_size != 0 else 0.01
    ticks = sl_price_distance / tick_size
    
    # Calculate risk per lot (assuming 1 lot)
    # tick_value is profit for 1 tick per 1 lot
    risk_per_lot = ticks * symbol_info.trade_tick_value
    
    if risk_per_lot == 0: return 0.01
    
    lot_size = round(risk_amount / risk_per_lot, 2)
    return max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))

def log_trade(ea_name, action, ai_reasoning, sl_points, ticket_id):
    """Logs trade details to a CSV file for strategy analysis."""
    with open('trade_journal.csv', mode='a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow([datetime.now(), ticket_id, ea_name, action, ai_reasoning, sl_points])

def get_symbol_filling_mode(symbol_info) -> int:
    """Detects the order filling mode supported by the broker for this symbol."""
    if symbol_info.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    elif symbol_info.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN

def get_current_atr(symbol: str = config.DEFAULT_SYMBOL, period: int = 14) -> float:
    """Calculates the current ATR(14) for the given symbol."""
    rates = mt5.copy_rates_from_pos(symbol, config.TIMEFRAME, 0, 100)
    if rates is None or len(rates) < period:
        return 0.0
    
    df = pd.DataFrame(rates)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    if talib is not None:
        return float(talib.ATR(df["high"].values, df["low"].values, df["close"].values, timeperiod=period)[-1])
    else:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs()
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

def get_current_spread(symbol: str = config.DEFAULT_SYMBOL) -> int:
    """Returns the current spread in points."""
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return 0
    return symbol_info.spread

def check_spread_safe(
    symbol: str = config.DEFAULT_SYMBOL,
    max_allowed_spread_points: int = config.MAX_ALLOWED_SPREAD_POINTS,
) -> bool:
    """Returns False if the current spread is too wide for safe trading."""
    current_spread = get_current_spread(symbol)
    if current_spread > max_allowed_spread_points:
        print(f"[WARNING] SPREAD WARNING: Current spread is {current_spread} points. Skipping cycle.")
        return False
    return True

def get_market_data(symbol: str = config.DEFAULT_SYMBOL) -> str:
    """Fetches M5 price data, calculates RSI(14) and ATR(14)."""
    rates = mt5.copy_rates_from_pos(symbol, config.TIMEFRAME, 0, 100)
    if rates is None or len(rates) == 0:
        return f"Error fetching data for {symbol}"

    df = pd.DataFrame(rates)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)

    # Native RSI calculation
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    current_rsi = float((100 - (100 / (1 + rs))).iloc[-1])
    
    # ATR fallback logic
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    current_atr = float(tr.rolling(14).mean().iloc[-1])

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return f"Error: Cannot find symbol {symbol}"

    atr_in_points = int(current_atr / symbol_info.point) if symbol_info.point else 0
    current_price = df["close"].iloc[-1]

    return f"{symbol} Market Data: Price: {current_price}, RSI(14): {current_rsi:.2f}, ATR(14) Volatility: {atr_in_points} points per 5M candle."

def execute_trade(
    action: str,
    symbol: str = config.DEFAULT_SYMBOL,
    lot_size: float = config.DEFAULT_LOT_SIZE,
    sl_points: int = config.DEFAULT_SL_POINTS,
    tp_points: int = config.DEFAULT_TP_POINTS,
    magic: int = config.BOT_MAGIC_NUMBER,
) -> tuple[str, int]:
    """Executes a market order and returns a message and the order ticket ID."""
    if action == "HOLD":
        return "Holding position.", 0

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return f"Error: Cannot find symbol {symbol}", 0

    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            return f"Error: Failed to select symbol {symbol}", 0
        symbol_info = mt5.symbol_info(symbol)

    point = symbol_info.point
    digits = symbol_info.digits
    filling_mode = get_symbol_filling_mode(symbol_info)

    if action == "BUY":
        order_type = mt5.ORDER_TYPE_BUY
        price = symbol_info.ask
        sl_price = price - (sl_points * point)
        tp_price = price + (tp_points * point)
    elif action == "SELL":
        order_type = mt5.ORDER_TYPE_SELL
        price = symbol_info.bid
        sl_price = price + (sl_points * point)
        tp_price = price - (tp_points * point)
    else:
        return f"Invalid action: {action}", 0

    sl_price = round(sl_price, digits)
    tp_price = round(tp_price, digits)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": order_type,
        "price": price,
        "sl": sl_price,
        "tp": tp_price,
        "deviation": 20,
        "magic": magic,
        "comment": "Gemini AI Agent",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    result = mt5.order_send(request)
    if result is None:
        return f"Order failed: MT5 returned None. Last error: {mt5.last_error()}", 0
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return f"Order failed: MT5 retcode {result.retcode}. Comment: {result.comment}", 0

    return f"SUCCESS: Executed {action} on {symbol}. SL: {sl_price}, TP: {tp_price}", result.order

def get_weekly_history():
    """Fetches deals from the last 7 days."""
    from_date = datetime.now() - timedelta(days=7)
    to_date = datetime.now()
    return mt5.history_deals_get(from_date, to_date) or ()

def weekly_strategy_critic():
    """Evaluates the win rate of each EA by cross-referencing trade_journal.csv with MT5 history."""
    journaled_trades = {}
    try:
        with open('trade_journal.csv', mode='r') as file:
            reader = csv.reader(file)
            for row in reader:
                if len(row) >= 6:
                    journaled_trades[int(row[1])] = row[2] 
    except FileNotFoundError:
        return "No journal file found."
    
    deals = get_weekly_history()
    ea_performance = {
        100001: {"wins": 0, "losses": 0, "status": "ACTIVE"},
        100002: {"wins": 0, "losses": 0, "status": "ACTIVE"},
        100003: {"wins": 0, "losses": 0, "status": "ACTIVE"}
    }
    
    for deal in deals:
        if deal.order in journaled_trades and deal.entry == mt5.DEAL_ENTRY_OUT:
            magic = deal.magic
            if magic in ea_performance:
                if deal.profit > 0:
                    ea_performance[magic]["wins"] += 1
                else:
                    ea_performance[magic]["losses"] += 1
    
    for magic, stats in ea_performance.items():
        total_trades = stats["wins"] + stats["losses"]
        if total_trades > 5:
            win_rate = stats["wins"] / total_trades
            if win_rate < 0.40:
                print(f"CRITIC WARNING: EA #{magic} win rate is {win_rate*100:.1f}%. Disabling.")
                ea_performance[magic]["status"] = "DISABLED"
    
    return ea_performance

def review_past_performance(magic_number: int = config.BOT_MAGIC_NUMBER) -> str:
    """Fetches the last 5 closed trades to evaluate strategy performance."""
    from_date = datetime.now() - timedelta(days=1)
    to_date = datetime.now()

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None or len(deals) == 0:
        return "No trades executed in the last 24 hours."

    bot_deals = [d for d in deals if d.magic == magic_number and d.entry == mt5.DEAL_ENTRY_OUT]
    if not bot_deals:
        return "No closed trades from the agent yet."

    recent_deals = bot_deals[-5:]
    feedback = "Performance of last 5 trades:\n"
    total_profit = 0.0
    consecutive_losses = 0

    for deal in recent_deals:
        result = "WIN" if deal.profit > 0 else "LOSS"
        feedback += f"- {deal.symbol}: {result} | Profit: {deal.profit:.2f}\n"
        total_profit += deal.profit
        if deal.profit < 0: consecutive_losses += 1
        else: consecutive_losses = 0

    feedback += f"\nTotal Session Profit: {total_profit:.2f}\n"
    if consecutive_losses >= 3:
        feedback += "\nSYSTEM WARNING: 3 consecutive losses detected."
    return feedback

def get_higher_timeframe_trend(symbol: str = config.DEFAULT_SYMBOL) -> str:
    """Calculates EMA 200 on the H1 timeframe to determine the macro trend."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 300)
    if rates is None or len(rates) < 200:
        return f"Error: Not enough H1 candle data"

    df = pd.DataFrame(rates)
    df["close"] = df["close"].astype(float)
    # Native EMA calculation
    ema_200 = float(df["close"].ewm(span=200, adjust=False).mean().iloc[-1])
    
    current_price = df["close"].iloc[-1]
    return f"BULLISH (Price {current_price:.2f} above H1 EMA 200)" if current_price > ema_200 else f"BEARISH (Price {current_price:.2f} below H1 EMA 200)"

def is_market_open(symbol: str) -> bool:
    """Checks if broker allows trading AND if current time is within allowed hours."""
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None or symbol_info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
        return False
        
    # Get Broker Server Time
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False
        
    dt_server = datetime.fromtimestamp(tick.time)
    
    # 1. Weekend Check (0 = Monday, 6 = Sunday)
    # Market usually closes late Friday and opens early Monday
    if dt_server.weekday() >= 5:
        return False
        
    # 2. Daily Time Check (Adjust hours as needed for your specific broker)
    # Current example: Disables trading between 23:00 and 00:00 server time if needed.
    # To keep 24h trading during weekdays, you can comment this block out.
    current_hour = dt_server.hour
    # Example: If your broker has a daily maintenance close at 23:00
    if current_hour >= 23 or current_hour < 0:
        return False
        
    return True

def update_historical_outcomes(db_path="trades.db"):
    """
    Scans the database for trades older than 1 hour missing an outcome,
    fetches the exact MT5 price from that future timestamp, and updates the row.
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Find records older than 1 hour where the future price is still NULL
        # Note: SQLite stores datetime as strings or integers. Assuming timestamp column is stored as string 'YYYY-MM-DD HH:MM:SS.mmmmmm'
        # We need to find logs where timestamp is < (now - 1 hour)
        one_hour_ago = datetime.now() - timedelta(hours=1)
        
        cursor.execute("""
            SELECT id, timestamp 
            FROM trade_logs 
            WHERE timestamp < ? AND actual_price_1h_later IS NULL
        """, (one_hour_ago,))
        
        pending_records = cursor.fetchall()
        
        # 2. Fetch the historical price and update the database
        for record_id, timestamp_str in pending_records:
            # Parse timestamp string to datetime object
            timestamp_dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
            target_time = timestamp_dt + timedelta(hours=1)
            
            # Fetch the specific 1-minute candle at the target time
            rates = mt5.copy_rates_from(config.DEFAULT_SYMBOL, mt5.TIMEFRAME_M1, target_time, 1)
            
            if rates is not None and len(rates) > 0:
                future_close_price = rates[0]['close']
                cursor.execute("""
                    UPDATE trade_logs 
                    SET actual_price_1h_later = ? 
                    WHERE id = ?
                """, (future_close_price, record_id))
                
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error updating historical outcomes: {e}")

trading_tools = [
    get_market_data,
    execute_trade,
    review_past_performance,
    get_higher_timeframe_trend,
    weekly_strategy_critic,
    is_market_open,
    log_trade_decision,
    update_historical_outcomes,
]
