"""
Walk-Forward Backtester

Tests signal predictions against actual price movements over configurable horizons.
Supports both historical data and synthetic data generation for validation.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from signals.signal_definitions import (
    LiquidationCascadeSignal, FundingRateExtremeSignal, OIDivergenceSignal,
    ConfluenceSignal, Direction, SignalResult, evaluate_all,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
HISTORICAL_DIR = PROJECT_ROOT / "intelligence" / "data" / "historical"
RESULTS_DIR = Path(__file__).parent / "results"

HORIZONS_MINUTES = [5, 15, 60, 240]  # 5m, 15m, 1h, 4h


@dataclass
class Prediction:
    timestamp: str
    asset: str
    signal_name: str
    direction: Direction
    confidence: float
    reasoning: str


@dataclass
class BacktestResult:
    signal_name: str
    horizon_min: int
    total_predictions: int
    hit_rate: float
    avg_return_pct: float
    sharpe: float
    returns: list[float] = field(default_factory=list)


# ── Synthetic Data Generator ─────────────────────────────────────────────────

def generate_synthetic_data(
    n_steps: int = 500,
    initial_price: float = 69000.0,
    volatility: float = 0.002,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    Generate synthetic time-series data with realistic-ish market microstructure.
    Each step represents ~5 minutes.
    """
    rng = random.Random(seed)
    np_rng = np.random.RandomState(seed)

    data = []
    price = initial_price
    oi = 20000.0
    funding = 0.00001
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for i in range(n_steps):
        # Price: geometric brownian motion with occasional jumps
        drift = 0.0
        shock = 0 
        if rng.random() < 0.03:  # 3% chance of a "cascade" event
            shock = rng.choice([-1, 1]) * rng.uniform(0.005, 0.02)
        ret = np_rng.normal(drift, volatility) + shock
        price *= (1 + ret)

        # OI: correlated with price action but with its own noise
        oi_change = np_rng.normal(0, 0.005) + ret * rng.uniform(-0.5, 1.5)
        oi *= (1 + oi_change)

        # Funding: mean-reverts, but trends with sentiment and occasionally spikes
        funding += np_rng.normal(0, 0.0002) + (ret * 0.003)
        funding *= 0.98  # gentler mean reversion — real funding can stay extreme for hours
        # Occasional funding spikes (market dislocation)
        if rng.random() < 0.02:
            funding += rng.choice([-1, 1]) * rng.uniform(0.0005, 0.002)

        # Build snapshot matching our intelligence data format
        funding_extreme = abs(funding) > 0.001

        # Estimate liquidation zones (simplified)
        nearby_alerts = []
        for lev in [20, 50]:
            long_liq = price * (1 - 1/lev)
            short_liq = price * (1 + 1/lev)
            if (price - long_liq) / price <= 0.02:
                nearby_alerts.append({"side": "long", "price": round(long_liq, 2), "leverage": lev})
            if (short_liq - price) / price <= 0.02:
                nearby_alerts.append({"side": "short", "price": round(short_liq, 2), "leverage": lev})

        step = {
            "timestamp": t.isoformat(),
            "price": round(price, 2),
            "price_return": round(ret, 6),
            "liq_data": {
                "mid_price": round(price, 2),
                "open_interest": round(oi, 2),
                "funding_rate": round(funding, 8),
                "nearby_alerts": nearby_alerts,
            },
            "funding_data": {
                "current_rate": round(funding, 8),
                "momentum": "accelerating" if abs(funding) > 0.0005 else "stable",
                "current_rate_annualized": round(funding * 3 * 365, 4),
                "is_extreme": funding_extreme,
            },
            "oi_data": {
                "oi_delta_pct": round(oi_change, 6),
                "price_delta_pct": round(ret, 6),
                "interpretation": (
                    "new_longs_entering" if oi_change > 0 and ret > 0 else
                    "new_shorts_entering" if oi_change > 0 and ret < 0 else
                    "shorts_closing" if oi_change < 0 and ret > 0 else
                    "longs_closing"
                ),
            },
        }
        data.append(step)
        t += timedelta(minutes=5)

    return data


# ── Backtesting Engine ───────────────────────────────────────────────────────

def run_backtest(
    data: list[dict[str, Any]],
    horizons: list[int] | None = None,
    signal_params: dict[str, Any] | None = None,
) -> list[BacktestResult]:
    """
    Walk-forward backtest: at each step, generate signals and compare
    prediction to actual price change over each horizon.
    """
    horizons = horizons or HORIZONS_MINUTES
    params = signal_params or {}

    liq_sig = LiquidationCascadeSignal(cluster_pct=params.get("liquidation_cluster_pct", 0.02))
    fund_sig = FundingRateExtremeSignal(threshold=params.get("funding_rate_threshold", 0.001))
    oi_sig = OIDivergenceSignal()
    conf_sig = ConfluenceSignal(min_score=params.get("min_confluence", 40.0))

    # steps_per_horizon: 5min per step
    steps_per = {h: h // 5 for h in horizons}

    # Collect returns per signal per horizon
    # signal_name -> horizon -> list of returns (positive if prediction correct)
    returns: dict[str, dict[int, list[float]]] = {}

    for i, step in enumerate(data):
        signals = evaluate_all(
            liq_data=step.get("liq_data"),
            funding_data=step.get("funding_data"),
            oi_data=step.get("oi_data"),
            liq_signal=liq_sig,
            funding_signal=fund_sig,
            oi_signal=oi_sig,
            confluence_signal=conf_sig,
        )

        for sig_name, sig_result in signals.items():
            if not sig_result.is_active:
                continue

            if sig_name not in returns:
                returns[sig_name] = {h: [] for h in horizons}

            for h in horizons:
                future_idx = i + steps_per[h]
                if future_idx >= len(data):
                    continue

                future_price = data[future_idx]["price"]
                current_price = step["price"]
                price_return = (future_price - current_price) / current_price

                # Signed return: positive if prediction correct
                if sig_result.direction == Direction.LONG:
                    signed_return = price_return * sig_result.confidence
                elif sig_result.direction == Direction.SHORT:
                    signed_return = -price_return * sig_result.confidence
                else:
                    continue

                returns[sig_name][h].append(signed_return)

    # Compile results
    results: list[BacktestResult] = []
    for sig_name, horizon_returns in returns.items():
        for h, rets in horizon_returns.items():
            if not rets:
                results.append(BacktestResult(sig_name, h, 0, 0.0, 0.0, 0.0))
                continue

            hits = sum(1 for r in rets if r > 0)
            avg_ret = np.mean(rets) * 100
            std_ret = np.std(rets) if len(rets) > 1 else 1.0
            sharpe = (np.mean(rets) / std_ret * math.sqrt(252 * 24 * 12)) if std_ret > 0 else 0.0

            results.append(BacktestResult(
                sig_name, h, len(rets),
                round(hits / len(rets), 4),
                round(float(avg_ret), 4),
                round(float(sharpe), 2),
                rets,
            ))

    return results


def print_results(results: list[BacktestResult]) -> None:
    """Pretty-print backtest results."""
    print(f"\n{'='*80}")
    print(f"  {'Signal':<25} {'Horizon':<10} {'Trades':<8} {'Hit Rate':<10} {'Avg Ret%':<10} {'Sharpe':<8}")
    print(f"{'='*80}")
    for r in sorted(results, key=lambda x: (x.signal_name, x.horizon_min)):
        print(f"  {r.signal_name:<25} {r.horizon_min:>4}m     {r.total_predictions:<8} "
              f"{r.hit_rate:>7.1%}   {r.avg_return_pct:>+8.4f}   {r.sharpe:>6.2f}")
    print(f"{'='*80}")


def save_results(results: list[BacktestResult], label: str = "backtest") -> Path:
    """Save results to JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{label}_{ts}.json"
    out = []
    for r in results:
        out.append({
            "signal": r.signal_name, "horizon_min": r.horizon_min,
            "trades": r.total_predictions, "hit_rate": r.hit_rate,
            "avg_return_pct": r.avg_return_pct, "sharpe": r.sharpe,
        })
    path.write_text(json.dumps(out, indent=2))
    logger.info("Saved results to %s", path)
    return path


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("Generating synthetic data (500 steps × 5min)...")
    data = generate_synthetic_data(n_steps=500)
    print(f"  Price range: ${min(d['price'] for d in data):,.0f} – ${max(d['price'] for d in data):,.0f}")

    print("\nRunning backtest...")
    results = run_backtest(data)
    print_results(results)

    path = save_results(results, "synthetic")
    print(f"\nResults saved to {path}")


if __name__ == "__main__":
    main()
