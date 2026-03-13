"""
Liquidation Tracker

Estimates liquidation price levels from Hyperliquid's visible data.

NOTE: Hyperliquid doesn't expose all users' positions publicly. What we CAN get:
- Per-asset open interest and funding rates from metaAndAssetCtxs
- Order book depth from l2Book
- We estimate liquidation zones by analyzing OI concentration and leverage patterns.

For actual per-user liquidation prices, we'd need to track known whale addresses.
This module focuses on what's publicly derivable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from utils.hyperliquid_client import HyperliquidClient

logger = logging.getLogger(__name__)

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "DOGE"]
DATA_DIR = Path(__file__).parent / "data" / "live"


def _load_config() -> dict[str, Any]:
    cfg_path = Path(__file__).parent.parent / "execution" / "config.yaml"
    if cfg_path.exists():
        return yaml.safe_load(cfg_path.read_text()) or {}
    return {}


def _estimate_liquidation_zones(
    mid_price: float,
    open_interest: float,
    funding_rate: float,
    cluster_pct: float,
) -> dict[str, Any]:
    """
    Estimate where liquidation clusters likely sit based on:
    - Current price and OI magnitude
    - Funding rate sign (positive = longs dominant = more long liquidations below)
    - Standard leverage tiers (2x, 3x, 5x, 10x, 20x, 50x)

    Returns estimated liquidation density zones.
    """
    leverage_tiers = [2, 3, 5, 10, 20, 50]
    zones: list[dict[str, Any]] = []

    for lev in leverage_tiers:
        # Longs get liquidated below current price at ~1/lev distance
        long_liq = mid_price * (1 - 1 / lev)
        # Shorts get liquidated above current price
        short_liq = mid_price * (1 + 1 / lev)

        # Estimate relative weight — higher leverage = less OI but more violent
        # Funding rate tells us skew: positive funding → more longs
        if funding_rate > 0:
            long_weight = 0.6
            short_weight = 0.4
        elif funding_rate < 0:
            long_weight = 0.4
            short_weight = 0.6
        else:
            long_weight = 0.5
            short_weight = 0.5

        # Higher leverage tiers have less OI but are more fragile
        tier_weight = 1.0 / lev

        zones.append({
            "leverage": lev,
            "long_liq_price": round(long_liq, 2),
            "short_liq_price": round(short_liq, 2),
            "long_density_weight": round(long_weight * tier_weight, 4),
            "short_density_weight": round(short_weight * tier_weight, 4),
            "long_distance_pct": round(1 / lev, 4),
            "short_distance_pct": round(1 / lev, 4),
        })

    # Flag zones within cluster_pct of current price
    nearby: list[dict[str, Any]] = []
    for z in zones:
        if z["long_distance_pct"] <= cluster_pct:
            nearby.append({"side": "long", "price": z["long_liq_price"], "leverage": z["leverage"]})
        if z["short_distance_pct"] <= cluster_pct:
            nearby.append({"side": "short", "price": z["short_liq_price"], "leverage": z["leverage"]})

    return {
        "mid_price": mid_price,
        "open_interest": open_interest,
        "funding_rate": funding_rate,
        "zones": zones,
        "nearby_alerts": nearby,
    }


async def run_liquidation_tracker(client: HyperliquidClient) -> dict[str, Any]:
    """Run liquidation analysis for tracked assets."""
    config = _load_config()
    cluster_pct = config.get("signals", {}).get("liquidation_cluster_pct", 0.02)

    data = await client.meta_and_asset_ctxs()
    meta_info, asset_ctxs = data[0], data[1]
    universe = meta_info.get("universe", [])

    # Build name→index map
    asset_map = {u["name"]: i for i, u in enumerate(universe)}

    mids = await client.all_mids()
    results: dict[str, Any] = {}

    for coin in TRACKED_ASSETS:
        idx = asset_map.get(coin)
        if idx is None or idx >= len(asset_ctxs):
            logger.warning("Asset %s not found in universe", coin)
            continue

        ctx = asset_ctxs[idx]
        mid_str = mids.get(coin)
        if not mid_str:
            continue

        mid_price = float(mid_str)
        oi = float(ctx.get("openInterest", "0"))
        funding = float(ctx.get("funding", "0"))

        analysis = _estimate_liquidation_zones(mid_price, oi, funding, cluster_pct)
        results[coin] = analysis

        if analysis["nearby_alerts"]:
            logger.info(
                "⚠️  %s has %d liquidation zones within %.1f%% of price",
                coin, len(analysis["nearby_alerts"]), cluster_pct * 100,
            )

    # Save snapshot
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = DATA_DIR / f"liquidation_{ts}.json"
    snapshot_path.write_text(json.dumps(results, indent=2))
    logger.info("Saved liquidation snapshot to %s", snapshot_path)

    return results


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    async with HyperliquidClient() as client:
        results = await run_liquidation_tracker(client)
        for coin, data in results.items():
            print(f"\n{'='*60}")
            print(f"  {coin}  mid=${data['mid_price']}  OI={data['open_interest']}  funding={data['funding_rate']}")
            if data["nearby_alerts"]:
                print(f"  ⚠️  NEARBY LIQUIDATION ZONES:")
                for a in data["nearby_alerts"]:
                    print(f"    {a['side']} {a['leverage']}x @ ${a['price']}")
            else:
                print(f"  ✅ No liquidation clusters within alert range")


if __name__ == "__main__":
    asyncio.run(main())
