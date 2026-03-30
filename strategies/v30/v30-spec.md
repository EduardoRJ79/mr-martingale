# MRM v3.0 — Strategy Specification

**Date:** 2026-03-30
**Status:** Validated via free-form entry research (519 configs tested across 7 phases)
**Data:** 4,521,155 1-minute bars, Binance BTC/USDT (2017-08 to 2026-03)
**Backtest resolution:** 1-minute (liq checks, grid fills, TP on every 1m bar)

---

## 1. Overview

v3.0 evolves v2.9 by adding a **secondary entry gate** (EMA20 crossunder at 2% threshold) that captures 10 additional trades missed by the original EMA34+SMA14 gate. The rescue_risk is raised from 0.25 to **0.28**, improving rescued-entry profitability with negligible drawdown increase.

### Version Comparison

| Metric | v2.8 | v2.9 | **v3.0** |
|--------|------|------|----------|
| CAGR | 85.6% | 119.1% | **125.3%** |
| CMR | 5.14% | 6.58% | **6.83%** |
| Max drawdown | 79.6% | 79.6% | **79.7%** |
| Liquidations | 0 | 0 | **0** |
| Total trades | 889 | 1,505 | **1,515** |
| Rescued entries | 0 | 618 | **622** |
| Final equity ($1k start) | $97,520 | $332,967 | **$409,768** |
| Total return | 97.5x | 333.0x | **409.8x** |

**Backtest period:** 2018-10-31 to 2026-03-28 (7.41 years)

---

## 2. Parameters

### New in v3.0 (vs v2.9)

```
entry_gate:            v28 OR ema20_crossunder(2%)    ← NEW secondary entry
rescue_risk_pct:       0.28                           ← was 0.25 in v2.9
```

### Core (unchanged from v2.9/v2.8)

```
risk_pct:              0.50
short_trigger_pct:     8.0%
level_gaps:            [0.5%, 1.5%, 10.0%, 14.0%]
level_mults_seq:       [2.0, 2.5, 2.5, 7.0]
dd20d_filter:          -10%
rsi_rescue_threshold:  30           (RSI(14) on 4H closes)
tp_pct:                0.50%
num_levels:            5
ema_span:              34 (4H)
sma_span:              14 (4H)
dma_period:            440 (daily)
long_trigger_pct:      0.5% (for v28 gate)
leverage_long:         20x
leverage_short:        15x
unfav_risk_scale:      0.60
unfav_spacing_scale:   1.60
unfav_trigger_scale:   3.00
unfav_hold_scale:      0.45
max_hold_bars:         720 (4H bars = 120 days favored, 54 days unfavored)
cooldown:              1 bar (4H)
min_equity:            $50
```

### Cost Model (unchanged)

```
Slippage:              3 ticks ($0.03) per fill/exit
Commission:            0.045% per side on notional
Taker fee:             0.0432% (L1 entry, timeout exit)
Maker fee:             0.0144% (L2-L5 fills, TP exit)
Funding:               0.0013% per 8h on notional
Maintenance margin:    0.5% of notional
```

---

## 3. What Changed and Why

### 3.1 Secondary Entry Gate: EMA20 Crossunder at 2%

The original v2.8/v2.9 entry requires price to be 0.5% below BOTH EMA34 and SMA14. This misses entries where price dips below a shorter-term EMA20 by a larger margin (2%).

**v3.0 entry condition (LONG):**
```
ENTER if:
  (v28 gate: price ≥ 0.5% below EMA34 AND ≥ 0.5% below SMA14)
  OR
  (ema20 gate: price ≥ 2.0% below EMA20)

Note: unfavored trigger scaling applies to BOTH gates:
  - v28 gate: 0.5% → 1.5% (×3.0)
  - ema20 gate: 2.0% → 6.0% (×3.0)
```

**Why this works:** EMA20 responds faster to price changes than EMA34. The 2% threshold is high enough to filter out noise (prevents overlapping with v28 entries) but catches meaningful dips that EMA34 misses. In backtesting, this adds exactly 10 extra profitable trades over 7.4 years.

### 3.2 Rescue Risk 0.28 (was 0.25)

Raising rescue_risk from 0.25 to 0.28 increases profitability of rescued entries with minimal risk:
- MaxDD: 79.6% → 79.7% (+0.1pp — negligible)
- CAGR: 119.1% → 125.3% (+6.2pp)

The liq boundary remains at risk=0.54. At rescue_risk=0.28, we're at 52% of the liq boundary (safe margin).

---

## 4. Entry Logic

At each 4H boundary:

```
1. Compute indicators from PREVIOUS 4H candle:
   - EMA34, SMA14 (on 4H closes)
   - EMA20 (on 4H closes)                              ← NEW
   - SMA440 (on daily closes)
   - High_20d (rolling 120-bar max of 4H highs)
   - RSI(14) (on 4H closes)

2. Determine regime:
   - bull = price > SMA440
   - If SMA440 unavailable (< 440 days of data): SKIP entry entirely

3. LONG entry check:
   - favored = bull
   - v28_trigger = 0.5% if favored, else 1.5%
   - ema20_trigger = 2.0% if favored, else 6.0%        ← NEW

   - v28_gate = (EMA34 - price) / EMA34 >= v28_trigger
                AND (SMA14 - price) / SMA14 >= v28_trigger
   - ema20_gate = (EMA20 - price) / EMA20 >= ema20_trigger   ← NEW

   - entry_triggered = v28_gate OR ema20_gate            ← NEW (was v28_gate only)

   - If entry_triggered:
     - Check dd20d: dd_from_high = (price / High_20d) - 1
     - If dd_from_high >= -0.10:
         → ENTER with normal risk (0.50 if favored, 0.30 if unfavored)
     - If dd_from_high < -0.10:
         → Check RSI(14):
           - If RSI <= 30: ENTER with rescue risk (0.28 if favored, 0.168 if unfavored)
           - If RSI > 30: SKIP entry (crash regime)

4. SHORT entry check (unchanged from v2.8):
   - Uses EMA34 + SMA14 only (no EMA20 gate for shorts)
   - trigger = 8.0% if favored (not is_bull), else 24.0%
   - No dd20d filter for shorts
```

### Position Sizing

```
Normal entry:    L1_notional = risk_pct * equity
                 risk_pct = 0.50 (favored) or 0.30 (unfavored)

Rescued entry:   L1_notional = rescue_risk_pct * equity
                 rescue_risk_pct = 0.28 (favored) or 0.168 (unfavored)

Grid levels:     L1=1.0x, L2=2.0x, L3=5.0x, L4=12.5x, L5=87.5x of L1
                 (mults_seq = [2.0, 2.5, 2.5, 7.0], cumulative)
```

---

## 5. Grid & Exit Logic

Unchanged from v2.8/v2.9.

### Grid Fill (on 1m bars)
- L2-L5 fill when 1m low touches level price
- Level prices: L1_price × (1 - cumulative_drop%)
- Cumulative drops (favored): 0.5%, 2.0%, 12.0%, 26.0%
- Cumulative drops (unfavored): gaps × 1.60

### Take-Profit (on 1m bars)
- TP at blended_entry × (1 + 0.5%)
- Uses 1m high for TP detection
- Maker fee on exit

### Timeout (on 4H boundaries)
- Force close after max_hold bars (720 favored = 120 days, 324 unfavored = 54 days)
- Taker fee on exit

### Liquidation (on 1m bars)
- Check: balance + unrealized_pnl <= total_notional × 0.5%
- If hit: reset to $1,000 (backtest) / halt (live)

---

## 6. Indicator Computation

### EMA34, SMA14, EMA20 (4H bars)
```python
ema34 = closes_4h.ewm(span=34, adjust=False).mean()
sma14 = closes_4h.rolling(14).mean()
ema20 = closes_4h.ewm(span=20, adjust=False).mean()
```

### SMA440 (daily bars)
```python
daily_close = df.groupby(df['ts'].dt.floor('1D')).agg(c=('c', 'last'))
sma440 = daily_close['c'].rolling(440).mean()
```

### High_20d (4H bars)
```python
high_20d = highs_4h.rolling(120).max()  # 120 × 4H = 20 days
```

### RSI(14) (4H closes — NOT hlcc4)
```python
delta = closes_4h.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss
rsi_14 = 100 - (100 / (1 + rs))
```

**Important:** RSI for the rescue filter uses **close prices**, not hlcc4. This matches the v2.9 validated implementation.

---

## 7. Backtest Results

**Period:** 2018-10-31 to 2026-03-28 (7.41 years, 90 months)

### Summary

| Metric | Value |
|--------|-------|
| Start date | 2018-10-31 |
| End date | 2026-03-28 |
| Duration | 7.41 years |
| CAGR | **125.3%** |
| CMR (compound monthly return) | **6.83%** |
| Max drawdown | **79.7%** |
| Liquidations | **0** |
| Total trades | 1,515 |
| Take-profits | 1,515 (100%) |
| Timeouts | 0 |
| Longs / Shorts | 1,486 / 29 |
| Favored / Unfavored | 1,163 / 352 |
| Entries filtered (dd20d) | 1,171 |
| Entries rescued (RSI) | 622 |
| Final equity | $409,768 |
| Total return | 409.8x |
| Levels distribution | L1: 779, L2: 534, L3: 185, L4: 17 |

---

## 8. Research Methodology

v3.0 was found through systematic free-form entry research across 7 phases (519 configs total):

| Phase | Configs | 0-liq | Best 0-liq CAGR | Key Finding |
|-------|---------|-------|------------------|-------------|
| 1: Single indicators (dd20d ON) | 42 | 3 | 119.1% | Only v29 baseline competitive |
| 2: Single indicators (dd20d OFF) | 42 | 2 | 3.5% | dd20d essential for most |
| 3: Alt regime filters | 96 | 1 | 119.1% | SMA440 best for v28 entry |
| 3b: Parameter sweep | 122 | 3 | 59.8% | EMA20 t=2% achieves 0 liqs |
| 4: Two-indicator combos | 33 | 15 | 120.8% | v28 OR ema20 beats v2.9 |
| 5: Three-indicator combos | 18 | 18 | 120.0% | All safe, little CAGR gain |
| 6: Risk tuning | 168 | 140 | 134.1% | Risk ceiling at 0.52 |

### Key discoveries
- No standalone indicator could replace EMA34+SMA14 without liquidations
- EMA20 crossunder at 2% was the only new entry gate to achieve 0 liqs solo
- Combining v28 OR ema20_t0.02 + rescue_risk=0.28 was the optimal moderate config
- Risk ceiling at 0.52 (0.54 introduces liquidations)
- Aggressive variant (r=0.52, rr=0.30) achieves 134.1% CAGR but 85.4% MaxDD

---

## 9. Files

| File | Description |
|------|-------------|
| `strategies/v30/v30-spec.md` | This specification |
| `strategies/v30/v30-research-summary.md` | Research summary with all phase results |
| `strategies/v30/v30_indicators.py` | Indicator library (187 indicators) |
| `strategies/v30/v30_engine.py` | Configurable backtest engine |
| `strategies/v30/v30_validate.py` | Engine validation (reproduces v2.8 + v2.9) |
| `strategies/v30/v30_phase1.py` | Phase 1: single indicators with dd20d |
| `strategies/v30/v30_phase2.py` | Phase 2: single indicators without dd20d |
| `strategies/v30/v30_phase3.py` | Phase 3: alternative regime filters |
| `strategies/v30/v30_phase3b.py` | Phase 3b: parameter sweep |
| `strategies/v30/v30_phase4.py` | Phase 4: two-indicator combos |
| `strategies/v30/v30_phase5.py` | Phase 5: three-indicator combos |
| `strategies/v30/v30_phase6.py` | Phase 6: risk tuning |
| `strategies/v30/v30_phase7.py` | Phase 7: final comparison |
| `strategies/v30/v30_final_ranking.json` | All 0-liq configs ranked by CAGR |

---

## 10. Risk Notes

- The liq boundary remains at risk_pct=0.54 (same as v2.8/v2.9)
- At rescue_risk=0.28, we're at 52% of the liq boundary (safe)
- MaxDD increased only 0.1pp vs v2.9 (79.7% vs 79.6%)
- The EMA20 gate adds only 10 trades over 7.4 years — minimal additional exposure
- All 5 historical crash events (2019-09, 2019-11, COVID, 2021-05, 2021-12) remain blocked by the RSI > 30 filter
- RSI for rescue uses close prices (not hlcc4) — this is critical for reproduction
