import time
import MetaTrader5 as mt5
import json
import gc
from google import genai
from google.genai import types

import config
from tools import trading_tools, review_past_performance, check_spread_safe, get_market_data, execute_trade, send_telegram_alert, calculate_dynamic_lot_size, log_trade, get_current_atr, get_current_spread, get_higher_timeframe_trend
from diagnostics import run_startup_test
import strategies


SYSTEM_INSTRUCTIONS = """
You are an autonomous crypto trading agent focused on short-term M5 trades.

RULE 1: Evaluate Market Data. Check RSI and ATR.
RULE 2: Dynamic Risk Management. Bitcoin is highly volatile. 
- Set your Stop Loss to 2.0x the current ATR. 
- Set your Take Profit to 4.0x the current ATR (a 1:2 risk/reward ratio).
RULE 3: If RSI is between 45 and 55, the market is chopping sideways. Output 'HOLD'.
"""


def ask_gatekeeper_with_retry(prompt: str, chat_session, max_retries: int = 4) -> str:
    """
    Sends a prompt to Gemini with exponential backoff for 503 errors.
    If the API remains down, it defaults to REJECT to protect the account.
    """
    delay = 1 # Start with a 1-second delay
    
    for attempt in range(max_retries):
        try:
            # Attempt to send the message
            response = chat_session.send_message(prompt)
            return response.text.strip().upper()
            
        except Exception as e:
            error_msg = str(e)
            
            # Check if the error is a 503 or High Demand issue
            if "503" in error_msg or "UNAVAILABLE" in error_msg:
                print(f"⚠️ API Overloaded (503). Retrying in {delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2 # Exponential backoff: waits 1s, then 2s, then 4s, then 8s
            else:
                # If it is a different error (like an invalid API key), print it and break
                print(f"❌ Unhandled API Error: {error_msg}")
                break
                
    # If the loop exhausts all retries, fail safely.
    print("🚨 Gatekeeper API completely offline. Defaulting to REJECT to protect capital.")
    return "REJECT"

def run_trading_bot():
    """Main autonomous trading loop powered by Gemini."""
    # Send startup notification
    send_telegram_alert(f"🚀 Trading Bot Started on {config.DEFAULT_SYMBOL}")
    
    client = genai.Client()

    chat = client.chats.create(
        model=config.MODEL_NAME,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTIONS,
            tools=trading_tools,
            temperature=config.TEMPERATURE,
        ),
    )

    print(f"\n[AI AGENT ACTIVE] Model: {config.MODEL_NAME} | Symbol: {config.DEFAULT_SYMBOL}")
    print(f"Risk Rules: SL=2.0x ATR, TP=4.0x ATR, Daily Loss Limit=${config.MAX_DAILY_LOSS}")
    print(f"Checking market every {config.LOOP_INTERVAL_SECONDS} seconds...\n")

    while True:
        try:
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
                    raw_response = ask_gatekeeper_with_retry(prompt, chat)
                    
                    try:
                        # Extract JSON from response (handling potential markdown formatting)
                        json_str = raw_response.replace('```json', '').replace('```', '').strip()
                        decision_data = json.loads(json_str)
                        ai_decision = decision_data.get('decision', 'REJECT').upper()
                        ai_reasoning = decision_data.get('reasoning', 'No reasoning provided.')
                    except:
                        print(f"Failed to parse JSON response: {raw_response}")
                        ai_decision = 'REJECT'
                        ai_reasoning = 'Failed to parse AI decision.'
                    
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
