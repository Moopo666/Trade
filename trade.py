import time
import datetime
import MetaTrader5 as mt5
import json
import atexit
import gc
from google import genai
from google.genai import types

import config
from tools import (
    trading_tools,
    review_past_performance,
    check_spread_safe,
    execute_trade,
    send_telegram_alert,
    calculate_dynamic_lot_size,
    log_trade,
    is_market_open,
    update_historical_outcomes,
    get_unified_market_data,
    manage_existing_positions
)
from diagnostics import run_startup_test
import strategies
from telemetry import TelemetryManager

# Register shutdown alert
atexit.register(lambda: send_telegram_alert("🛑 Trading Bot Stopped."))


SYSTEM_INSTRUCTIONS = """
You are an autonomous Gold (XAU/USD) trading agent focused on short-term M5 trades.

RULE 1: Evaluate Market Data. Check RSI and ATR.
RULE 2: Dynamic Risk Management. Gold is highly sensitive to USD strength and US session liquidity.
- Set your Stop Loss to 2.0x the current ATR. 
- Set your Take Profit to 4.0x the current ATR (a 1:2 risk/reward ratio).
RULE 3: If RSI is between 45 and 55, the market is chopping sideways. Output 'HOLD'.
RULE 4: Be aware of sharp mean-reversions around key psychological levels.
"""


def clean_and_parse_json(raw_text: str) -> dict:
    """Strips markdown code blocks and normalizes JSON keys to lowercase."""
    text = raw_text.strip()
    
    # 1. Remove markdown backticks if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Strip top backticks line (e.g., ```json)
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Strip bottom backticks line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        
    # 2. Parse JSON
    parsed = json.loads(text)
    
    # 3. Normalize keys to lowercase (handles DECISION vs decision)
    normalized = {str(k).lower(): v for k, v in parsed.items()}
    
    return {
        "decision": str(normalized.get("decision", "REJECT")).upper(),
        "reasoning": str(normalized.get("reasoning", "No reasoning provided."))
    }

def ask_gatekeeper_with_retry(client, prompt: str, max_retries: int = 4) -> dict:
    """Sends prompt to Gemini, with robust JSON cleaning and 503 retry logic."""
    delay = 1
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            
            # Clean markdown and parse json safely
            return clean_and_parse_json(response.text)
            
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg:
                print(f"⚠️ API Overloaded (503/429). Retrying in {delay}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"⚠️ JSON Parsing or API Error: {error_msg}")
                # Try cleaning response text if it exists
                if 'response' in locals() and hasattr(response, 'text'):
                    try:
                        return clean_and_parse_json(response.text)
                    except Exception:
                        pass
                break
                
    return {
        "decision": "REJECT", 
        "reasoning": "Gatekeeper formatting or API error. Defaulting to REJECT to protect capital."
    }

def run_trading_bot():
    """Main autonomous trading loop powered by Gemini."""
    # Send startup notification
    send_telegram_alert(f"🚀 Trading Bot Started on {config.DEFAULT_SYMBOL}")
    
    print(f"\n[AI AGENT ACTIVE] Model: {config.MODEL_NAME} | Symbol: {config.DEFAULT_SYMBOL}")
    print(f"Risk Rules: SL=2.0x ATR, TP=4.0x ATR, Daily Loss Limit=${config.MAX_DAILY_LOSS}")
    print(f"Checking market every {config.LOOP_INTERVAL_SECONDS} seconds...\n")

    # Initialize Telemetry
    telemetry = TelemetryManager()

    while True:
        try:
            telemetry.increment('total_scans')
            
            # 1. Pre-trade Safeguards
            if not check_spread_safe(symbol=config.DEFAULT_SYMBOL, max_allowed_spread_points=config.MAX_ALLOWED_SPREAD_POINTS):
                telemetry.increment('spread_rejects')
                time.sleep(config.LOOP_INTERVAL_SECONDS)
                continue

            # MANDATORY: Check for existing open positions
            positions = mt5.positions_get(symbol=config.DEFAULT_SYMBOL)
            active_pos = [p for p in positions if p.magic == config.BOT_MAGIC_NUMBER] if positions else []

            # Fetch unified market data
            market_data = get_unified_market_data(config.DEFAULT_SYMBOL)
            if not market_data:
                time.sleep(config.LOOP_INTERVAL_SECONDS)
                continue

            # --- HEARTBEAT CHECK ---
            current_time = datetime.datetime.now()
            if current_time.hour != telemetry.last_heartbeat_hour:
                account_info = mt5.account_info()
                equity = account_info.equity if account_info else 0.0
                telemetry.send_hourly_report(current_time, equity, market_data.get('spread', 'N/A'))

            if active_pos:
                # Handle existing positions (Manage BE/Partial Close)
                manage_existing_positions(active_pos[0], market_data['m5_atr'])
                time.sleep(config.LOOP_INTERVAL_SECONDS)
                continue
            
            # 2. Trend & Signal Evaluation (Using RSI Crossover on completed bars)
            is_uptrend = market_data['m5_close'] > market_data['m5_ema50']
            is_downtrend = market_data['m5_close'] < market_data['m5_ema50']
            
            if is_uptrend: telemetry.increment('uptrend_count')
            if is_downtrend: telemetry.increment('downtrend_count')
            
            rsi_buy_signal = (market_data['prev_m1_rsi'] < 30) and (market_data['curr_m1_rsi'] >= 30)
            rsi_sell_signal = (market_data['prev_m1_rsi'] > 70) and (market_data['curr_m1_rsi'] <= 70)

            if rsi_buy_signal or rsi_sell_signal: telemetry.increment('rsi_touches')

            # 3. Order Execution with Dynamic Risk
            sl_points = int(market_data['m5_atr'] * 2.0)
            tp_points = int(market_data['m5_atr'] * 4.0)
            lot_size = calculate_dynamic_lot_size(config.DEFAULT_SYMBOL, sl_points)

            if is_uptrend and rsi_buy_signal:
                execute_trade(action="BUY", symbol=config.DEFAULT_SYMBOL, lot_size=lot_size, sl_points=sl_points, tp_points=tp_points)
                
            elif is_downtrend and rsi_sell_signal:
                execute_trade(action="SELL", symbol=config.DEFAULT_SYMBOL, lot_size=lot_size, sl_points=sl_points, tp_points=tp_points)

            # 4. Check performance for circuit breaker
            performance_data = review_past_performance()
            if "Total Session Profit:" in performance_data:
                current_pnl = float(performance_data.split("Total Session Profit: ")[1].split("\n")[0].strip())
                if current_pnl <= config.MAX_DAILY_LOSS:
                    send_telegram_alert(f"🚨 CIRCUIT BREAKER TRIGGERED: Daily loss limit reached. Shutting down.")
                    mt5.shutdown()
                    break

        except Exception as e:
            send_telegram_alert(f"🚨 BOT CRITICAL ERROR: {str(e)}")
            time.sleep(60)

        # Loop Interval polling
        time.sleep(config.LOOP_INTERVAL_SECONDS)
        gc.collect()


if __name__ == "__main__":
    if not mt5.initialize():
        print("[ERROR] MT5 initialization failed. Ensure the terminal is open.")
        quit()

    print(f"Connected to MT5 Account: {mt5.account_info().login}")

    is_ready = run_startup_test(symbol=config.DEFAULT_SYMBOL)

    if is_ready:
        run_trading_bot()
    else:
        print("[ERROR] Bot shutdown due to diagnostic failure.")
        mt5.shutdown()
