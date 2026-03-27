# PineScript Paper Trading Specification
## Mastermind Pipeline — POLICY_LOCK_2026-03-02

**Document Version:** 2026-03-25  
**Classification:** VW Family Office — Portfolio High Risk — Quantitative Strategies  
**Status:** PAPER ACTIVE

---

## Executive Summary

This document specifies the paper trading harness for the top 4 PineScript strategies migrated from TradingView backtests to live paper trading on Hyperliquid.

| Strategy | Code | Asset | Timeframe | Initial Capital | Status |
|----------|------|-------|-----------|-----------------|--------|
| Sweet v4.4.4 | PINE-001 | DOGE | 15m | $1,000 | PAPER_ACTIVE |
| Swing BTC 4h | PINE-003 | BTC | 4h (1m agg) | $1,000 | PAPER_ACTIVE |
| Swing ETH 4h | PINE-004 | ETH | 4h (1m agg) | $1,000 | PAPER_ACTIVE |
| Gaussian V4H v4.0 | PINE-006 | ETH | 4h (1m agg) | $1,000 | PAPER_ACTIVE |

**Total Allocated Capital:** $4,000 USD (paper)  
**Paper Trading Period:** 14 days (2026-03-25 to 2026-04-08)

---

## Infrastructure Components

### 1. Base Paper Trading Client (`hl_paper_client.py`)

The Hyperliquid paper trading client simulates live order execution without real capital at risk.

**Key Features:**
- Order placement/cancellation simulation
- Position tracking with P&L calculation
- Liquidation price calculation
- Cross-margin liquidation simulation
- State persistence (JSON)
- Equity curve tracking

**Account State Tracked:**
- Equity balance
- Available/margin balances
- Open positions (asset, side, size, entry price, leverage)
- Realized/unrealized P&L
- Peak equity and max drawdown

### 2. Risk Management Harness (`risk_harness.py`)

Implements the Mastermind risk policy per POLICY_LOCK_2026-03-02.

**Kill Switch (40% Max Drawdown):**
- Triggered when equity drops 40% from starting capital
- **Action:** Stops ALL trading immediately
- **Recovery:** Manual reset required after review

**Circuit Breaker (10% Daily Loss):**
- Triggered when daily realized losses exceed 10%
- **Action:** Blocks new positions, allows exits
- **Recovery:** Auto-reset at UTC midnight

**Position Limits:**
- Max leverage: 3-5x (strategy dependent)
- Max positions per strategy: 1-2
- Max position size: 20% of equity per trade
- No trading outside 00:00-23:59 UTC

### 3. Telemetry & Logging (`paper_telemetry.py`)

Comprehensive logging for analysis and signal parity checking.

**Trade Log (`{strategy}_trades.csv`):**
- Entry/exit timestamps
- Prices, sizes, P&L
- Exit reasons
- Bars held

**Equity Curve (`{strategy}_equity.csv`):**
- Timestamp, equity, unrealized P&L
- Realized daily P&L
- Drawdown tracking

**Signal Log (`{strategy}_signals.csv`):**
- Entry/exit signals with timestamps
- Price at signal
- Confidence scores
- Backtest match flags

**Summary JSON (`{strategy}_summary.json`):**
- Aggregate performance metrics
- Win rate, total return, max DD
- Trade counts

---

## Strategy Specifications

### PINE-001: Sweet v4.4.4 (DOGE 15m)

**Configuration:**
```yaml
initial_equity: $1,000
risk_per_trade: 10%
leverage: 3x
asset: DOGE
timeframe: 15m
```

**Indicators:**
- Supertrend (Length: 22, Mult: 5.1813)
- HMA Filter (Length: 68)
- TEMA Filter (Length: 95)
- DMI Filter (Length: 56)
- Chop Filter (Length: 7, Threshold: 38.2)
- Gaussian Exit (Period: 144, Poles: 2, TR Mult: 0.655)
- Chandelier Exit (Lookback: 4, Mult: 1.8788)

**Entry Logic:**
- LONG: Supertrend UP + HMA UP + TEMA UP + DMI OK + Chop < 38.2
- SHORT: Supertrend DOWN + HMA DOWN + TEMA DOWN + DMI OK + Chop < 38.2
- Cooldown: 4 bars between trades

**Exit Logic:**
- Gaussian channel breach
- Chandelier stop hit
- Supertrend direction flip

**Expected Performance (Backtest 2021-2024):**
- Return: +1440.54%
- Max DD: 2.31%
- Win Rate: 65.11%
- Profit Factor: 6.63
- Liquidations: 0

---

### PINE-003: Swing BTC 4h

**Configuration:**
```yaml
initial_equity: $1,000
risk_per_trade: 15%
leverage: 5x
asset: BTC
timeframe: 4h (aggregated from 1m)
```

**Indicators:**
- EMA 20/50 trend
- Swing High/Low (10-bar)
- ATR 14 (stop/position sizing)
- RSI 14 (momentum)
- MACD (12/26/9)
- Volume ratio

**Entry Logic:**
- LONG: EMA bullish + Price > Swing High + RSI 40-70 + MACD bullish + Volume OK
- SHORT: EMA bearish + Price < Swing Low + RSI 30-60 + MACD bearish + Volume OK

**Exit Logic:**
- ATR-based stop loss (2x ATR)
- ATR-based take profit (4x ATR)
- Trend reversal (EMA cross)

**Expected Performance (Backtest):**
- Return: +932%
- Max DD: ~15-20%
- Liquidations: 0

---

### PINE-004: Swing ETH 4h

**Configuration:**
```yaml
initial_equity: $1,000
risk_per_trade: 15%
leverage: 5x
asset: ETH
timeframe: 4h (aggregated from 1m)
```

**Indicators:**
- EMA 12/26/50 (trend)
- Bollinger Bands (20, 2.0)
- ATR 14
- RSI 14
- MACD (12/26/9)
- Stochastic (14, 3)
- Pivot Points (R1/S1)

**Entry Logic:**
- LONG: EMA bullish stack + Price > R1 + RSI 45-75 + MACD bullish + Stoch > 20
- SHORT: EMA bearish stack + Price < S1 + RSI 25-60 + MACD bearish + Stoch < 80

**Exit Logic:**
- ATR-based stops (2.5x ATR)
- Bollinger band breach
- EMA trend reversal
- RSI extreme (80/20)

**Expected Performance (Backtest):**
- Return: +1078%
- Max DD: ~18-22%
- Liquidations: 0

---

### PINE-006: Gaussian V4H v4.0 (ETH)

**Configuration:**
```yaml
initial_equity: $1,000
risk_per_trade: 20%
leverage: 4x
asset: ETH
timeframe: 4h (aggregated from 1m)
```

**Indicators:**
- Gaussian Channel (Period: 144, Poles: 2)
- TR Multiplier: 1.414
- Channel color detection (GREEN/RED)
- Band width (volatility)

**Entry Logic:**
- LONG: Channel flips to GREEN
- SHORT: Channel flips to RED
- Always in market (binary strategy)

**Exit Logic:**
- Opposite color flip
- Band breach (overextended)
- Center line breach

**Expected Performance (Backtest):**
- Return: +763%
- Max DD: ~25-30%
- Liquidations: 0

---

## Execution Flow

```
1m Candle Arrival (WebSocket)
    ↓
Update Position Prices
    ↓
Check Risk Limits (Kill Switch / Circuit Breaker)
    ↓
Aggregate to Strategy Timeframe (if needed)
    ↓
Calculate Indicators
    ↓
Check Exit Signals (if in position)
    ↓
Check Entry Signals (if flat)
    ↓
Execute Orders (simulated)
    ↓
Log to Telemetry
    ↓
Persist State
```

---

## File Structure

```
execution/
├── hl_paper_client.py           # Base paper client
├── risk_harness.py              # Risk management
├── paper_telemetry.py           # Logging/telemetry
├── pine001_sweet_v4_runner.py   # PINE-001 runner
├── pine003_swing_btc_runner.py  # PINE-003 runner
├── pine004_swing_eth_runner.py  # PINE-004 runner
├── pine006_gaussian_v4h_runner.py # PINE-006 runner
├── master_paper_runner.py       # Master orchestrator
└── paper_logs/                  # Generated logs
    ├── PINE-001-Sweet-v4.4.4-DOGE_trades.csv
    ├── PINE-001-Sweet-v4.4.4-DOGE_equity.csv
    ├── PINE-001-Sweet-v4.4.4-DOGE_signals.csv
    ├── PINE-001-Sweet-v4.4.4-DOGE_summary.json
    └── [similar for PINE-003, PINE-004, PINE-006]
```

---

## Signal Parity Check

To ensure live signals match backtest signals:

1. **Log All Signals:** Every entry/exit signal logged with timestamp and price
2. **Compare to Backtest:** Weekly parity check against historical backtest signals
3. **Tolerance:** ±60 seconds, ±0.1% price difference acceptable
4. **Alert:** If <95% parity, investigate indicator calculation differences

---

## Risk Monitoring Dashboard

**Key Metrics to Monitor:**

| Metric | Alert Threshold | Critical Threshold |
|--------|-----------------|-------------------|
| Max DD | 25% | 40% (kill switch) |
| Daily Loss | 5% | 10% (circuit breaker) |
| Consecutive Losses | 3 | 5 |
| Signal Parity | 95% | 90% |
| Liquidations | Any | N/A |

---

## 14-Day Paper Trading Protocol

**Week 1 (Days 1-7):**
- Monitor all 4 strategies for signal generation
- Verify position sizing matches spec
- Check exit logic triggers correctly
- Daily risk metric review

**Week 2 (Days 8-14):**
- Full performance tracking
- Compare to backtest expectations
- Signal parity validation
- Go/No-Go decision for live trading

**Promotion Criteria to Live:**
1. 0 liquidations
2. Max DD < 35%
3. Signal parity > 95%
4. Risk harness functioning correctly
5. Equity curve directionally matches backtest

**Rejection Criteria:**
1. Any liquidation
2. Max DD > 40%
3. Signal parity < 85%
4. Kill switch triggered >1 time
5. Strategy behavior diverges significantly from backtest

---

## Next Actions

1. **Initialize all 4 runners** (master_paper_runner.py)
2. **Connect to HL WebSocket** for 1m data feed
3. **Begin 14-day paper period** (2026-03-25)
4. **Daily monitoring** of risk metrics
5. **Weekly signal parity checks**
6. **Final review on 2026-04-08** for live promotion decision

---

*Document Version: 2026-03-25*  
*Author: Winnie (subagent)*  
*Classification: VW Family Office — Internal Use*
