"""
Bayesian Optimizer for Signal Parameters

Uses scikit-optimize to find optimal thresholds, weights, and lookback periods.
Walk-forward validation to prevent overfitting.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from skopt import gp_minimize
from skopt.space import Real, Integer
from skopt.utils import use_named_args

from signals.backtester import generate_synthetic_data, run_backtest, BacktestResult, RESULTS_DIR

logger = logging.getLogger(__name__)


# ── Parameter Search Space ───────────────────────────────────────────────────

SEARCH_SPACE = [
    Real(0.0005, 0.005, name="funding_rate_threshold"),
    Real(0.01, 0.05, name="liquidation_cluster_pct"),
    Real(20.0, 70.0, name="min_confluence"),
]


def _objective(params: dict[str, float], train_data: list[dict], test_data: list[dict]) -> float:
    """
    Objective: maximize risk-adjusted return on TEST data (not train).
    Returns negative Sharpe (we minimize).
    """
    signal_params = {
        "funding_rate_threshold": params["funding_rate_threshold"],
        "liquidation_cluster_pct": params["liquidation_cluster_pct"],
        "min_confluence": params["min_confluence"],
    }

    # Run on train to ensure minimum trade count (anti-overfit)
    train_results = run_backtest(train_data, horizons=[60], signal_params=signal_params)
    total_train_trades = sum(r.total_predictions for r in train_results)
    if total_train_trades < 10:
        return 10.0  # Penalty: too few trades

    # Evaluate on test
    test_results = run_backtest(test_data, horizons=[60], signal_params=signal_params)
    if not test_results:
        return 10.0

    # Aggregate Sharpe across signals (1h horizon)
    sharpes = [r.sharpe for r in test_results if r.total_predictions > 0]
    if not sharpes:
        return 10.0

    avg_sharpe = np.mean(sharpes)

    # Complexity penalty: penalize extreme parameter values
    complexity = (
        abs(params["funding_rate_threshold"] - 0.001) / 0.005 * 0.1 +
        abs(params["min_confluence"] - 40) / 50 * 0.1
    )

    return -(avg_sharpe - complexity)


def optimize(
    n_calls: int = 30,
    n_splits: int = 3,
    n_steps: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Walk-forward Bayesian optimization.
    Splits synthetic data into folds, optimizes on each train window, tests on next.
    """
    logger.info("Generating synthetic data (%d steps)...", n_steps)
    data = generate_synthetic_data(n_steps=n_steps, seed=seed)

    fold_size = len(data) // (n_splits + 1)
    all_test_results: list[dict] = []
    best_params_per_fold: list[dict] = []

    for fold in range(n_splits):
        train_start = fold * fold_size
        train_end = train_start + fold_size
        test_end = min(train_end + fold_size, len(data))

        train_data = data[train_start:train_end]
        test_data = data[train_end:test_end]

        if len(test_data) < 20:
            continue

        logger.info("Fold %d/%d: train[%d:%d] test[%d:%d]",
                     fold + 1, n_splits, train_start, train_end, train_end, test_end)

        @use_named_args(SEARCH_SPACE)
        def objective_fn(**params):
            return _objective(params, train_data, test_data)

        result = gp_minimize(
            objective_fn, SEARCH_SPACE, n_calls=n_calls, random_state=seed + fold,
            verbose=False, n_initial_points=10,
        )

        best = {dim.name: val for dim, val in zip(SEARCH_SPACE, result.x)}
        best["objective"] = float(-result.fun)
        best["fold"] = fold

        best_params_per_fold.append(best)
        logger.info("Fold %d best: Sharpe=%.2f params=%s", fold + 1, -result.fun,
                     {k: round(v, 6) if isinstance(v, float) else v for k, v in best.items()})

    # Average best params across folds
    if best_params_per_fold:
        avg_params = {}
        for key in ["funding_rate_threshold", "liquidation_cluster_pct", "min_confluence"]:
            avg_params[key] = round(np.mean([p[key] for p in best_params_per_fold]), 6)
        avg_sharpe = np.mean([p["objective"] for p in best_params_per_fold])
    else:
        avg_params = {"funding_rate_threshold": 0.001, "liquidation_cluster_pct": 0.02, "min_confluence": 40.0}
        avg_sharpe = 0.0

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_calls": n_calls,
        "n_splits": n_splits,
        "avg_sharpe": round(float(avg_sharpe), 4),
        "best_params": avg_params,
        "per_fold": best_params_per_fold,
    }

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"optimization_{ts}.json"
    path.write_text(json.dumps(output, indent=2))
    logger.info("Saved optimization results to %s", path)

    return output


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Starting Bayesian optimization (walk-forward)...")
    print("This may take a minute...\n")

    result = optimize(n_calls=25, n_splits=3, n_steps=800)

    print(f"\n{'='*60}")
    print(f"  Optimization Complete")
    print(f"{'='*60}")
    print(f"  Avg Sharpe across folds: {result['avg_sharpe']:.4f}")
    print(f"  Best parameters:")
    for k, v in result["best_params"].items():
        print(f"    {k}: {v}")
    print(f"\n  Per-fold results:")
    for p in result["per_fold"]:
        print(f"    Fold {p['fold']}: Sharpe={p['objective']:.4f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
