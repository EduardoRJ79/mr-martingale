"""
MRM v3.0 — Phase 3: Alternative Regime Filters
================================================
Cross top entry indicators with alternative regime filters.
Include near-misses (1 liq, >30% CAGR) — a better regime might fix them.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from v30_indicators import load_data
from v30_engine import run_backtest
from v30_phase1 import (
    v28_entry, make_ema_crossunder_entry, make_pivot_break_entry,
    make_boll_lower_entry, make_donchian_break_entry, make_donchian_support_entry,
    make_rsi_entry, make_spanb_entry, sma440_regime,
)
import numpy as np


# ── Alternative regime functions ─────────────────────────────────────────────

def make_spanb_regime(period):
    key = f'span_b_{period}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

def make_gauss_mid_regime(period, mult=1.0):
    key = f'gauss_mid_{period}_{mult}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

def make_donchian_mid_regime(period):
    key = f'don_mid_{period}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

def make_ema200_regime():
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind['ema_200'][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

REGIME_FILTERS = [
    ('sma440', sma440_regime),
    ('spanb240', make_spanb_regime(240)),
    ('spanb350', make_spanb_regime(350)),
    ('gauss144', make_gauss_mid_regime(144, 1.0)),
    ('don_mid200', make_donchian_mid_regime(200)),
    ('ema200', make_ema200_regime()),
]


# ── Top entries from Phase 1/2 ──────────────────────────────────────────────
# 0-liq: v29_baseline (119.1%), don_break (3.5%)
# Near-misses (1 liq, high CAGR): ema20_cross (79.6%), ema50_cross (72.4%),
#   pivot_10 (60.2%), pivot_7 (51.6%), boll_20_2.0 (32.3%), boll_50_2.0 (19.7%)

TOP_ENTRIES = [
    ('v28_entry', v28_entry),
    ('ema20_cross', make_ema_crossunder_entry(20, 0.005)),
    ('ema50_cross', make_ema_crossunder_entry(50, 0.005)),
    ('pivot_10', make_pivot_break_entry(10)),
    ('pivot_7', make_pivot_break_entry(7)),
    ('boll_20_2.0', make_boll_lower_entry(20, 2.0)),
    ('boll_50_2.0', make_boll_lower_entry(50, 2.0)),
    ('don_supp_120_2pct', make_donchian_support_entry(120, 0.02)),
]

# dd20d modes to test per entry
DD20D_MODES = [
    ('dd_on', {'use_dd20d': True, 'use_rsi_rescue': True}),
    ('dd_off', {'use_dd20d': False, 'use_rsi_rescue': False}),
]


if __name__ == '__main__':
    data = load_data()
    results = []
    t0 = time.time()

    configs = []
    for entry_name, entry_fn in TOP_ENTRIES:
        for regime_name, regime_fn in REGIME_FILTERS:
            for dd_name, dd_cfg in DD20D_MODES:
                label = f"p3_{entry_name}__{regime_name}__{dd_name}"
                configs.append((label, entry_fn, regime_fn, dd_cfg))

    print(f"\nPhase 3: {len(configs)} configs (top entries x regimes x dd20d modes)")
    print("=" * 100)

    for idx, (label, entry_fn, regime_fn, dd_cfg) in enumerate(configs):
        t1 = time.time()
        r = run_backtest(data, entry_fn, regime_fn, config=dd_cfg, label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        print(f"  [{idx+1:3d}/{len(configs)}] {label:<55} CAGR={r['cagr']:>7.1f}%  "
              f"trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase3_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    total_time = time.time() - t0
    zero_liq = [r for r in results if r['liq'] == 0]
    zero_liq_sorted = sorted(zero_liq, key=lambda r: r['cagr'], reverse=True)

    print(f"\n{'=' * 100}")
    print(f"Phase 3 complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 15 (0-liq, by CAGR):")
    for r in zero_liq_sorted[:15]:
        print(f"  {r['label']:<55} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
    print(f"\nResults saved to {out_path}")
