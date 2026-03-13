"""
v2 data fetch — standalone, no dependency on v1 execution/config.

Provides:
  get_mid_price(coin)           → float
  get_candles(coin, interval, n) → list of OHLCV dicts
  get_regime(coin)               → ('bull'|'bear'|'unknown', sma_400d, price)

Hyperliquid public API, no auth required.
"""
import logging
import time
import requests

log = logging.getLogger("mrm_v2.data_fetch")

HL_API = "https://api.hyperliquid.xyz"

_INTERVAL_MS = {
    "1m":   60_000,
    "5m":   300_000,
    "15m":  900_000,
    "1h":   3_600_000,
    "4h":   14_400_000,
    "1d":   86_400_000,
}


def get_mid_price(coin: str) -> float:
    resp = requests.post(
        f"{HL_API}/info",
        json={"type": "allMids"},
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()[coin])


def get_candles(coin: str, interval: str, n: int) -> list:
    """Return list of candle dicts with keys: t, o, h, l, c, v."""
    if interval not in _INTERVAL_MS:
        raise ValueError(f"Unknown interval {interval!r}")
    ims = _INTERVAL_MS[interval]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - n * ims
    resp = requests.post(
        f"{HL_API}/info",
        json={
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def compute_regime(coin: str, regime_period: int = 400, fetch_n: int = 430) -> tuple:
    """
    Fetch daily candles and compute the 400-day SMA regime.

    Returns:
        (regime, sma_val, current_price)
        regime: 'bull' | 'bear' | 'unknown'
    """
    try:
        candles = get_candles(coin, "1d", fetch_n)
        closes = [float(c["c"]) for c in candles[:-1]]   # exclude in-progress bar
        price = float(get_mid_price(coin))

        if len(closes) < regime_period:
            log.warning(
                f"Only {len(closes)} daily closes — need {regime_period} for 400d SMA. "
                "Regime: unknown (allowing both directions)."
            )
            return ("unknown", None, price)

        sma = sum(closes[-regime_period:]) / regime_period

        if price > sma:
            regime = "bull"
        else:
            regime = "bear"

        log.info(
            f"Regime: {regime.upper()} | price=${price:,.0f} | "
            f"400d SMA=${sma:,.0f} | {'+' if price > sma else '-'}"
            f"{abs((price/sma - 1)*100):.1f}%"
        )
        return (regime, sma, price)

    except Exception as e:
        log.error(f"compute_regime failed: {e}")
        return ("unknown", None, None)
