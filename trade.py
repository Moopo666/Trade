import time
import MetaTrader5 as mt5
import json
import gc
import atexit
from google import genai
from google.genai import types

import config
from tools import trading_tools, review_past_performance, check_spread_safe, get_market_data, execute_trade, send_telegram_alert, calculate_dynamic_lot_size, log_trade, get_current_atr, get_current_spread, get_higher_timeframe_trend
from diagnostics import run_startup_test
import strategies

# Register shutdown alert
atexit.register(lambda: send_telegram_alert("🛑 Trading Bot Stopped."))


SYSTEM_INSTRUCTIONS = """
You are an autonomous crypto trading agent focused on short-term M5 trades.

RULE 1: Evaluate Market Data. Check RSI and ATR.
RULE 2: Dynamic Risk Management. Bitcoin is highly volatile. 
- Set your Stop Loss to 2.0x the current ATR. 
- Set your Take Profit to 4.0x the current ATR (a 1:2 risk/reward ratio).
RULE 3: If RSI is between 45 and 55, the market is chopping sideways. Output 'HOLD'.
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
                model="gemini-2.5-flash",
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
    
    client = genai.Client()

    # (chat object no longer used by ask_gatekeeper_with_retry)
    
    print(f"\n[AI AGENT ACTIVE] Model: {config.MODEL_NAME} | Symbol: {config.DEFAULT_SYMBOL}")
    print(f"Risk Rules: SL=2.0x ATR, TP=4.0x ATR, Daily Loss Limit=${config.MAX_DAILY_LOSS}")
    print(f"Checking market every {config.LOOP_INTERVAL_SECONDS} seconds...\n")

    # Initialize heartbeat timer
    last_heartbeat_time = time.time()
    HEARTBEAT_INTERVAL = 3600 # 1 hour

    while True:
        try:
            # --- HEARTBEAT CHECK ---
            current_time = time.time()
            if current_time - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                terminal_info = mt5.terminal_info()
                account_info = mt5.account_info()
                send_telegram_alert(
                    f"🟢 <b>[SYSTEM HEARTBEAT]</b>\n\n"
                    f"<b>Status:</b> Active\n"
                    f"<b>MT5 Connected:</b> {terminal_info.connected}\n"
                    f"<b>Account Equity:</b> ${account_info.equity:.2f}"
                )
                last_heartbeat_time = current_time

            # 0. Check for existing open positions (Machine-gun trading prevention)
            open_positions = mt5.positions_get(symbol="BTCUSD")
            if open_positions is not None and len(open_positions) > 0:
                print("Trade already open. Waiting for it to hit SL/TP...")
                time.sleep(300)
                continue

            # 1. Spread filter
            if not check_spread_safe(symbol="BTCUSD", max_allowed_spread_points=2500):
                time.sleep(300)
                continue
                
            print("Scanning EAs for signals...")
            
            # 1. Collect all signals from your EAs
            active_signals = []
            
            rsi_signal = strategies.ea_rsi_reversion("BTCUSD")
            if rsi_signal: active_signals.append(rsi_signal)
                
            ema_signal = strategies.ea_ema_crossover("BTCUSD")
            if ema_signal: active_signals.append(ema_signal)
                
            # 2. Process signals through the AI Gatekeeper
            if not active_signals:
                print("No EA signals generated this cycle.")
            else:
                for signal in active_signals:
                    print(f"\n[SIGNAL DETECTED] {signal['strategy_name']} wants to {signal['action']}.")
                    
                    # Fetch context for the AI
                    atr = get_current_atr("BTCUSD")
                    macro_trend = get_higher_timeframe_trend("BTCUSD")
                    spread = get_current_spread("BTCUSD")
                    market_context = get_market_data("BTCUSD")
                    
                    prompt = f"""
                    You are the 'Gatekeeper AI', a ruthless quantitative risk manager for an automated trading fund.
                    An internal algorithmic sub-agent (EA) has generated a trade proposal. Your strict duty is to approve or veto this signal to protect capital.
                    --- SUB-AGENT SIGNAL ---
                    Symbol: {signal['symbol']}
                    Strategy Name: {signal['strategy_name']}
                    Proposed Action: {signal['action']}
                    Technical Reasoning: {signal['reason']}
                    --- CURRENT MARKET CONTEXT ---
                    Macro Trend (H1 EMA 200): {macro_trend}
                    Current Volatility (ATR): {atr} points
                    Current Spread: {spread} points
                    --- GATEKEEPER RISK RULES ---
                    1. TREND ALIGNMENT: You must brutally reject counter-trend trades (e.g., BUYing when the H1 trend is bearish) unless the sub-agent's mean-reversion reasoning is exceptionally strong.
                    2. VOLATILITY CHECK: If ATR is violently expanding (indicating news/chop) or the spread is too wide, REJECT the trade to avoid slippage.
                    3. LOGIC CHECK: Does the sub-agent's reasoning actually make sense in this broader context EAs are often "dumb"—you are the intelligence.
                    4. CAPITAL PRESERVATION: When in doubt, REJECT. Only approve high-probability setups.
                    Evaluate the signal against the market context. You must output a structured JSON response containing:
                    1. 'decision': Exactly 'APPROVE' or 'REJECT'.
                    2. 'reasoning': ONE concise sentence explaining exactly why you approved or rejected it.
                    """
                    
                    # Pass signal through Gatekeeper using retry logic
                    decision_data = ask_gatekeeper_with_retry(client, prompt)
                    
                    ai_decision = decision_data.get('decision', 'REJECT').upper()
                    ai_reasoning = decision_data.get('reasoning', 'No reasoning provided.')
                    
                    print(f"AI Gatekeeper Decision: {ai_decision} | Reasoning: {ai_reasoning}")
                    
                    if "APPROVE" in ai_decision:
                        print("Executing approved trade...")
                        # Calculate dynamic parameters
                        sl_points = int(atr * 2.0)
                        tp_points = int(atr * 4.0)
                        lot_size = calculate_dynamic_lot_size("BTCUSD", sl_points)
                        
                        # Capture return tuple (message, ticket_id)
                        result_msg, ticket_id = execute_trade(action=signal['action'], symbol=signal['symbol'], magic=signal['magic'], 
                                               lot_size=lot_size, sl_points=sl_points, tp_points=tp_points)
                        
                        # Send alert on success & Log trade
                        if "SUCCESS" in result_msg:
                            send_telegram_alert(f"✅ Trade Executed: {signal['strategy_name']} {signal['action']}\n{result_msg}")
                            # Log trade using ai_reasoning as the journal reasoning
                            log_trade(signal['strategy_name'], signal['action'], ai_reasoning, sl_points, ticket_id)
                        else:
                            send_telegram_alert(f"❌ Trade Failed: {signal['strategy_name']}\n{result_msg}")

                    else:
                        print(f"Trade vetoed by AI.")

            # 2. Check performance for circuit breaker
            performance_data = review_past_performance()

            if "Total Session Profit:" in performance_data:
                profit_str = performance_data.split("Total Session Profit: ")[1].split("\n")[0].strip()
                current_pnl = float(profit_str)

                # 2. THE CIRCUIT BREAKER
                if current_pnl <= config.MAX_DAILY_LOSS:
                    print(f"[STOP] CIRCUIT BREAKER TRIGGERED: Daily loss limit of {config.MAX_DAILY_LOSS} reached. Shutting down system.")
                    send_telegram_alert(f"🚨 CIRCUIT BREAKER TRIGGERED: Daily loss limit of {config.MAX_DAILY_LOSS} reached. Shutting down system.")
                    mt5.shutdown()
                    break

        except Exception as e:
            error_message = f"🚨 BOT CRITICAL ERROR: {str(e)}"
            print(error_message)
            send_telegram_alert(error_message)
            time.sleep(60)

        # Wait for the next candle close
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
