# Mr Martingale — Version History

**Purpose:** concise lineage of the major strategy branches from the original live version through the current canonical winner.

> **Comparability note:** the early v1 research used a shorter 27.7-month window, while later versions were tested on the stricter long-window framework with refined methodology. So the CAGR figures below are faithful to each branch's research context, but not all are perfectly apples-to-apples.

---

## v1.0
**First live Mr. Martingale strategy** — original dual-sided Hyperliquid mean-reversion Martingale grid.

- Live-trading family launched first
- Simpler 4-level style architecture
- No long-DMA soft-bias regime model
- Older sizing / geometry assumptions
- **CAGR:** ~**92.6%** (original 27.7-month research window)
- **Liquidations:** **0** on that original shorter-window test
- **Why superseded:** later full-history work showed the architecture was not robust enough

---

## v2.0
**First major redesign branch** — true compounding + no stop-loss + spacing-only liquidation control + 5 levels + 400DMA hard regime gate.

- Introduced the next-gen high-growth architecture
- Used hard bull/bear gating
- 5 levels
- Early convex / late-expand style geometry
- **CAGR:** **320.5%**
- **Liquidations:** **0**
- **Status:** **invalidated later** because this result did not survive exact liquidation simulation

---

## v2.1 (first pass)
**Soft-bias upgrade to v2.0** — unfavored side degraded instead of fully disabled.

- Replaced hard gate with soft bias
- Unfavored side remained tradable with lower risk / wider spacing / stricter trigger / shorter hold
- **CAGR:** **376.1%**
- **Liquidations:** **0**
- **Status:** **invalidated later** because it still depended on the older non-exact liquidation framework

---

## v2.1 (corrected exact-liq baseline)
**First fully trusted corrected version** after exact liquidation modeling.

- Same soft-bias family, but the earlier 30% risk assumption was cut down
- Established the first honest safe baseline under exact-liq simulation
- **CAGR:** **81.9%**
- **Liquidations:** **0**
- **Status:** important correction step, but not final winner

---

## v2.1 (re-optimized exact-liq winner)
**Corrected v2.1 fully re-optimized** under exact liquidation simulation.

- Re-optimized risk, spacing, multipliers, short trigger, hold, and soft-bias settings
- Key winning config became:
  - risk **25%**
  - **440DMA**
  - **soft bias**
  - level gaps **[0.5, 1.5, 9.0, 6.0]**
  - level multipliers **[2.0, 2.5, 2.5, 7.0]**
  - short trigger **1.5%**
  - max hold **160 bars**
- **CAGR:** **189.0%** fair comparison / **~194.4%** optimized peak
- **Liquidations:** **0**
- **Status:** this became the true winner and later got promoted to v3.0

---

## v2.2
**Regime machine branch** — richer state classification (trend / chop / accumulation / distribution / transition).

- Tried to add more nuanced regime awareness
- Sounded smarter, but reduced productive trading too much
- **Best CAGR tested:** about **70.5%** at a safe risk point
- **Liquidations:** **0** in the safe range
- **Status:** **rejected** — underperformed v2.1 soft bias everywhere that mattered

---

## v2.3
**Favored-side amplification branch** — tried pushing the favored side hotter than baseline.

- Tested higher favored-side risk, easier favored triggers, tighter favored spacing, stronger favored multipliers, longer favored holds
- Result: most candidates liquidated, and none beat the baseline while preserving zero liquidation
- **Best surviving reference point:** baseline remained about **194.4% CAGR** with **0** liquidations
- **Status:** **rejected**

---

## v3.0
**Canonical current winner** — formal promotion of the corrected exact-liq v2.1 winner.

- Current official research / paper target
- True compounding
- No stop-loss
- 5 levels
- 440d SMA soft-bias regime
- Risk **25%**
- Level gaps **[0.5, 1.5, 9.0, 6.0]**
- Level multipliers **[2.0, 2.5, 2.5, 7.0]**
- Short trigger **1.5%**
- Max hold **160 bars**
- **CAGR:** **189.0%** fair / **~194.4%** optimized
- **Liquidations:** **0**
- **Status:** current canonical strategy and current paper-trading target

---

## Current conclusion
The strategy history now resolves to this:
- **v1.0** was the original live family
- **v2.0** and early **v2.1** produced exciting but overly optimistic results
- **v2.2** and **v2.3** were explicitly tested and rejected
- **v3.0** is the current canonical answer because it survived the stricter exact-liquidation standard
