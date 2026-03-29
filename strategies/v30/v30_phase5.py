"""
MRM v3.0 — Phase 5: Three-Indicator Combinations
==================================================
Add a third quality gate to Phase 4 winners.
Top Phase 4 0-liq configs:
  1. v28 OR ema20_t0.02 (dd-0.10): 120.8% CAGR
  2. v28 AND RSI14≤60: 118.3% CAGR
  3. ema20 AND atr≤1.2: 116.2% CAGR
  4. v28 AND RSI14≤50: 111.7% CAGR
  5. ema20 AND spanb240: 79.8% CAGR
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

def make_rsi_filter(period, threshold):
    key = f'rsi_{period}'
    def fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val <= threshold
    return fn

def make_atr_ratio_filter(threshold):
    def fn(ind, px, prev_candle, is_bull):
        val = ind['atr_ratio_14_60'][prev_candle]
        return not np.isnan(val) and val <= threshold
    return fn

def make_spanb_filter(period):
    key = f'span_b_{period}'
    def fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return fn

def make_velocity_filter(period, threshold):
    key = f'velocity_{period}'
    def fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val >= threshold
    return fn

def make_and(fn_a, fn_b):
    def entry_fn(ind, px, prev_candle, is_bull):
        return fn_a(ind, px, prev_candle, is_bull) and fn_b(ind, px, prev_candle, is_bull)
    return entry_fn

def make_or(fn_a, fn_b):
    def entry_fn(ind, px, prev_candle, is_bull):
        return fn_a(ind, px, prev_candle, is_bull) or fn_b(ind, px, prev_candle, is_bull)
    return entry_fn

def make_and3(fn_a, fn_b, fn_c):
    def entry_fn(ind, px, prev_candle, is_bull):
        return (fn_a(ind, px, prev_candle, is_bull) and
                fn_b(ind, px, prev_candle, is_bull) and
                fn_c(ind, px, prev_candle, is_bull))
    return entry_fn

def make_or_and(fn_or_a, fn_or_b, fn_and):
    """(A OR B) AND C"""
    def entry_fn(ind, px, prev_candle, is_bull):
        return ((fn_or_a(ind, px, prev_candle, is_bull) or
                 fn_or_b(ind, px, prev_candle, is_bull)) and
                fn_and(ind, px, prev_candle, is_bull))
    return entry_fn


if __name__ == '__main__':
    data = load_data()
    results = []
    t0 = time.time()
    configs = []

    e_v28 = v28_entry
    e_ema20_5 = make_ema_cross(20, 0.005)
    e_ema20_20 = make_ema_cross(20, 0.020)

    # Add quality gates to top Phase 4 winners
    # 1. (v28 OR ema20_t0.02) AND quality_gate
    base_or = make_or(e_v28, e_ema20_20)
    for gate_name, gate_fn in [
        ('rsi14le50', make_rsi_filter(14, 50)),
        ('rsi14le60', make_rsi_filter(14, 60)),
        ('atr1.0', make_atr_ratio_filter(1.0)),
        ('atr1.2', make_atr_ratio_filter(1.2)),
        ('spanb240', make_spanb_filter(240)),
        ('spanb120', make_spanb_filter(120)),
        ('vel6_ge0', make_velocity_filter(6, 0.0)),
    ]:
        label = f'p5_v28orEma20t02_AND_{gate_name}'
        entry = make_and(base_or, gate_fn)
        configs.append((label, entry, sma440_regime,
                        {'use_dd20d': True, 'use_rsi_rescue': True}))

    # 2. ema20_t0.005 AND atr≤1.2 AND quality_gate
    base_ema_atr = make_and(e_ema20_5, make_atr_ratio_filter(1.2))
    for gate_name, gate_fn in [
        ('rsi14le50', make_rsi_filter(14, 50)),
        ('rsi14le60', make_rsi_filter(14, 60)),
        ('spanb240', make_spanb_filter(240)),
        ('spanb120', make_spanb_filter(120)),
        ('vel6_ge0', make_velocity_filter(6, 0.0)),
    ]:
        label = f'p5_ema20atr12_AND_{gate_name}'
        entry = make_and(base_ema_atr, gate_fn)
        configs.append((label, entry, sma440_regime,
                        {'use_dd20d': True, 'use_rsi_rescue': True}))

    # 3. ema20_t0.005 AND spanb240 AND quality_gate
    base_ema_spanb = make_and(e_ema20_5, make_spanb_filter(240))
    for gate_name, gate_fn in [
        ('rsi14le50', make_rsi_filter(14, 50)),
        ('atr1.2', make_atr_ratio_filter(1.2)),
        ('vel6_ge0', make_velocity_filter(6, 0.0)),
    ]:
        label = f'p5_ema20spanb240_AND_{gate_name}'
        entry = make_and(base_ema_spanb, gate_fn)
        configs.append((label, entry, sma440_regime,
                        {'use_dd20d': True, 'use_rsi_rescue': True}))

    # 4. (v28 OR ema20_t0.02) AND atr (combine coverage with vol filter)
    for atr_t in [1.0, 1.2, 1.5]:
        label = f'p5_v28orEma20t02_AND_atr{atr_t}'
        entry = make_and(base_or, make_atr_ratio_filter(atr_t))
        configs.append((label, entry, sma440_regime,
                        {'use_dd20d': True, 'use_rsi_rescue': True}))

    print(f"\nPhase 5: {len(configs)} configs (three-indicator combinations)")
    print("=" * 100)

    for idx, (label, entry_fn, regime_fn, cfg) in enumerate(configs):
        t1 = time.time()
        r = run_backtest(data, entry_fn, regime_fn, config=cfg, label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        print(f"  [{idx+1:2d}/{len(configs)}] {label:<50} CAGR={r['cagr']:>7.1f}%  "
              f"trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase5_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    total_time = time.time() - t0
    zero_liq = sorted([r for r in results if r['liq'] == 0], key=lambda r: r['cagr'], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"Phase 5 complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 10 (0-liq, by CAGR):")
    for r in zero_liq[:10]:
        print(f"  {r['label']:<50} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
