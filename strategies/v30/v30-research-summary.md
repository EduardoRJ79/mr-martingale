# MRM v3.0 — Free-Form Entry Research Summary

**Date:** 2026-03-30
**Configs tested:** 519 unique combinations
**Zero-liquidation configs found:** 180
**Configs beating v2.9 (119.1% CAGR):** 20

---

## Chosen v3.0 Configuration

**v28 OR ema20_t0.02, risk_pct=0.50, rescue_risk_pct=0.28** (moderate scenario)

| Metric | Value |
|--------|-------|
| Backtest period | 2018-10-31 to 2026-03-28 (7.41 years) |
| Resolution | 1-minute (4,521,155 bars) |
| CAGR | **125.3%** |
| CMR | **6.83%** |
| Max drawdown | **79.7%** |
| Liquidations | **0** |
| Trades | 1,515 |
| Final equity | $409,768 (409.8x) |

See `v30-spec.md` for full specification.

---

## Key Finding

The v2.8 EMA34+SMA14 entry gate remains the strongest single entry indicator. No standalone alternative achieved 0 liquidations with competitive CAGR. However, **risk tuning and entry-gate combinations** yielded 20 configs that beat v2.9's 119.1% CAGR while maintaining 0 liquidations.

## Top 5 Candidates (all 0 liquidations)

| Rank | Config | CAGR | MaxDD | Trades |
|------|--------|------|-------|--------|
| 1 | v28 OR ema20_t0.02, r=0.52, rr=0.30 | **134.1%** | 85.4% | 1,515 |
| 2 | v28 baseline, r=0.52, rr=0.30 | **132.1%** | 85.4% | 1,505 |
| 3 | v28 OR ema20_t0.02, r=0.52, rr=0.28 | **131.0%** | 82.7% | 1,515 |
| 4 | v28 baseline, r=0.52, rr=0.28 | **129.0%** | 82.7% | 1,505 |
| 5 | v28 OR ema20_t0.02, r=0.50, rr=0.28 | **125.3%** | 79.7% | 1,515 |

## v2.9 Baseline for Comparison

| Metric | v2.9 | Best v3.0 Candidate |
|--------|------|---------------------|
| CAGR | 119.1% | **134.1%** (+15pp) |
| MaxDD | 79.6% | 85.4% (+5.8pp) |
| Trades | 1,505 | 1,515 (+10) |
| Liquidations | 0 | 0 |

## What Worked

1. **Risk tuning** was the biggest lever: raising risk_pct from 0.50 to 0.52 and rescue_risk from 0.25 to 0.28-0.30 boosted CAGR by 10-15pp while maintaining 0 liqs. 0.54 introduced liquidations.

2. **v28 OR ema20_t0.02** entry combination marginally outperformed v28 alone by capturing 10 extra trades from the wider EMA20 crossunder gate.

3. **EMA20 AND ATR ratio <= 1.2** was the best non-v28 entry: 121.2% CAGR (with r=0.50, rr=0.30), confirming that low-volatility dip entries are profitable.

4. **Three-indicator combos** were universally safe (18/18 zero liqs in Phase 5) but added complexity without significant CAGR improvement.

## What Did NOT Work

1. **Single standalone indicators** (RSI, StochRSI, SpanB, Chandelier, Gaussian, Velocity, Price>SMA): All produced multiple liquidations, even with dd20d + RSI rescue.

2. **Alternative regime filters** (SpanB, Gaussian mid, Donchian mid, EMA200): All underperformed SMA440 when paired with the v28 entry. SMA440 is uniquely suited to this strategy.

3. **Pivot breakout entries**: Consistently had exactly 1 liquidation across ALL parameter combinations, regime filters, and risk levels. A single crash event (likely the COVID crash or a similar V-shaped drop) is incompatible with the breakout-after-new-high logic.

4. **Wider grid spacing**: Increased liquidations rather than preventing them.

## dd20d Filter Verdict

dd20d is **essential** for the v28 entry (prevents 9 liquidations). For other entry indicators, dd20d reduces but doesn't eliminate liquidations. The RSI rescue filter adds significant value by recovering 618 blocked entries with reduced risk.

## Recommendation

**Conservative (lower drawdown):** Keep v2.9 parameters unchanged (r=0.50, rr=0.25 = 119.1% CAGR, 79.6% MaxDD).

**Moderate:** v28 OR ema20_t0.02 with r=0.50, rr=0.28 = **125.3% CAGR**, 79.7% MaxDD. Marginal drawdown increase for +6pp CAGR.

**Aggressive:** v28 OR ema20_t0.02 with r=0.52, rr=0.30 = **134.1% CAGR**, 85.4% MaxDD. Trades +5.8pp MaxDD for +15pp CAGR.

## Phases Summary

| Phase | Configs | 0-liq | Best 0-liq CAGR | Key Finding |
|-------|---------|-------|------------------|-------------|
| 1: Single indicators (dd20d ON) | 42 | 3 | 119.1% | Only v29 baseline competitive |
| 2: Single indicators (dd20d OFF) | 42 | 2 | 3.5% | dd20d essential for most |
| 3: Alt regime filters | 96 | 1 | 119.1% | SMA440 best for v28 entry |
| 3b: Parameter sweep | 122 | 3 | 59.8% | EMA20 t=2% achieves 0 liqs |
| 4: Two-indicator combos | 33 | 15 | 120.8% | v28 OR ema20 beats v2.9 |
| 5: Three-indicator combos | 18 | 18 | 120.0% | All safe, little CAGR gain |
| 6: Risk tuning | 168 | 140 | 134.1% | Risk ceiling at 0.52 |
