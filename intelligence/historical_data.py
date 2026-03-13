"""
Historical Data Fetcher — Hyperliquid Real Data

Pulls REAL historical data from Hyperliquid's public API:
- Funding rates (every ~1h, paginated 500/request)
- OHLCV candles (various intervals, paginated 500/request)
- No historical OI endpoint exists — we can only capture current snapshots

Data stored as compressed CSV under intelligence/data/historical/
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
DATA_DIR = Path(__file__).parent / "data" / "historical"

ASSETS = ["BTC", "ETH", "SOL"]

# Hyperliquid data starts ~May 2023. Start from April 1 to be safe.
EARLIEST_MS = int(datetime(2023, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)

REQUEST_DELAY = 0.15


class HyperliquidHistoricalFetcher:

    def __init__(self, rate_limit: float = REQUEST_DELAY):
        self.rate_limit = rate_limit
        self._last_request = 0.0
        self._client: httpx.Client | None = None
        self._request_count = 0

    def __enter__(self):
        self._client = httpx.Client(timeout=30.0)
        return self

    def __exit__(self, *args):
        if self._client:
            self._client.close()
            self._client = None

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.monotonic()

    def _post(self, payload: dict[str, Any], retries: int = 3) -> Any:
        if not self._client:
            raise RuntimeError("Use 'with' context manager")
        for attempt in range(retries):
            self._throttle()
            try:
                resp = self._client.post(INFO_URL, json=payload)
                self._request_count += 1
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Rate limited, waiting %ds...", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning("HTTP error %s, retry in %ds", e.response.status_code, wait)
                    time.sleep(wait)
                else:
                    raise
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                if attempt < retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Connection error, retry in %ds: %s", wait, e)
                    time.sleep(wait)
                else:
                    raise

    def fetch_funding_history(self, coin: str, start_ms: int | None = None,
                               end_ms: int | None = None) -> list[dict]:
        """Fetch ALL historical funding rates. Paginates 500 per page."""
        start = start_ms or EARLIEST_MS
        end = end_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        all_records: list[dict] = []
        cursor = start

        while cursor < end:
            data = self._post({
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cursor,
                "endTime": end,
            })
            if not data:
                break
            all_records.extend(data)
            last_time = data[-1]["time"]
            if last_time <= cursor:
                break
            cursor = last_time + 1
            if len(data) < 500:
                break
            if self._request_count % 50 == 0:
                logger.info("  %s funding: %d records so far (at %s)",
                           coin, len(all_records),
                           datetime.utcfromtimestamp(last_time / 1000).strftime("%Y-%m-%d"))

        return all_records

    def save_funding(self, coin: str, records: list[dict]) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"funding_{coin}.csv.gz"
        with gzip.open(path, "wt", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp_ms", "datetime_utc", "coin", "funding_rate", "premium"])
            for r in records:
                ts = r["time"]
                dt = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([ts, dt, r["coin"], r["fundingRate"], r["premium"]])
        logger.info("Saved %d funding records for %s to %s", len(records), coin, path)
        return path

    def fetch_candles(self, coin: str, interval: str = "1h",
                      start_ms: int | None = None, end_ms: int | None = None) -> list[dict]:
        """Fetch ALL historical candles. Paginates 500 per page."""
        start = start_ms or EARLIEST_MS
        end = end_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        all_candles: list[dict] = []
        cursor = start

        while cursor < end:
            data = self._post({
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end,
                },
            })
            if not data:
                break
            all_candles.extend(data)
            last_close = data[-1]["T"]
            if last_close <= cursor:
                break
            cursor = last_close + 1
            if len(data) < 500:
                break
            if self._request_count % 50 == 0:
                logger.info("  %s %s candles: %d so far (at %s)",
                           coin, interval, len(all_candles),
                           datetime.utcfromtimestamp(last_close / 1000).strftime("%Y-%m-%d"))

        return all_candles

    def save_candles(self, coin: str, interval: str, candles: list[dict]) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"candles_{coin}_{interval}.csv.gz"
        with gzip.open(path, "wt", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["open_time_ms", "close_time_ms", "datetime_utc", "coin",
                           "interval", "open", "close", "high", "low", "volume", "num_trades"])
            for c in candles:
                dt = datetime.utcfromtimestamp(c["t"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([c["t"], c["T"], dt, c["s"], c["i"],
                               c["o"], c["c"], c["h"], c["l"], c["v"], c["n"]])
        logger.info("Saved %d candles (%s) for %s to %s", len(candles), interval, coin, path)
        return path


def load_funding_csv(coin: str) -> list[dict]:
    path = DATA_DIR / f"funding_{coin}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"No funding data for {coin} at {path}")
    records = []
    with gzip.open(path, "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                "timestamp_ms": int(row["timestamp_ms"]),
                "datetime_utc": row["datetime_utc"],
                "coin": row["coin"],
                "funding_rate": float(row["funding_rate"]),
                "premium": float(row["premium"]),
            })
    return records


def load_candles_csv(coin: str, interval: str = "1h") -> list[dict]:
    path = DATA_DIR / f"candles_{coin}_{interval}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"No candle data for {coin}/{interval} at {path}")
    candles = []
    with gzip.open(path, "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append({
                "open_time_ms": int(row["open_time_ms"]),
                "close_time_ms": int(row["close_time_ms"]),
                "datetime_utc": row["datetime_utc"],
                "coin": row["coin"],
                "interval": row["interval"],
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row["volume"]),
                "num_trades": int(row["num_trades"]),
            })
    return candles


def fetch_all(assets: list[str] | None = None, candle_intervals: list[str] | None = None):
    """Fetch all historical data for all assets."""
    assets = assets or ASSETS
    intervals = candle_intervals or ["1h", "4h"]

    with HyperliquidHistoricalFetcher() as fetcher:
        for coin in assets:
            logger.info("=" * 60)
            logger.info("Fetching funding history for %s...", coin)
            funding = fetcher.fetch_funding_history(coin)
            if funding:
                fetcher.save_funding(coin, funding)
                first_dt = datetime.utcfromtimestamp(funding[0]["time"] / 1000).strftime("%Y-%m-%d")
                last_dt = datetime.utcfromtimestamp(funding[-1]["time"] / 1000).strftime("%Y-%m-%d")
                logger.info("  %s: %d funding records (%s to %s)", coin, len(funding), first_dt, last_dt)
            else:
                logger.warning("  %s: No funding data!", coin)

            for interval in intervals:
                logger.info("Fetching %s candles for %s...", interval, coin)
                candles = fetcher.fetch_candles(coin, interval)
                if candles:
                    fetcher.save_candles(coin, interval, candles)
                    first_dt = datetime.utcfromtimestamp(candles[0]["t"] / 1000).strftime("%Y-%m-%d")
                    last_dt = datetime.utcfromtimestamp(candles[-1]["t"] / 1000).strftime("%Y-%m-%d")
                    logger.info("  %s %s: %d candles (%s to %s)", coin, interval, len(candles), first_dt, last_dt)
                else:
                    logger.warning("  %s %s: No candle data!", coin, interval)

        logger.info("=" * 60)
        logger.info("Total API requests: %d", fetcher._request_count)


def verify_data():
    """Spot-check saved data for sanity."""
    print("\n" + "=" * 70)
    print("DATA VERIFICATION")
    print("=" * 70)

    for coin in ASSETS:
        print(f"\n--- {coin} ---")

        try:
            funding = load_funding_csv(coin)
            print(f"  Funding: {len(funding)} records")
            if funding:
                print(f"    Range: {funding[0]['datetime_utc']} to {funding[-1]['datetime_utc']}")
                rates = [r["funding_rate"] for r in funding]
                avg_r = sum(rates) / len(rates)
                print(f"    Avg rate: {avg_r:.8f}, Min: {min(rates):.8f}, Max: {max(rates):.8f}")
                if abs(avg_r) > 0.01:
                    print("    WARNING: Average funding rate seems extreme!")
                else:
                    print("    OK: Funding rates look reasonable")
        except FileNotFoundError:
            print("  Funding: NOT FOUND")

        for interval in ["1h", "4h"]:
            try:
                candles = load_candles_csv(coin, interval)
                print(f"  Candles ({interval}): {len(candles)} records")
                if candles:
                    print(f"    Range: {candles[0]['datetime_utc']} to {candles[-1]['datetime_utc']}")
                    print(f"    First close: ${candles[0]['close']:,.2f}, Last close: ${candles[-1]['close']:,.2f}")
            except FileNotFoundError:
                print(f"  Candles ({interval}): NOT FOUND")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify_data()
    else:
        print("Fetching ALL historical data from Hyperliquid...")
        print(f"Assets: {ASSETS}")
        print(f"This will take several minutes due to pagination + rate limits.\n")
        fetch_all()
        verify_data()
