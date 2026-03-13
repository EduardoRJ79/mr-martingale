"""
Funding Rate Monitor

Tracks funding rates on Hyperliquid, flags extremes, and calculates momentum.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml

from utils.hyperliquid_client import HyperliquidClient

logger = logging.getLogger(__name__)

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "DOGE"]
LIVE_DIR = Path(__file__).parent / "data" / "live"
HISTORICAL_DIR = Path(__file__).parent / "data" / "historical"


def _load_config() -> dict[str, Any]:
    cfg_path = Path(__file__).parent.parent / "execution" / "config.yaml"
    if cfg_path.exists():
        return yaml.safe_load(cfg_path.read_text()) or {}
    return {}


def _calc_momentum(rates: list[float]) -> str:
    """Simple momentum: compare recent avg to older avg."""
    if len(rates) < 4:
        return "insufficient_data"
    recent = sum(rates[-2:]) / 2
    older = sum(rates[:2]) / 2
    if abs(recent) > abs(older) * 1.2:
        return "accelerating"
    elif abs(recent) < abs(older) * 0.8:
        return "decelerating"
    return "stable"


async def run_funding_monitor(client: HyperliquidClient) -> dict[str, Any]:
    """Check current funding rates and recent history."""
    config = _load_config()
    threshold = config.get("signals", {}).get("funding_rate_threshold", 0.001)

    data = await client.meta_and_asset_ctxs()
    meta_info, asset_ctxs = data[0], data[1]
    universe = meta_info.get("universe", [])
    asset_map = {u["name"]: i for i, u in enumerate(universe)}

    # Get historical funding (last 24h)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - 24 * 3600 * 1000

    results: dict[str, Any] = {}

    for coin in TRACKED_ASSETS:
        idx = asset_map.get(coin)
        if idx is None or idx >= len(asset_ctxs):
            continue

        ctx = asset_ctxs[idx]
        current_rate = float(ctx.get("funding", "0"))

        # Fetch 24h history
        try:
            history = await client.funding_history(coin, start_ms)
        except Exception as e:
            logger.warning("Failed to get funding history for %s: %s", coin, e)
            history = []

        historical_rates = [float(h.get("fundingRate", "0")) for h in history]
        momentum = _calc_momentum(historical_rates)

        is_extreme = abs(current_rate) >= threshold
        direction = "longs_paying" if current_rate > 0 else "shorts_paying" if current_rate < 0 else "neutral"

        result = {
            "current_rate": current_rate,
            "current_rate_annualized": round(current_rate * 3 * 365, 4),  # 8h funding periods
            "is_extreme": is_extreme,
            "direction": direction,
            "momentum": momentum,
            "history_24h_count": len(historical_rates),
            "history_24h_avg": round(sum(historical_rates) / len(historical_rates), 8) if historical_rates else None,
            "history_24h_max": max(historical_rates) if historical_rates else None,
            "history_24h_min": min(historical_rates) if historical_rates else None,
        }
        results[coin] = result

        if is_extreme:
            logger.info(
                "🚨 %s funding EXTREME: %.4f%% (%s, %s)",
                coin, current_rate * 100, direction, momentum,
            )

    # Save live snapshot (matches oi_tracker / liquidation_tracker pattern)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = LIVE_DIR / f"funding_{ts_tag}.json"
    snapshot_path.write_text(json.dumps(results, indent=2))
    logger.info("Saved funding snapshot to %s", snapshot_path)

    # Append to historical file
    HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    hist_path = HISTORICAL_DIR / "funding_rates.jsonl"
    with open(hist_path, "a") as f:
        f.write(json.dumps({"timestamp": ts, "data": results}) + "\n")
    logger.info("Appended funding data to %s", hist_path)

    return results


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    async with HyperliquidClient() as client:
        results = await run_funding_monitor(client)
        for coin, data in results.items():
            extreme_flag = " 🚨 EXTREME" if data["is_extreme"] else ""
            print(f"\n{coin}: {data['current_rate']*100:.4f}%{extreme_flag}")
            print(f"  Direction: {data['direction']}  Momentum: {data['momentum']}")
            print(f"  Annualized: {data['current_rate_annualized']*100:.1f}%")
            if data["history_24h_avg"] is not None:
                print(f"  24h avg: {data['history_24h_avg']*100:.4f}%  ({data['history_24h_count']} samples)")


if __name__ == "__main__":
    asyncio.run(main())
