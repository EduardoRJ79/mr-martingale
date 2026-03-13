# Mr Martingale — Development Pipeline

> Canonical pipeline for strategy R&D, asset expansion, and sweep-based validation.
> Created: 2026-03-11
> Owner: Winnie / F1
> Scope: research only unless Brian explicitly approves live changes

---

## 0) Current Active Core-Strategy Pipeline (v3)

This section is now the active optimization pipeline for the BTC Mr Martingale v3 research branch.
It supersedes the older assumption that the next work is only multi-asset expansion.

### Current v3 research baseline
- **Strategy family:** true compounding, no stop-loss, spacing-only liquidation control
- **Current proposed version:** `MR_MARTINGALE_V3_STRATEGY.md`
- **Current paper bot:** `v2/` (paper only, live v1 remains untouched)
- **Current optimized baseline (as of 2026-03-12):**
  - 5 levels
  - 440DMA soft-bias regime filter
  - level gaps `[0.5, 1.5, 9.0, 6.0]`
  - level multipliers `[2.0, 2.5, 2.5, 7.0]`
  - risk per entry 25%
  - short trigger 1.5%
  - max hold 160 bars
  - exact-liq validated

### Current optimization principle
Work the system in loops, not one-off sweeps:
1. optimize one subsystem,
2. re-check interactions,
3. re-optimize the full stack,
4. memorialize only the current winner.

### Current status of the regime work
- **v2.1 soft-bias branch** beat hard gating and became the base for the current winner.
- **v2.2 regime machine:** tested and rejected.
- **v2.3 favored-side amplification:** tested and rejected.
- The corrected exact-liq re-optimization produced the current **v3.0 canonical winner**.

### Branch outcome history

#### v2.2 Regime Machine — Rejected
- Tested a richer multi-state classifier (trend/chop/accumulation/distribution/transition)
- Underperformed the simpler soft-bias model at every safe risk level
- Do not adopt

#### v2.3 Favored-Side Amplification — Rejected
- Tested hotter favored-side risk / trigger / spacing / multiplier ideas
- No candidate beat baseline without reintroducing liquidation
- Do not adopt

#### Exact-liq re-optimization — Completed
- Re-ran the soft-bias branch under exact liquidation simulation
- Found the current v3.0 canonical winner

### Important rule
All future strategy work should branch from the **v3.0 canonical baseline**, not from stale v2 assumptions.

---

## 1) Current Ground Truth

### Live / Production
- **BTC v1** in `execution/` remains the only production bot.
- **v2** is research / paper only.
- No live config changes are implied by this file.

### Prior Multi-Asset Findings (already established)
- Earlier multi-asset research ranked **XRP** highest on robustness among the first screened set.
- **SOL** showed the highest absolute backtest upside.
- **ETH** showed the best stability / lowest OOS degradation.
- Existing source files:
  - `RESEARCH_MULTI_ASSET.md`
  - `reports/multi_asset_strategy_returns_snapshot_2026-02-27.md`
  - `signals/multi_asset_results/rankings.csv`

### Hyperliquid Availability Check (2026-03-11)
- **EURUSD:** not listed on HL
- **AUDNZD:** not listed on HL
- Therefore FX pairs are **blocked for this HL-native pipeline** until they are listed.

---

## 2) Research Rules For New Assets

When expanding beyond BTC, every candidate must be screened on all of the following:

1. **HL listing status** — must be currently tradeable on Hyperliquid
2. **Max leverage** — must adapt the sweep to the asset's actual HL cap
3. **Liquidity** — use current HL `dayNtlVlm` as a first-pass filter
4. **Position granularity** — `szDecimals` matters; coarse sizing can distort results
5. **Volatility regime** — trigger / TP / timeout may need per-asset tuning
6. **Risk metrics** — prioritize **Calmar**, max DD, liquidation count, and OOS stability over raw return
7. **No assumptions from BTC** — each asset gets its own leverage-aware sweep

### Default leverage-aware sweep policy
For each asset, test leverage as a function of its HL cap instead of forcing BTC assumptions:
- **40x cap assets:** sweep up to 20x long / 15x short first, then extend if justified
- **25x cap assets:** sweep 10x / 15x / 20x bands where allowed
- **20x cap assets:** sweep 8x / 10x / 12x / 15x / 20x bands
- **10x cap assets:** sweep 5x / 6x / 8x / 10x bands
- Never exceed the current HL max leverage for the asset

---

## 3) Current HL Candidate Queue

### Tier A — Highest Priority (already partially validated)
| Asset | HL Max Lev | Why it stays high priority | Status |
|---|---:|---|---|
| BTC | 40x | Baseline / live reference | Active |
| ETH | 25x | Highest liquidity after BTC; best stability in prior screen | Re-test under v2 geometry |
| SOL | 20x | Strong upside / high trade frequency in prior screen | Re-test under v2 geometry |
| XRP | 20x | Best prior robustness score; must handle `szDecimals=0` carefully | Re-test under v2 geometry |

### Tier B — Next HL Research Candidates
| Asset | HL Max Lev | Rationale | Initial Priority |
|---|---:|---|---|
| HYPE | 10x | Very high current HL volume; native venue asset | High |
| BNB | 10x | Large-cap, cleaner structure than memecoins | High |
| SUI | 10x | Good volume and trend participation | High |
| LINK | 10x | Mature large-cap alt, decent volume, mean-reversion candidate | High |
| AVAX | 10x | Liquid large-cap with enough movement | High |
| AAVE | 10x | Lower volume than majors but cleaner than small caps | Medium |
| BCH | 10x | Tradeable and decently liquid, but structurally noisier | Medium |
| LTC | 10x | Mature asset, lower beta than SOL-style names | Medium |
| NEAR | 10x | Good enough liquidity for test queue | Medium |
| UNI | 10x | Large-cap DeFi proxy, worth screening | Medium |
| DOT | 10x | Large-cap but lower volume | Medium |
| ADA | 10x | Very coarse sizing (`szDecimals=0`) and lower realized edge expectation | Medium |
| TRX | 10x | High listing quality but may be too low-vol for this framework | Low |

### Tier C — Deprioritized / likely bad fit
- Memecoin-heavy instruments (`kPEPE`, `kSHIB`, `kBONK`, etc.)
- Very coarse sizing + low-quality structure combinations
- Anything with weak leverage, thin volume, or obvious listing quality issues

---

## 4) Asset Expansion Task Board

### Stage 0 — Pipeline / Infrastructure
- [x] Create dedicated development pipeline file
- [ ] Add a reusable asset-screening script that snapshots HL universe + leverage + volume
- [ ] Save current HL universe snapshot to `signals/multi_asset_results/hl_universe_snapshot_2026-03-11.json`
- [ ] Save ranked candidate shortlist to `signals/multi_asset_results/hl_candidate_queue_2026-03-11.csv`

### Stage 1 — Confirm / refresh prior winners under current assumptions
- [ ] Re-run **ETH** with current fee model + current v2 research geometry
- [ ] Re-run **SOL** with current fee model + current v2 research geometry
- [ ] Re-run **XRP** with current fee model + current v2 research geometry
- [ ] Compare ETH / SOL / XRP on:
  - CAGR
  - monthly compounded rate
  - max DD
  - Calmar ratio
  - liquidation count
  - timeout count
  - OOS degradation
- [ ] Decide whether **XRP still wins on robustness** under the updated research stack

### Stage 2 — Expand beyond the original four
- [ ] Build 10x-cap research cohort: HYPE, BNB, SUI, LINK, AVAX, AAVE, BCH, LTC, NEAR, UNI, DOT, ADA, TRX
- [ ] Add leverage-aware parameter sweeps for 10x assets (cannot inherit BTC leverage)
- [ ] Rank the 10x cohort by:
  - Calmar ratio
  - max DD
  - liquidation frequency
  - OOS degradation
  - trade count sufficiency
- [ ] Promote only the top survivors into paper-trade candidates

### Stage 3 — FX / non-crypto expansion
- [ ] Monitor HL listings for **EURUSD**
- [ ] Monitor HL listings for **AUDNZD**
- [ ] If either becomes available on HL, add to the 10x/20x asset pipeline immediately
- [ ] Until then: do **not** treat off-HL FX backtests as directly deployable for this HL-native bot

### Stage 4 — Paper deployment candidates
- [ ] Build per-asset paper configs only after sweep validation is complete
- [ ] Start with the best 1-2 non-BTC assets, not all at once
- [ ] Require a short paper-trade burn-in before any live discussion

---

## 5) Immediate Next Research Pass

### Pass A — refresh existing leaders
1. ETH
2. SOL
3. XRP

### Pass B — first new HL-native sweep cohort
1. HYPE
2. BNB
3. SUI
4. LINK
5. AVAX

### Pass C — second cohort
1. AAVE
2. BCH
3. LTC
4. NEAR
5. UNI
6. DOT
7. ADA
8. TRX

---

## 6) Notes / Operating Guidance

- The old `TODO.md` is still useful for broad strategy ideas, but **this file is now the canonical asset-expansion pipeline**.
- If future research contradicts the old ranking, update this file first.
- Use pipe-delimited summary tables when posting results back to Discord.
- Prefer robustness and survivability over flashy CAGR.
- Do not move any candidate to live discussion without leverage-aware testing on its own HL constraints.

---

## 7) Current Best Read (as of 2026-03-11)

If asked today what to test next:
1. **ETH** — safest first revalidation
2. **XRP** — prior robustness leader, but sizing granularity must be respected
3. **SOL** — strongest upside candidate
4. **HYPE** — first genuinely new HL-native asset worth screening
5. **BNB / SUI / LINK / AVAX** — strongest next wave among current 10x names
