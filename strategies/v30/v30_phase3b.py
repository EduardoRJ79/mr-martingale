"""
MRM v3.0 — Phase 3b: Parameter Sweep on Near-Misses
=====================================================
Near-miss configs (1 liq) from Phase 1-3 — sweep parameters to try to
eliminate the single liquidation while preserving CAGR.

Approach:
1. Identify the liq event for each entry type
2. Sweep parameters: trigger thresholds, dd20d threshold, risk, spacing
3. Try tighter risk/spacing that might prevent the single liq
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from v30_indicators import load_data
from v30_engine import run_backtest
from v30_phase1 import sma440_regime
import numpy as np


# ── Entry factories with adjustable parameters ──────────────────────────────

def make_ema_crossunder_entry(period, trigger_pct=0.005):
    key = f'ema_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        ema = ind[key][prev_candle]
        if np.isnan(ema) or ema <= 0:
            return False
        trigger = trigger_pct if is_bull else trigger_pct * 3.0
        return (ema - px) / ema >= trigger
    return entry_fn

def make_pivot_break_entry(lookback):
    key = f'last_pivot_price_{lookback}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_boll_lower_entry(period, mult):
    key = f'boll_lower_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px <= val
    return entry_fn

def make_gauss_mid_regime(period, mult=1.0):
    key = f'gauss_mid_{period}_{mult}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn


if __name__ == '__main__':
    data = load_data()
    results = []
    t0 = time.time()

    configs = []

    # ── 1. EMA crossunder sweeps (best near-miss: ema20_cross, 79.6% CAGR, 1 liq)
    # Sweep trigger_pct and dd20d threshold (periods must exist as ema_{period})
    for period in [20, 50, 100]:
        for trig in [0.003, 0.005, 0.007, 0.010, 0.015, 0.020]:
            for dd_thresh in [-0.08, -0.10, -0.12, -0.15]:
                label = f"p3b_ema{period}_t{trig}_dd{dd_thresh}"
                entry_fn = make_ema_crossunder_entry(period, trig)
                cfg = {'use_dd20d': True, 'use_rsi_rescue': True, 'dd20d_threshold': dd_thresh}
                configs.append((label, entry_fn, sma440_regime, cfg))

    # ── 2. EMA crossunder with reduced risk (might prevent the single liq)
    for period in [20, 50]:
        for risk in [0.35, 0.40, 0.42, 0.45, 0.48]:
            label = f"p3b_ema{period}_risk{risk}"
            entry_fn = make_ema_crossunder_entry(period, 0.005)
            cfg = {'use_dd20d': True, 'use_rsi_rescue': True, 'risk_pct': risk}
            configs.append((label, entry_fn, sma440_regime, cfg))

    # ── 3. Pivot break sweeps (only 5, 7, 10 exist in indicator library)
    for lb in [5, 7, 10]:
        for dd_thresh in [-0.08, -0.10, -0.12, -0.15]:
            label = f"p3b_pivot{lb}_dd{dd_thresh}"
            entry_fn = make_pivot_break_entry(lb)
            cfg = {'use_dd20d': True, 'use_rsi_rescue': True, 'dd20d_threshold': dd_thresh}
            configs.append((label, entry_fn, sma440_regime, cfg))

    # Pivot with reduced risk
    for lb in [10]:
        for risk in [0.35, 0.40, 0.42, 0.45, 0.48]:
            label = f"p3b_pivot{lb}_risk{risk}"
            entry_fn = make_pivot_break_entry(lb)
            cfg = {'use_dd20d': True, 'use_rsi_rescue': True, 'risk_pct': risk}
            configs.append((label, entry_fn, sma440_regime, cfg))

    # Pivot with gauss144 regime (best alt regime from Phase 3)
    for lb in [7, 10]:
        for dd_thresh in [-0.08, -0.10, -0.12, -0.15]:
            label = f"p3b_pivot{lb}_gauss144_dd{dd_thresh}"
            entry_fn = make_pivot_break_entry(lb)
            cfg = {'use_dd20d': True, 'use_rsi_rescue': True, 'dd20d_threshold': dd_thresh}
            configs.append((label, entry_fn, make_gauss_mid_regime(144, 1.0), cfg))

    # ── 4. Bollinger sweeps (mults in indicator library: 1.5, 2.0, 2.5)
    for period in [20, 30, 50]:
        for mult in [1.5, 2.0, 2.5]:
            label = f"p3b_boll_{period}_{mult}"
            entry_fn = make_boll_lower_entry(period, mult)
            cfg = {'use_dd20d': True, 'use_rsi_rescue': True}
            configs.append((label, entry_fn, sma440_regime, cfg))

    # ── 5. Wider spacing (might prevent liq by reducing grid exposure)
    for period in [20, 50]:
        for spacing_scale in [1.2, 1.5, 2.0]:
            gaps = [g * spacing_scale for g in [0.5, 1.5, 10.0, 14.0]]
            label = f"p3b_ema{period}_space{spacing_scale}x"
            entry_fn = make_ema_crossunder_entry(period, 0.005)
            cfg = {'use_dd20d': True, 'use_rsi_rescue': True, 'level_gaps': gaps}
            configs.append((label, entry_fn, sma440_regime, cfg))

    print(f"\nPhase 3b: {len(configs)} configs (parameter sweeps on near-misses)")
    print("=" * 100)

    for idx, (label, entry_fn, regime_fn, cfg) in enumerate(configs):
        t1 = time.time()
        r = run_backtest(data, entry_fn, regime_fn, config=cfg, label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        print(f"  [{idx+1:3d}/{len(configs)}] {label:<50} CAGR={r['cagr']:>7.1f}%  "
              f"trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase3b_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    total_time = time.time() - t0
    zero_liq = [r for r in results if r['liq'] == 0]
    zero_liq_sorted = sorted(zero_liq, key=lambda r: r['cagr'], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"Phase 3b complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 15 (0-liq, by CAGR):")
    for r in zero_liq_sorted[:15]:
        print(f"  {r['label']:<50} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
    print(f"\nResults saved to {out_path}")
