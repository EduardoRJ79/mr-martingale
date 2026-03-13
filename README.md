# Mr Martingale

> Current canonical research label: **v3.0** (`MR_MARTINGALE_V3_STRATEGY.md`)

# Quant — Multi-Signal Confluence Research

## Mr. Martingale Local Console (v1.3.0)

Run the local trading terminal:

```bash
cd Personal/Financial/Portfolio/HighRisk/Quant
python3 -m streamlit run execution/console_app.py
```

Manual buttons (`Manual Long`, `Manual Short`, `Manual Close`) now queue commands to the live bot via `execution/commands/`.

## Current Status: ⏳ DATA COLLECTION PHASE

3-signal confluence (funding + OI proxy + liquidation proxy) shows marginal edge on real data — not enough for live trading. Building forward data collection for 4th signal (L2 order book imbalance).

## Architecture

```
Quant/
├── intelligence/              # Data feeds (Hyperliquid API)
│   ├── historical_data.py     # Real data fetcher (funding + candles)
│   ├── funding_monitor.py     # Live funding rate tracker
│   ├── oi_tracker.py          # Live OI tracker
│   ├── liquidation_ws.py      # WebSocket trade/liquidation collector ← NEW
│   ├── orderbook_collector.py # L2 book snapshot collector ← NEW
│   └── data/
│       ├── historical/        # Compressed CSV (funding + candles, 2023-2026)
│       └── live/              # Real-time snapshots + forward data
│           ├── liquidations/  # Trade stream data (JSONL.gz)
│           └── orderbook/     # L2 snapshots (JSONL.gz)
├── signals/                   # Signal engine + backtesting
│   ├── signal_definitions.py  # 4 signal types (v3) ← UPDATED
│   ├── confluence_engine.py   # Multi-signal weighted scoring ← NEW
│   ├── confluence_backtester.py # Real data confluence tester
│   ├── hft_backtester.py      # Lower TF + leverage backtest lib ← NEW
│   ├── run_hft_analysis.py    # Full HFT analysis runner ← NEW
│   ├── real_data_backtester.py  # Single-signal real data tester
│   └── results/               # Reports and JSON outputs
│       ├── go_no_go_confluence.md  # Latest Go/No-Go report
│       └── *.json             # Raw backtest results
├── execution/                 # Position sizing, risk management
│   └── config.yaml            # Strategy parameters
├── meta/                      # Regime detection
├── utils/                     # Hyperliquid API client
├── STRATEGY.md                # Current strategy thesis (v3)
├── JOURNAL.md                 # Full research chronolog
└── RESEARCH-HYPERLIQUID.md    # API capabilities
```

## Quick Start

```bash
cd Personal/Financial/Portfolio/HighRisk/Quant

# Run confluence backtest (uses cached historical data)
PYTHONPATH=. .venv/bin/python signals/confluence_backtester.py

# Run HFT timeframe + leverage analysis (5m, 15m, 1h)
PYTHONPATH=. .venv/bin/python signals/run_hft_analysis.py

# Fetch fresh historical data (if needed)
PYTHONPATH=. .venv/bin/python intelligence/historical_data.py

# Start L2 order book collector (runs continuously)
PYTHONPATH=. .venv/bin/python intelligence/orderbook_collector.py --interval 60

# Start trade/liquidation collector (runs continuously)
PYTHONPATH=. .venv/bin/python intelligence/liquidation_ws.py
```

## Signal Types

| # | Signal | Source | Historical? | Status |
|---|--------|--------|-------------|--------|
| 1 | Funding P99 Mean-Reversion | Funding rates | ✅ Yes (23k+ records) | Validated: weak but real |
| 2 | OI Divergence Proxy | Volume + price | ✅ Yes (5k+ candles) | Validated: marginal |
| 3 | Liquidation Cascade Proxy | Price action | ✅ Yes (5k+ candles) | Validated: marginal |
| 4 | Order Book Imbalance | L2 book data | ❌ No (needs collection) | Collector built |

## Key Findings

1. **Funding signal alone**: P99 mean-reversion shows 61% hit (BTC) but only 67 trades in 2+ years
2. **3-signal confluence**: Hit rates 50-58%, positive avg returns, but insufficient for live trading
3. **HFT lower TF thesis**: ❌ REJECTED — hit rates degrade at 5m/15m, fees eat the edge, leverage amplifies risk
4. **Missing ingredient**: L2 book imbalance (leading indicator) may complete the thesis
5. **Honest assessment**: No tradeable edge yet. Infrastructure built. Awaiting forward data.

## Tools

| Script | Purpose |
|--------|---------|
| `signals/confluence_backtester.py` | 3-signal confluence backtest (1h candles) |
| `signals/hft_backtester.py` | Lower TF + leverage backtest library |
| `signals/run_hft_analysis.py` | Full HFT analysis runner (5m/15m/1h × 1-3x leverage) |
| `signals/real_data_backtester.py` | Single-signal real data backtester |
| `intelligence/historical_data.py` | Hyperliquid data fetcher (funding + candles) |
| `intelligence/orderbook_collector.py` | L2 book snapshot collector (forward data) |
| `intelligence/liquidation_ws.py` | Trade/liquidation WS collector (forward data) |

## Future: Agent Board Architecture (Phase 5)

When signals are validated and execution is built, the decision layer upgrades to a **multi-agent board**:

| Seat | Role | Job |
|------|------|-----|
| Analyst | Data interpreter | Runs confluence, reports market state |
| Sentiment | Crowd reader | CT/news/social signals, contrarian flags |
| Risk | Adversarial skeptic | Veto power, sizing, correlation checks |
| Executor | Trade placer | Entry timing, stops, position management |

Key principle: **Risk Agent has veto power.** No single agent can place a trade alone. All reasoning is logged for post-mortem review. See `STRATEGY.md` Phase 5 for full architecture.

## History

- **v1** (2026-02-14): Mean-reversion funding — built on synthetic data, invalidated
- **v2** (2026-02-16): Contrarian/momentum funding — invalidated by real data
- **v3** (2026-02-16): Multi-signal confluence — marginal, awaiting L2 data
- **v3.1** (2026-02-16): HFT lower TF + leverage — tested and rejected
- **v3.2** (2026-02-16): Agent Board architecture sketched (Phase 5)
