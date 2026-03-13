"""
Focused Backtest — Single Signal Analysis

Tests funding_extreme (contrarian/inverted) in isolation with parameter tuning.
"""
import json, logging, math, random
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from signals.backtester import generate_synthetic_data, BacktestResult, HORIZONS_MINUTES
from signals.signal_definitions import (
    Direction, SignalResult, FundingRateExtremeSignal,
)
from signals.inversion_analysis import InvertedSignalWrapper, run_mc_inverted
from signals.monte_carlo import (
    run_monte_carlo, print_summary, REGIMES, generate_regime_data,
    block_bootstrap, compute_sim_metrics, jitter_params, DEFAULT_PARAMS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RESULTS_DIR = Path(__file__).parent / "results"


def backtest_single_signal(data, threshold=0.001, horizons=None, inverted=True):
    """Backtest just the funding_extreme signal."""
    horizons = horizons or HORIZONS_MINUTES
    inner = FundingRateExtremeSignal(threshold=threshold)
    sig = InvertedSignalWrapper(inner) if inverted else inner
    spp = {h: h // 5 for h in horizons}
    rets = {h: [] for h in horizons}

    for i, step in enumerate(data):
        fd = step.get("funding_data")
        if not fd:
            continue
        r = sig.evaluate(fd)
        if not r.is_active:
            continue
        for h in horizons:
            fi = i + spp[h]
            if fi >= len(data):
                continue
            pr = (data[fi]["price"] - step["price"]) / step["price"]
            if r.direction == Direction.LONG:
                rets[h].append(pr * r.confidence)
            elif r.direction == Direction.SHORT:
                rets[h].append(-pr * r.confidence)

    results = []
    for h, rs in rets.items():
        if not rs:
            results.append(BacktestResult("funding_contrarian", h, 0, 0, 0, 0))
            continue
        hits = sum(1 for x in rs if x > 0)
        avg = np.mean(rs) * 100
        std = np.std(rs) if len(rs) > 1 else 1.0
        sh = (np.mean(rs) / std * math.sqrt(252*24*12)) if std > 0 else 0.0
        results.append(BacktestResult(
            "funding_contrarian", h, len(rs),
            round(hits/len(rs), 4), round(float(avg), 4), round(float(sh), 2), rs))
    return results


def parameter_sweep(data, inverted=True):
    """Sweep funding_rate_threshold to find optimal."""
    thresholds = [0.0003, 0.0005, 0.0007, 0.001, 0.0013, 0.0015, 0.002, 0.003]
    print(f"\n{'='*80}")
    print(f"  PARAMETER SWEEP: funding_rate_threshold")
    print(f"{'='*80}")
    print(f"  {'Threshold':<12} {'5m Sharpe':>10} {'15m Sharpe':>11} {'1h Sharpe':>10} {'4h Sharpe':>10} {'4h HitRate':>10} {'4h AvgRet':>10}")
    print(f"  {'-'*75}")

    best_sharpe_4h = -999
    best_threshold = 0.001

    for t in thresholds:
        results = backtest_single_signal(data, threshold=t, inverted=inverted)
        by_h = {r.horizon_min: r for r in results}
        r4h = by_h.get(240)
        r1h = by_h.get(60)
        r15 = by_h.get(15)
        r5 = by_h.get(5)

        s5 = r5.sharpe if r5 and r5.total_predictions > 0 else 0
        s15 = r15.sharpe if r15 and r15.total_predictions > 0 else 0
        s1h = r1h.sharpe if r1h and r1h.total_predictions > 0 else 0
        s4h = r4h.sharpe if r4h and r4h.total_predictions > 0 else 0
        hr4h = r4h.hit_rate if r4h and r4h.total_predictions > 0 else 0
        ar4h = r4h.avg_return_pct if r4h and r4h.total_predictions > 0 else 0

        marker = " ← BEST" if s4h > best_sharpe_4h else ""
        if s4h > best_sharpe_4h:
            best_sharpe_4h = s4h
            best_threshold = t

        print(f"  {t:<12.4f} {s5:>10.2f} {s15:>11.2f} {s1h:>10.2f} {s4h:>10.2f} {hr4h:>9.1%} {ar4h:>+9.4f}%{marker}")

    print(f"\n  Best threshold: {best_threshold} (4h Sharpe: {best_sharpe_4h:.2f})")
    return best_threshold


def mc_single_signal(n_sims=200, n_steps=300, threshold=0.001, halt_dd=15.0, seed=42):
    """Monte Carlo for single funding_contrarian signal."""
    base_data = generate_synthetic_data(n_steps=n_steps*2, seed=seed)
    rng = random.Random(seed)
    regimes = list(REGIMES.keys())
    sims = []

    for i in range(n_sims):
        regime = rng.choice(regimes)
        ss = rng.randint(0, 2**31)
        if rng.random() < 0.5:
            d = block_bootstrap(base_data, block_size=20, n_blocks=n_steps//20, rng=random.Random(ss))
        else:
            d = generate_regime_data(n_steps=n_steps, regime=regime, seed=ss)

        # Jitter threshold
        jrng = random.Random(ss)
        t = threshold * (1 + jrng.uniform(-0.15, 0.15))
        bt = backtest_single_signal(d, threshold=t, horizons=[60], inverted=True)
        sim = compute_sim_metrics(bt, regime, {"funding_rate_threshold": t})
        sims.append(sim)
        if (i+1) % max(1, n_sims//10) == 0:
            print(f"  MC single-signal: {i+1}/{n_sims}")

    metrics = {
        "total_return_pct": [s.total_return_pct for s in sims],
        "max_drawdown_pct": [s.max_drawdown_pct for s in sims],
        "sharpe": [s.sharpe for s in sims],
        "sortino": [s.sortino for s in sims],
        "win_rate": [s.win_rate for s in sims],
        "max_consecutive_losses": [float(s.max_consecutive_losses) for s in sims],
        "worst_single_trade_pct": [s.worst_single_trade_pct for s in sims],
    }
    pcts = {}
    for nm, vs in metrics.items():
        a = np.array(vs)
        pcts[nm] = {f"p{p}": round(float(np.percentile(a, p)), 4) for p in [5,25,50,75,95]}
        pcts[nm]["mean"] = round(float(np.mean(a)), 4)
        pcts[nm]["std"] = round(float(np.std(a)), 4)

    ra = np.array(metrics["total_return_pct"])
    da = np.array(metrics["max_drawdown_pct"])

    from signals.monte_carlo import MonteCarloSummary
    return MonteCarloSummary(
        n_simulations=n_sims, percentiles=pcts,
        ruin_probability=round(float(np.mean(da > halt_dd)), 4),
        worst_case=sims[int(np.argmin(ra))], best_case=sims[int(np.argmax(ra))],
        param_sensitivity={"funding_rate_threshold": round(float(
            np.corrcoef([s.param_jitter.get("funding_rate_threshold",0) for s in sims], ra)[0,1]
        ) if np.std(ra)>0 else 0.0, 4)},
    )


def main():
    seed = 42
    data = generate_synthetic_data(n_steps=500, seed=seed)

    print(f"\nData: {len(data)} steps, ${min(d['price'] for d in data):,.0f}–${max(d['price'] for d in data):,.0f}")

    # 1. Baseline: single signal backtest
    print(f"\n{'='*80}")
    print("  SINGLE SIGNAL: funding_contrarian (inverted funding_extreme)")
    print(f"{'='*80}")
    results = backtest_single_signal(data, threshold=0.001, inverted=True)
    print(f"\n  {'Horizon':<10} {'Trades':<8} {'Hit Rate':<10} {'Avg Return':<12} {'Sharpe':<8}")
    print(f"  {'-'*50}")
    for r in results:
        if r.total_predictions > 0:
            print(f"  {r.horizon_min:>4}m     {r.total_predictions:<8} {r.hit_rate:>7.1%}   {r.avg_return_pct:>+10.4f}%  {r.sharpe:>6.2f}")

    # 2. Parameter sweep
    best_t = parameter_sweep(data, inverted=True)

    # 3. Backtest with optimal threshold
    print(f"\n{'='*80}")
    print(f"  OPTIMAL THRESHOLD BACKTEST (threshold={best_t})")
    print(f"{'='*80}")
    opt_results = backtest_single_signal(data, threshold=best_t, inverted=True)
    print(f"\n  {'Horizon':<10} {'Trades':<8} {'Hit Rate':<10} {'Avg Return':<12} {'Sharpe':<8}")
    print(f"  {'-'*50}")
    for r in opt_results:
        if r.total_predictions > 0:
            print(f"  {r.horizon_min:>4}m     {r.total_predictions:<8} {r.hit_rate:>7.1%}   {r.avg_return_pct:>+10.4f}%  {r.sharpe:>6.2f}")

    # 4. Monte Carlo single signal
    print(f"\n{'='*80}")
    print(f"  MONTE CARLO: SINGLE SIGNAL (200 sims, threshold={best_t})")
    print(f"{'='*80}")
    mc = mc_single_signal(n_sims=200, threshold=best_t, seed=seed)
    print_summary(mc)

    # 5. Control: non-inverted single signal
    print(f"\n{'='*80}")
    print(f"  CONTROL: NON-INVERTED funding_extreme (threshold={best_t})")
    print(f"{'='*80}")
    ctrl = backtest_single_signal(data, threshold=best_t, inverted=False)
    print(f"\n  {'Horizon':<10} {'Trades':<8} {'Hit Rate':<10} {'Avg Return':<12} {'Sharpe':<8}")
    print(f"  {'-'*50}")
    for r in ctrl:
        if r.total_predictions > 0:
            print(f"  {r.horizon_min:>4}m     {r.total_predictions:<8} {r.hit_rate:>7.1%}   {r.avg_return_pct:>+10.4f}%  {r.sharpe:>6.2f}")

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "best_threshold": best_t,
        "single_signal_backtest": [
            {"h": r.horizon_min, "n": r.total_predictions, "hr": r.hit_rate,
             "ret": r.avg_return_pct, "sharpe": r.sharpe}
            for r in opt_results if r.total_predictions > 0
        ],
        "monte_carlo": {
            "median_return": mc.percentiles["total_return_pct"]["p50"],
            "median_sharpe": mc.percentiles["sharpe"]["p50"],
            "ruin_prob": mc.ruin_probability,
        },
    }
    path = RESULTS_DIR / f"focused_backtest_{ts}.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nResults saved to {path}")


if __name__ == "__main__":
    main()
