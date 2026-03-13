# Quant Journal

## 2026-02-16 — HFT Timeframe + Leverage Analysis

### Summary
Tested whether the thin confluence edge (~53-58% hit rate at 1h) could be amplified by going to lower timeframes (5m, 15m) and applying moderate leverage (2-3x). **Result: NO — the edge degrades at lower timeframes and leverage makes things worse.**

### Data Pulled
- BTC/ETH/SOL 5m candles: ~5,000 each (17 days: Jan 30 – Feb 16, 2026)
- BTC/ETH/SOL 15m candles: ~5,000 each (52 days: Dec 26, 2025 – Feb 16, 2026)
- Hyperliquid API caps at ~5,000 candles regardless of interval — can't get more historical data at sub-hourly

### Key Findings

**1. Hit rate DEGRADES at lower timeframes:**
- 5m base: avg 46.8% hit rate (below random!)
- 15m base: avg 44.5% hit rate
- 1h base: avg 50.6% hit rate
- The funding signal is hourly — forward-filling it to 5m candles adds noise, not information

**2. Fees destroy the edge at short horizons:**
- Round trip fee: 0.05% per trade at 1x
- Average raw edge at 5m/30m horizon: +0.041% → net after fees: -0.009%
- Need to hold 2h+ for the edge to overcome fees at 5m base
- At 1h base, edge > fees starting at 1h horizon (+0.094% raw, +0.043% net)

**3. Leverage amplifies risk more than reward:**
- 2x leverage: requires 67% hit rate to break even (we achieve 50-55%)
- 3x leverage: requires 75.5% hit rate, ruin probability 54-96%
- Average max drawdown at 3x: -82% (complete ruin)

**4. Trade count increases but quality drops:**
- 5m: ~590 trades (10-12% of bars fire) — not selective enough
- 1h: ~206 trades (3-5% of bars fire) — more selective, better hit rates

**5. Only bright spot:** BTC 15m/4h P99 (68.1% hit, 47 trades, Sharpe 15.8) — but only 52 days of data, statistically fragile.

### Monte Carlo Results (Best ETH 5m configs)
- 1x: 7-32% median return, 0% ruin probability — sounds OK but on 17 days of data
- 2x: 15-63% median return, 1-71% ruin probability
- 3x: 22-95% median return, 9-96% ruin probability

### Verdict: ❌ NO-GO for HFT frequency play
The HFT thesis fails because:
1. Signals are hourly/multi-hour in nature — faster evaluation doesn't create more information
2. Fee costs (0.05%) exceed the tiny per-trade edge at sub-2h horizons
3. Leverage multiplies drawdowns as much as returns
4. 5m data window (17 days) is too short for reliable conclusions

### Files Created
- `signals/hft_backtester.py` — Lower TF + leverage backtester
- `signals/run_hft_analysis.py` — Full analysis runner
- `signals/results/go_no_go_hft.md` — Comprehensive Go/No-Go report
- `signals/results/hft_analysis_*.json` — Raw results data
- `intelligence/data/historical/candles_*_5m.csv.gz` — 5m candle data
- `intelligence/data/historical/candles_*_15m.csv.gz` — 15m candle data

### Lesson
**Frequency scaling only works if the signal itself scales with frequency.** Our signals (funding extremes, volume patterns, wick patterns) operate on hourly timescales. Evaluating them every 5 minutes doesn't create more opportunities — it creates more false positives. This is fundamentally different from HFT strategies that exploit microsecond-level microstructure.

---

## 2026-02-16 — Multi-Signal Confluence Build & Test

### Summary
Built the full 4-signal confluence system and backtested the 3 historically-testable signals against real Hyperliquid data. Result: **marginal edge, NOT sufficient for live trading**. Forward data collectors built for the 4th signal (L2 book imbalance).

### What Was Built

**Signal Definitions (`signals/signal_definitions.py`):**
1. `FundingRateExtremeSignal` — P99 mean-reversion, percentile-adaptive thresholds
2. `OIDivergenceSignal` — Volume+price proxy (since no historical OI endpoint exists)
3. `LiquidationCascadeProxySignal` — Wick patterns + V-shapes from price action
4. `OrderBookImbalanceSignal` — L2 bid/ask imbalance (forward-only)

**Confluence Engine (`signals/confluence_engine.py`):**
- Weighted scoring with agreement bonus, conflict penalty, coverage multiplier
- Handles missing signals gracefully (normalizes weights to available signals)
- Configurable minimum score and minimum active signal count

**Confluence Backtester (`signals/confluence_backtester.py`):**
- Uses 1h candles as base timeframe
- Efficient pointer-based funding rate lookup (O(1) per bar instead of O(n))
- Monte Carlo bootstrap, stability (IS/OOS split)
- Extended parameter sweep across percentiles and thresholds

**Forward Data Collectors:**
- `intelligence/liquidation_ws.py` — WebSocket trade collector (flags liquidation-like events)
- `intelligence/orderbook_collector.py` — Periodic L2 book snapshots with imbalance metrics

### Key Results (Extended Parameter Sweep)

Best configs by asset (most trades with positive metrics):

| Asset | Config | Trades | 1h Hit | 4h Hit | 12h Hit | 4h Sharpe |
|-------|--------|--------|--------|--------|---------|-----------|
| BTC | P95 ms=15 | 175 | 58.3% | 50.3% | 54.3% | 2.71 |
| ETH | P97 ms=15 | 157 | 51.0% | 55.4% | 54.8% | 3.14 |
| SOL | P95 ms=15 | 186 | 52.1% | 50.0% | 53.2% | 5.99 |

SOL deep-dive (186 trades): positive avg return at ALL horizons (1h-24h), profit factor 1.15-1.42. Monte Carlo: 90% prob positive at 4h but 49% ruin probability.

### Verdict: ❌ NO-GO for 3-signal confluence alone
- Hit rates cluster 50-55% — above noise but not enough
- Average returns tiny (~0.1-0.4% per trade at 4h-12h) — fees could erase edge
- Risk metrics unacceptable (high ruin probability in Monte Carlo)
- Sample sizes drop to single digits at higher confluence thresholds

### Missing Piece: Order Book Imbalance
The three tested signals are all LAGGING (react to what happened). The 4th signal (L2 book imbalance) is LEADING (shows intent). A leading + lagging combination is a known pattern in quant strategies. Forward data collectors are built and ready to deploy.

### Files Created/Updated
- `signals/signal_definitions.py` — 4 signal types (v3)
- `signals/confluence_engine.py` — weighted multi-signal scoring
- `signals/confluence_backtester.py` — real data confluence tester
- `intelligence/liquidation_ws.py` — WebSocket trade/liquidation collector
- `intelligence/orderbook_collector.py` — L2 book snapshot collector
- `signals/results/go_no_go_confluence.md` — comprehensive No-Go report
- `STRATEGY.md` — v3 (confluence thesis, data collection phase)
- `README.md` — updated architecture
- `execution/config.yaml` — updated parameters

### Next Steps
1. Deploy forward data collectors (L2 book + trade WS)
2. Collect 2-4 weeks of live data
3. Re-test with 4-signal confluence
4. If still no edge: pivot to fundamentally different approach

---

## 2026-02-16 — REAL DATA VALIDATION: Strategy Invalidated

### Summary
Pulled 23,709 real funding records per asset and 5,000+ candle bars from Hyperliquid API. The contrarian funding signal that showed 74% hit rate on synthetic data has **no tradeable edge on real data**.

### Data Retrieved
| Asset | Funding Records | 4h Candles | Date Range |
|-------|----------------|------------|------------|
| BTC | 23,709 | 5,001 | May 2023 – Feb 2026 |
| ETH | 23,709 | 5,001 | May 2023 – Feb 2026 |
| SOL | 23,709 | 5,000 | May 2023 – Feb 2026 |

### Critical Discovery: Funding Rate Scale Mismatch

Real Hyperliquid funding rates are **10-50x smaller** than synthetic data used:

| Percentile | Synthetic (est.) | Real BTC | Real ETH | Real SOL |
|------------|-----------------|----------|----------|----------|
| Median abs | ~0.0005 | 0.0000125 | 0.0000125 | 0.0000125 |
| P90 abs | ~0.001 | 0.0000502 | 0.0000557 | 0.0000712 |
| P95 abs | ~0.002 | 0.0000752 | 0.0000776 | 0.0001066 |
| P99 abs | ~0.003+ | 0.0001308 | 0.0001284 | 0.0001942 |

The threshold of 0.001 (used in all prior backtests) triggers **ZERO** trades on BTC/ETH real data.

### Backtest Results (Percentile-Adaptive Thresholds)

**P90 Contrarian (best-case, ~1000 trades):**
| Asset | 4h Hit | 24h Hit | 24h AvgRet | 24h Sharpe |
|-------|--------|---------|------------|------------|
| BTC | 50.8% | 49.9% | -0.037% | -0.27 |
| ETH | 51.1% | 48.9% | +0.101% | +0.53 |
| SOL | 49.9% | 53.7% | +0.544% | +2.11 |

**P99 Classic Mean-Reversion (surprise finding, ~67-96 trades):**
| Asset | 4h Hit | 4h Sharpe | 12h Hit |
|-------|--------|-----------|---------|
| BTC | 61.2% | +5.33 | 65.7% |
| ETH | 56.0% | +4.60 | 56.0% |
| SOL | 51.0% | -1.62 | 51.0% |

### Stability Test: FAIL

| Asset | In-Sample Sharpe | Out-of-Sample Sharpe |
|-------|-----------------|---------------------|
| BTC | +3.63 | -2.80 |
| ETH | +2.78 | -0.97 |
| SOL | +2.92 | +0.23 |

All assets degrade massively out-of-sample. The signal is not stable.

### Monte Carlo: Unacceptable Risk

| Metric | BTC | ETH | SOL |
|--------|-----|-----|-----|
| Prob Positive (100 trades) | 45.8% | 60.1% | 88.6% |
| Prob >15% Drawdown | 88.8% | 96.4% | 98.6% |

Even for SOL (best case), 98.6% of simulations hit a >15% drawdown.

### Verdict: ❌ NO-GO
Do not proceed to paper trading. The edge does not exist on real data.

### Files Created
- `intelligence/historical_data.py` — Real data fetcher (Hyperliquid API)
- `intelligence/data/historical/*.csv.gz` — Compressed funding + candle data
- `signals/real_data_backtester.py` — Real data backtester (percentile thresholds)
- `signals/results/go_no_go_real_data.md` — Comprehensive Go/No-Go report
- `signals/results/real_data_backtest_v2_*.json` — Raw results

### Lessons
1. **Validate data source FIRST.** Two days wasted on synthetic data.
2. **Hyperliquid ≠ Binance.** Hourly funding, smaller rates, different dynamics.
3. **Synthetic data is dangerous.** It can create edges that don't exist.
4. **The inversion trick only works if the signal fails on REAL data.**

---

## 2026-02-16 — Lessons Learned

### CRITICAL MISTAKE: Built analysis on synthetic data
All backtesting from project kickoff through the inversion analysis was running on **synthetic/simulated data**, not real Hyperliquid historical data. This means:
- The original NO-GO verdict may or may not be valid
- The inversion analysis results (74% hit rate, +14.6% median return) may or may not be valid
- Two days of signal work, Monte Carlo sims, and parameter tuning are unverified

**Root cause:** Never validated data source before building on top of it. The backtester generated synthetic price paths instead of pulling real historical candles/funding from Hyperliquid API.

**Rule going forward:** ALWAYS verify data source first. Real data before any analysis. No exceptions.

---

## 2026-02-16 — Signal Inversion Analysis & Strategy Overhaul

### Problem
Go/No-Go report from 2026-02-14 showed NO-GO verdict:
- funding_extreme: NEGATIVE returns across ALL timeframes (-0.8% avg at 4h)
- Median return: -14%, Ruin probability: 80%, Sharpe: -4.70
- The strategy was losing money consistently

### Key Insight: If a signal is consistently WRONG, invert it
The funding_extreme signal had a 26% hit rate at 4h — far below random (50%). A signal that is reliably wrong is just as informative as one that is reliably right.

### Inversion Analysis Results

**Backtest (funding_extreme inverted vs original):**
| Metric | Original | Inverted | Delta |
|--------|----------|----------|-------|
| 4h Hit Rate | 26.0% | 74.0% | +48pp |
| 4h Avg Return | -0.8006% | +0.8006% | +1.6% |
| 4h Sharpe | -199.22 | +199.22 | +398 |

**Monte Carlo (200 sims):**
| Metric | Original | Funding Inverted | Delta |
|--------|----------|-----------------|-------|
| Median Return | -12.37% | +14.62% | +27.0% |
| Median Sharpe | -4.33 | +4.80 | +9.1 |
| Ruin Probability | 79.0% | 46.5% | -32.5pp |

**Verdict changed from NO-GO to CONDITIONAL.**

### Why Was the Signal Inverted?
The original funding_extreme signal assumed mean-reversion: "extreme positive funding → expect price to fall." In crypto, the opposite appears true — extreme funding reflects genuine directional conviction, and the "mean reversion" crowd becomes exit liquidity for the momentum.

This is consistent with known crypto market dynamics:
- Funding can stay extreme for extended periods during trends
- Mean-reversion shorts get squeezed, adding to the move
- The original signal was fighting the trend

### Parameter Tuning
- Sweep of funding_rate_threshold from 0.0003 to 0.003
- Higher threshold = fewer trades but higher accuracy (100% hit rate at 0.003, but only 27 trades)
- Sweet spot: 0.001 (default) gives 259 trades with 74% hit rate at 4h
- All thresholds show positive returns when inverted

### Hyperliquid API Research (RESEARCH-HYPERLIQUID.md)
- Confirmed API has much more capability than we use
- Key gap: no real liquidation data — we estimate zones, not actual liquidations
- Can get: real-time liquidation events via WebSocket, historical candles, funding history
- **All backtests currently use synthetic data** — must validate with real data

### Files Created/Updated
- `signals/inversion_analysis.py` — full inversion testing framework
- `signals/focused_backtest.py` — single-signal parameter tuning
- `signals/generate_inverted_report.py` — Go/No-Go for inverted strategy
- `RESEARCH-HYPERLIQUID.md` — API capability research
- `STRATEGY.md` — updated to v2 (contrarian funding)
- `signals/results/go_no_go_inverted_*.md` — new CONDITIONAL Go/No-Go report
- `signals/results/inversion_analysis_*.json` — raw analysis data
- `signals/results/focused_backtest_*.json` — single-signal results

### Next Steps
1. Pull real Hyperliquid historical data and rerun backtests
2. Paper trade inverted funding signal for 2-4 weeks
3. Add WebSocket liquidation event tracking
4. Add L2 order book imbalance signal

## 2026-02-14 — Project Kickoff
- Brainstormed ideas in Discord #crypto › Ideas thread
- Evaluated: Polymarket arb (edge squeezed), AI tournament (cool but unproven), Bayesian optimization (practical foundation)
- Landed on meta-strategy: "Trade the traders, not the market"
- Core insight: Crypto's transparency lets us observe what big players are forced to do
- Scaffolded project structure under Personal/Financial/Portfolio/HighRisk/Quant/
- Next: Build Phase 1 intelligence layer — start with Hyperliquid liquidation data

## 2026-02-14 14:01 UTC — OPENED LONG BTC
- **Mode:** paper
- **Signal:** confluence (confidence: 48%)
- **Entry:** $97,500.00 (filled: $97,695.00)
- **Size:** $297.67
- **Stop:** $95,545.71 (2.20%)
- **Target:** $101,602.80 (4.00%)
- **Reasoning:** Confluence=48, conf=48% | Kelly frac=0.0614, scaled=0.0298 | Size=2.98% of $10,000 = $298 | Stop=2.20%, Target=4.00%, R:R=1.8:1
