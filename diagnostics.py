import time
import MetaTrader5 as mt5
import config
from tools import get_symbol_filling_mode


def run_startup_test(
    symbol: str = config.DEFAULT_SYMBOL,
    lot_size: float = config.DEFAULT_LOT_SIZE,
) -> bool:
    """Executes a micro-trade and closes it immediately to verify API connectivity."""
    print(f"\n--- RUNNING STARTUP DIAGNOSTIC ON {symbol} ---")

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"[FAIL] Error: Cannot find symbol {symbol}. Check Market Watch.")
        return False

    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            print(f"[FAIL] Error: Cannot select symbol {symbol} in Market Watch.")
            return False

    filling_mode = get_symbol_filling_mode(symbol_info)

    # 1. SEND THE TEST BUY ORDER
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"[FAIL] Error: Cannot get live prices for {symbol}.")
        return False

    buy_price = tick.ask
    buy_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": mt5.ORDER_TYPE_BUY,
        "price": buy_price,
        "deviation": 20,
        "magic": config.TEST_MAGIC_NUMBER,
        "comment": "Startup Test Buy",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    print("Sending Test BUY order...")
    buy_result = mt5.order_send(buy_request)

    if buy_result is None or buy_result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = buy_result.retcode if buy_result else "None"
        comment = buy_result.comment if buy_result else mt5.last_error()
        print(f"[FAIL] BUY Failed: MT5 retcode {retcode}. {comment}")
        return False

    position_ticket = buy_result.order
    print(f"[OK] BUY Successful! Position Ticket: {position_ticket}")

    print("Waiting 3 seconds...")
    time.sleep(3)

    # 2. SEND THE OPPOSITE SELL ORDER TO CLOSE THE POSITION
    tick = mt5.symbol_info_tick(symbol)
    sell_price = tick.bid if tick else buy_price
    close_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot_size,
        "type": mt5.ORDER_TYPE_SELL,
        "position": position_ticket,
        "price": sell_price,
        "deviation": 20,
        "magic": config.TEST_MAGIC_NUMBER,
        "comment": "Startup Test Close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    print("Sending Test CLOSE order...")
    close_result = mt5.order_send(close_request)

    if close_result is None or close_result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = close_result.retcode if close_result else "None"
        comment = close_result.comment if close_result else mt5.last_error()
        print(f"[FAIL] CLOSE Failed: MT5 retcode {retcode}. {comment}")
        print("[WARNING] You have an open test position that must be closed manually in MT5!")
        return False

    print("[OK] CLOSE Successful! System is 100% ready for AI control.\n")
    return True


if __name__ == "__main__":
    if not mt5.initialize():
        print("[ERROR] MT5 initialization failed. Ensure terminal is open.")
        quit()

    try:
        success = run_startup_test()
        print(f"Startup Test Result: {'SUCCESS' if success else 'FAILED'}")
    finally:
        mt5.shutdown()
