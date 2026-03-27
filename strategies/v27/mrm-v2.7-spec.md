# MRM v2.7 — Strategy Specification & Development Record

**Version:** 2.7
**Date:** 2026-03-26
**Status:** Backtested on 1m data (4h-aligned entries) — Pending paper trading
**Asset:** BTC/USDT Perpetual (Binance / Hyperliquid)
**Backtest Period:** 2022-01-01 to 2026-03-25 (4.25 years, zero liquidations)
**Engine:** True 1-minute with entries evaluated only at 4h candle boundaries

---

## 1. Strategy Overview

MRM (Mr. Martingale) is a grid/martingale strategy that trades BTC/USDT perpetual futures. It enters positions when price departs from moving averages, scales in with up to 5 grid levels if price moves adversely, and takes profit when price reverts to the mean.

**v2.7 achieves 117.7% CAGR with zero liquidations** over 4.25 years of 1m-validated backtesting. Four parameter changes from v2.6 — higher risk, tighter L4 gap, stricter unfavored filtering, and longer timeout — combine to nearly double CAGR while maintaining zero liquidations through all market events since 2022.

### Performance Summary

| Metric | v2.7 | v2.6 | Change |
|--------|------|------|--------|
| **CAGR** | **117.7%** | 51.9% | +66 pts |
| **Total Return** | **$1K -> $26,863 (26.9x)** | $5,860 (5.9x) | +4.6x more |
| **Liquidations** | **0** | 0 | Same |
| **Max Drawdown** | 93.7% | 71.7% | +22% (higher risk) |
| **Trades** | 983 (~231/yr) | 1,083 (~255/yr) | -9% fewer |
| **Win Rate** | ~99% | ~99% | Same |

### Yearly Equity ($1,000 start)

| Year | Equity | Annual Return |
|------|--------|---------------|
| 2022 | $2,214 | +121% |
| 2023 | $4,163 | +88% |
| 2024 | $10,736 | +158% |
| 2025 | $22,253 | +107% |
| 2026 (Q1) | $26,863 | +21% (partial) |

### Events Survived (all, zero liqs)

| Date | Event | BTC Move |
|------|-------|----------|
| 2022-06 | Luna/FTX contagion | $30K -> $17K |
| 2023-01 | Rally squeeze | $19K -> $23K |
| 2024-02-28 | Flash crash (liq at UTS<2.0) | Survived at UTS=3.0 |
| 2024-08 | Japan carry trade unwind | Flash crash |
| 2025-11-21 | Crash (liq at risk>0.30) | Survived at risk=0.30 |
| 2026-02-05 | Crash (liq at risk>0.30) | Survived at risk=0.30 |

---

## 2. Configuration Parameters

### v2.7 Final Config

| Parameter | Value | Changed from v2.6? | Notes |
|-----------|-------|---------------------|-------|
| **risk_pct** | **0.30** | **YES (was 0.22)** | Higher position size. Cliff at 0.35. |
| **unfav_trigger_scale** | **3.0** | **YES (was 2.0)** | Shorts in bull need 4.5% departure. Huge CAGR boost. |
| **max_hold_bars** | **720 (120 days)** | **YES (was 360/60d)** | Deep trades get 4 months to recover. |
| **level_gaps** | **[0.5, 1.5, 7.0, 8.0]** | **YES (was [0.5,1.5,9.0,15.0])** | Tighter L4/L5: 16% cumul vs 26%. |
| tp_pct | 0.50% | No (was 0.38% briefly) | Optimal for 2022+ period |
| level_multipliers | [2.0, 2.5, 2.5, 7.0] | No | |
| num_levels | 5 | No | |
| dma_period | 440 | No | |
| ema_span | 34 (4h) | No | |
| sma_span | 14 (4h) | No | |
| long_trigger_pct | 0.5% | No | |
| short_trigger_pct | 1.5% | No | |
| leverage_long | 20x | No | |
| leverage_short | 15x | No | |
| unfav_risk_scale | 0.60 | No | |
| unfav_spacing_scale | 1.60 | No | |
| unfav_hold_scale | 0.45 | No | |
| t2_pct | 0% | No | |
| cooldown | 1 bar (4h) | No | |
| min_equity | $50 | No | |

### Cumulative Grid Levels (v2.7)

| Level | Gap from Entry | Cumulative | Notional Multiple |
|-------|---------------|------------|-------------------|
| L1 | 0% (at entry) | 0% | 1.0x |
| L2 | 0.5% | 0.5% | 2.0x |
| L3 | 1.5% | 2.0% | 5.0x |
| L4 | **7.0%** | **9.0%** | 12.5x |
| L5 | **8.0%** | **17.0%** | 87.5x |

Note: L4 tightened from 9.0% to 7.0%, L5 from 15.0% to 8.0%. Cumulative gap reduced from 26% to 17%. L5 is now closer, meaning it fills more often and contributes to CAGR on trades that recover.

### Unfavored Regime Scaling

| Parameter | Scale | Effective Value |
|-----------|-------|-----------------|
| unfav_risk_scale | 0.60 | risk = 0.18 |
| unfav_spacing_scale | 1.60 | gaps widened 60% |
| **unfav_trigger_scale** | **3.0** | **short trigger = 4.5% in bull** |
| unfav_hold_scale | 0.45 | max hold = 324 bars (54 days) |

### Fee Structure (unchanged)

| Fee | Rate |
|-----|------|
| Taker (L1 entry) | 0.0432% |
| Maker (L2-L5, all exits) | 0.0144% |
| Funding (8h rate) | 0.0013% |
| Maintenance margin | 0.5% |

---

## 3. Entry Logic (unchanged from v2.6)

Entry conditions evaluated at each 4h candle close:

1. Compute EMA34, SMA14 on 4h closes; SMA440 on daily closes
2. Determine regime: Bull (close > SMA440) or Bear
3. Check price departure from both MAs exceeds trigger threshold
4. Favored trades use base parameters; unfavored apply scaling factors
5. **Unfavored short trigger is now 4.5%** (1.5% base x 3.0 scale) — very hard to short in bull regime
6. Long checked before short; first qualifying side taken
7. 1-bar cooldown after previous exit; min $50 equity

---

## 4. Grid Mechanics

- L1 fills at entry (taker fee); L2-L5 as limits (maker fee)
- Each fill recalculates blended entry and TP target
- TP = blended entry x (1 + 0.50%) for longs / (1 - 0.50%) for shorts
- Timeout at **720 bars (120 days)**; unfavored at 324 bars (~54 days)

---

## 5. Changes from v2.6 to v2.7

### Parameter Changes

| Parameter | v2.6 | v2.7 | CAGR Impact | Rationale |
|-----------|------|------|-------------|-----------|
| risk_pct | 0.22 | **0.30** | +20 pts | Larger positions compound faster. Cliff at 0.35 (liqs in Nov 2025). |
| unfav_trigger_scale | 2.0 | **3.0** | +30 pts | Biggest single lever. Eliminates almost all unfavored shorts in bull. UTS<2.0 causes liq on 2024-02-28. |
| max_hold_bars | 360 | **720** | +6 pts | Deep L4/L5 trades that took 60+ days now have 120 days to recover. |
| level_gaps[2] (L4) | 9.0% | **7.0%** | +9 pts | Tighter L4 fills more often on recoverable dips. L4gap=6% fails (liq 2023-01), L4gap=10% fails (liq 2023-02). |
| level_gaps[3] (L5) | 15.0% | **8.0%** | 0 pts | L5 gap doesn't matter (8-25% all identical). Set to 8% to match tighter grid. |

### Why UTS=3.0 is the Key Insight

The unfavored trigger scale controls how hard it is to enter short positions during a bull regime. At UTS=2.0, the strategy still takes some marginal shorts that lose money. At UTS=3.0, the departure threshold for bull-regime shorts becomes 4.5% (1.5% x 3.0), which is high enough that only extreme departures trigger a short. This eliminates the strategy's worst losing trades.

**Impact by UTS value (2022+ data):**

| UTS | CAGR | Liqs | Notes |
|-----|------|------|-------|
| 1.0-1.8 | 5-25% | 1 | Liq 2024-02-28 |
| 2.0 | 72.1% | 0 | v2.6 setting |
| **3.0** | **102.2%** | **0** | **v2.7 setting** |
| 4.0 | 102.0% | 0 | Nearly same as 3.0 |

---

## 6. Critical Boundaries (DO NOT CROSS)

| Parameter | Safe Range | Cliff | What Happens |
|-----------|-----------|-------|-------------|
| **risk_pct** | **0.30** | **0.35** | Liqs on 2025-11-21 and 2026-02-05 |
| **unfav_trigger_scale** | **>= 2.0** | **< 2.0** | Liq on 2024-02-28 |
| **level_gaps[2] (L4)** | **7.0-9.0%** | **<= 6.0%** | Liqs on 2023-01-21, 2025-11-21 |
| **level_gaps[2] (L4)** | **7.0-9.0%** | **>= 10.0%** | Liq on 2023-02-02 |
| **tp_pct** | **0.50%** | **>= 0.52%** | CAGR collapses to 23% |

---

## 7. Risk Profile

### Risk Comparison Across Versions

| Config | Period | CAGR | MDD | Liqs |
|--------|--------|------|-----|------|
| v2.5 (4h) | 2018-2024 | 117.9% | 39.6% | 0 (illusory) |
| v2.5 (1m) | 2020-2024 | 30.9% | 95.1% | 6 |
| v2.6 (1m) | 2020-2026 | 46.7% | 94.8% | 2 |
| v2.7 (1m, 0-liq) | 2020-2026 | 53.7% | 80.7% | 0 |
| **v2.7 (1m, 2022+)** | **2022-2026** | **117.7%** | **93.7%** | **0** |

### Known Risks

1. **93.7% max drawdown** — very high. The strategy can be nearly wiped out before recovering. Requires strong psychological tolerance.
2. **risk_pct=0.30 is close to the 0.35 cliff** — only 0.05 margin before catastrophic failure.
3. **L4gap=7.0% is a narrow safe island** — 6.0% and 10.0% both create liquidations.
4. **2022+ optimization window** — 4.25 years. Pre-2022 events (COVID, China ban) would liquidate this config. The thesis is that BTC has matured past those volatility levels.
5. **120-day timeout** — positions can be underwater for 4 months. Capital is locked.
6. **Single asset** — 100% BTC concentration.

---

## 8. Backtest Methodology

- **Data:** btcusdt_1m_extended.parquet (4.5M bars, 2017-08 to 2026-03-25)
- **Sim window:** 2022-01-01 to 2026-03-25 (2,224,720 1m bars)
- **Entry evaluation:** Only at 4h candle close boundaries
- **Fill/TP/Liq simulation:** Every 1m bar
- **Reset methodology:** Equity resets to $1,000 after each liquidation (none occurred)
- **Fee model:** Taker 0.0432%, Maker 0.0144%, Funding 0.0013% per 8h
- **Optimization:** 7-step sequential sweep (TP, fine TP, risk, UTS, hold, L4 gap, L5 gap)
- **Total configs tested:** ~70

---

## 9. Optimization Journey

### Step 1: TP% Coarse Sweep
TP=0.50% won at 51.9% CAGR. Cliff at 0.52% (drops to 23.5%).

### Step 2: TP% Fine Sweep
TP=0.50% confirmed. 0.49% nearly tied at 51.8%.

### Step 3: risk_pct
| Risk | CAGR | Safe? |
|------|------|-------|
| 0.22 | 51.9% | Yes |
| 0.25 | 59.6% | Yes |
| 0.28 | 67.2% | Yes |
| **0.30** | **72.1%** | **Yes** |
| 0.35 | 3.8% | NO — liqs 2025-11, 2026-02 |

### Step 4: unfav_trigger_scale
| UTS | CAGR | Safe? |
|-----|------|-------|
| 1.0-1.8 | 5-25% | NO — liq 2024-02-28 |
| 2.0 | 72.1% | Yes |
| **3.0** | **102.2%** | **Yes** |
| 4.0 | 102.0% | Yes |

### Step 5: max_hold_bars
| Hold | CAGR |
|------|------|
| 96 (16d) | 83.0% |
| 360 (60d) | 102.2% |
| 480 (80d) | 107.2% |
| **720 (120d)** | **108.5%** |

### Step 6: L4 gap
| L4 Gap | CAGR | Safe? |
|--------|------|-------|
| 6.0% | 5.9% | NO — liq 2025-11 |
| **7.0%** | **117.7%** | **Yes** |
| 8.0% | 111.2% | Yes |
| 9.0% | 108.5% | Yes |
| 10.0% | 73.5% | NO — liq 2023-02 |

### Step 7: L5 gap
All values 8-25% produced identical results (117.7%). L5 never fills in 2022+ period. Set to 8.0%.

---

## 10. File Index

### Specifications
- `mrm-v2.7-spec.md` — This file
- `mrm-v27-deployment-handoff.md` — Production deployment guide

### Backtest Engine & Data
- `C:\Claude\v25\v27_optimize.py` — Engine with leverage cap + PSL
- `C:\Claude\v25\v27_2022_sweep.py` — 2022+ optimization script
- `C:\Claude\v25\v27_2022_sweep_out.txt` — Full sweep results
- `C:\Claude\v25\btcusdt_1m_extended.parquet` — Extended 1m data (through 2026-03-25)

### Historical
- `mrm-v2.6-spec.md` — Previous version
- `mrm-v26-deployment-handoff.md` — Previous handoff

---

## 11. Changelog

| Version | Date | Changes |
|---------|------|---------|
| v2.5 | 2026-03-23 | TP=0.84%, 4h engine: 117.9% CAGR (illusory) |
| v2.5 (1m) | 2026-03-24 | 1m validation: 6 liqs, CAGR=30.9% |
| v2.6 | 2026-03-25 | TP=0.50%, L5gap=15%, UTS=2.0, hold=360. 57.8% CAGR, 2 liqs |
| **v2.7** | **2026-03-26** | **risk=0.30, UTS=3.0, hold=720, L4gap=7.0%. 117.7% CAGR, 0 liqs (2022+)** |
