#!/usr/bin/env python3
"""
fetch_binance_recent.py — Download recent BTC/USDT 1m klines via Binance REST API.

Fills the gap left by data.binance.vision (which only has completed months).
Downloads from 2025-01-01 to now and appends to the existing parquet.

Uses ThreadPoolExecutor for parallel downloads — splits the date range into
daily chunks, each fetching 1440 bars (one full day of 1m data).

Usage:
  python tools/fetch_binance_recent.py                    # default: 2025-01-01 to now
  python tools/fetch_binance_recent.py --start 2025-01-01
  python tools/fetch_binance_recent.py --workers 10
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import pandas as pd
import requests

# ─── Configuration ──────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
INTERVAL = "1m"
LIMIT = 1000  # max per API call
DAY_MS = 86_400_000  # 1 day in ms
BARS_PER_DAY = 1440

BASE_URL = "https://api.binance.com/api/v3/klines"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "signals" / "multi_asset_results"
OUTPUT_FILE = OUTPUT_DIR / "btcusdt_binance_1m_2017_2026.parquet"

DEFAULT_START = "2025-01-01"
DEFAULT_WORKERS = 10  # conservative for API (vs CDN)

_lock = Lock()
_progress = {"done": 0, "total": 0, "bars": 0, "failed": 0}


def ms(dt_str: str) -> int:
    return int(pd.Timestamp(dt_str, tz="UTC").timestamp() * 1000)


def fetch_day(start_ms: int) -> list[list]:
    """Fetch one day of 1m klines (2 API calls of 1000 + 440 bars)."""
    end_ms = start_ms + DAY_MS - 1
    all_rows = []
    cursor = start_ms

    while cursor <= end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": LIMIT,
        }
        for attempt in range(5):
            try:
                r = requests.get(BASE_URL, params=params, timeout=30)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 30))
                    print(f"\n  Rate limited -- waiting {wait}s...")
                    time.sleep(wait)
                    continue
                if r.status_code == 418:
                    wait = int(r.headers.get("Retry-After", 120))
                    print(f"\n  IP temp-banned -- waiting {wait}s...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                batch = r.json()
                break
            except requests.RequestException as e:
                wait = 2 ** attempt
                print(f"\n  Error: {e} -- retry in {wait}s...")
                time.sleep(wait)
                batch = []
        else:
            batch = []

        if not batch:
            break

        all_rows.extend(batch)
        last_open = batch[-1][0]
        cursor = last_open + 60_000

        if len(batch) < LIMIT:
            break

    day_str = pd.Timestamp(start_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    with _lock:
        _progress["done"] += 1
        _progress["bars"] += len(all_rows)
        if not all_rows:
            _progress["failed"] += 1
        p = _progress
        pct = p["done"] / p["total"] * 100
        print(
            f"\r  Days: {p['done']:>4}/{p['total']}"
            f"  ({pct:5.1f}%)"
            f"  bars: {p['bars']:>9,}"
            f"  last: {day_str}   ",
            end="", flush=True,
        )

    return all_rows


def rows_to_df(all_rows: list[list]) -> pd.DataFrame:
    """Convert raw klines to DataFrame."""
    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time"])

    min_ms = 1_483_228_800_000  # 2017-01-01
    max_ms = 1_893_456_000_000  # 2030-01-01
    df = df[(df["open_time"] >= min_ms) & (df["open_time"] <= max_ms)]

    df["ts"] = pd.to_datetime(df["open_time"].astype(int), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.rename(columns={
        "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v",
    })
    return df[["ts", "o", "h", "l", "c", "v"]]


def main():
    parser = argparse.ArgumentParser(
        description="Download recent BTC/USDT 1m data via Binance API (parallel)"
    )
    parser.add_argument("--start", default=DEFAULT_START,
                        help="Start date YYYY-MM-DD (default: 2025-01-01)")
    parser.add_argument("--end", default=None,
                        help="End date YYYY-MM-DD (default: now)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Concurrent requests (default: {DEFAULT_WORKERS})")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    start_ms = ms(args.start)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000) if args.end is None else ms(args.end)

    # Auto-detect start from existing parquet
    existing_df = None
    if OUTPUT_FILE.exists():
        existing_df = pd.read_parquet(OUTPUT_FILE)
        last_ts = existing_df["ts"].max()
        auto_start = int(last_ts.timestamp() * 1000) + 60_000
        if auto_start > start_ms:
            start_ms = auto_start
            print(f"Existing data ends at {last_ts}")
            print(f"  Starting from next minute\n")

    # Generate daily chunk start times
    day_starts = list(range(start_ms, end_ms, DAY_MS))
    _progress.update(done=0, total=len(day_starts), bars=0, failed=0)

    print(f"Downloading {SYMBOL} {INTERVAL} via Binance API")
    print(f"  From: {pd.Timestamp(start_ms, unit='ms', tz='UTC')}")
    print(f"  To:   {pd.Timestamp(end_ms, unit='ms', tz='UTC')}")
    print(f"  Days: {len(day_starts)}")
    print(f"  Workers: {args.workers}")
    print()

    t0 = time.time()
    all_rows = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_day, s): s for s in day_starts}
        for future in as_completed(futures):
            rows = future.result()
            all_rows.extend(rows)

    elapsed = time.time() - t0
    print(f"\n\nFetch completed in {elapsed:.0f}s")
    print(f"  Failed days: {_progress['failed']}")

    if not all_rows and existing_df is None:
        print("No data downloaded.")
        sys.exit(1)

    df = rows_to_df(all_rows) if all_rows else pd.DataFrame()

    if existing_df is not None and not df.empty:
        df = pd.concat([existing_df, df], ignore_index=True)
    elif existing_df is not None:
        df = existing_df

    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    df.to_parquet(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"  Rows:  {len(df):,}")
    print(f"  From:  {df['ts'].min()}")
    print(f"  To:    {df['ts'].max()}")
    size_mb = OUTPUT_FILE.stat().st_size / 1_048_576
    print(f"  Size:  {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
