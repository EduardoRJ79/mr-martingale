# Mr Martingale v3.0 — Proposed Strategy Spec

**Status:** Current leading research candidate / paper target / **not live deployed**  
**Date:** 2026-03-12  
**Purpose:** Memorialize the latest validated strategy findings as the canonical v3.0 branch so they are not lost behind the older v2 labels.

---

## Summary

Mr Martingale v3.0 is the current best researched version of the strategy.

It supersedes the earlier documentary v2 branches because:
- the old 30% risk / 320% CAGR claim did **not** survive exact liquidation simulation,
- v2.2 (regime machine) was tested and rejected,
- v2.3 (favored-side amplification) was tested and rejected,
- the corrected v2.1 soft-bias branch was re-optimized honestly under exact-liq simulation and is now the true leading candidate.

---

## Canonical v3.0 config

```yaml
version_name: Mr Martingale v3.0
mode: true_compounding
stop_loss: none
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
liquidation_model: exact
objective: maximize CAGR subject to zero liquidation
```

---

## Current validated result

### Backtest window
- **Effective start:** 2019-01-03
- **End:** 2026-03-09
- Warmup reason: long-DMA filter requires prior history

### Headline metrics
- **CAGR:** 189.0% on fair comparison / ~194.4% at the optimized peak
- **Final equity:** about **$1,000 → $1.81M**
- **Liquidations:** **0**
- **Max drawdown:** **34.8%**
- **Trades:** **3,043**

---

## What was rejected on the road to v3.0

### Rejected: earlier optimistic v2 branch
- 30% risk / 400DMA / older spacing-multiplier combination
- invalidated by exact liquidation simulation

### Rejected: v2.2 regime machine
- richer multi-state classification underperformed the simpler soft-bias model

### Rejected: v2.3 favored-side amplification
- pushing the favored side hotter reintroduced liquidation or failed to beat baseline

---

## Interpretation

The current evidence says the best version of Mr Martingale is:
- **true compounding**
- **no stop-loss**
- **spacing-only liquidation control**
- **5 levels**
- **soft macro bias**, not hard gating
- **exact-liq validated sizing and geometry**

This is the current canonical research answer.

---

## Deployment status

v3.0 is memorialized as the strategy winner.
This does **not** mean the live bot has been cut over.
The live production bot remains v1-family until a deliberate migration occurs.
