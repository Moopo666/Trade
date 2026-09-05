import csv
from google import genai
from google.genai import types

def run_weekly_critic():
    print("--- RUNNING WEEKLY STRATEGY CRITIC ---")
    
    # 1. Read the Trade Journal
    try:
        with open('trade_journal.csv', mode='r') as file:
            journal_data = file.read()
    except FileNotFoundError:
        print("Error: trade_journal.csv not found. Let the bot run and collect trades first.")
        return
    if not journal_data.strip():
        print("Trade journal is empty. No trades to review yet.")
        return
        
    # 2. Prompt Gemini to analyze performance patterns
    client = genai.Client()
    
    # Note: CSV currently logs: Timestamp, Ticket ID, EA Name, Action, Reasoning, SL Points.
    # PnL data would require cross-referencing with MT5 history, which this script can do.
    prompt = f"""
    You are a Lead Quantitative Analyst reviewing the trade log of an automated trading agent over the past week.
    
    Here is the recent trade log (Timestamp, Ticket ID, EA/Strategy, Action, AI Reasoning, SL Points):
    {journal_data}
    
    Tasks:
    1. Identify any recurring patterns in losing trades (e.g., entering during high ATR, counter-trend trades, choppiness).
    2. Evaluate whether the Gatekeeper AI is being too lenient or too strict based on the reasoning provided.
    3. Output EXACTLY ONE new rule or instruction constraint to add to the Gatekeeper's System Prompt for next week to improve win rate.
    """
    
    response = client.models.generate_content(
        model="gemini-2.0-flash", # Using flash for speed and cost-efficiency
        contents=prompt
    )
    
    print("\n--- CRITIC AI REPORT & PROMPT RECOMMENDATION ---")
    print(response.text)

if __name__ == "__main__":
    run_weekly_critic()
