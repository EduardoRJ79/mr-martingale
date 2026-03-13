# Mr. Martingale — Independent Audit Report

**Auditor:** Opus 4.6 (independent subagent, no prior involvement in strategy development)  
**Date:** 2026-02-27  
**Scope:** Full end-to-end review of strategy design, backtests, execution code, and risk claims  
**For:** Brian Van Winkle + Eddie

---

## Executive Summary

The execution engine (v1.3.1) is well-built and production-grade. The MA-filter + grid-DCA concept is sound in principle. **However, the backtest framework has a critical measurement flaw that makes the headline numbers unreliable.** The 0% Max Drawdown and 100% win rate reported in the 4H backtest are artifacts of how drawdown is tracked and how tight the TP is — not evidence that the strategy is risk-free. The 5-minute stress test (your own data) contradicts the 4H claims directly.

**Bottom line:** The strategy likely has positive expectancy in bull/sideways markets, but the risk is materially higher than what has been communicated. The current live config at 20x leverage with [0.5, 1.5, 3.0, 3.0] gaps is on the aggressive end of survivability.

---

## 1. What Is Solid ✅

**Execution code quality (HIGH CONFIDENCE)**
- v1.2.0 safety hardening is excellent: explicit order-status verification, no inferred fills, startup reconciliation against exchange state, emergency rollback on failed grid opens
- Single-side invariant enforced (no simultaneous long+short)
- Scoped cancellation (only this grid's orders, not all coin orders)
- Paper/live mode separation is clean

**MA filter concept (MEDIUM-HIGH CONFIDENCE)**
- The 162-combo MA optimizer that landed on EMA34+SMA14 is methodologically sound
- Walk-forward validation with train/test splits is the right approach
- Waiting for statistical deviation before entering (not trading every candle) is a legitimate edge

**Grid mechanics (MEDIUM CONFIDENCE)**
- DCA into dislocations is a well-understood strategy for mean-reverting assets
- 0.5% TP on a leveraged position is realistic — BTC's 4H range typically exceeds this
- Dynamic margin sizing (1.6% of current balance) compounds naturally

**Live results so far (LIMITED CONFIDENCE — too small a sample)**
- 10 closed trades, 10 wins, +$20.40 in 2.4 days on a $400 account
- Max level reached: L3 (twice) — deeper levels untested in live
- The bot works mechanically as designed

---

## 2. What Is Model Artifact / Unreliable ⚠️

### The Drawdown Bug (CRITICAL)

Both backtester versions (`grid_backtest_dual_v2.py` and `multi_asset_optimizer.py`) track drawdown using **realized equity only** — they update `peak_equity` and calculate drawdown only when a trade closes, using the `account` balance.

They do NOT include unrealized PnL of the currently open grid.

This is the specific code pattern in both files:

```python
# After each trade closes:
equity += pnl
peak_equity = max(peak_equity, equity)
dd = (peak_equity - equity) / peak_equity * 100
max_dd = max(max_dd, dd)
```

**What this means:** If the strategy opens a grid, price drops 5% through all 5 levels, the account is deeply underwater — but as long as the TP eventually hits, the drawdown is never recorded. The "Max DD" metric only captures permanent losses, not the interim pain.

### Why 100% Win Rate ≠ No Risk

With a 0.5% TP on 4H candles, BTC's typical intra-bar range (~2-4%) almost always reaches the TP within 1-2 bars. The average hold is 1.09 bars in the BTC backtest. So yes, almost every grid closes in profit.

But this masks what happens DURING the hold:
- Price can wick 3-8% against you within a single 4H bar
- At 20x leverage with all 5 levels filled ($198 margin on $3,976 notional), a 5% adverse move creates an unrealized loss of ~$199 — essentially your entire margin
- The 4H backtest only sees the candle's high/low, not the actual price path within the bar

### The Rankings Are Fantasy Numbers

From `rankings.json`:
- BTC: 100% win rate, 0% max DD, Sharpe 30.1, profit factor ∞
- All four coins: 100% win rate, 0% max DD

These numbers are artifacts. No real trading strategy has a Sharpe of 30 or infinite profit factor. The optimizer found parameters that never lose on a realized basis at 4H resolution — which is a statement about the tight TP and coarse time resolution, not about the strategy's actual risk.

---

## 3. The 5-Minute Stress Test Tells the Real Story

Your own 5-minute resolution sweep (`btc_level_spacing_sweep_5m.json`) reveals what the 4H backtest hides:

| Gap Config | Trades | Losses | Liquidations | Max DD | PnL % |
|---|---|---|---|---|---|
| **[0.5, 1.5, 3.0, 3.0]** (CURRENT) | 1,561 | 12 | **4** | **100%** | +325% |
| [1.0, 2.5, 5.0, 7.5] | 1,230 | 17 | 0 | 94.9% | +84% |
| [1.5, 4.5, 9.0, 9.0] | 1,066 | 17 | **0** | 78.5% | +20% |
| [0.4, 1.2, 2.4, 2.4] (tighter) | 1,603 | 7 | **6** | 100% | +197% |

**The current config has 4 liquidation events at 5-minute resolution.** Only the widest spacing (3x factor) achieved 0 liquidations.

The 4H backtest reports "0 liquidations" for the same config because 4H candles can't see the intrabar wick that causes the liquidation — the bar closes above the liq price even though it briefly touched it.

---

## 4. Answering Brian's Question: "If Not Liquidated, MDD Should Be 0, Right?"

**No.** Here's why, in plain English:

**Max Drawdown (MDD)** = the biggest peak-to-trough decline in your account value at any point in time.

Imagine your account hits $450. Then you open a grid and BTC drops 3%. Your account is now worth $420 (on paper — the positions are underwater). Then BTC bounces back, TP hits, and you close at $452. Your realized balance went 450 → 452 — looks great! But your account was actually worth $420 in the middle. That's a 6.7% drawdown that the backtest never records because it only looks at your balance when trades close.

**The correct MDD calculation** should mark-to-market every bar:
```
effective_equity = realized_balance + unrealized_PnL_of_open_positions
```

The backtester doesn't do this. So MDD = 0% just means "every trade that closed was profitable," not "the account was never underwater."

**In reality,** when a full 5-level grid is open at 20x leverage, the max margin deployed is $198 ($6.40 × [1+2+4+8+16] = $198.40) — that's 49.6% of a $400 account. If BTC moves against you by just 2.5% beyond L5, that $198 margin is mostly gone.

---

## 5. Eddie's Concerns — Direct Answers

**Reliability:**
- The strategy does what it claims mechanically — the code is solid
- But "100% win rate" is misleading — it means "100% of closed trades were profitable at 4H resolution"
- At 5m resolution with the current config: 99.2% win rate with 4 liquidations

**Risk of ruin:**
- At current settings with 20x leverage: **yes, ruin is possible**
- The 5m backtest shows 4 liquidation events over ~2 years of data
- Each liquidation in the backtest crushes account to 10% of prior value
- The 5m backtest still shows +325% total because the wins between liquidations are large enough to recover — but this is not a guarantee going forward

**Max margin used:**
- L1 through L5 fully filled: 49.6% of account ($198 on $400)
- This is confirmed by both the config math and the 5m sweep data

**Leverage safety:**
- 20x is aggressive for the current gap spacing
- At wider gaps [1.5, 4.5, 9.0, 9.0], 20x has 0 liquidations in the 5m backtest
- At current gaps [0.5, 1.5, 3.0, 3.0], even 20x gets liquidated 4 times
- The gap spacing matters more than the leverage number itself

**Intrabar wick handling:**
- The live bot polls every 5 minutes but relies on resting limit orders (L2-L5 are GTC limits on exchange)
- This means level fills happen correctly even during wicks — the exchange handles them
- **BUT**: there is no stop-loss or liquidation protection on-exchange — if price wicks through all levels and keeps going, the exchange liquidates the position. The bot learns about it after the fact.

---

## 6. Key Metrics Table

| Metric | 4H Backtest | 5m Stress Test | Reality Check |
|---|---|---|---|
| Win Rate | 100% | 99.2% | Tight TP drives high win rate, but tail losses are catastrophic |
| Max Drawdown | 0% | 100% | 4H number is wrong (realized-only tracking) |
| Liquidations | 0 | 4 | 4H resolution misses intrabar wicks |
| Sharpe Ratio | 30.1 | 3.4 | 4H number is meaningless |
| Total PnL (2y) | +1,198% | +325% | 5m is more realistic but still assumes perfect execution |
| Max Margin Used | 49.6% | 49.6% | Consistent — this is real |
| Avg Hold Time | ~1 bar (4h) | — | Most grids close fast; risk is in the rare deep ones |

---

## 7. Recommendations

### Do Now
1. **Fix the drawdown calculation** in both backtester files. Add mark-to-market unrealized PnL tracking every bar. This is a 10-line code change that will produce honest MDD numbers.
2. **Widen the gaps** — consider [1.0, 2.5, 5.0, 7.5] at minimum. The current [0.5, 1.5, 3.0, 3.0] gaps are too tight for 20x leverage based on your own 5m data. The tradeoff: fewer trades, lower total return, but zero liquidations.
3. **Stop citing 0% MDD or 100% win rate** in discussions. These are measurement artifacts, not strategy properties.

### Backtest Next
1. **2022 bear market data** — this is the single biggest unknown. All existing tests are Nov 2023 – Feb 2026 (bull/recovery). BTC dropped 77% in 2022. The strategy's behavior in that regime is completely untested.
2. **Run the 5m backtest with corrected MDD tracking** to get honest intra-trade drawdown numbers.
3. **Lower leverage sweep at 5m**: test 10x and 15x with current gaps. Find the leverage at which the current gaps have 0 liquidations.

### Settings to Keep
- EMA34 + SMA14 MA pair (well-optimized, walk-forward validated)
- 0.5% TP (works well for the tight mean-reversion thesis)
- 1.6% base margin with 2x multiplier (reasonable compounding)
- 120h timeout (sensible safety net)
- Long 0.5% / Short 2.5% trigger asymmetry (respects BTC's upward bias)

### Settings to Change or Investigate
- **Gap spacing**: Widen. [1.0, 2.5, 5.0, 7.5] is the sweet spot from 5m data (0 liquidations, still decent returns)
- **Leverage**: Consider 15x for longs (not just shorts) until bear market data is tested
- **On-exchange stop-loss**: Consider placing a stop-loss order below L5 that caps the maximum loss instead of relying on exchange liquidation

---

## 8. What to Say in the Thread

Brian, here's a version you can paste or adapt:

> **Audit update from an independent review (Opus 4.6, fresh eyes on the full codebase):**
>
> ✅ **Execution code is solid** — the bot does what it says, safety checks are real, reconciliation logic is good.
>
> ⚠️ **The 0% max drawdown and 100% win rate are measurement artifacts.** The backtest only tracks drawdown when trades close — it doesn't mark-to-market during open positions. Since the 0.5% TP almost always hits, every closed trade is a win. But the account can be significantly underwater during the hold.
>
> 🔴 **The 5-minute stress test (our own data) shows 4 liquidation events** with the current gap spacing [0.5, 1.5, 3.0, 3.0] at 20x leverage. The 4H backtest misses these because it can't see intrabar wicks.
>
> **What this means:** The strategy works and likely has positive expectancy, but the risk is higher than what I've been claiming. I need to either widen the gaps or lower the leverage. The 5m data shows that gaps of [1.0, 2.5, 5.0, 7.5] eliminate liquidations while keeping most of the edge.
>
> **Eddie's right to push on this.** The honest numbers: ~99% win rate (not 100%), real MDD is significant during open positions (not 0%), and the strategy has not been tested in a bear market. I'm going to fix the drawdown tracking, widen the gaps, and run bear market tests before making any more claims about risk.

---

## Confidence Levels

| Claim | Confidence | Basis |
|---|---|---|
| Execution code is production-quality | **High** | Direct code review, safety patterns verified |
| MA filter provides statistical edge | **Medium-High** | Walk-forward validated, 162-combo search |
| Strategy has positive expectancy in bull market | **Medium** | 5m backtest shows profit net of liquidations, live results consistent |
| Strategy survives a bear market | **Unknown** | No 2022 data tested — this is the biggest gap |
| Current gaps are safe at 20x | **Low** | 5m data shows 4 liquidations |
| Wider gaps [1.0, 2.5, 5.0, 7.5] are safe at 20x | **Medium** | 5m data shows 0 liquidations, but only ~2 years of data |

---

*End of audit. No live code was modified. Report saved to `AUDIT_OPUS_REPORT.md` in strategy root.*
