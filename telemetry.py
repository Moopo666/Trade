import datetime
import config
from tools import send_telegram_alert

class TelemetryManager:
    def __init__(self):
        self.stats = {
            'total_scans': 0,
            'uptrend_count': 0,
            'downtrend_count': 0,
            'rsi_touches': 0,
            'spread_rejects': 0
        }
        self.last_heartbeat_hour = -1

    def increment(self, key):
        if key in self.stats:
            self.stats[key] += 1

    def reset_stats(self):
        for key in self.stats:
            self.stats[key] = 0

    def send_hourly_report(self, current_time, equity, current_spread):
        msg = (
            f"🟢 [HEARTBEAT] Account Equity: ${equity:.2f}\n\n"
            f"📊 HOURLY DIAGNOSTICS:\n"
            f"🔍 Scans Performed: {self.stats['total_scans']}\n"
            f"📈 Uptrend Detected: {self.stats['uptrend_count']} times\n"
            f"📉 Downtrend Detected: {self.stats['downtrend_count']} times\n"
            f"🎯 RSI Zone Touches: {self.stats['rsi_touches']}\n"
            f"⚠️ Spread Rejects: {self.stats['spread_rejects']}\n"
            f"⏱️ Current Spread: {current_spread} (Max {config.MAX_ALLOWED_SPREAD_POINTS})"
        )
        send_telegram_alert(msg)
        print(f"[{current_time.strftime('%H:%M')}] Telemetry Heartbeat sent to Telegram.")
        self.reset_stats()
        self.last_heartbeat_hour = current_time.hour
