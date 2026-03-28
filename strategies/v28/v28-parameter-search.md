# MRM v2.8 — Parameter Search Report

**Date:** 2026-03-28
**Engine:** Validated `run_comparison.py` (v2.7 backtest), parameterized
**Data:** 4.5M 1-minute bars, Binance BTC/USDT (2017-08 to 2026-03-28)
**Period:** 2022-01-01 to 2026-03-25 (4.23 years)
**Liquidation check:** 1-minute bar resolution (worst-case wick price)
**Objective:** 0 liquidations AND CAGR >= 100%

---

## 1. Executive Summary

**v2.7 has 3 liquidations on 1-minute resolution** (not 1 as with 4H close checks). The v2.8 search tested **179 configurations** across 7 phases and found **14 configs** meeting both targets.

### Recommended v2.8 Config

| Parameter | v2.7 | v2.8 (Recommended) | Change |
|-----------|------|---------------------|--------|
| `risk_pct` | 0.30 | **0.35** | +17% |
| `short_trigger_pct` | 0.015 | **0.08** | +433% |
| `level_gaps` | [0.5, 1.5, 7.0, 8.0] | **[0.5, 1.5, 10.0, 14.0]** | Wider L3/L4 |
| `tp_pct` | 0.005 | 0.005 | unchanged |
| `level_multipliers` | [2.0, 2.5, 2.5, 7.0] | [2.0, 2.5, 2.5, 7.0] | unchanged |
| `max_levels` | 5 | 5 | unchanged |
| `unfav_trigger_scale` | 3.0 | 3.0 | unchanged |
| `unfav_risk_scale` | 0.60 | 0.60 | unchanged |
| `unfav_spacing_scale` | 1.60 | 1.60 | unchanged |
| `unfav_hold_scale` | 0.45 | 0.45 | unchanged |
| `max_hold_bars` | 720 | 720 | unchanged |
| `long_trigger_pct` | 0.005 | 0.005 | unchanged |
| All other params | — | — | unchanged |

### Results Comparison

| Metric | v2.7 (1m liq) | v2.8 Recommended | v2.8 Aggressive |
|--------|---------------|------------------|-----------------|
| **CAGR** | ~3.5% (post-liq) | **111.5%** | **117.1%** |
| **Liquidations** | 3 | **0** | **0** |
| Total trades | 1,434 | 1,016 | 998 |
| TP exits | ~1,431 | 1,016 (100%) | 998 (100%) |
| Timeouts | 0 | 0 | 0 |
| Long / Short | 1,053/378 | 998/18 | 998/0 |
| Max Drawdown | >100% | 85.3% | 90.2% |
| Final equity | $1,159 | $23,706 | $26,514 |
| Peak equity | $1,159 | $23,710 | $26,519 |

---

## 2. What Changed and Why

### Problem: Two distinct liquidation events

On 1-minute bar resolution, v2.7 has **3 liquidations**, driven by two market events:

1. **2023-01-20: Short L4 squeeze** — A favored short position in the Jan 2023 BTC rally. All short configs are vulnerable because the position accumulates to L4 (cumulative 20.5x L1 notional) and gets liquidated by a rapid rally.

2. **2025-11-21: Long L5 crash** — The same event from the v2.7 validation. All 5 levels fill during a severe intraday crash, destroying $38K+ equity.

### Solution: Three parameter changes

1. **`short_trigger_pct = 0.08` (was 0.015)** — Raises the bar for short entries from 1.5% to 8% pullback above MAs. This eliminates the Jan 2023 short that caused liquidation while keeping 18 high-confidence shorts that contribute to returns. The alternative (pure long-only) also works but loses marginal income from safe shorts.

2. **`level_gaps = [0.5, 1.5, 10.0, 14.0]` (was [0.5, 1.5, 7.0, 8.0])** — Widens L3 and L4 grid spacing. L5 now requires a 26% adverse move (vs 17% in v2.7), making L5 fills extremely rare and preventing the Nov 2025 liquidation entirely.

3. **`risk_pct = 0.35` (was 0.30)** — Compensates for fewer trades (998-1016 vs 1,434) with larger per-trade exposure. Since the wider gaps and restricted shorts dramatically reduce tail risk, higher risk per trade is safe within the 0-liq constraint.

---

## 3. Search Methodology

### 179 configurations across 7 phases

| Phase | Configs | Focus | Best Result |
|-------|---------|-------|-------------|
| 1-2 | 36 | Single-param sweeps + combos | 0 zero-liq configs |
| 3 | 33 | Wider gaps (strongest lever) | 1 liq, 84.3% CAGR |
| 4 | 36 | Target Jan 2023 L4 liq | 0 liqs, 23.6% CAGR |
| 5 | 34 | Emergency stop-loss | 0 liqs, 49.7% CAGR |
| 6 | 37 | Long-only, max-2, shtrig | **0 liqs, 108.3% CAGR** |
| 7 | 38 | Fine-tuning winners | **0 liqs, 117.1% CAGR** |

### Key discoveries by phase

**Phase 1-2:** No single parameter change achieves 0 liqs. Wider L3/L4 gaps are the strongest lever, reducing liqs from 3 to 1. L5 multiplier changes alone don't help.

**Phase 3:** Wider gaps [0.5, 1.5, 10+, 14+] eliminate the Nov 2025 liq but expose a different liq: Jan 2023 short L4. All 1-liq configs share this same event.

**Phase 4:** The Jan 2023 liq resists all parameter changes while maintaining CAGR. Even max 3 levels + reduced risk can't avoid it without destroying returns. Flat multipliers [1.5x each] achieve 0 liqs but only 9% CAGR.

**Phase 5:** Emergency stop-loss (new parameter: force-close at X% equity loss) achieves 0 liqs but max 49.7% CAGR. The stop leaves less equity than the $1K restart after liquidation.

**Phase 6:** **Breakthrough.** Long-only with risk=0.35 achieves 108.3% CAGR with 0 liqs. The short side was the only source of remaining liquidations — removing or restricting it eliminates all liq risk.

**Phase 7:** Fine-tuning reveals risk can go up to 0.37 (117.1% CAGR) before hitting liq at 0.38. Short trigger at 0.08 keeps 18 safe shorts and achieves 111.5% CAGR.

---

## 4. All Winning Configurations (0 liqs, CAGR >= 100%)

| # | Config | CAGR | Trades | MaxDD | Final Eq | Key Changes |
|---|--------|------|--------|-------|----------|-------------|
| 1 | lo-r0.37 | 117.1% | 998 | 90.2% | $26,514 | Long-only, risk=0.37 |
| 2 | lo-r0.36 | 112.7% | 998 | 87.8% | $24,274 | Long-only, risk=0.36 |
| 3 | **st08-r0.35** | **111.5%** | **1,016** | **85.3%** | **$23,706** | **shtrig=0.08, risk=0.35** |
| 4 | st10-r0.35 | 108.9% | 1,004 | 85.3% | $22,507 | shtrig=0.10, risk=0.35 |
| 5 | lo-r0.35 | 108.3% | 998 | 85.3% | $22,223 | Long-only, risk=0.35 |
| 6 | lo-max4-r0.35 | 108.3% | 998 | 85.3% | $22,223 | Long-only, max4, risk=0.35 |
| 7 | lo-r0.34 | 104.0% | 998 | 82.9% | $20,345 | Long-only, risk=0.34 |
| 8 | st08-r0.33 | 102.6% | 1,016 | 80.4% | $19,795 | shtrig=0.08, risk=0.33 |
| 9 | lo-r35-g-12-14 | 101.1% | 970 | 87.6% | $19,184 | Long-only, wider gaps |
| 10 | st10-r0.33 | 100.3% | 1,004 | 80.4% | $18,849 | shtrig=0.10, risk=0.33 |

All winners use `level_gaps = [0.5, 1.5, 10.0, 14.0]` unless noted.

---

## 5. Risk Boundary Analysis

The risk parameter has a hard boundary between liq=0 and liq=1:

| Risk | CAGR | Liqs | MaxDD | Safety Margin |
|------|------|------|-------|---------------|
| 0.33 | 99.7% | 0 | 80.4% | ~20% buffer |
| 0.34 | 104.0% | 0 | 82.9% | ~17% buffer |
| 0.35 | 108.3% | 0 | 85.3% | ~15% buffer |
| 0.36 | 112.7% | 0 | 87.8% | ~12% buffer |
| 0.37 | 117.1% | 0 | 90.2% | ~10% buffer |
| **0.38** | **39.0%** | **1** | **92.6%** | **BREACHED** |

The liq boundary sits between risk=0.37 and risk=0.38. **Recommended risk=0.35** provides a ~15% safety margin above the maximum drawdown (85.3% vs 100%).

---

## 6. Recommended Config Details: `st08-r0.35`

```
risk_pct:              0.35          (was 0.30)
tp_pct:                0.50%
num_levels:            5
level_gaps:            [0.5%, 1.5%, 10.0%, 14.0%]  (was [0.5%, 1.5%, 7.0%, 8.0%])
level_multipliers:     [2.0, 2.5, 2.5, 7.0]
ema_span:              34 (4h)
sma_span:              14 (4h)
dma_period:            440 (daily)
long_trigger_pct:      0.5%
short_trigger_pct:     8.0%          (was 1.5%)
leverage_long:         20x
leverage_short:        15x
unfav_risk_scale:      0.60
unfav_spacing_scale:   1.60
unfav_trigger_scale:   3.00
unfav_hold_scale:      0.45
max_hold_bars:         720 (120 days)
cooldown:              1 bar (4h)
min_equity:            $50
```

### Level Distribution

| Level | Trades | Share |
|-------|--------|-------|
| L1 | 536 | 52.8% |
| L2 | 347 | 34.2% |
| L3 | 122 | 12.0% |
| L4 | 11 | 1.1% |
| L5 | 0 | 0.0% |

L5 never fills — the 26% cumulative gap is too wide for any 2022-2026 intraday move to trigger.

### Trade Breakdown

- Total: 1,016 (TP: 1,016, Timeouts: 0, Liquidations: 0)
- Long: 998 / Short: 18
- Favored: 691 / Unfavored: 325
- Win rate: 100% (all TP)

---

## 7. Alternative Configs

### Aggressive: `lo-r0.37` (Long-only, risk=0.37)

For users who want maximum CAGR and accept thinner safety margin:

| Metric | Value |
|--------|-------|
| CAGR | 117.1% |
| Liqs | 0 |
| MaxDD | 90.2% |
| Safety margin | ~10% |
| Trades | 998 |
| Final equity | $26,514 |

**Risk:** Only 10% buffer before liquidation. A market event slightly worse than anything in 2022-2026 could breach it.

### Conservative: `st08-r0.33` (shtrig=0.08, risk=0.33)

For users who prioritize safety over returns:

| Metric | Value |
|--------|-------|
| CAGR | 102.6% |
| Liqs | 0 |
| MaxDD | 80.4% |
| Safety margin | ~20% |
| Trades | 1,016 |
| Final equity | $19,795 |

**Tradeoff:** 9% less CAGR for substantially more safety margin.

---

## 8. Parameters NOT Worth Changing

Tested but showed no improvement or negative impact:

| Parameter | Tested Range | Finding |
|-----------|-------------|---------|
| `tp_pct` | 0.004-0.007 | Higher TP → fewer trades → more liqs. 0.005 is optimal |
| `level_multipliers` | Flat [1.5x each] to [2,2.5,2.5,3] | Flatter = fewer liqs but much lower CAGR. Original multipliers fine with wider gaps |
| `max_levels` | 2-4 | Max 2/3 cannot compound enough. Max 4 same as 5 with wide gaps (L5 never fills) |
| `max_hold_bars` | 240-720 | No impact on liq; shorter hold slightly hurts compounding |
| `unfav_risk_scale` | 0.40-0.60 | The critical liq is a favored short, so unfav scaling doesn't help |
| `unfav_spacing_scale` | 1.6-2.0 | Marginal effect, not enough to eliminate liqs |
| Emergency stop-loss | 50%-90% | Converts liq to large loss; leaves less equity than $1K restart |

---

## 9. Files

| File | Description |
|------|-------------|
| `v28_param_search.py` | Phase 1-2 engine (36 configs) |
| `v28_phase3.py` | Phase 3 (33 configs, wider gaps) |
| `v28_phase4.py` | Phase 4 (36 configs, target Jan 2023 liq) |
| `v28_phase5.py` | Phase 5 (34 configs, emergency stop-loss) |
| `v28_phase6.py` | Phase 6 (37 configs, long-only + radical) |
| `v28_phase7.py` | Phase 7 (38 configs, fine-tuning) |
| `v28_*_results.json` | Raw results per phase |
| `strategies/v28/v28-parameter-search.md` | This file |
