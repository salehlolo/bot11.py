#!/usr/bin/env python3
"""Script بسيط لجلب بيانات السوق العامة من API بايننس.

Usage:
    python binance_api_reader.py BTCUSDT
"""

import sys
import json
from urllib import request, parse
from typing import List, Dict, Any


def fetch_klines(symbol: str, interval: str = "1m", limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch recent candlestick data from Binance.

    Parameters
    ----------
    symbol : str
        Market pair, e.g. ``"BTCUSDT"``.
    interval : str, optional
        Candlestick timeframe (default ``"1m"``).
    limit : int, optional
        Number of candles to retrieve (max 1000, default 10).
    """
    url = "https://api.binance.com/api/v3/klines"
    params = parse.urlencode({"symbol": symbol.upper(), "interval": interval, "limit": limit})
    with request.urlopen(f"{url}?{params}", timeout=10) as resp:
        data = json.loads(resp.read())
    candles = [
        {
            "open_time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in data
    ]
    return candles


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    candles = fetch_klines(symbol)
    for c in candles:
        print(c)


if __name__ == "__main__":
    main()
