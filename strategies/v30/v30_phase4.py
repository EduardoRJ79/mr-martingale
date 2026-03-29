"""
MRM v3.0 — Phase 4: Two-Indicator Combinations
================================================
Combine 0-liq entries with high-CAGR near-miss entries using AND/OR logic.
Goal: get higher CAGR than the 0-liq singles while maintaining 0 liqs.

0-liq entries: v28_entry (119.1%), ema20_t0.02 (59.8%)
Near-misses: ema20_t0.005 (79.6%), ema50 (72.4%), pivot_10 (60.2%)
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from v30_indicators import load_data
from v30_engine import run_backtest
from v30_phase1 import sma440_regime
import numpy as np


# ── Entry factories ──────────────────────────────────────────────────────────

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
        if np.isnan(ema) or ema <= 0:
            return False
        trigger = trigger_pct if is_bull else trigger_pct * 3.0
        return (ema - px) / ema >= trigger
    return entry_fn

def make_pivot_break(lookback):
    key = f'last_pivot_price_{lookback}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_rsi_filter(period, threshold):
    """RSI <= threshold as a filter (oversold confirmation)."""
    key = f'rsi_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val <= threshold
    return entry_fn

def make_boll_lower(period, mult):
    key = f'boll_lower_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px <= val
    return entry_fn

def make_atr_ratio_filter(threshold):
    """ATR ratio <= threshold (low vol calm market)."""
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind['atr_ratio_14_60'][prev_candle]
        return not np.isnan(val) and val <= threshold
    return entry_fn

def make_spanb_filter(period):
    """Price > Span B as confirmation."""
    key = f'span_b_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn


# ── Combinators ──────────────────────────────────────────────────────────────

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

    # Base entries
    e_v28 = v28_entry
    e_ema20_5 = make_ema_cross(20, 0.005)
    e_ema20_20 = make_ema_cross(20, 0.020)
    e_ema50_5 = make_ema_cross(50, 0.005)
    e_pivot10 = make_pivot_break(10)
    e_pivot7 = make_pivot_break(7)
    e_boll20 = make_boll_lower(20, 2.0)

    # Filters
    f_rsi14_30 = make_rsi_filter(14, 30)
    f_rsi14_40 = make_rsi_filter(14, 40)
    f_rsi14_50 = make_rsi_filter(14, 50)
    f_rsi7_30 = make_rsi_filter(7, 30)
    f_atr_08 = make_atr_ratio_filter(0.8)
    f_atr_10 = make_atr_ratio_filter(1.0)
    f_atr_12 = make_atr_ratio_filter(1.2)
    f_spanb240 = make_spanb_filter(240)
    f_spanb120 = make_spanb_filter(120)

    # ── OR combos (more trades) ──────────────────────────────────────────
    # v28 OR ema20_t0.02 (both 0-liq, combine for more trades)
    configs.append(('p4_v28_OR_ema20t02', make_or(e_v28, e_ema20_20),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True, 'dd20d_threshold': -0.12}))
    configs.append(('p4_v28_OR_ema20t02_dd10', make_or(e_v28, e_ema20_20),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True, 'dd20d_threshold': -0.10}))

    # v28 OR pivot (pivot has great CAGR but 1 liq)
    configs.append(('p4_v28_OR_pivot10', make_or(e_v28, e_pivot10),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))
    configs.append(('p4_v28_OR_pivot7', make_or(e_v28, e_pivot7),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # v28 OR boll20 (boll has 1 liq but 32% CAGR)
    configs.append(('p4_v28_OR_boll20', make_or(e_v28, e_boll20),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # ema20_t0.02 OR pivot10
    configs.append(('p4_ema20t02_OR_pivot10', make_or(e_ema20_20, e_pivot10),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True, 'dd20d_threshold': -0.12}))
    configs.append(('p4_ema20t02_OR_pivot10_dd10', make_or(e_ema20_20, e_pivot10),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # ── AND combos (more selective, fewer trades) ────────────────────────
    # EMA crossunder AND RSI oversold (confirm dip is oversold)
    for rsi_thresh in [30, 40, 50]:
        configs.append((f'p4_ema20t5_AND_rsi14le{rsi_thresh}',
                        make_and(e_ema20_5, make_rsi_filter(14, rsi_thresh)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))
        configs.append((f'p4_ema50t5_AND_rsi14le{rsi_thresh}',
                        make_and(e_ema50_5, make_rsi_filter(14, rsi_thresh)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # EMA crossunder AND ATR ratio (low vol confirmation)
    for atr_t in [0.8, 1.0, 1.2]:
        configs.append((f'p4_ema20t5_AND_atr{atr_t}',
                        make_and(e_ema20_5, make_atr_ratio_filter(atr_t)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # EMA crossunder AND Span B (price above support)
    configs.append(('p4_ema20t5_AND_spanb240', make_and(e_ema20_5, f_spanb240),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))
    configs.append(('p4_ema20t5_AND_spanb120', make_and(e_ema20_5, f_spanb120),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))
    configs.append(('p4_ema50t5_AND_spanb240', make_and(e_ema50_5, f_spanb240),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # v28 AND RSI filter (more selective v2.8)
    for rsi_thresh in [40, 50, 60]:
        configs.append((f'p4_v28_AND_rsi14le{rsi_thresh}',
                        make_and(e_v28, make_rsi_filter(14, rsi_thresh)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # Pivot AND RSI (filter dangerous pivot entries)
    for rsi_thresh in [40, 50, 60]:
        configs.append((f'p4_pivot10_AND_rsi14le{rsi_thresh}',
                        make_and(e_pivot10, make_rsi_filter(14, rsi_thresh)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # Pivot AND ATR ratio
    for atr_t in [0.8, 1.0, 1.2]:
        configs.append((f'p4_pivot10_AND_atr{atr_t}',
                        make_and(e_pivot10, make_atr_ratio_filter(atr_t)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # Pivot AND Span B
    configs.append(('p4_pivot10_AND_spanb240', make_and(e_pivot10, f_spanb240),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))
    configs.append(('p4_pivot10_AND_spanb120', make_and(e_pivot10, f_spanb120),
                    sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    # Boll AND RSI (confirm mean reversion with oversold)
    for rsi_thresh in [30, 40, 50]:
        configs.append((f'p4_boll20_AND_rsi14le{rsi_thresh}',
                        make_and(e_boll20, make_rsi_filter(14, rsi_thresh)),
                        sma440_regime, {'use_dd20d': True, 'use_rsi_rescue': True}))

    print(f"\nPhase 4: {len(configs)} configs (two-indicator combinations)")
    print("=" * 100)

    for idx, (label, entry_fn, regime_fn, cfg) in enumerate(configs):
        t1 = time.time()
        r = run_backtest(data, entry_fn, regime_fn, config=cfg, label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        print(f"  [{idx+1:2d}/{len(configs)}] {label:<45} CAGR={r['cagr']:>7.1f}%  "
              f"trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase4_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    total_time = time.time() - t0
    zero_liq = [r for r in results if r['liq'] == 0]
    zero_liq_sorted = sorted(zero_liq, key=lambda r: r['cagr'], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"Phase 4 complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 15 (0-liq, by CAGR):")
    for r in zero_liq_sorted[:15]:
        print(f"  {r['label']:<45} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
    print(f"\nResults saved to {out_path}")
