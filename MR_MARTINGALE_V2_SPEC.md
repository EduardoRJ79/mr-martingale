# Mr Martingale v2 — Proposed Strategy Spec

**Status:** Proposed / researched / paper implementation running / **not live deployed**  
**Date:** 2026-03-12  
**Purpose:** Memorialize the current leading exact-liquidation v2.1 candidate.

---

## Summary

This document captures the current best candidate for the next major version of Mr Martingale.

This is **not** the currently running live bot. The live bot remains unchanged.

The current winning research branch is:
- **true compounding** (entry size recalculated from current equity)
- **no stop-loss**
- **spacing-only liquidation control**
- **5 ladder levels**
- **soft-bias regime model**
- **440DMA regime filter** (current optimized winner)
- **exact liquidation simulation validated**

The current leading candidate from the corrected exact-liq optimization loop is:

```yaml
version_name: Mr Martingale v2.1
mode: true_compounding
stop_loss: none
risk_pct_per_entry: 25%
levels: 5
regime_filter: 440d SMA
soft_bias:
  unfav_risk_scale: 0.65
  unfav_spacing_scale: 1.50
  unfav_trigger_scale: 1.50
  unfav_hold_scale: 0.50
level_gaps: [0.5, 1.5, 9.0, 6.0]
per_level_multipliers: [2.0, 2.5, 2.5, 7.0]
long_trigger_pct: 0.5
short_trigger_pct: 1.5
max_hold_bars: 160
objective: max CAGR with exact-liq zero-liquidation constraint
```

---

## Research Result

### Backtest window
- **Effective start:** 2019-01-03
- **End:** 2026-03-09
- **Reason 2019 start:** long-DMA warmup requirement (2018+ policy still honored methodologically)

### Headline result
- **CAGR:** 189.0% (fair DMA=400 comparison); ~194% at the optimized 440DMA peak
- **Final equity:** ~$1,000 → ~$1.81M on the fair-comparison run
- **Liquidations:** 0
- **Max drawdown:** 34.8%
- **Trades:** 3,043

### Interpretation
The earlier 30% risk / 320.5% CAGR branch was invalidated by stricter exact-liquidation simulation. The corrected winner keeps the same family (true compounding, no stop, soft bias) but lowers risk, improves spacing/multipliers, lowers the short trigger, and materially outperforms the corrected 81.9% baseline while preserving zero liquidations.

---

## Current Recommendation

If/when this version is implemented, it should be introduced as a **new bot version**, not as an in-place silent mutation of the existing live bot.

Recommended rollout path:
1. run as a separate paper bot (already in progress)
2. monitor and validate operations
3. compare directly against the current live bot behavior
4. only then consider live migration

---

## Explicit Non-Claim

This document does **not** mean the current live bot has already been upgraded.
It only records the best current research candidate.

Live bot remains on the existing version until a deliberate implementation and cutover happens.
