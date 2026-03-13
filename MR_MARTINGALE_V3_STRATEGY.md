# Mr Martingale v3.0 Strategy

**Status:** Current leading research candidate / paper target / not yet live deployed  
**Date:** 2026-03-12  
**Bot family:** Mr Martingale  
**Strategy type:** True-compounding, no-stop, spacing-controlled mean-reversion with soft regime bias and exact-liq validation

---

## 1. Why v3.0 exists

Mr Martingale accumulated several research branches under the v2 label. That became confusing because:
- some earlier results were later invalidated by stricter liquidation modeling,
- some later branches (v2.2, v2.3) were tested and rejected,
- the actual current winner is a corrected and re-optimized descendant of v2.1.

So this file promotes the **current winning branch** to **v3.0** as the canonical research strategy.

---

## 2. Plain-English Summary

Mr Martingale v3.0 is a high-risk BTC mean-reversion strategy built around:
- **true compounding**,
- **no stop-loss**,
- **spacing-only liquidation control**,
- **5 ladder levels**,
- a **440-day SMA** macro filter,
- **soft regime bias** rather than hard side-blocking,
- and an **exact-liq validated** geometry.

The strategy is designed to maximize CAGR **without liquidating**, not to minimize intra-trade discomfort.

---

## 3. Core Parameters

```yaml
risk_pct_per_entry: 25%
levels: 5
regime_filter: 440d SMA
regime_mode: soft_bias
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
stop_loss: none
```

---

## 4. Design logic

### 4.1 True compounding
Each new trade sizes from current equity:

```text
base_btc = risk_pct × equity / price
```

This keeps the strategy proportional as the account grows or shrinks.

### 4.2 No stop-loss
v3.0 does not use a stop-loss. Risk is controlled by:
- long-DMA regime context,
- degraded unfavored-side behavior,
- spacing geometry,
- per-level multiplier geometry,
- time-based exit discipline,
- exact-liq tested sizing.

### 4.3 Soft bias beats hard gating
The unfavored side is not disabled. It is weakened:
- lower risk,
- wider spacing,
- stricter trigger,
- shorter hold.

This outperformed both:
- hard gating,
- more complex regime-machine classification.

### 4.4 Five levels are still the best current structure
4L underperformed. 5L remained the best current topology after exact-liq re-optimization.

---

## 5. Geometry

### Level gaps
```yaml
[0.5, 1.5, 9.0, 6.0]
```

This corrected the earlier `[0.5, 1.5, 8.0, 7.0]` profile. Moving one point of depth earlier in the deep ladder materially improved the exact-liq result.

### Per-level multipliers
```yaml
[2.0, 2.5, 2.5, 7.0]
```

This replaced the earlier `[1.5, 2.0, 3.0, 5.0]` branch under exact-liq truth.

---

## 6. Regime filter

The current winning regime filter is:
- **440-day SMA**

The system uses this as a macro anchor, then applies **soft bias** rather than binary enable/disable behavior.

The v2.2 attempt to create a richer regime machine (trend/chop/accumulation/distribution/transition) did **not** improve the strategy and has been rejected.

---

## 7. Risk and liquidation truth

The major lesson of the latest research cycle was that optimistic backtests are not enough.

v3.0 is based on **exact liquidation simulation**.
That invalidated an earlier 30% risk branch and led to the corrected v3.0 geometry at **25% risk**.

So v3.0 is not just “the latest idea.” It is the latest idea that survived the stricter truth standard.

---

## 8. Performance

### Effective test window
- 2019-01-03 → 2026-03-09

### Headline result
- **CAGR:** 189.0% fair / ~194.4% optimized peak
- **Final equity:** ~$1,000 → ~$1.81M
- **Liquidations:** 0
- **Max drawdown:** 34.8%
- **Trades:** 3,043

This meaningfully beat the corrected prior baseline of 81.9% CAGR while also reducing drawdown.

---

## 9. What did NOT survive

### Rejected branch 1
- 30% risk / earlier v2 optimization result
- failed under exact-liq simulation

### Rejected branch 2 — v2.2
- regime machine / multi-state classifier
- more nuance, less money

### Rejected branch 3 — v2.3
- favored-side amplification
- no candidate beat baseline without reintroducing liquidation

---

## 10. Deployment status

v3.0 is the current canonical **research and paper target**.
It is **not yet the live production bot**.

The live bot remains on the v1 family until an explicit migration decision is made.

---

## Appendix — Version lineage

For the full branch-by-branch evolution of the strategy, see:
- `MR_MARTINGALE_VERSION_HISTORY.md`

---

## 11. Recommended next operating posture

- treat v3.0 as the current canonical strategy
- keep paper bot aligned to the v3.0 parameters
- only move toward live cutover after operational paper validation
- continue future improvements as v3.x branches, not as more confusing v2 sub-labels
