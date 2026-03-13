"""
Runner — Main Orchestrator

Collects data → runs signals → checks confluence → risk check → execute.
Single entry point: python -m execution.runner

Modes: --paper (default), --live (future, blocked)
       --simulate: inject a synthetic high-confluence scenario for testing
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.hyperliquid_client import HyperliquidClient
from intelligence.liquidation_tracker import run_liquidation_tracker
from intelligence.funding_monitor import run_funding_monitor
from intelligence.oi_tracker import run_oi_tracker
from signals.signal_definitions import (
    evaluate_all, Direction, SignalResult, ConfluenceSignal,
)
from execution.portfolio import Portfolio
from execution.risk_manager import RiskManager
from execution.executor import Executor

logger = logging.getLogger(__name__)

TRACKED_ASSETS = ["BTC", "ETH", "SOL", "DOGE"]


def _synthetic_scenario() -> dict[str, dict]:
    """
    Generate a synthetic high-confluence scenario for testing the full pipeline.
    Simulates: BTC with extreme funding + nearby liquidation clusters + bullish OI.
    """
    return {
        "BTC": {
            "liq": {
                "mid_price": 97500,
                "open_interest": 25000,
                "funding_rate": -0.002,
                "nearby_alerts": [
                    {"side": "short", "price": 99450, "leverage": 25},
                    {"side": "short", "price": 99900, "leverage": 50},
                    {"side": "short", "price": 98500, "leverage": 10},
                    {"side": "long", "price": 95550, "leverage": 50},
                ],
            },
            "funding": {
                "current_rate": -0.0018,
                "momentum": "accelerating",
                "current_rate_annualized": -0.197,
            },
            "oi": {
                "oi_delta_pct": 0.05,
                "price_delta_pct": 0.02,
                "interpretation": "new_longs_entering",
            },
        },
    }


async def collect_live_data(client: HyperliquidClient) -> tuple[dict, dict, dict, dict[str, float]]:
    """Fetch all intelligence feeds + current prices."""
    liq_results, funding_results, oi_results = await asyncio.gather(
        run_liquidation_tracker(client),
        run_funding_monitor(client),
        run_oi_tracker(client),
    )

    # Get current prices
    mids = await client.all_mids()
    prices = {}
    for asset in TRACKED_ASSETS:
        if asset in mids:
            prices[asset] = float(mids[asset])

    return liq_results, funding_results, oi_results, prices


def run_cycle(
    liq_results: dict,
    funding_results: dict,
    oi_results: dict,
    prices: dict[str, float],
    executor: Executor,
    portfolio: Portfolio,
) -> dict:
    """Run one full cycle: signals → risk → execute. Returns summary."""
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": executor.mode,
        "assets_scanned": 0,
        "signals_fired": 0,
        "trades_opened": 0,
        "trades_closed": 0,
        "decisions": [],
    }

    # First, check stops/targets on existing positions
    closed = executor.check_and_close(prices)
    summary["trades_closed"] = len(closed)

    # Then scan for new signals
    for asset in TRACKED_ASSETS:
        price = prices.get(asset)
        if not price:
            continue
        summary["assets_scanned"] += 1

        liq_data = liq_results.get(asset)
        funding_data = funding_results.get(asset)
        oi_data = oi_results.get(asset)

        signals = evaluate_all(
            liq_data=liq_data,
            funding_data=funding_data,
            oi_data=oi_data,
        )

        confluence = signals.get("confluence")
        if not confluence:
            continue

        decision = {
            "asset": asset,
            "price": price,
            "confluence_score": confluence.metadata.get("score", 0),
            "direction": confluence.direction.value,
            "active_signals": confluence.metadata.get("active", []),
            "action": "NO_TRADE",
        }

        if confluence.is_active:
            summary["signals_fired"] += 1
            result = executor.execute_signal(asset, price, confluence)
            if result:
                summary["trades_opened"] += 1
                decision["action"] = "OPENED"
            else:
                decision["action"] = "REJECTED"
        else:
            decision["action"] = "BELOW_THRESHOLD"

        summary["decisions"].append(decision)

    return summary


def print_summary(summary: dict, portfolio: Portfolio, prices: dict[str, float]) -> None:
    """Print a clean run summary."""
    print(f"\n{'═'*60}")
    print(f"  RUNNER CYCLE — {summary['timestamp'][:19]} UTC")
    print(f"  Mode: {summary['mode'].upper()}")
    print(f"{'═'*60}")
    print(f"  Assets scanned:    {summary['assets_scanned']}")
    print(f"  Signals fired:     {summary['signals_fired']}")
    print(f"  Trades opened:     {summary['trades_opened']}")
    print(f"  Trades closed:     {summary['trades_closed']}")
    print()

    for d in summary["decisions"]:
        icon = {"OPENED": "🟢", "REJECTED": "🔴", "BELOW_THRESHOLD": "⚪", "NO_TRADE": "⚪"}.get(d["action"], "?")
        print(f"  {icon} {d['asset']:<6} ${d['price']:>10,.2f}  "
              f"score={d['confluence_score']:>5.1f}  "
              f"{d['direction']:>7}  → {d['action']}")
        if d.get("active_signals"):
            print(f"           Signals: {', '.join(d['active_signals'])}")

    print()
    print(portfolio.summary(prices))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Quant Runner — one execution cycle")
    parser.add_argument("--paper", action="store_true", default=True, help="Paper trading mode (default)")
    parser.add_argument("--live", action="store_true", help="Live mode (NOT IMPLEMENTED)")
    parser.add_argument("--simulate", action="store_true", help="Inject synthetic high-confluence scenario")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital for fresh portfolio")
    args = parser.parse_args()

    if args.live:
        print("🚫 Live trading is NOT IMPLEMENTED. Use --paper.")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    portfolio = Portfolio(initial_capital=args.capital)
    risk_mgr = RiskManager()
    executor = Executor(portfolio, risk_mgr, mode="paper")

    if args.simulate:
        print("\n⚡ SIMULATION MODE — injecting synthetic high-confluence scenario\n")
        scenario = _synthetic_scenario()
        liq_results = {a: d["liq"] for a, d in scenario.items()}
        funding_results = {a: d["funding"] for a, d in scenario.items()}
        oi_results = {a: d["oi"] for a, d in scenario.items()}
        prices = {a: d["liq"]["mid_price"] for a, d in scenario.items()}
    else:
        print("\n📡 Fetching live data from Hyperliquid...\n")
        async with HyperliquidClient() as client:
            liq_results, funding_results, oi_results, prices = await collect_live_data(client)

    summary = run_cycle(liq_results, funding_results, oi_results, prices, executor, portfolio)
    print_summary(summary, portfolio, prices)


if __name__ == "__main__":
    asyncio.run(main())
