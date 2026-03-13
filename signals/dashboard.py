"""
Signal Dashboard

Runs all signals against current live data and prints a clean summary.
"What should I be paying attention to right now?"
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from intelligence.liquidation_tracker import run_liquidation_tracker
from intelligence.funding_monitor import run_funding_monitor
from intelligence.oi_tracker import run_oi_tracker
from utils.hyperliquid_client import HyperliquidClient
from signals.signal_definitions import (
    LiquidationCascadeSignal, FundingRateExtremeSignal, OIDivergenceSignal,
    ConfluenceSignal, Direction, SignalResult, evaluate_all,
)

logger = logging.getLogger(__name__)

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "DOGE"]

# Color codes for terminal output
class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _dir_color(d: Direction) -> str:
    if d == Direction.LONG:
        return C.GREEN
    elif d == Direction.SHORT:
        return C.RED
    return C.DIM


def _conf_bar(conf: float, width: int = 20) -> str:
    filled = int(conf * width)
    return "█" * filled + "░" * (width - filled)


def print_signal(result: SignalResult) -> None:
    """Print a single signal result."""
    color = _dir_color(result.direction)
    active = "●" if result.is_active else "○"
    print(f"  {active} {C.BOLD}{result.name:<25}{C.RESET} "
          f"{color}{result.direction.value:>7}{C.RESET}  "
          f"[{_conf_bar(result.confidence)}] {result.confidence:.0%}")
    print(f"    {C.DIM}{result.reasoning}{C.RESET}")


def print_confluence(result: SignalResult) -> None:
    """Print confluence score prominently."""
    score = result.metadata.get("score", 0)
    color = _dir_color(result.direction)

    if score >= 60:
        urgency = f"{C.RED}{C.BOLD}🚨 HIGH"
    elif score >= 40:
        urgency = f"{C.YELLOW}⚠️  MODERATE"
    else:
        urgency = f"{C.DIM}○  LOW"

    print(f"\n  {C.BOLD}CONFLUENCE{C.RESET}  {urgency}{C.RESET}  "
          f"Score: {color}{C.BOLD}{score:.0f}/100{C.RESET} → "
          f"{color}{result.direction.value}{C.RESET}")
    if result.metadata.get("active"):
        print(f"    Signals: {', '.join(result.metadata['active'])}")
    print(f"    {C.DIM}{result.reasoning}{C.RESET}")


async def run_dashboard() -> None:
    """Fetch live data and run all signals."""
    print(f"\n{C.BOLD}{'═'*70}")
    print(f"  SIGNAL DASHBOARD  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'═'*70}{C.RESET}")

    async with HyperliquidClient() as client:
        # Fetch all intelligence feeds in parallel
        liq_results, funding_results, oi_results = await asyncio.gather(
            run_liquidation_tracker(client),
            run_funding_monitor(client),
            run_oi_tracker(client),
        )

    for asset in TRACKED_ASSETS:
        liq_data = liq_results.get(asset)
        funding_data = funding_results.get(asset)
        oi_data = oi_results.get(asset)

        if not liq_data:
            continue

        price = liq_data.get("mid_price", 0)
        print(f"\n{C.CYAN}{C.BOLD}  ── {asset}  ${price:,.2f} ──{C.RESET}")

        signals = evaluate_all(
            liq_data=liq_data,
            funding_data=funding_data,
            oi_data=oi_data,
        )

        for name, result in signals.items():
            if name == "confluence":
                continue
            print_signal(result)

        if "confluence" in signals:
            print_confluence(signals["confluence"])

    print(f"\n{C.BOLD}{'═'*70}{C.RESET}")
    print(f"  {C.DIM}Mode: PAPER  |  Exchange: Hyperliquid  |  Refresh: run again{C.RESET}\n")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_dashboard())


if __name__ == "__main__":
    main()
