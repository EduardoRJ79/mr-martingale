"""
Open Interest Tracker

Tracks OI changes per asset, calculates deltas, and correlates with price direction.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.hyperliquid_client import HyperliquidClient

logger = logging.getLogger(__name__)

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "DOGE"]
DATA_DIR = Path(__file__).parent / "data" / "live"
HISTORICAL_DIR = Path(__file__).parent / "data" / "historical"


def _load_previous_snapshot() -> dict[str, Any] | None:
    """Load most recent OI snapshot for delta calculation."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(DATA_DIR.glob("oi_*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text())
    except Exception:
        return None


def _interpret_oi_price(oi_delta: float, price_delta: float) -> str:
    """Interpret OI + price relationship."""
    if oi_delta > 0 and price_delta > 0:
        return "new_longs_entering"  # Bullish until overleveraged
    elif oi_delta > 0 and price_delta < 0:
        return "new_shorts_entering"  # Bearish until overleveraged
    elif oi_delta < 0 and price_delta > 0:
        return "shorts_closing"  # Less sustainable rally
    elif oi_delta < 0 and price_delta < 0:
        return "longs_closing"  # Less sustainable drop
    return "neutral"


async def run_oi_tracker(client: HyperliquidClient) -> dict[str, Any]:
    """Track open interest changes."""
    data = await client.meta_and_asset_ctxs()
    meta_info, asset_ctxs = data[0], data[1]
    universe = meta_info.get("universe", [])
    asset_map = {u["name"]: i for i, u in enumerate(universe)}

    mids = await client.all_mids()
    previous = _load_previous_snapshot()

    results: dict[str, Any] = {}

    for coin in TRACKED_ASSETS:
        idx = asset_map.get(coin)
        if idx is None or idx >= len(asset_ctxs):
            continue

        ctx = asset_ctxs[idx]
        oi = float(ctx.get("openInterest", "0"))
        mid_price = float(mids.get(coin, "0"))
        mark_price = float(ctx.get("markPx", "0"))
        oi_usd = oi * mid_price  # OI in coin units → USD

        result: dict[str, Any] = {
            "open_interest_coins": oi,
            "open_interest_usd": round(oi_usd, 2),
            "mid_price": mid_price,
            "mark_price": mark_price,
        }

        # Calculate delta if we have previous data
        if previous and coin in previous:
            prev = previous[coin]
            prev_oi = prev.get("open_interest_coins", 0)
            prev_price = prev.get("mid_price", 0)

            if prev_oi > 0 and prev_price > 0:
                oi_delta = oi - prev_oi
                oi_delta_pct = oi_delta / prev_oi
                price_delta = mid_price - prev_price
                price_delta_pct = price_delta / prev_price

                result["oi_delta_coins"] = round(oi_delta, 4)
                result["oi_delta_pct"] = round(oi_delta_pct, 6)
                result["price_delta_pct"] = round(price_delta_pct, 6)
                result["interpretation"] = _interpret_oi_price(oi_delta, price_delta)

                logger.info(
                    "%s OI: %.0f (Δ%.2f%%) Price: $%.2f (Δ%.2f%%) → %s",
                    coin, oi, oi_delta_pct * 100, mid_price, price_delta_pct * 100,
                    result.get("interpretation", "?"),
                )
        else:
            result["oi_delta_coins"] = None
            result["interpretation"] = "no_previous_data"

        results[coin] = result

    # Save snapshot
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = DATA_DIR / f"oi_{ts}.json"
    snapshot_path.write_text(json.dumps(results, indent=2))

    # Append to time series
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = HISTORICAL_DIR / "oi_timeseries.jsonl"
    with open(hist_path, "a") as f:
        f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "data": results}) + "\n")

    logger.info("Saved OI snapshot to %s", snapshot_path)
    return results


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    async with HyperliquidClient() as client:
        results = await run_oi_tracker(client)
        for coin, data in results.items():
            print(f"\n{coin}:")
            print(f"  OI: {data['open_interest_coins']:.2f} coins (${data['open_interest_usd']:,.0f})")
            print(f"  Price: ${data['mid_price']}")
            if data.get("oi_delta_coins") is not None:
                print(f"  OI Δ: {data['oi_delta_pct']*100:.2f}%  Price Δ: {data['price_delta_pct']*100:.2f}%")
                print(f"  Interpretation: {data['interpretation']}")
            else:
                print(f"  (First run — no delta available)")


if __name__ == "__main__":
    asyncio.run(main())
