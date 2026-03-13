# Mr. Martingale — TODO & Future Improvements

> I check this every two days and ping Brian in #mr-martingale to work on items together.
> Last reminder sent: 2026-02-21
> Canonical asset-expansion pipeline now lives in `DEV_PIPELINE.md` (created 2026-03-11).

---

## 🔴 Priority 1 — Regime Mode (Big Project)

**Goal:** Identify and adapt to market regimes so the strategy auto-optimizes for current conditions rather than running static parameters in all environments.

### Regime Detection
Weekly analysis combining:
- **Charting** — price structure, trend direction, key levels
- **RSI** — overbought/oversold on higher timeframes
- **Pi-cycle top indicator** — macro bull/bear signals
- **Fear & Greed Index** — sentiment positioning
- **Social media signals** — volume/tone analysis
- **On-chain / open interest data** — already collecting via intelligence scripts

### Regime Types
1. **Bull Market** — trending up, MAs rising, sentiment positive
2. **Distribution / Sideways** — range-bound, MAs flat, choppy price action
3. **Bear Market** — trending down, MAs falling, sentiment fearful

### Parameter Optimization Per Regime
- Run full sweep analysis (trigger %, leverage, TP %, level gaps, timeout) for each regime separately
- Produce three optimized configs: `config_bull.py`, `config_sideways.py`, `config_bear.py`
- Bot loads the appropriate config based on current regime assessment

### Workflow
1. F1 runs weekly market analysis (every Sunday)
2. Posts report to #mr-martingale with regime assessment
3. If F1 thinks regime has shifted → flags it to Brian
4. Brian reviews → agrees or disagrees → if agree, switches config
5. Manual override always — Brian makes the final call

### Notes
- Don't build this yet. Full project for later.
- Bear market backtest data needed first (2022 dataset) — get this before starting regime work
- The regime detection logic should be honest, not confirmation-biased

---

## 🟡 Priority 2 — Bear Market Backtest

**Goal:** Run the current strategy parameters against 2022 data (BTC -77% drawdown).

- Need historical candle data going back to at least Jan 2022
- Run `grid_backtest_dual_v2.py` (or equivalent) on that period
- Key questions:
  - How many liquidations occur at 20x?
  - What is max drawdown?
  - Does the timeout/force-close save the account or make it worse?
  - What leverage is survivable through a 2022-style bear?

**Why this matters:** All current projections are based on Nov 2023 – Feb 2026 (bull market). The strategy's real durability is unknown without this.

---

## 🟢 Backlog / Nice to Have

- [ ] **Dashboard / UI** — some lightweight way to see bot status without reading log files
- [ ] **Position size sanity cap** — Brian declined this, but worth revisiting at scale (if account reaches $10k+, notional exposure at 20x gets very large)
- [ ] **Multi-asset** — expand beyond BTC (ETH? SOL?) once strategy is proven live
- [ ] **Poll frequency tuning** — currently 5 min; could go to 15 min with minimal impact since trigger is based on 4H MAs
- [ ] **TP optimization** — 0.5% TP was set by feel + backtest; could sweep 0.3% / 0.5% / 0.75% / 1.0% per regime

---

## ✅ Completed

- [x] Strategy design (MA filter, dual-sided, grid spacing)
- [x] 162-combo MA optimizer → settled on EMA34 + SMA14
- [x] Leverage sweep → settled on 20x long / 15x short
- [x] Compounding backtest → 1.6% of balance per L1
- [x] Full execution engine (config, hl_client, paper_client, grid_state, notifier, grid_bot)
- [x] Paper trading mode live (Feb 21, 2026)
- [x] `ma21` → `sma14` label fix across codebase
- [x] Independent code/strategy review completed
- [x] F1.md knowledge base created
