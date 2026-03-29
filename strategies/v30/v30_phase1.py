"""
MRM v3.0 — Phase 1: Single Indicators WITH dd20d + RSI rescue
================================================================
Test each entry indicator individually with SMA440 regime, dd20d ON, RSI rescue ON.
This is the baseline: same safety filters as v2.9, but different entry gates.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from v30_indicators import load_data
from v30_engine import run_backtest
import numpy as np


# ── Regime function ──────────────────────────────────────────────────────────

def sma440_regime(ind, px, prev_candle, sma440_val):
    if sma440_val is None or (isinstance(sma440_val, float) and np.isnan(sma440_val)):
        return True
    return px > sma440_val


# ── Entry function factories ─────────────────────────────────────────────────

def make_rsi_entry(period, threshold):
    """Enter when RSI <= threshold (oversold bounce)."""
    key = f'rsi_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val <= threshold
    return entry_fn

def make_stochrsi_entry(rsi_len, stoch_len, smooth_k, low=20, high=80):
    """Enter when StochRSI K <= low (oversold) or K >= high (momentum)."""
    key = f'stoch_k_{rsi_len}_{stoch_len}_{smooth_k}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and (val <= low or val >= high)
    return entry_fn

def make_spanb_entry(period):
    """Enter when price > Span B (support held)."""
    key = f'span_b_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_chandelier_entry(period, mult):
    """Enter when price > chandelier stop (uptrend)."""
    key = f'chand_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_gauss_lower_entry(period, mult):
    """Enter when price <= gaussian lower band (mean reversion)."""
    key = f'gauss_lower_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px <= val
    return entry_fn

def make_donchian_break_entry(period):
    """Enter when price > N-bar high (breakout)."""
    key = f'don_high_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_donchian_support_entry(period, pct=0.02):
    """Enter when price is within pct of N-bar low (bounce from support)."""
    key = f'don_low_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        if np.isnan(val) or val <= 0:
            return False
        return (px / val - 1) <= pct
    return entry_fn

def make_boll_lower_entry(period, mult):
    """Enter when price <= Bollinger lower band (mean reversion)."""
    key = f'boll_lower_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px <= val
    return entry_fn

def make_atr_ratio_entry(threshold=0.8):
    """Enter when ATR ratio <= threshold (low vol = calm market)."""
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind['atr_ratio_14_60'][prev_candle]
        return not np.isnan(val) and val <= threshold
    return entry_fn

def make_velocity_entry(period, threshold=0.0):
    """Enter when velocity >= threshold (price rising or stable)."""
    key = f'velocity_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val >= threshold
    return entry_fn

def make_pivot_break_entry(lookback):
    """Enter when price > last pivot high price (structural breakout)."""
    key = f'last_pivot_price_{lookback}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_ema_crossunder_entry(period, trigger_pct=0.005):
    """Enter when price is trigger_pct below EMA(N) — MA dip buy."""
    key = f'ema_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        ema = ind[key][prev_candle]
        if np.isnan(ema) or ema <= 0:
            return False
        trigger = trigger_pct if is_bull else trigger_pct * 3.0
        return (ema - px) / ema >= trigger
    return entry_fn

def make_price_above_sma_entry(period):
    """Enter when price > SMA(N) — simple trend filter."""
    key = f'sma_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

# v2.8 baseline for comparison
def v28_entry(ind, px, prev_candle, is_bull):
    ema = ind['ema34'][prev_candle]
    sma = ind['sma14'][prev_candle]
    if np.isnan(ema) or np.isnan(sma):
        return False
    trigger = 0.005 if is_bull else 0.015
    return (ema - px) / ema >= trigger and (sma - px) / sma >= trigger


# ── Config grid ──────────────────────────────────────────────────────────────

PHASE1_CONFIGS = [
    # v2.9 baseline (v2.8 entry + dd20d + RSI rescue)
    ('p1_v29_baseline', v28_entry),

    # RSI oversold
    ('p1_rsi7_le25', make_rsi_entry(7, 25)),
    ('p1_rsi7_le30', make_rsi_entry(7, 30)),
    ('p1_rsi14_le25', make_rsi_entry(14, 25)),
    ('p1_rsi14_le30', make_rsi_entry(14, 30)),
    ('p1_rsi14_le35', make_rsi_entry(14, 35)),
    ('p1_rsi14_le40', make_rsi_entry(14, 40)),
    ('p1_rsi21_le30', make_rsi_entry(21, 30)),

    # StochRSI extremes
    ('p1_stoch_4_7_3', make_stochrsi_entry(4, 7, 3)),
    ('p1_stoch_14_14_3', make_stochrsi_entry(14, 14, 3)),
    ('p1_stoch_11_7_20', make_stochrsi_entry(11, 7, 20)),

    # Span B support
    ('p1_spanb_120', make_spanb_entry(120)),
    ('p1_spanb_240', make_spanb_entry(240)),
    ('p1_spanb_350', make_spanb_entry(350)),

    # Chandelier uptrend
    ('p1_chand_22_3', make_chandelier_entry(22, 3.0)),
    ('p1_chand_44_3', make_chandelier_entry(44, 3.0)),
    ('p1_chand_71_3.9', make_chandelier_entry(71, 3.9)),

    # Gaussian lower band (mean reversion)
    ('p1_gauss_91_1.0', make_gauss_lower_entry(91, 1.0)),
    ('p1_gauss_144_1.5', make_gauss_lower_entry(144, 1.5)),
    ('p1_gauss_266_1.9', make_gauss_lower_entry(266, 1.9)),

    # Donchian breakout
    ('p1_don_break_120', make_donchian_break_entry(120)),
    ('p1_don_break_200', make_donchian_break_entry(200)),

    # Donchian support (bounce)
    ('p1_don_supp_120_2pct', make_donchian_support_entry(120, 0.02)),
    ('p1_don_supp_120_5pct', make_donchian_support_entry(120, 0.05)),

    # Bollinger lower band
    ('p1_boll_20_2.0', make_boll_lower_entry(20, 2.0)),
    ('p1_boll_20_2.5', make_boll_lower_entry(20, 2.5)),
    ('p1_boll_50_2.0', make_boll_lower_entry(50, 2.0)),

    # ATR ratio
    ('p1_atr_ratio_0.6', make_atr_ratio_entry(0.6)),
    ('p1_atr_ratio_0.8', make_atr_ratio_entry(0.8)),
    ('p1_atr_ratio_1.0', make_atr_ratio_entry(1.0)),

    # Velocity
    ('p1_vel_6_0pct', make_velocity_entry(6, 0.0)),
    ('p1_vel_12_0pct', make_velocity_entry(12, 0.0)),
    ('p1_vel_24_neg1pct', make_velocity_entry(24, -0.01)),

    # Pivot breakout
    ('p1_pivot_5', make_pivot_break_entry(5)),
    ('p1_pivot_7', make_pivot_break_entry(7)),
    ('p1_pivot_10', make_pivot_break_entry(10)),

    # EMA crossunder (different periods)
    ('p1_ema20_cross', make_ema_crossunder_entry(20, 0.005)),
    ('p1_ema50_cross', make_ema_crossunder_entry(50, 0.005)),
    ('p1_ema100_cross', make_ema_crossunder_entry(100, 0.005)),
    ('p1_ema200_cross', make_ema_crossunder_entry(200, 0.005)),

    # Price above SMA (trend)
    ('p1_above_sma50', make_price_above_sma_entry(50)),
    ('p1_above_sma200', make_price_above_sma_entry(200)),
]


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    data = load_data()
    results = []
    t0 = time.time()

    print(f"\nPhase 1: {len(PHASE1_CONFIGS)} configs (dd20d ON, RSI rescue ON, SMA440 regime)")
    print("=" * 90)

    for idx, (label, entry_fn) in enumerate(PHASE1_CONFIGS):
        t1 = time.time()
        r = run_backtest(data, entry_fn, sma440_regime,
                         config={'use_dd20d': True, 'use_rsi_rescue': True},
                         label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        print(f"  [{idx+1:2d}/{len(PHASE1_CONFIGS)}] {label:<30} CAGR={r['cagr']:>7.1f}%  "
              f"MaxDD={r['max_dd']:>5.1f}%  trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase1_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    total_time = time.time() - t0
    zero_liq = [r for r in results if r['liq'] == 0]
    zero_liq_sorted = sorted(zero_liq, key=lambda r: r['cagr'], reverse=True)

    print(f"\n{'=' * 90}")
    print(f"Phase 1 complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 10 (0-liq, by CAGR):")
    for r in zero_liq_sorted[:10]:
        print(f"  {r['label']:<30} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
    print(f"\nResults saved to {out_path}")
