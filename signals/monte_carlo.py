"""
Monte Carlo Stress Tester

Runs thousands of simulated scenarios to stress test the strategy under
randomized market conditions, bootstrapped histories, and parameter perturbation.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from signals.backtester import generate_synthetic_data, run_backtest, BacktestResult

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"

# ── Volatility Regimes ───────────────────────────────────────────────────────

REGIMES = {
    "calm":     {"volatility": 0.001, "jump_prob": 0.01, "jump_mag": (0.003, 0.008)},
    "trending": {"volatility": 0.0015, "jump_prob": 0.02, "jump_mag": (0.005, 0.015), "drift": 0.0002},
    "choppy":   {"volatility": 0.004, "jump_prob": 0.05, "jump_mag": (0.005, 0.015)},
    "crash":    {"volatility": 0.006, "jump_prob": 0.08, "jump_mag": (0.01, 0.04), "drift": -0.0003},
    "melt_up":  {"volatility": 0.003, "jump_prob": 0.04, "jump_mag": (0.008, 0.025), "drift": 0.0004},
}


@dataclass
class SimulationResult:
    """Metrics for a single Monte Carlo run."""
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    win_rate: float
    max_consecutive_losses: int
    worst_single_trade_pct: float
    num_trades: int
    regime: str
    param_jitter: dict[str, float]


@dataclass
class MonteCarloSummary:
    """Aggregate statistics across all simulations."""
    n_simulations: int
    percentiles: dict[str, dict[str, float]]  # metric -> {p5, p25, p50, p75, p95}
    ruin_probability: float  # P(max_drawdown > halt_threshold)
    worst_case: SimulationResult
    best_case: SimulationResult
    param_sensitivity: dict[str, float]  # parameter -> correlation with return


# ── Synthetic Data with Regime ───────────────────────────────────────────────

def generate_regime_data(
    n_steps: int = 500,
    initial_price: float = 69000.0,
    regime: str = "calm",
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Generate synthetic data under a specific volatility regime."""
    r = REGIMES[regime]
    vol = r["volatility"]
    drift = r.get("drift", 0.0)
    jump_prob = r["jump_prob"]
    jump_lo, jump_hi = r["jump_mag"]

    return generate_synthetic_data(
        n_steps=n_steps,
        initial_price=initial_price,
        volatility=vol,
        seed=seed if seed is not None else random.randint(0, 2**31),
    )


def block_bootstrap(
    data: list[dict[str, Any]],
    block_size: int = 20,
    n_blocks: int | None = None,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """
    Block bootstrap: shuffle blocks of data to create alternate histories
    while preserving short-range autocorrelation.
    """
    rng = rng or random.Random()
    total = len(data)
    if n_blocks is None:
        n_blocks = total // block_size

    blocks = []
    for _ in range(n_blocks):
        start = rng.randint(0, total - block_size)
        blocks.extend(data[start:start + block_size])

    # Recompute prices from returns to make price series continuous
    if not blocks:
        return data[:1]

    result = []
    price = blocks[0]["price"]
    for i, step in enumerate(blocks):
        new_step = dict(step)
        if i > 0:
            ret = step.get("price_return", 0.0)
            price *= (1 + ret)
        new_step["price"] = round(price, 2)
        new_step["liq_data"] = dict(step.get("liq_data", {}))
        new_step["liq_data"]["mid_price"] = round(price, 2)
        result.append(new_step)

    return result


# ── Core Metrics ─────────────────────────────────────────────────────────────

def compute_sim_metrics(
    results: list[BacktestResult],
    regime: str,
    param_jitter: dict[str, float],
) -> SimulationResult:
    """Compute aggregate metrics from backtest results for one simulation."""
    all_returns: list[float] = []
    for r in results:
        all_returns.extend(r.returns)

    if not all_returns:
        return SimulationResult(
            total_return_pct=0.0, max_drawdown_pct=0.0, sharpe=0.0,
            sortino=0.0, win_rate=0.0, max_consecutive_losses=0,
            worst_single_trade_pct=0.0, num_trades=0,
            regime=regime, param_jitter=param_jitter,
        )

    arr = np.array(all_returns)
    total_return = float(np.prod(1 + arr) - 1) * 100

    # Max drawdown from cumulative returns
    cum = np.cumprod(1 + arr)
    peak = np.maximum.accumulate(cum)
    dd = (peak - cum) / peak
    max_dd = float(np.max(dd)) * 100 if len(dd) > 0 else 0.0

    # Sharpe (annualized, ~12 trades/day × 252 days)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr)) if len(arr) > 1 else 1.0
    sharpe = (mean_r / std_r * math.sqrt(3024)) if std_r > 0 else 0.0

    # Sortino
    downside = arr[arr < 0]
    down_std = float(np.std(downside)) if len(downside) > 1 else 1.0
    sortino = (mean_r / down_std * math.sqrt(3024)) if down_std > 0 else 0.0

    # Win rate
    wins = int(np.sum(arr > 0))
    win_rate = wins / len(arr)

    # Max consecutive losses
    max_consec = 0
    current = 0
    for r in arr:
        if r <= 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0

    worst_trade = float(np.min(arr)) * 100

    return SimulationResult(
        total_return_pct=round(total_return, 4),
        max_drawdown_pct=round(max_dd, 4),
        sharpe=round(sharpe, 4),
        sortino=round(sortino, 4),
        win_rate=round(win_rate, 4),
        max_consecutive_losses=max_consec,
        worst_single_trade_pct=round(worst_trade, 4),
        num_trades=len(arr),
        regime=regime,
        param_jitter=param_jitter,
    )


# ── Monte Carlo Runner ───────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    "funding_rate_threshold": 0.001,
    "liquidation_cluster_pct": 0.02,
    "min_confluence": 40.0,
}


def jitter_params(
    base: dict[str, float],
    pct: float = 0.15,
    rng: random.Random | None = None,
) -> dict[str, float]:
    """Apply random ±pct jitter to each parameter."""
    rng = rng or random.Random()
    return {k: v * (1 + rng.uniform(-pct, pct)) for k, v in base.items()}


def run_monte_carlo(
    n_sims: int = 1000,
    n_steps: int = 500,
    base_params: dict[str, float] | None = None,
    halt_drawdown_pct: float = 15.0,
    jitter_pct: float = 0.15,
    use_bootstrap: bool = True,
    seed: int = 42,
) -> MonteCarloSummary:
    """
    Run Monte Carlo stress test.

    1. For each sim, pick a random regime
    2. Generate or bootstrap data
    3. Jitter parameters
    4. Backtest and collect metrics
    """
    base = base_params or dict(DEFAULT_PARAMS)
    rng = random.Random(seed)
    regime_names = list(REGIMES.keys())
    sim_results: list[SimulationResult] = []

    # Generate base dataset for bootstrapping
    base_data = generate_synthetic_data(n_steps=n_steps * 2, seed=seed)

    for i in range(n_sims):
        regime = rng.choice(regime_names)
        sim_seed = rng.randint(0, 2**31)

        # Data source: alternate between regime generation and bootstrap
        if use_bootstrap and rng.random() < 0.5:
            data = block_bootstrap(base_data, block_size=20,
                                   n_blocks=n_steps // 20, rng=random.Random(sim_seed))
        else:
            data = generate_regime_data(n_steps=n_steps, regime=regime, seed=sim_seed)

        # Jitter params
        params = jitter_params(base, pct=jitter_pct, rng=random.Random(sim_seed))

        # Backtest (1h horizon only for speed)
        bt_results = run_backtest(data, horizons=[60], signal_params=params)
        sim = compute_sim_metrics(bt_results, regime, params)
        sim_results.append(sim)

        if (i + 1) % max(1, n_sims // 10) == 0:
            logger.info("Monte Carlo progress: %d/%d", i + 1, n_sims)

    # Aggregate
    metrics = {
        "total_return_pct": [s.total_return_pct for s in sim_results],
        "max_drawdown_pct": [s.max_drawdown_pct for s in sim_results],
        "sharpe": [s.sharpe for s in sim_results],
        "sortino": [s.sortino for s in sim_results],
        "win_rate": [s.win_rate for s in sim_results],
        "max_consecutive_losses": [float(s.max_consecutive_losses) for s in sim_results],
        "worst_single_trade_pct": [s.worst_single_trade_pct for s in sim_results],
    }

    percentiles: dict[str, dict[str, float]] = {}
    for name, vals in metrics.items():
        arr = np.array(vals)
        percentiles[name] = {
            "p5": round(float(np.percentile(arr, 5)), 4),
            "p25": round(float(np.percentile(arr, 25)), 4),
            "p50": round(float(np.percentile(arr, 50)), 4),
            "p75": round(float(np.percentile(arr, 75)), 4),
            "p95": round(float(np.percentile(arr, 95)), 4),
            "mean": round(float(np.mean(arr)), 4),
            "std": round(float(np.std(arr)), 4),
        }

    # Ruin probability
    dd_arr = np.array(metrics["max_drawdown_pct"])
    ruin_prob = float(np.mean(dd_arr > halt_drawdown_pct))

    # Worst/best case
    returns_arr = np.array(metrics["total_return_pct"])
    worst_idx = int(np.argmin(returns_arr))
    best_idx = int(np.argmax(returns_arr))

    # Parameter sensitivity: correlation of each param with total return
    param_keys = list(base.keys())
    sensitivity: dict[str, float] = {}
    for pk in param_keys:
        param_vals = np.array([s.param_jitter.get(pk, 0) for s in sim_results])
        if np.std(param_vals) > 0 and np.std(returns_arr) > 0:
            corr = float(np.corrcoef(param_vals, returns_arr)[0, 1])
            sensitivity[pk] = round(corr, 4)
        else:
            sensitivity[pk] = 0.0

    return MonteCarloSummary(
        n_simulations=n_sims,
        percentiles=percentiles,
        ruin_probability=round(ruin_prob, 4),
        worst_case=sim_results[worst_idx],
        best_case=sim_results[best_idx],
        param_sensitivity=sensitivity,
    )


def save_results(summary: MonteCarloSummary) -> Path:
    """Save Monte Carlo results to JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"monte_carlo_{ts}.json"

    def sim_to_dict(s: SimulationResult) -> dict:
        return {
            "total_return_pct": s.total_return_pct,
            "max_drawdown_pct": s.max_drawdown_pct,
            "sharpe": s.sharpe, "sortino": s.sortino,
            "win_rate": s.win_rate,
            "max_consecutive_losses": s.max_consecutive_losses,
            "worst_single_trade_pct": s.worst_single_trade_pct,
            "num_trades": s.num_trades,
            "regime": s.regime,
            "param_jitter": s.param_jitter,
        }

    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_simulations": summary.n_simulations,
        "percentiles": summary.percentiles,
        "ruin_probability": summary.ruin_probability,
        "worst_case": sim_to_dict(summary.worst_case),
        "best_case": sim_to_dict(summary.best_case),
        "param_sensitivity": summary.param_sensitivity,
    }
    path.write_text(json.dumps(out, indent=2))
    logger.info("Saved Monte Carlo results to %s", path)
    return path


def print_summary(summary: MonteCarloSummary) -> None:
    """Print a clean Monte Carlo summary to terminal."""
    print(f"\n{'='*70}")
    print(f"  MONTE CARLO STRESS TEST — {summary.n_simulations} Simulations")
    print(f"{'='*70}")

    print(f"\n  {'Metric':<28} {'P5':>8} {'P25':>8} {'P50':>8} {'P75':>8} {'P95':>8}")
    print(f"  {'-'*68}")
    for metric, pcts in summary.percentiles.items():
        label = metric.replace("_", " ").title()
        print(f"  {label:<28} {pcts['p5']:>8.2f} {pcts['p25']:>8.2f} "
              f"{pcts['p50']:>8.2f} {pcts['p75']:>8.2f} {pcts['p95']:>8.2f}")

    print(f"\n  Ruin Probability (DD > halt): {summary.ruin_probability:.1%}")
    print(f"\n  Worst Case: {summary.worst_case.total_return_pct:+.2f}% return, "
          f"{summary.worst_case.max_drawdown_pct:.2f}% DD, regime={summary.worst_case.regime}")
    print(f"  Best Case:  {summary.best_case.total_return_pct:+.2f}% return, "
          f"{summary.best_case.max_drawdown_pct:.2f}% DD, regime={summary.best_case.regime}")

    print(f"\n  Parameter Sensitivity (correlation with return):")
    for param, corr in summary.param_sensitivity.items():
        bar = "█" * int(abs(corr) * 20)
        sign = "+" if corr > 0 else "-" if corr < 0 else " "
        print(f"    {param:<30} {sign}{abs(corr):.4f} {bar}")

    print(f"{'='*70}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Running Monte Carlo stress test (100 simulations)...")

    summary = run_monte_carlo(n_sims=100, n_steps=300, seed=42)
    print_summary(summary)

    path = save_results(summary)
    print(f"\nResults saved to {path}")


if __name__ == "__main__":
    main()
