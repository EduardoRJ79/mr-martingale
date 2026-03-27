# Asset Tuning Pipeline — Cross-Asset Strategy Optimization

**Document Version:** 2026-03-25  
**Classification:** VW Family Office — Portfolio High Risk — Quantitative Strategies  
**Pipeline Status:** 🟡 INITIAL SWEEP IN PROGRESS

---

## Executive Summary

This document defines the systematic asset-discovery and parameter-tuning pipeline for all PineScript strategies. The pipeline enables:

1. **Asset Universe Discovery** — Systematic testing across 25+ crypto assets
2. **Cross-Asset Backtesting** — Strategy × Asset performance matrix
3. **Parameter Optimization** — Grid search + walk-forward validation
4. **Asset Suitability Scoring** — Multi-factor ranking system
5. **Promotion Path** — Paper → Live deployment criteria

---

## 1. Asset Universe Definition

### 1.1 Asset Classes

| Class | Assets | Description | Data Priority |
|-------|--------|-------------|---------------|
| **Major** | BTC, ETH, SOL, XRP, DOGE, BNB, LTC, ADA, AVAX, LINK | Large-cap, high liquidity | P0 (Critical) |
| **Mid-cap** | MATIC, DOT, UNI, AAVE, COMP, CRV, SUSHI | Established DeFi/protocol tokens | P1 (High) |
| **Meme/Volatile** | PEPE, WIF, SHIB, BONK, FLOKI | High volatility, sentiment-driven | P2 (Medium) |

### 1.2 Data Availability Matrix

| Asset | 1m Data Available | Source | Date Range | Status |
|-------|-------------------|--------|------------|--------|
| BTC | ✅ | Binance | 2017-2026 | READY |
| ETH | ✅ | Binance | 2017-2024 | READY |
| SOL | ⏳ | Pending | — | QUEUED |
| XRP | ⏳ | Pending | — | QUEUED |
| DOGE | ⏳ | Pending | — | QUEUED |
| Others | ⏳ | Pending | — | BACKLOG |

### 1.3 Data Requirements

- **Minimum History:** 3 years (2021-01-01 minimum)
- **Resolution:** 1-minute OHLCV preferred, 5-minute acceptable
- **Quality:** No gaps > 1 hour, wick data included
- **Source:** Binance, Hyperliquid, or Coinbase

---

## 2. Cross-Asset Backtest Framework

### 2.1 Methodology: Liquidation-Restart

```yaml
Methodology: Liquidation-Restart (LRM)
Initial Capital: $1,000 per test
Reset Condition: Account equity ≤ $0 (liquidation)
Post-Reset: Account reset to $1,000, trading continues
Timeframe: 1-minute resolution
Period: 2021-01-01 to present (minimum 4 years)
Fees: 0.045% taker fee per trade
Slippage: 3 ticks modeled
```

### 2.2 Success Criteria by Phase

| Phase | Return Threshold | Max DD | Liquidations | Win Rate | Profit Factor |
|-------|------------------|--------|--------------|----------|---------------|
| **Discovery** | >+50% | <75% | ≤2 | >40% | >1.2 |
| **Optimization** | >+100% | <50% | 0 | >45% | >1.5 |
| **Paper Ready** | >+200% | <30% | 0 | >50% | >2.0 |
| **Live Ready** | >+500% | <25% | 0 | >55% | >3.0 |

### 2.3 Performance Matrix Template

Results stored in: `ASSET_STRATEGY_MATRIX_YYYY-MM-DD.json`

```json
{
  "matrix_version": "2026-03-25",
  "strategies": ["PINE-001", "PINE-003", "PINE-004", "PINE-006"],
  "assets": ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "LTC", "ADA"],
  "results": {
    "PINE-001": {
      "BTC": { "return": 1440.5, "max_dd": 2.31, "liqs": 0, "status": "PASS" },
      "ETH": { "return": -45.2, "max_dd": 67.8, "liqs": 1, "status": "FAIL" }
    }
  }
}
```

---

## 3. Parameter Optimization Process

### 3.1 Optimization Workflow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Asset-Specific │────▶│  Grid Search    │────▶│  Walk-Forward   │
│  Discovery Pass │     │  (±20% bounds)  │     │  Validation     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                          ┌─────────────────┐            │
                          │  Optimal Config │◄───────────┘
                          │  per Asset      │
                          └─────────────────┘
```

### 3.2 Grid Search Parameters by Strategy

#### PINE-001: Sweet v4.4.4

| Parameter | Base | Grid Range | Step | Notes |
|-----------|------|------------|------|-------|
| st_length | 22 | 18-26 | 2 | Supertrend period |
| st_multiplier | 5.1813 | 4.14-6.22 | 0.5 | ATR multiplier |
| gauss_period | 144 | 115-173 | 16 | Gaussian exit period |
| hma_length | 68 | 54-82 | 7 | HMA filter length |
| tema_length | 95 | 76-114 | 10 | TEMA filter length |
| chop_threshold | 38.2 | 35-45 | 2.5 | Choppiness threshold |

#### PINE-003/PINE-004: Swing 4h

| Parameter | Base | Grid Range | Step | Notes |
|-----------|------|------------|------|-------|
| ema_fast | 9 | 7-11 | 1 | Fast EMA period |
| ema_slow | 21 | 17-25 | 2 | Slow EMA period |
| rsi_period | 14 | 10-18 | 2 | RSI period |
| rsi_threshold | 50 | 45-55 | 2.5 | RSI threshold |
| atr_mult | 2.0 | 1.5-2.5 | 0.25 | ATR stop multiplier |

#### PINE-006: Gaussian V4H

| Parameter | Base | Grid Range | Step | Notes |
|-----------|------|------------|------|-------|
| poles | 4 | 2-6 | 1 | Gaussian poles |
| period | 144 | 115-173 | 16 | Sampling period |
| tr_mult | 1.414 | 1.13-1.70 | 0.15 | TR multiplier |
| reduced_lag | true | true/false | — | Reduced lag mode |

### 3.3 Walk-Forward Validation

```yaml
Training Period: 2021-01-01 to 2023-12-31 (3 years)
Testing Period: 2024-01-01 to 2025-03-25 (15 months)
Validation Criteria:
  - Training return > +100%
  - Testing return > +50%
  - Testing max DD < training max DD × 1.5
  - No liquidations in either period
```

### 3.4 Parameter Sensitivity Documentation

For each (strategy, asset) pair, document:

1. **Sensitivity Rank** — Which parameters most affect performance
2. **Robustness Score** — % of grid configs passing criteria
3. **Overfitting Risk** — Train vs. test performance decay
4. **Stability Window** — Optimal params stable across time windows

---

## 4. Asset Suitability Scoring

### 4.1 Scoring Rubric (0-100 scale)

#### Liquidity Score (25 points)

| Metric | Weight | Calculation |
|--------|--------|-------------|
| Avg Daily Volume | 10 pts | Log10($ volume) scaled |
| Bid-Ask Spread | 10 pts | <0.1% = 10pts, >1% = 0pts |
| Market Cap | 5 pts | Top 10 = 5pts, Top 100 = 1pt |

#### Volatility Regime Fit (25 points)

| Metric | Weight | Calculation |
|--------|--------|-------------|
| ATR/Price Ratio | 10 pts | Strategy-optimal range |
| Volatility Clustering | 10 pts | GARCH-type persistence |
| Vol of Vol | 5 pts | Stability measure |

#### Trend Characteristics (25 points)

| Metric | Weight | Calculation |
|--------|--------|-------------|
| Trend Strength (ADX) | 10 pts | Avg ADX over period |
| Trend Duration | 10 pts | Average trend length |
| Trend Efficiency | 5 pts | Price change / path length |

#### Strategy-Specific Metrics (25 points)

| Strategy | Key Metric | Scoring |
|----------|------------|---------|
| Sweet v4 | Mean reversion cycles | % time in chop vs. trend |
| Swing 4h | 4H swing amplitude | Avg 4H range |
| Gaussian | Color-flip frequency | Optimal signal count |

### 4.2 Asset Rankings by Strategy

Rankings updated after each sweep: `ASSET_RANKINGS_YYYY-MM-DD.json`

---

## 5. Promotion Criteria

### 5.1 Paper Trading Entry

```yaml
Requirements:
  - Backtest return: >+200%
  - Backtest max DD: <30%
  - Zero liquidations
  - Profit factor: >2.0
  - At least 50 trades over 4 years
  - Walk-forward validation passed
Capital Allocation: $1,000 per strategy-asset pair
Duration: 14 days minimum
```

### 5.2 Live Trading Entry

```yaml
Requirements:
  - Paper return: >+10% (annualized)
  - Paper max DD: <15%
  - Zero liquidations in paper
  - Win rate: >50%
  - At least 5 trades in paper period
  - Sharpe ratio: >1.0
Capital Allocation: $5,000 initial per pair
Scaling: Double every +20% return, max $50,000
```

### 5.3 Live Trading Exit

```yaml
Hard Stops:
  - Max DD hit: -20% from peak
  - Consecutive losses: 5 in a row
  - Weekly loss: >-10%
  - Monthly loss: >-15%

Review Triggers:
  - Underperformance: <50% of backtested return over 3 months
  - Regime change: Asset volatility shifts >2x from baseline
  - Strategy decay: Win rate drops >10% from backtest
```

---

## 6. Pipeline Execution

### 6.1 Initial Sweep Status (2026-03-25)

| Strategy | Target Assets | Completed | In Progress | Remaining |
|----------|---------------|-----------|-------------|-----------|
| PINE-001 Sweet | 10 | 1 (BTC) | 1 (ETH) | 8 |
| PINE-003 Swing BTC | 10 | 1 (BTC) | 1 (ETH) | 8 |
| PINE-004 Swing ETH | 10 | 1 (ETH) | 1 (BTC) | 8 |
| PINE-006 Gaussian | 10 | 1 (ETH) | 1 (BTC) | 8 |

### 6.2 Automation Scripts

| Script | Purpose | Location |
|--------|---------|----------|
| `cross_asset_sweep.py` | Run strategy on all available assets | `execution/cross_asset_sweep.py` |
| `param_grid_search.py` | Grid search optimizer | `execution/param_grid_search.py` |
| `walk_forward_validator.py` | WFV implementation | `execution/walk_forward_validator.py` |
| `asset_scorer.py` | Calculate suitability scores | `execution/asset_scorer.py` |
| `promotion_gate.py` | Check promotion criteria | `execution/promotion_gate.py` |

### 6.3 Update Schedule

| Update Type | Frequency | Trigger |
|-------------|-----------|---------|
| Asset Matrix | After each sweep | Manual or batch complete |
| Rankings | Weekly | Every Sunday |
| Optimal Params | After WFV | Grid search complete |
| Paper Status | Daily | Runner telemetry |
| Live Status | Real-time | Trading events |

---

## 7. Registry Updates

### 7.1 PINESCRIPT_REGISTRY.md Sections

Each strategy entry includes:

```markdown
### PINE-XXX: Strategy Name

**Asset-Specific Variants:**
| Asset | Config File | Return | Max DD | Status |
|-------|-------------|--------|--------|--------|
| BTC | `pineXXX_btc_config.json` | +XXX% | X.X% | PAPER/LIVE |
| ETH | `pineXXX_eth_config.json` | +XXX% | X.X% | PAPER/LIVE |
```

### 7.2 Configuration Files

Asset-specific configs stored in:
```
execution/configs/
├── pine001/
│   ├── sweet_btc_15m.json
│   ├── sweet_eth_15m.json
│   └── sweet_sol_15m.json
├── pine003/
│   ├── swing_btc_4h.json
│   └── swing_eth_4h.json
└── pine006/
    ├── gaussian_btc_4h.json
    └── gaussian_eth_4h.json
```

---

## 8. Risk Management

### 8.1 Per-Strategy Limits

| Limit | Value | Action on Breach |
|-------|-------|------------------|
| Max concurrent assets | 5 per strategy | Queue additional |
| Max paper capital | $5,000 per strategy | Require approval |
| Max live capital | $50,000 per strategy | Hard cap |
| Correlation threshold | 0.8 between assets | Diversify or drop |

### 8.2 Portfolio Limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| Total paper capital | $20,000 | 4 strategies × $5,000 |
| Total live capital | $100,000 | Risk budget |
| Max single asset exposure | 25% | Concentration risk |
| Max meme/volatile allocation | 20% | Volatility risk |

---

## 9. Output Files

| File | Description | Updated By |
|------|-------------|------------|
| `ASSET_TUNING_PIPELINE.md` | This document | Manual |
| `ASSET_STRATEGY_MATRIX_*.json` | Performance matrix | `cross_asset_sweep.py` |
| `ASSET_RANKINGS_*.json` | Suitability scores | `asset_scorer.py` |
| `PARAM_OPTIMIZATION_*.json` | Optimal configs | `param_grid_search.py` |
| `WALK_FORWARD_RESULTS_*.json` | WFV results | `walk_forward_validator.py` |
| `PAPER_PROMOTION_QUEUE.json` | Ready for paper | `promotion_gate.py` |
| `LIVE_PROMOTION_QUEUE.json` | Ready for live | `promotion_gate.py` |

---

## 10. Current Results

### 10.1 Winning Strategy-Asset Combinations (2026-03-25)

| Rank | Strategy | Asset | Return | Max DD | Calmar | Status |
|:----:|----------|-------|--------|--------|--------|--------|
| 1 | PINE-001 Sweet | BTC | +1440% | 2.31% | 623 | ✅ Paper Active |
| 2 | PINE-004 Swing | ETH | +1078% | 9.68% | 111 | ✅ Paper Active |
| 3 | PINE-003 Swing | BTC | +932% | 9.60% | 97 | ✅ Paper Active |
| 4 | PINE-006 Gaussian | ETH | +763% | 2.70% | 283 | ✅ Paper Active |

### 10.2 Parameter Optimization Queue

| Priority | Strategy | Asset | Base Return | Optimization Status |
|----------|----------|-------|-------------|---------------------|
| 1 | Sweet | ETH | Testing | GRID SEARCH QUEUED |
| 2 | Swing | BTC | +932% | WFV QUEUED |
| 3 | Gaussian | BTC | Testing | GRID SEARCH QUEUED |
| 4 | Swing | ETH | +1078% | WFV QUEUED |

---

## Appendix A: Data Fetching Commands

```bash
# Fetch additional asset data
python execution/fetch_binance_data.py --asset SOL --timeframe 1m --start 2021-01-01
python execution/fetch_binance_data.py --asset XRP --timeframe 1m --start 2021-01-01
python execution/fetch_binance_data.py --asset DOGE --timeframe 1m --start 2021-01-01
```

## Appendix B: Quick Reference

```bash
# Run full sweep on new asset
python execution/cross_asset_sweep.py --asset SOL --all-strategies

# Run grid search for specific pair
python execution/param_grid_search.py --strategy PINE-001 --asset ETH

# Check promotion status
python execution/promotion_gate.py --report

# Update all rankings
python execution/asset_scorer.py --update-all
```

---

*Last Updated: 2026-03-25 by Winnie (subagent)*  
*Next Review: After initial sweep completion*
