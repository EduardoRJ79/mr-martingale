"""
MRM v3.0 — Phase 6: Risk Tuning
=================================
Sweep risk_pct and rescue_risk on top 0-liq configs from Phases 4-5.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from v30_indicators import load_data
from v30_engine import run_backtest
from v30_phase1 import sma440_regime
import numpy as np


def v28_entry(ind, px, prev_candle, is_bull):
    ema = ind['ema34'][prev_candle]
    sma = ind['sma14'][prev_candle]
    if np.isnan(ema) or np.isnan(sma):
        return False
    trigger = 0.005 if is_bull else 0.015
    return (ema - px) / ema >= trigger and (sma - px) / sma >= trigger

def make_ema_cross(period, trigger_pct):
    key = f'ema_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        ema = ind[key][prev_candle]
        if np.isnan(ema) or ema <= 0: return False
        trigger = trigger_pct if is_bull else trigger_pct * 3.0
        return (ema - px) / ema >= trigger
    return entry_fn

def make_atr_ratio_filter(threshold):
    def fn(ind, px, prev_candle, is_bull):
        val = ind['atr_ratio_14_60'][prev_candle]
        return not np.isnan(val) and val <= threshold
    return fn

def make_and(fn_a, fn_b):
    def entry_fn(ind, px, prev_candle, is_bull):
        return fn_a(ind, px, prev_candle, is_bull) and fn_b(ind, px, prev_candle, is_bull)
    return entry_fn

def make_or(fn_a, fn_b):
    def entry_fn(ind, px, prev_candle, is_bull):
        return fn_a(ind, px, prev_candle, is_bull) or fn_b(ind, px, prev_candle, is_bull)
    return entry_fn


if __name__ == '__main__':
    data = load_data()
    results = []
    t0 = time.time()
    configs = []

    # Top 0-liq entries to tune
    entries = [
        ('v28orEma20t02', make_or(v28_entry, make_ema_cross(20, 0.020)),
         {'use_dd20d': True, 'use_rsi_rescue': True}),
        ('ema20t5_atr12', make_and(make_ema_cross(20, 0.005), make_atr_ratio_filter(1.2)),
         {'use_dd20d': True, 'use_rsi_rescue': True}),
        ('v28_baseline', v28_entry,
         {'use_dd20d': True, 'use_rsi_rescue': True}),
    ]

    risk_values = [0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54]
    rescue_values = [0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30]

    for entry_name, entry_fn, base_cfg in entries:
        for risk in risk_values:
            for rescue in rescue_values:
                label = f'p6_{entry_name}_r{risk}_rr{rescue}'
                cfg = {**base_cfg, 'risk_pct': risk, 'rescue_risk_pct': rescue}
                configs.append((label, entry_fn, sma440_regime, cfg))

    print(f"\nPhase 6: {len(configs)} configs (risk tuning)")
    print("=" * 100)

    for idx, (label, entry_fn, regime_fn, cfg) in enumerate(configs):
        t1 = time.time()
        r = run_backtest(data, entry_fn, regime_fn, config=cfg, label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        if (idx + 1) % 10 == 0 or r['liq'] == 0:
            print(f"  [{idx+1:3d}/{len(configs)}] {label:<50} CAGR={r['cagr']:>7.1f}%  "
                  f"trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase6_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    total_time = time.time() - t0
    zero_liq = sorted([r for r in results if r['liq'] == 0], key=lambda r: r['cagr'], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"Phase 6 complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 15 (0-liq, by CAGR):")
    for r in zero_liq[:15]:
        print(f"  {r['label']:<50} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
