# Mr Martingale v2 Strategy

**Status:** Proposed / researched / paper implementation running / not yet live deployed  
**Date:** 2026-03-12  
**Bot family:** Mr Martingale  
**Strategy type:** True-compounding, no-stop, spacing-controlled mean-reversion with soft regime bias

---

## 1. Plain-English Summary

Mr Martingale v2.1 is the current leading research candidate for the next major version of the strategy.

The core idea is:
- trade mean reversion,
- size every new trade as a fixed fraction of current equity,
- use ladder spacing (not stop-loss) to control liquidation risk,
- keep a 5-level ladder,
- use a long-term regime filter,
- bias toward the favored macro side without fully disabling the opposite side,
- and use exact liquidation simulation as the truth standard for optimization.

This version is explicitly designed for the **high-risk portfolio** and optimized for:
1. **maximum long-run CAGR**,
2. **true compounding**,
3. **zero liquidations under exact-liq simulation**,
4. **no stop-loss**.

It is not the currently running live bot. It is the leading researched successor.

---

## 2. Current Recommended Parameters

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
objective: maximize CAGR with exact zero-liquidation constraint
```

---

## 3. Core Design Principles

### 3.1 True compounding
Every new trade recalculates entry size from current equity:

```text
base_btc = risk_pct × equity / price
```

If equity rises, the next trade gets larger. If equity falls, the next trade gets smaller.

### 3.2 No stop-loss
This strategy does **not** use a stop-loss. Risk is controlled by:
- regime filter,
- soft side-biasing,
- ladder geometry,
- proportional sizing,
- time-based exit logic.

### 3.3 Exact liquidation simulation is mandatory
All current claims are now grounded in exact liquidation simulation. Earlier optimistic results that did not survive this stricter standard have been superseded.

### 3.4 Soft bias, not hard gating
The current winning branch does not fully disable the unfavored side. Instead, the unfavored side is degraded:
- lower risk
- wider spacing
- stricter trigger
- shorter hold

This materially outperformed hard gating.

### 3.5 5 levels remain optimal in the current family
The best exact-liq winner still uses 5 levels, but with corrected spacing and per-level multipliers.

---

## 4. Regime Filter

The current winning regime filter is a **440-day SMA**. This slightly outperformed the 400-day baseline during the exact-liq re-optimization.

Practical note:
- 400 remains a strong nearby anchor
- 440 is the current optimized winner
- long-DMA filters have cliff behavior, so this should be treated as an optimized parameter rather than an eternal truth

---

## 5. Soft-Bias Regime Model

Current winning soft-bias settings:

```yaml
unfav_risk_scale:    0.65
unfav_spacing_scale: 1.50
unfav_trigger_scale: 1.50
unfav_hold_scale:    0.50
```

Interpretation:
- favored side remains fully active
- unfavored side stays tradable but is degraded rather than blocked
- this keeps profitable counter-regime dislocations while avoiding the over-trading that killed more complex regime-machine ideas

---

## 6. Ladder Geometry

### 6.1 Number of levels
- **5 levels**

### 6.2 Spacing
Current best spacing:

```yaml
[0.5, 1.5, 9.0, 6.0]
```

This was a subtle but important improvement over `[0.5, 1.5, 8.0, 7.0]`.
Shifting 1% from the last gap into the prior gap improved survival geometry and materially increased CAGR.

### 6.3 Per-level multipliers
Current best per-level multiplier schedule:

```yaml
[2.0, 2.5, 2.5, 7.0]
```

This replaced the earlier convex `[1.5, 2.0, 3.0, 5.0]` branch under the stricter exact-liq optimization.

---

## 7. Trigger and Hold Logic

- **Long trigger:** 0.5%
- **Short trigger:** 1.5%
- **Max hold:** 160 bars

The lower short trigger was a major source of improvement because it added many profitable short trades without reintroducing liquidation under the corrected geometry.

---

## 8. Headline Result

### Data window
- **Effective simulation start:** 2019-01-03
- **End:** 2026-03-09

### Headline result for the current recommended config
- **CAGR:** 189.0% (fair comparison) / ~194% at the optimized DMA peak
- **Final equity:** about $1,000 → $1.81M
- **Liquidations:** 0
- **Max drawdown:** 34.8%
- **Trades:** 3,043

This more than doubled the corrected 81.9% CAGR baseline while halving drawdown.

---

## 9. Important Caveats

### 9.1 Not deployed to live
This file records the best current research candidate. It does not mean the live bot already uses these settings.

### 9.2 Backtests are still abstractions
Exact liquidation modeling improves trust materially, but live trading still adds slippage, latency, partial fills, and behavioral path differences.

### 9.3 Risk cliff remains sharp
The optimization found a sharp liquidation cliff around 25–26% risk. This means the current winner should be treated as tuned, not casually overextended.

---

## 10. Recommended Rollout Path

1. implement / update the separate v2.1 paper bot to the exact-liq winner
2. monitor it in paper mode
3. then pursue **v2.3 favored-side amplification** from this corrected baseline
4. only later consider live cutover discussions

---

## 11. Canonical Reference

Short summary:
- `MR_MARTINGALE_V2_SPEC.md`

Full descriptive strategy doc:
- `MR_MARTINGALE_V2_STRATEGY.md`
