"""
Inversion Analysis — Tests whether inverting failing signals produces profit.
"""
from __future__ import annotations
import json, logging, math, random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np

from signals.backtester import (
    generate_synthetic_data, run_backtest, BacktestResult, print_results, HORIZONS_MINUTES,
)
from signals.signal_definitions import (
    Direction, SignalResult, FundingRateExtremeSignal, OIDivergenceSignal,
    LiquidationCascadeSignal, ConfluenceSignal,
)
from signals.monte_carlo import (
    run_monte_carlo, MonteCarloSummary, print_summary,
    REGIMES, generate_regime_data, block_bootstrap, compute_sim_metrics,
    jitter_params, DEFAULT_PARAMS,
)

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).parent / "results"


class InvertedSignalWrapper:
    def __init__(self, inner):
        self.inner = inner
    def evaluate(self, data):
        r = self.inner.evaluate(data)
        d = Direction.SHORT if r.direction == Direction.LONG else (
            Direction.LONG if r.direction == Direction.SHORT else Direction.NEUTRAL)
        return SignalResult(r.name, d, r.confidence, f"[INV] {r.reasoning}", r.metadata)


def run_backtest_inverted(data, horizons=None, signal_params=None, invert_signals=None):
    horizons = horizons or HORIZONS_MINUTES
    params = signal_params or {}
    inv_all = invert_signals is None
    inv_set = set(invert_signals or [])

    liq = LiquidationCascadeSignal(cluster_pct=params.get("liquidation_cluster_pct", 0.02))
    fund = FundingRateExtremeSignal(threshold=params.get("funding_rate_threshold", 0.001))
    oi = OIDivergenceSignal()
    if inv_all or "liquidation_cascade" in inv_set: liq = InvertedSignalWrapper(liq)
    if inv_all or "funding_extreme" in inv_set: fund = InvertedSignalWrapper(fund)
    if inv_all or "oi_divergence" in inv_set: oi = InvertedSignalWrapper(oi)
    conf = ConfluenceSignal(min_score=params.get("min_confluence", 40.0))

    spp = {h: h // 5 for h in horizons}
    rets_map = {}
    for i, step in enumerate(data):
        sigs, comps = {}, []
        if step.get("liq_data"):
            r = liq.evaluate(step["liq_data"]); sigs[r.name] = r; comps.append(r)
        if step.get("funding_data"):
            r = fund.evaluate(step["funding_data"]); sigs[r.name] = r; comps.append(r)
        if step.get("oi_data"):
            r = oi.evaluate(step["oi_data"]); sigs[r.name] = r; comps.append(r)
        c = conf.evaluate(comps); sigs[c.name] = c

        for sn, sr in sigs.items():
            if not sr.is_active: continue
            if sn not in rets_map: rets_map[sn] = {h: [] for h in horizons}
            for h in horizons:
                fi = i + spp[h]
                if fi >= len(data): continue
                pr = (data[fi]["price"] - step["price"]) / step["price"]
                if sr.direction == Direction.LONG: rets_map[sn][h].append(pr * sr.confidence)
                elif sr.direction == Direction.SHORT: rets_map[sn][h].append(-pr * sr.confidence)

    results = []
    for sn, hr in rets_map.items():
        for h, rs in hr.items():
            if not rs:
                results.append(BacktestResult(sn, h, 0, 0.0, 0.0, 0.0)); continue
            hits = sum(1 for r in rs if r > 0)
            avg = np.mean(rs) * 100
            std = np.std(rs) if len(rs) > 1 else 1.0
            sh = (np.mean(rs) / std * math.sqrt(252*24*12)) if std > 0 else 0.0
            results.append(BacktestResult(sn, h, len(rs), round(hits/len(rs),4),
                                          round(float(avg),4), round(float(sh),2), rs))
    return results


def run_mc_inverted(n_sims=100, n_steps=500, base_params=None, halt_dd=15.0,
                    jitter_pct=0.15, invert_signals=None, seed=42):
    base = base_params or dict(DEFAULT_PARAMS)
    rng = random.Random(seed)
    regimes = list(REGIMES.keys())
    sims = []
    base_data = generate_synthetic_data(n_steps=n_steps*2, seed=seed)
    for i in range(n_sims):
        regime = rng.choice(regimes)
        ss = rng.randint(0, 2**31)
        if rng.random() < 0.5:
            d = block_bootstrap(base_data, block_size=20, n_blocks=n_steps//20, rng=random.Random(ss))
        else:
            d = generate_regime_data(n_steps=n_steps, regime=regime, seed=ss)
        p = jitter_params(base, pct=jitter_pct, rng=random.Random(ss))
        bt = run_backtest_inverted(d, horizons=[60], signal_params=p, invert_signals=invert_signals)
        sims.append(compute_sim_metrics(bt, regime, p))
        if (i+1) % max(1, n_sims//10) == 0: logger.info("MC inv: %d/%d", i+1, n_sims)

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
    sens = {}
    for pk in base:
        pv = np.array([s.param_jitter.get(pk, 0) for s in sims])
        sens[pk] = round(float(np.corrcoef(pv, ra)[0,1]), 4) if np.std(pv)>0 and np.std(ra)>0 else 0.0
    return MonteCarloSummary(
        n_simulations=n_sims, percentiles=pcts,
        ruin_probability=round(float(np.mean(da > halt_dd)), 4),
        worst_case=sims[int(np.argmin(ra))], best_case=sims[int(np.argmax(ra))],
        param_sensitivity=sens,
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seed = 42
    N = 200

    data = generate_synthetic_data(n_steps=500, seed=seed)
    print(f"Data: {len(data)} steps, ${min(d['price'] for d in data):,.0f}–${max(d['price'] for d in data):,.0f}\n")

    print("=" * 70)
    print("  ORIGINAL BACKTEST")
    print("=" * 70)
    orig = run_backtest(data)
    print_results(orig)

    print("=" * 70)
    print("  FUNDING_EXTREME INVERTED")
    print("=" * 70)
    fund_inv = run_backtest_inverted(data, invert_signals=["funding_extreme"])
    print_results(fund_inv)

    print("=" * 70)
    print("  ALL SIGNALS INVERTED")
    print("=" * 70)
    all_inv = run_backtest_inverted(data, invert_signals=None)
    print_results(all_inv)

    print("=" * 70)
    print("  OI_DIVERGENCE INVERTED (control)")
    print("=" * 70)
    oi_inv = run_backtest_inverted(data, invert_signals=["oi_divergence"])
    print_results(oi_inv)

    print(f"\n{'='*70}\n  MONTE CARLO: ORIGINAL ({N} sims)\n{'='*70}")
    mc_orig = run_monte_carlo(n_sims=N, n_steps=300, seed=seed)
    print_summary(mc_orig)

    print(f"\n{'='*70}\n  MONTE CARLO: FUNDING INVERTED ({N} sims)\n{'='*70}")
    mc_fund = run_mc_inverted(n_sims=N, n_steps=300, invert_signals=["funding_extreme"], seed=seed)
    print_summary(mc_fund)

    print(f"\n{'='*70}\n  MONTE CARLO: ALL INVERTED ({N} sims)\n{'='*70}")
    mc_all = run_mc_inverted(n_sims=N, n_steps=300, invert_signals=None, seed=seed)
    print_summary(mc_all)

    # Summary comparison
    print(f"\n{'='*70}")
    print("  FINAL COMPARISON")
    print(f"{'='*70}")
    for label, mc in [("Original", mc_orig), ("Funding Inv", mc_fund), ("All Inv", mc_all)]:
        p = mc.percentiles
        print(f"  {label:<15} Return: {p['total_return_pct']['p50']:>+8.2f}%  "
              f"Sharpe: {p['sharpe']['p50']:>+7.2f}  "
              f"Ruin: {mc.ruin_probability:>5.1%}  "
              f"Win: {p['win_rate']['p50']:>5.1%}")

    # Save comprehensive results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backtest": {
            "original": [{"sig": r.signal_name, "h": r.horizon_min, "n": r.total_predictions,
                          "hr": r.hit_rate, "ret": r.avg_return_pct, "sharpe": r.sharpe}
                         for r in orig if r.total_predictions > 0],
            "funding_inverted": [{"sig": r.signal_name, "h": r.horizon_min, "n": r.total_predictions,
                                  "hr": r.hit_rate, "ret": r.avg_return_pct, "sharpe": r.sharpe}
                                 for r in fund_inv if r.total_predictions > 0],
            "all_inverted": [{"sig": r.signal_name, "h": r.horizon_min, "n": r.total_predictions,
                              "hr": r.hit_rate, "ret": r.avg_return_pct, "sharpe": r.sharpe}
                             for r in all_inv if r.total_predictions > 0],
        },
        "monte_carlo": {
            "original": {"median_return": mc_orig.percentiles["total_return_pct"]["p50"],
                         "median_sharpe": mc_orig.percentiles["sharpe"]["p50"],
                         "ruin_prob": mc_orig.ruin_probability},
            "funding_inverted": {"median_return": mc_fund.percentiles["total_return_pct"]["p50"],
                                 "median_sharpe": mc_fund.percentiles["sharpe"]["p50"],
                                 "ruin_prob": mc_fund.ruin_probability},
            "all_inverted": {"median_return": mc_all.percentiles["total_return_pct"]["p50"],
                             "median_sharpe": mc_all.percentiles["sharpe"]["p50"],
                             "ruin_prob": mc_all.ruin_probability},
        }
    }
    path = RESULTS_DIR / f"inversion_analysis_{ts}.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nResults saved to {path}")


if __name__ == "__main__":
    main()
