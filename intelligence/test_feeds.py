"""
Test script — runs all three intelligence feeds once and prints results.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.hyperliquid_client import HyperliquidClient
from intelligence.liquidation_tracker import run_liquidation_tracker
from intelligence.funding_monitor import run_funding_monitor
from intelligence.oi_tracker import run_oi_tracker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger = logging.getLogger("test_feeds")

    async with HyperliquidClient() as client:
        # 1. Test basic connectivity
        logger.info("=" * 60)
        logger.info("Testing Hyperliquid API connectivity...")
        meta = await client.meta()
        logger.info("✅ Connected — %d assets in universe", len(meta.get("universe", [])))

        mids = await client.all_mids()
        logger.info("✅ Got mid prices for %d assets", len(mids))
        for coin in ["BTC", "ETH", "SOL", "DOGE"]:
            logger.info("   %s: $%s", coin, mids.get(coin, "N/A"))

        # 2. Liquidation Tracker
        logger.info("=" * 60)
        logger.info("Running Liquidation Tracker...")
        liq_results = await run_liquidation_tracker(client)
        for coin, data in liq_results.items():
            alerts = data.get("nearby_alerts", [])
            status = f"⚠️ {len(alerts)} nearby zones" if alerts else "✅ clear"
            logger.info("   %s: mid=$%s OI=%s funding=%s — %s",
                        coin, data["mid_price"], data["open_interest"], data["funding_rate"], status)

        # 3. Funding Monitor
        logger.info("=" * 60)
        logger.info("Running Funding Monitor...")
        funding_results = await run_funding_monitor(client)
        for coin, data in funding_results.items():
            flag = " 🚨" if data["is_extreme"] else ""
            logger.info("   %s: %.4f%% (%s, %s)%s",
                        coin, data["current_rate"] * 100, data["direction"], data["momentum"], flag)

        # 4. OI Tracker
        logger.info("=" * 60)
        logger.info("Running OI Tracker...")
        oi_results = await run_oi_tracker(client)
        for coin, data in oi_results.items():
            logger.info("   %s: OI=%.2f coins ($%s) — %s",
                        coin, data["open_interest_coins"],
                        f"{data['open_interest_usd']:,.0f}",
                        data.get("interpretation", "first_run"))

        logger.info("=" * 60)
        logger.info("✅ All feeds complete!")


if __name__ == "__main__":
    asyncio.run(main())
