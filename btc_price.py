"""Stream live Bitcoin prices from Binance over WebSocket.

Usage:
    pip install websockets
    python btc_price.py            # BTC/USDT
    python btc_price.py ethusdt    # any Binance symbol

Press Ctrl+C to stop.
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import websockets

BASE_URL = os.environ.get("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws")

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def format_line(symbol, price, last_price, trade_time_ms):
    ts = datetime.fromtimestamp(trade_time_ms / 1000).strftime("%H:%M:%S")
    if last_price is None or price == last_price:
        return f"[{ts}] {symbol}: {price:,.2f}"
    elif price > last_price:
        color, arrow = GREEN, "▲"
    else:
        color, arrow = RED, "▼"
    return f"[{ts}] {symbol}: {color}{price:,.2f} {arrow}{RESET}"


async def stream(symbol):
    url = f"{BASE_URL}/{symbol.lower()}@trade"
    display = symbol.upper()
    last_price = None
    backoff = 1

    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                print(f"Connected: {url}")
                backoff = 1
                async for raw in ws:
                    msg = json.loads(raw)
                    price = float(msg["p"])
                    # Only print when the price moves, to keep the output readable.
                    if price != last_price:
                        print(format_line(display, price, last_price, msg["T"]))
                        last_price = price
            # A clean close ends the loop without raising; still back off.
            reason = "server closed the connection"
        except (OSError, websockets.ConnectionClosed) as e:
            reason = e
        print(f"Connection lost ({reason}); reconnecting in {backoff}s...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "btcusdt"
    try:
        asyncio.run(stream(symbol))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
