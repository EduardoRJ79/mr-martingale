"""
Meta Runner — Phase 4

The 'smart' entry point that wraps the basic runner with meta-layer intelligence:
1. Detect regime
2. Run bot behavior models
3. Get adapted signal weights
4. Pass into the existing signal/execution pipeline

Usage:
  PYTHONPATH=. .venv/bin/python -m meta.meta_runner [--simulate] [--compare]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from meta.bot_behavior_model import BotBehaviorModel
from meta.regime_detector import RegimeDetector, _load_jsonl
from meta.adaptation_engine import AdaptationEngine, DEFAULT_WEIGHTS
from signals.signal_definitions import (
    evaluate_all, ConfluenceSignal, Direction, SignalResult,
)

logger = logging.getLogger(__name__)

HIST_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"
TRACKED_ASSETS = ["BTC", "ETH", "SOL", "DOGE"]


def _synthetic_scenario():
    """Same synthetic scenario as basic runner for comparison."""
    return {
        "BTC": {
            "liq": {
                "mid_price": 97500, "open_interest": 25000, "funding_rate": -0.002,
                "nearby_alerts": [
                    {"side": "short", "price": 99450, "leverage": 25},
                    {"side": "short", "price": 99900, "leverage": 50},
                    {"side": "short", "price": 98500, "leverage": 10},
                    {"side": "long", "price": 95550, "leverage": 50},
                ],
            },
            "funding": {"current_rate": -0.0018, "momentum": "accelerating", "current_rate_annualized": -0.197},
            "oi": {"oi_delta_pct": 0.05, "price_delta_pct": 0.02, "interpretation": "new_longs_entering"},
        },
    }


def run_meta_cycle(
    liq_results: dict, funding_results: dict, oi_results: dict,
    adapted_weights: dict[str, float],
) -> dict[str, dict[str, SignalResult]]:
    """Run signals with adapted weights for all assets."""
    confluence = ConfluenceSignal(weights=adapted_weights)
    all_results = {}

    for asset in TRACKED_ASSETS:
        liq = liq_results.get(asset)
        fund = funding_results.get(asset)
        oi = oi_results.get(asset)
        if not any([liq, fund, oi]):
            continue

        results = evaluate_all(
            liq_data=liq, funding_data=fund, oi_data=oi,
            confluence_signal=confluence,
        )
        all_results[asset] = results

    return all_results


def run_basic_cycle(
    liq_results: dict, funding_results: dict, oi_results: dict,
) -> dict[str, dict[str, SignalResult]]:
    """Run signals with DEFAULT weights (basic runner behavior)."""
    confluence = ConfluenceSignal(weights=DEFAULT_WEIGHTS)
    all_results = {}

    for asset in TRACKED_ASSETS:
        liq = liq_results.get(asset)
        fund = funding_results.get(asset)
        oi = oi_results.get(asset)
        if not any([liq, fund, oi]):
            continue

        results = evaluate_all(
            liq_data=liq, funding_data=fund, oi_data=oi,
            confluence_signal=confluence,
        )
        all_results[asset] = results

    return all_results


async def main():
    parser = argparse.ArgumentParser(description="Meta Runner — smart entry point")
    parser.add_argument("--simulate", action="store_true", help="Use synthetic scenario")
    parser.add_argument("--compare", action="store_true", help="Show basic vs meta comparison")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    # ── Step 1: Load historical data for meta models ─────────────────────
    oi_history = _load_jsonl(HIST_DIR / "oi_timeseries.jsonl")
    funding_history = _load_jsonl(HIST_DIR / "funding_rates.jsonl")
    logger.info("Loaded %d OI snapshots, %d funding snapshots", len(oi_history), len(funding_history))

    # ── Step 2: Regime Detection ─────────────────────────────────────────
    detector = RegimeDetector(window=15)
    regime = detector.classify(oi_history, funding_history)

    # ── Step 3: Bot Behavior Models ──────────────────────────────────────
    behavior_model = BotBehaviorModel()
    behavior_model.fit()
    predictions = behavior_model.predict()

    # ── Step 4: Adaptation Engine ────────────────────────────────────────
    engine = AdaptationEngine()
    adapted = engine.adapt(regime, predictions)

    # ── Step 5: Get market data (live or synthetic) ──────────────────────
    if args.simulate:
        scenario = _synthetic_scenario()
        liq_results = {a: d["liq"] for a, d in scenario.items()}
        funding_results = {a: d["funding"] for a, d in scenario.items()}
        oi_results = {a: d["oi"] for a, d in scenario.items()}
    else:
        from utils.hyperliquid_client import HyperliquidClient
        from intelligence.liquidation_tracker import run_liquidation_tracker
        from intelligence.funding_monitor import run_funding_monitor
        from intelligence.oi_tracker import run_oi_tracker

        async with HyperliquidClient() as client:
            liq_results, funding_results, oi_results = await asyncio.gather(
                run_liquidation_tracker(client),
                run_funding_monitor(client),
                run_oi_tracker(client),
            )

    # ── Step 6: Run signals with adapted weights ─────────────────────────
    meta_results = run_meta_cycle(liq_results, funding_results, oi_results, adapted.signal_weights)

    # ── Output ───────────────────────────────────────────────────────────
    print("\n" + "═" * 70)
    print("  META RUNNER — INTELLIGENT SIGNAL ANALYSIS")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("═" * 70)

    print(f"\n  📊 REGIME: {regime.regime.value} (confidence: {regime.confidence:.0%})")
    print(f"     {regime.description}")
    print(f"     Risk scale: {adapted.risk_scale}x | Position mult: {adapted.position_size_multiplier}x")

    print(f"\n  🤖 BOT BEHAVIOR:")
    for p in predictions:
        print(f"     [{p.model_name}] {p.interpretation}")

    print(f"\n  ⚙️  ADAPTED WEIGHTS:")
    for k, v in adapted.signal_weights.items():
        base = DEFAULT_WEIGHTS.get(k, 0)
        diff = v - base
        arrow = "↑" if diff > 0.01 else "↓" if diff < -0.01 else "="
        print(f"     {k}: {v:.1%} (base {base:.0%}) {arrow}")

    if adapted.edge_decay_alert:
        print(f"\n  ⚠️  EDGE DECAY: {adapted.edge_decay_detail}")

    print(f"\n  📈 SIGNAL RESULTS (META):")
    for asset, signals in meta_results.items():
        conf = signals.get("confluence")
        if conf:
            icon = "🟢" if conf.is_active else "⚪"
            print(f"     {icon} {asset}: {conf.direction.value} score={conf.metadata.get('score', 0):.1f}/100")
            print(f"        {conf.reasoning}")

    # ── Comparison mode ──────────────────────────────────────────────────
    if args.compare:
        basic_results = run_basic_cycle(liq_results, funding_results, oi_results)

        print(f"\n  📉 COMPARISON: BASIC vs META")
        print(f"  {'─' * 60}")
        print(f"  {'Asset':<8} {'Basic Score':>12} {'Meta Score':>12} {'Basic Dir':>10} {'Meta Dir':>10}")
        for asset in TRACKED_ASSETS:
            b = basic_results.get(asset, {}).get("confluence")
            m = meta_results.get(asset, {}).get("confluence")
            if b and m:
                b_score = b.metadata.get("score", 0)
                m_score = m.metadata.get("score", 0)
                print(f"  {asset:<8} {b_score:>12.1f} {m_score:>12.1f} {b.direction.value:>10} {m.direction.value:>10}")

    print(f"\n  📝 ADJUSTMENT LOG:")
    for line in adapted.adjustments_log:
        print(f"     • {line}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
