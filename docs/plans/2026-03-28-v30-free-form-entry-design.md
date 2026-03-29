# MRM v3.0 — Free-Form Entry Research Design

**Date:** 2026-03-28
**Objective:** Beat v2.9 (119.1% CAGR, 0 liqs, 79.6% MaxDD) by replacing EMA34/SMA14 entry gate with other indicators or combinations. Full sweep includes alternative regime filters.

---

## 1. Architecture

### File Structure

```
strategies/v30/
  v30_engine.py              # Reusable backtest with configurable entry/regime callbacks
  v30_indicators.py          # All indicator computations on 4H bars
  v30_phase1.py              # Single indicators WITH dd20d + RSI rescue
  v30_phase2.py              # Single indicators WITHOUT dd20d
  v30_phase3.py              # Single indicators + alternative regime filters
  v30_phase3b.py             # Param sweep on top performers from P1-3
  v30_phase4.py              # Two-indicator combos
  v30_phase5.py              # Three-indicator combos
  v30_phase6.py              # Risk tuning
  v30_phase7.py              # Final comparison vs v2.9
  v30_phase{N}_results.json  # Results per phase
  v30-spec.md                # Final v3.0 spec (if winner found)
  v30-research-summary.md    # Full research narrative
```

### Engine Module (`v30_engine.py`)

Extracted from `backtest_v28.py`. The simulation loop, grid logic, exit logic, cost model, and 1-minute liq checks are **identical**. Only two things are configurable:

```python
config = {
    # Callbacks
    'entry_fn':          Callable[[dict, float, int], bool],  # (indicators, price, prev_candle) -> enter?
    'regime_fn':         Callable[[dict, float, int], bool],  # (indicators, price, prev_candle) -> is_bull?

    # Safety filters
    'use_dd20d':         True,
    'dd20d_threshold':   -0.10,
    'use_rsi_rescue':    True,
    'rsi_rescue_thresh': 30,

    # Risk
    'risk_pct':          0.50,
    'rescue_risk_pct':   0.25,

    # All other params unchanged from v2.8
    'tp_pct':            0.005,
    'level_gaps':        [0.5, 1.5, 10.0, 14.0],
    'level_mults':       [2.0, 2.5, 2.5, 7.0],
    'max_levels':        5,
    'long_trigger_pct':  None,   # Not used — entry_fn replaces MA triggers
    'short_trigger_pct': 0.08,   # Shorts unchanged
    'unfav_risk_scale':  0.60,
    'unfav_spacing_scale': 1.60,
    'unfav_trigger_scale': 3.00,
    'unfav_hold_scale':  0.45,
    'max_hold_bars':     720,
    'min_equity':        50,
}
```

The engine returns a result dict:
```python
{
    'cagr': float, 'cmr': float, 'max_dd': float,
    'liquidations': int, 'trades': int, 'long_count': int,
    'short_count': int, 'fav_count': int, 'unfav_count': int,
    'filtered_count': int, 'rescued_count': int,
    'final_equity': float, 'total_return': float,
    'level_dist': dict, 'liq_events': list,
}
```

### Indicator Library (`v30_indicators.py`)

Computes all indicators once on 4H bars, returns dict of numpy arrays.

| Indicator | Formula | Params to sweep |
|-----------|---------|-----------------|
| RSI | Wilder RSI on hlcc4 | period: 7, 10, 14, 21 |
| StochRSI | RSI → Stochastic(K,D) | rsi_len: 4-14, stoch_len: 7-18, k: 3-20, d: 3-19 |
| Span B | (HH + LL) / 2 | period: 120, 240, 300, 350, 462 |
| Chandelier Stop | High - ATR(N)*mult | atr_len: 22-71, mult: 2.0-3.9 |
| Gaussian Channel | SMA(N) ± ATR(N)*mult | sampling: 91-300, mult: 0.75-1.90 |
| Donchian Channel | N-bar high / low | period: 56, 120, 168, 230 |
| Bollinger Bands | SMA(N) ± std(N)*mult | period: 20-50, mult: 1.5-2.5 |
| ATR ratio | ATR(short) / ATR(long) | short: 14, long: 60-120 |
| Price velocity | ROC(N) | period: 6, 12, 24, 48 |
| Pivot High | ta.pivothigh(lookback) | lookback: 5-10 |
| EMA/SMA | Various crossovers | periods: 20, 50, 100, 200 |
| SMA440 | Daily close SMA | period: 440 (fixed) |
| High_20d | Rolling max of 4H highs | window: 120 (fixed) |

---

## 2. Entry Indicator Definitions

Each entry indicator is a function `(indicators, price, prev_candle) -> bool`:

| ID | Entry = True when... | Params |
|----|---------------------|--------|
| `rsi_oversold` | RSI(N) <= threshold | period, threshold |
| `stochrsi_extreme` | StochRSI K <= low OR K >= high | rsi_len, stoch_len, k, d, low, high |
| `spanb_above` | price > Span B(N) | period |
| `chandelier_up` | price > chandelier_stop(N, mult) | atr_len, mult |
| `gauss_lower` | price <= gaussian_lower_band(N, mult) | sampling, mult |
| `donchian_break` | price > donchian_high(N) | period |
| `donchian_support` | price - donchian_low(N) <= threshold% | period, threshold |
| `boll_lower` | price <= bollinger_lower(N, mult) | period, mult |
| `atr_ratio_low` | ATR(short)/ATR(long) <= threshold | short, long, threshold |
| `velocity_pos` | ROC(N) >= threshold | period, threshold |
| `pivot_break` | new pivot high above prev pivot | lookback |
| `ema_crossunder` | (EMA(N) - price) / EMA(N) >= trigger% | period, trigger |
| `price_above_sma` | price > SMA(N) | period |

---

## 3. Regime Filter Options

| ID | Bull = True when... | Params |
|----|---------------------|--------|
| `sma440` | price > daily SMA(440) | (fixed) |
| `spanb_regime` | price > Span B(N) on 4H | period: 240, 300, 350 |
| `gauss_mid` | price > gaussian_mid(N) on 4H | sampling: 91, 144, 200 |
| `donchian_mid` | price > (donchian_high + donchian_low)/2 | period: 120, 200 |
| `ema200` | price > EMA(200) on 4H | (fixed) |

---

## 4. Phase Plan

### Phase 1: Single indicators WITH dd20d + RSI rescue (~40 configs)

Each indicator tested as sole entry trigger. SMA440 regime. dd20d ON, RSI rescue ON.
Sweep 2-3 param values per indicator.

Purpose: Which indicators work with the existing safety net?

### Phase 2: Same indicators WITHOUT dd20d (~40 configs)

Same configs as Phase 1 but dd20d OFF, RSI rescue OFF.
Side-by-side comparison produces dd20d verdict per indicator:
- "dd20d essential": 0 liqs WITH dd20d, >0 liqs WITHOUT
- "dd20d helpful": 0 liqs both, better CAGR WITH dd20d
- "dd20d unnecessary": 0 liqs both, same or better CAGR WITHOUT
- "dd20d harmful": 0 liqs both, significantly better CAGR WITHOUT

### Phase 3: Single indicators + alt regime filters (~80 configs)

Top 5 entry indicators from P1/P2, crossed with 4 alt regime filters.
Tests whether SMA440 can be beaten.

Output: best regime per indicator.

### Phase 3.5: Parameter sweep on top performers (~60 configs)

Top 5-10 indicators/combos with 0 liqs from P1-3.
Sweep 4-6 values per key parameter (period, threshold).
Purpose: refine without overfitting.

### Phase 4: Two-indicator combinations (~60 configs)

Best single indicators from P1-3.5, each with optimal dd20d mode and regime filter.
Combine as AND (both must confirm) or OR (either triggers).

### Phase 5: Three-indicator combinations (~30 configs)

P4 winners + third indicator as quality gate.

### Phase 6: Risk tuning (~40 configs)

Sweep risk_pct (0.40-0.54 step 0.02) and rescue_risk (0.15-0.30 step 0.03) on top 0-liq configs.

### Phase 7: Final comparison (~10 configs)

Rank all 0-liq configs vs v2.9. Present v3.0 candidate with full stats.

**Total: ~360 configs**

---

## 5. Constraints

- 0 liquidations on full period (2018-10 to 2026-03)
- 1-minute bar liq checks (identical to v2.8)
- Same cost model (slippage, commission, funding)
- Same grid/exit logic
- All indicators on 4H bars from previous candle (no lookahead)
- Short entry logic unchanged from v2.8

---

## 6. Success Criteria

v3.0 candidate must:
1. 0 liquidations over full 7.41-year period
2. CAGR > 119.1% (beat v2.9) OR MaxDD < 79.6% at comparable CAGR
3. Reasonable trade count (>500 to avoid overfitting to few trades)

If no config beats v2.9, the research still has value: it confirms EMA34/SMA14 is optimal and documents the search space explored.
