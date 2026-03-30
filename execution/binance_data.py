"""
Fetch BTC/USDT daily candles from Binance public API for SMA440 calculation.
No authentication required. Cached in-memory between refreshes.
"""
import logging
import time
import requests

log = logging.getLogger("binance_data")

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_cache = {"ts": 0.0, "candles": []}
_CACHE_TTL = 14400  # 4 hours in seconds


def fetch_daily_closes(symbol: str = "BTCUSDT", limit: int = 500) -> list[float]:
    """
    Return up to `limit` daily close prices (oldest first) from Binance.
    Results cached for 4 hours.
    """
    now = time.time()
    if _cache["candles"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["candles"]

    try:
        resp = requests.get(
            _BINANCE_KLINES_URL,
            params={"symbol": symbol, "interval": "1d", "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Binance kline: [open_time, o, h, l, c, vol, close_time, ...]
        closes = [float(k[4]) for k in data]
        _cache["candles"] = closes
        _cache["ts"] = now
        log.info(f"Fetched {len(closes)} daily candles from Binance")
        return closes
    except Exception as e:
        log.error(f"Binance daily candle fetch failed: {e}")
        if _cache["candles"]:
            log.warning("Using stale cached daily candles")
            return _cache["candles"]
        return []
