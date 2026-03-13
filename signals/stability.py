"""
Parameter Stability Analyzer

Grid search around optimal parameter values to determine if performance
sits on a stable plateau or a knife-edge.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from signals.backtester import generate_synthetic_data, run_backtest

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"

DEFAULT_PARAMS = {
    "funding_rate_threshold": 0.001,
    "liquidation_cluster_pct": 0.02,
    "min_confluence": 40.0,
}

# How far to search around each param (±30%) and how many steps
GRID_RANGE_PCT = 0.30
GRID_STEPS = 7  # per dimension


def _sharpe_from_backtest(data: list[dict], params: dict[str, float]) -> float:
    """Run a backtest and return aggregate Sharpe."""
    results = run_backtest(data, horizons=[60], signal_params=params)
    sharpes = [r.sharpe for r in results if r.total_predictions > 0]
    return float(np.mean(sharpes)) if sharpes else 0.0


def grid_search_1d(
    data: list[dict],
    base_params: dict[str, float],
    param_name: str,
    n_steps: int = GRID_STEPS,
    range_pct: float = GRID_RANGE_PCT,
) -> dict[str, Any]:
    """
    Vary one parameter across a grid while holding others fixed.
    Returns values, performance at each point, and a stability score.
    """
    base_val = base_params[param_name]
    lo = base_val * (1 - range_pct)
    hi = base_val * (1 + range_pct)
    grid = np.linspace(lo, hi, n_steps).tolist()

    performances: list[float] = []
    for val in grid:
        p = dict(base_params)
        p[param_name] = val
        perf = _sharpe_from_backtest(data, p)
        performances.append(perf)

    # Stability score: how smooth is the curve?
    # Low second derivative = stable plateau, high = knife-edge
    arr = np.array(performances)
    if len(arr) < 3:
        stability_score = 0.5
    else:
        # Normalized second derivative
        second_deriv = np.diff(arr, n=2)
        max_perf = max(abs(arr.max() - arr.min()), 1e-6)
        roughness = float(np.mean(np.abs(second_deriv))) / max_perf
        # Map: 0 roughness → 1.0 score, high roughness → 0.0
        stability_score = max(0.0, min(1.0, 1.0 - roughness * 5))

    return {
        "param": param_name,
        "base_value": base_val,
        "grid": [round(v, 8) for v in grid],
        "performance": [round(p, 4) for p in performances],
        "stability_score": round(stability_score, 4),
        "best_value": round(grid[int(np.argmax(arr))], 8),
        "best_performance": round(float(np.max(arr)), 4),
    }


def grid_search_2d(
    data: list[dict],
    base_params: dict[str, float],
    param_a: str,
    param_b: str,
    n_steps: int = 5,
    range_pct: float = GRID_RANGE_PCT,
) -> dict[str, Any]:
    """
    Vary two parameters in a 2D grid for heatmap data.
    """
    val_a = base_params[param_a]
    val_b = base_params[param_b]
    grid_a = np.linspace(val_a * (1 - range_pct), val_a * (1 + range_pct), n_steps).tolist()
    grid_b = np.linspace(val_b * (1 - range_pct), val_b * (1 + range_pct), n_steps).tolist()

    heatmap: list[list[float]] = []
    for a in grid_a:
        row = []
        for b in grid_b:
            p = dict(base_params)
            p[param_a] = a
            p[param_b] = b
            perf = _sharpe_from_backtest(data, p)
            row.append(round(perf, 4))
        heatmap.append(row)

    return {
        "param_a": param_a,
        "param_b": param_b,
        "grid_a": [round(v, 8) for v in grid_a],
        "grid_b": [round(v, 8) for v in grid_b],
        "heatmap": heatmap,
    }


def analyze_stability(
    base_params: dict[str, float] | None = None,
    n_steps_data: int = 800,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Full stability analysis: 1D grid per param + 2D heatmaps for all pairs.
    """
    params = base_params or dict(DEFAULT_PARAMS)
    logger.info("Generating data for stability analysis...")
    data = generate_synthetic_data(n_steps=n_steps_data, seed=seed)

    # 1D analysis per parameter
    param_results: dict[str, Any] = {}
    for pname in params:
        logger.info("1D grid search: %s", pname)
        param_results[pname] = grid_search_1d(data, params, pname)

    # 2D heatmaps for all pairs
    heatmaps: list[dict[str, Any]] = []
    for pa, pb in combinations(params.keys(), 2):
        logger.info("2D grid search: %s × %s", pa, pb)
        heatmaps.append(grid_search_2d(data, params, pa, pb, n_steps=5))

    # Overall stability
    scores = {p: r["stability_score"] for p, r in param_results.items()}
    fragile = [p for p, s in scores.items() if s < 0.5]

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_params": params,
        "per_parameter": param_results,
        "heatmaps": heatmaps,
        "stability_scores": scores,
        "fragile_parameters": fragile,
        "overall_stability": round(float(np.mean(list(scores.values()))), 4),
    }

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"stability_{ts}.json"
    path.write_text(json.dumps(output, indent=2))
    logger.info("Saved stability results to %s", path)

    return output


def print_stability(result: dict[str, Any]) -> None:
    """Print stability analysis summary."""
    print(f"\n{'='*60}")
    print(f"  PARAMETER STABILITY ANALYSIS")
    print(f"{'='*60}")

    for pname, pdata in result["per_parameter"].items():
        score = pdata["stability_score"]
        status = "✅ STABLE" if score >= 0.7 else "⚠️  MODERATE" if score >= 0.4 else "🚨 FRAGILE"
        print(f"\n  {pname}:")
        print(f"    Base: {pdata['base_value']}  Best: {pdata['best_value']}  Score: {score:.2f} {status}")
        perfs = pdata["performance"]
        print(f"    Performance range: [{min(perfs):.2f}, {max(perfs):.2f}]")

    print(f"\n  Overall Stability: {result['overall_stability']:.2f}")
    if result["fragile_parameters"]:
        print(f"  ⚠️  Fragile: {', '.join(result['fragile_parameters'])}")
    else:
        print(f"  ✅ No fragile parameters detected")
    print(f"{'='*60}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Running parameter stability analysis...")

    result = analyze_stability()
    print_stability(result)


if __name__ == "__main__":
    main()
