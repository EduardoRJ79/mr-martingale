#!/usr/bin/env python3
"""
fetch_binance_data.py — Download BTC/USDT 1-minute klines from data.binance.vision.

Uses ThreadPoolExecutor for parallel downloads of monthly CSV zips.
Full download (2017-08 to today, ~4.5M bars) takes ~5-10 minutes.

Source: https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/
  (public CDN — no API key, no rate limits)

Saves to:
  signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet

Columns: ts (UTC datetime), o, h, l, c, v  (matching codebase convention)

Usage:
  python tools/fetch_binance_data.py                         # full download 2017-08 to today
  python tools/fetch_binance_data.py --start 2022-01-01      # partial
  python tools/fetch_binance_data.py --resume                 # resume from last saved timestamp
  python tools/fetch_binance_data.py --workers 10             # control concurrency
"""

import argparse
import io
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import pandas as pd
import requests

# ─── Configuration ──────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
INTERVAL = "1m"

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "signals" / "multi_asset_results"
OUTPUT_FILE = OUTPUT_DIR / "btcusdt_binance_1m_2017_2026.parquet"

DEFAULT_START = "2017-08"
DEFAULT_WORKERS = 20

CSV_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_vol", "trades", "taker_buy_base",
    "taker_buy_quote", "ignore",
]

# Thread-safe progress
_lock = Lock()
_progress = {"done": 0, "total": 0, "bars": 0, "skipped": 0}


def generate_months(start_ym: str, end_ym: str) -> list[str]:
    """Generate list of 'YYYY-MM' strings from start to end inclusive."""
    sy, sm = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    months = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def csv_to_df(csv_bytes: bytes) -> pd.DataFrame:
    """Parse CSV bytes into DataFrame with standard columns."""
    df = pd.read_csv(io.BytesIO(csv_bytes), header=None, names=CSV_COLUMNS)

    # Some months have extra columns or non-numeric open_time — filter bad rows
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time"])

    # Sanity check: valid ms timestamps are between 2017 and 2030
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


def fetch_month(year_month: str) -> pd.DataFrame | None:
    """Download and extract one monthly ZIP from data.binance.vision."""
    filename = f"{SYMBOL}-{INTERVAL}-{year_month}.zip"
    url = f"{BASE_URL}/{SYMBOL}/{INTERVAL}/{filename}"

    for attempt in range(5):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                with _lock:
                    _progress["done"] += 1
                    _progress["skipped"] += 1
                return None
            r.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                csv_bytes = zf.read(zf.namelist()[0])

            df = csv_to_df(csv_bytes)

            with _lock:
                _progress["done"] += 1
                _progress["bars"] += len(df)
                p = _progress
                pct = p["done"] / p["total"] * 100
                print(
                    f"\r  Months: {p['done']:>3}/{p['total']}"
                    f"  ({pct:5.1f}%)"
                    f"  bars: {p['bars']:>9,}"
                    f"  last: {year_month}   ",
                    end="", flush=True,
                )
            return df

        except (requests.RequestException, zipfile.BadZipFile) as e:
            wait = 2 ** attempt
            print(f"\n  {year_month} error: {e} — retry in {wait}s...")
            time.sleep(wait)

    print(f"\n  {year_month} FAILED after 5 attempts — skipping")
    with _lock:
        _progress["done"] += 1
        _progress["skipped"] += 1
    return None


def download_parallel(months: list[str], workers: int) -> pd.DataFrame:
    """Download all months in parallel with ThreadPoolExecutor."""
    _progress.update(done=0, total=len(months), bars=0, skipped=0)

    print(f"  Months to fetch: {len(months)}")
    print(f"  Workers: {workers}")
    print()

    results: list[pd.DataFrame] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_month, ym): ym for ym in months}
        for future in as_completed(futures):
            df = future.result()
            if df is not None and not df.empty:
                results.append(df)

    print(f"\n\n  Skipped (404/future): {_progress['skipped']}")

    if not results:
        return pd.DataFrame()
    return pd.concat(results, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(
        description="Download BTC/USDT 1m data from data.binance.vision (parallel)"
    )
    parser.add_argument("--start", default=DEFAULT_START,
                        help="Start month YYYY-MM (default: 2017-08)")
    parser.add_argument("--end", default=None,
                        help="End month YYYY-MM (default: current month)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last saved timestamp")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"Concurrent downloads (default: {DEFAULT_WORKERS})")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    end_ym = args.end or f"{now.year:04d}-{now.month:02d}"
    start_ym = args.start

    # Resume support
    existing_df = None
    if args.resume and OUTPUT_FILE.exists():
        existing_df = pd.read_parquet(OUTPUT_FILE)
        last_ts = existing_df["ts"].max()
        start_ym = f"{last_ts.year:04d}-{last_ts.month:02d}"
        print(f"Resuming from {last_ts} ({len(existing_df):,} existing rows)")
        print(f"  Re-fetching from month {start_ym}\n")

    months = generate_months(start_ym, end_ym)

    print(f"Downloading {SYMBOL} {INTERVAL} klines from data.binance.vision")
    print(f"  Range: {months[0]} → {months[-1]}")
    print(f"  ~{len(months) * 43_800:,} estimated bars")
    print()

    t0 = time.time()
    df = download_parallel(months, args.workers)
    elapsed = time.time() - t0

    if df.empty and existing_df is None:
        print("\nNo data downloaded.")
        sys.exit(1)

    print(f"\nFetch completed in {elapsed:.0f}s")

    # Merge with existing
    if existing_df is not None and not df.empty:
        df = pd.concat([existing_df, df], ignore_index=True)
    elif existing_df is not None:
        df = existing_df

    # Deduplicate & sort
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    # Save
    df.to_parquet(OUTPUT_FILE, index=False)
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"  Rows:  {len(df):,}")
    print(f"  Range: {df['ts'].min()} → {df['ts'].max()}")
    size_mb = OUTPUT_FILE.stat().st_size / 1_048_576
    print(f"  Size:  {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
