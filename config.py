import os
import MetaTrader5 as mt5
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(override=True)

# API Keys
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY is not set.")
    print("Please set your Gemini API key in a .env file:")
    print("  GEMINI_API_KEY=your_actual_gemini_api_key")
    quit()

# Sync with GOOGLE_API_KEY so genai.Client uses the .env key
os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY

# Telegram Notifications (Optional)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Model Configuration
MODEL_NAME = "gemini-2.0-flash"
TEMPERATURE = 0.1

# Trading Parameters
DEFAULT_SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
DEFAULT_LOT_SIZE = 0.01
DEFAULT_SL_POINTS = 500
DEFAULT_TP_POINTS = 1000

# Magic Numbers
BOT_MAGIC_NUMBER = 777777
TEST_MAGIC_NUMBER = 888888

# Risk Management
MAX_DAILY_LOSS = -50.00  # Circuit breaker threshold
MAX_ALLOWED_SPREAD_POINTS = 150  # Max spread in points for Gold (e.g., 150 points = $1.50)

# Autonomous Loop Interval
LOOP_INTERVAL_SECONDS = 300  # 5 minutes (M5 timeframe)
