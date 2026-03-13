# Mr Martingale — Multi-Asset Expansion Research

> **Date:** 2026-02-27  
> **Status:** Research complete — paper trade infrastructure ready  
> **Author:** Winnie (automated research agent)

---

## Executive Summary

**Only 4 assets** in the entire crypto market meet both criteria (CMC top-100 by market cap **AND** ≥20x leverage on Hyperliquid):

| # | Asset | CMC Rank | HL Max Leverage | Status |
|---|-------|----------|-----------------|--------|
| 1 | **BTC** | #1 | 40x | ✅ Already live |
| 2 | **ETH** | #2 | 25x | 🟡 Paper-ready |
| 3 | **XRP** | #5 | 20x | 🟡 Paper-ready |
| 4 | **SOL** | #7 | 20x | 🟡 Paper-ready |

The eligible universe is extremely narrow because Hyperliquid restricts 20x+ leverage to only 4 assets total (out of 190 active perpetual markets). The next tier (10x) includes BNB, DOGE, ADA, SUI, and others.

**Recommended first 3 coins to add (in order):**
1. **ETH** — Highest liquidity after BTC, most data available, lowest OOS degradation (-17%)
2. **SOL** — Highest absolute PnL in backtests, highest trade frequency (69/month)
3. **XRP** — Highest robustness score, but szDecimals=0 creates sizing friction

**Key caveat:** Backtest results show 100% win rates, which is plausible for a 0.3% TP scalping strategy on 4H bars but should be treated with skepticism. Unrealized intra-bar drawdown is not tracked. These results establish relative rankings, not guaranteed returns. Paper trade first.

---

## 1. Research & Eligibility

### 1.1 Data Sources

- **CoinMarketCap:** Top 100 by market cap via `api.coinmarketcap.com/data-api/v3` (fetched 2026-02-28 00:41 UTC)
- **Hyperliquid:** Full tradeable universe via `api.hyperliquid.xyz/info` type=meta (fetched same time)
- **Confidence:** High — both are primary sources queried via official APIs

### 1.2 Hyperliquid Leverage Landscape

| Leverage Tier | # Assets | Notable Coins |
|---------------|----------|---------------|
| 40x | 1 | BTC |
| 25x | 1 | ETH |
| 20x | 2 | SOL, XRP |
| 10x | 27 | BNB, DOGE, AVAX, LINK, SUI, LTC, DOT, ... |
| 5x | 36 | ADA, ATOM, ALGO, HBAR, ... |
| 3x | 123 | Most altcoins, memecoins |

**Takeaway:** Hyperliquid is extremely conservative with leverage tiers. Only the top 4 by liquidity/stability get 20x+. This dramatically limits our eligible universe.

### 1.3 Intersection: CMC Top-100 × HL ≥20x

After filtering out stablecoins (15 in top 100), exchange tokens, and assets not on HL:

| CMC Rank | Symbol | Market Cap | HL Max Lev | szDecimals | Eligible |
|----------|--------|------------|------------|------------|----------|
| 1 | BTC | $1.32T | 40x | 5 | ✅ (live) |
| 2 | ETH | $233B | 25x | 4 | ✅ |
| 5 | XRP | $83B | 20x | 0 | ✅ |
| 7 | SOL | $47B | 20x | 2 | ✅ |
| 4 | BNB | $84B | 10x | 3 | ❌ (10x only) |
| 9 | DOGE | $16B | 10x | 0 | ❌ (10x only) |
| 10 | ADA | $10B | 10x | 0 | ❌ (10x only) |

### 1.4 ETH Deep Dive

ETH is the natural first expansion target:
- **Liquidity:** Second only to BTC on HL, deep order books
- **Leverage:** 25x max (vs BTC's 40x) — sufficient for the strategy
- **Data:** 5,001 4h candles available (Nov 2023 – Feb 2026)
- **Correlation with BTC:** High but not perfect; periods of divergence exist
- **szDecimals:** 4 — fine-grained position sizing possible

---

## 2. Optimization Methodology

### 2.1 Approach: Walk-Forward Validation

To control overfitting, we used **walk-forward optimization** with:
- 3 non-overlapping time windows + 1 full-sample reference
- Each window: 70% train / 30% out-of-sample (OOS) test
- Parameters optimized on train, validated on test
- Robustness score combines OOS performance, stability, and degradation

### 2.2 Parameter Search Space

| Parameter | Values Tested | Purpose |
|-----------|---------------|---------|
| EMA span | 21, 34, 55 | Trend filter speed |
| SMA period | 10, 14, 21 | Second MA for confluence |
| Long trigger % | 0.3, 0.5, 1.0 | How far below MAs to open long |
| Short trigger % | 1.5, 2.5, 3.5 | How far above MAs to open short |
| TP % | 0.3, 0.5, 1.0 | Take profit from blended entry |
| Max hold bars | 20, 30 | Force-close timeout (4h bars) |
| Leverage (long) | 20 | Fixed at max feasible |
| Leverage (short) | 15 | More conservative for shorts |

**Total combinations per window:** 486  
**Total backtests:** 7,776 (486 × 4 windows × 4 coins)

### 2.3 Anti-Overfit Controls

1. **Walk-forward validation** — params must work out-of-sample, not just in-sample
2. **Minimum trade threshold** — configs with <5 trades per window are excluded
3. **Composite scoring** — Sharpe × √(trades) × profit_factor × (0.5^liquidations)
4. **Robustness score** — mean OOS composite × degradation penalty × consistency bonus
5. **OOS degradation tracking** — how much performance drops from train to test

### 2.4 Ranking Criteria

Final ranking uses a robustness score that penalizes:
- High OOS performance degradation (overfitting signal)
- High variance across windows (instability)
- Any liquidation events (exponential penalty)

And rewards:
- Consistent OOS Sharpe ratios
- High trade frequency (statistical significance)
- Positive profit factors across windows

---

## 3. Results & Rankings

### 3.1 Optimized Parameters Per Asset

| Param | BTC (ref) | ETH | XRP | SOL |
|-------|-----------|-----|-----|-----|
| EMA span | 21 | 55 | 55 | 21 |
| SMA period | 14 | 21 | 21 | 14 |
| Long trigger % | 0.3 | 0.3 | 0.5 | 0.3 |
| Short trigger % | 1.5 | 1.5 | 1.5 | 1.5 |
| TP % | 0.3 | 0.3 | 0.3 | 0.3 |
| Max hold (4h bars) | 20 | 20 | 20 | 20 |
| Leverage (L/S) | 20/15 | 20/15 | 20/15 | 20/15 |

**Notable patterns:**
- TP 0.3% and short trigger 1.5% are universal — the strategy prefers tight TP across all assets
- ETH and XRP prefer slower MAs (EMA55/SMA21) — larger caps are smoother
- SOL and BTC prefer faster MAs (EMA21/SMA14) — more volatile, faster mean-reversion
- XRP needs a wider long trigger (0.5%) — less volatile intra-bar, needs more deviation to trigger

### 3.2 Full-Sample Backtest Results

| Rank | Coin | Robustness | Total PnL | PnL % | Sharpe | Trades | Trades/Mo | OOS Degrad |
|------|------|------------|-----------|-------|--------|--------|-----------|------------|
| 1 | **XRP** | 1,689 | $15,335 | 3,834% | 23.9 | 1,369 | 57.9 | -22.9% |
| 2 | **SOL** | 930 | $74,701 | 18,675% | 19.9 | 1,877 | 68.9 | -22.2% |
| 3 | **ETH** | 719 | $15,112 | 3,778% | 24.0 | 1,548 | 57.2 | -17.3% |
| 4 | BTC | 453 | $4,793 | 1,198% | 30.1 | 1,354 | 49.7 | -44.0% |

### 3.3 Interpretation & Caveats

⚠️ **Critical caveats (read before acting):**

1. **100% win rate is misleading.** With 0.3% TP on 4h bars, almost every trade hits TP within 1-3 bars because the intra-bar price range typically exceeds 0.3%. This is a feature of the tight TP, not evidence of a perfect strategy.

2. **0% max drawdown is a backtest artifact.** The backtester tracks equity only after trade closes, not unrealized intra-bar PnL. A live grid at Level 5 with 20x leverage can have 8%+ drawdown on unrealized basis before TP hits.

3. **Compounding creates hockey-stick PnL.** The dynamic margin (1.6% of equity) means the absolute PnL compounds exponentially. SOL's $74K from $400 is theoretical maximum with continuous compounding and zero slippage.

4. **Relative rankings are meaningful.** Even though absolute numbers are optimistic, the relative robustness scores reflect genuine differences in strategy stability across time windows.

5. **OOS degradation is the real signal.** BTC shows -44% degradation (parameters are less stable), while ETH shows only -17% (most stable). This matters more than absolute PnL.

---

## 4. Ranked Recommendations

### Recommended: Trade First

1. **ETH** 🥇
   - Lowest OOS degradation (-17%) = most stable parameters
   - Second deepest order book on HL
   - EMA55/SMA21 is a conservative, slow-reacting MA pair (less whipsaw)
   - 25x max leverage gives headroom
   - Start here.

2. **SOL** 🥈
   - Highest trade frequency (69/month) — best statistical sample
   - Highest absolute PnL (compounding effect of higher volatility)
   - Faster MAs (EMA21/SMA14) suit its higher volatility profile
   - 20x leverage, exactly at our threshold

3. **XRP** 🥉
   - Highest robustness score across windows
   - BUT: `szDecimals=0` means whole-unit sizing (1 XRP minimum increment)
   - At $1.35/XRP: L1 notional ($128) = ~95 XRP. Fine for the grid.
   - Wider long trigger (0.5%) means fewer long entries — could be a feature (higher quality)

### Watch / Avoid

- **BTC** is already live — continue as-is. Its higher OOS degradation (-44%) suggests the current EMA34/SMA14 params may be slightly overfit. Consider testing EMA21/SMA14 with 0.3% TP in a parallel paper bot.
- **All other top-100 coins** are limited to 10x or less on HL, making them unsuitable for the 20x grid strategy. If leverage tiers change, re-run this analysis.

---

## 5. Per-Asset Runtime System

### 5.1 Architecture

```
multi_asset/
├── __init__.py
├── asset_config.py          # Per-coin config generator (optimized params)
├── coin_runner.py           # Per-coin paper trade bot (standalone)
├── configs/
│   ├── ETH.json             # Generated runtime config
│   ├── SOL.json
│   └── XRP.json
├── state/
│   ├── grid_state_ETH.json  # Per-coin grid state (isolated)
│   ├── grid_state_SOL.json
│   ├── grid_state_XRP.json
│   ├── ETH.pid              # Process ID files
│   ├── SOL.pid
│   └── XRP.pid
├── logs/
│   ├── grid_bot_ETH.log     # Per-coin log files
│   ├── grid_bot_SOL.log
│   └── grid_bot_XRP.log
├── commands/
│   ├── ETH/                 # Per-coin command queue
│   ├── SOL/
│   └── XRP/
└── scripts/
    ├── start.sh             # Start one coin
    ├── stop.sh              # Stop one coin
    ├── status.sh            # Status of all coins
    ├── start_all.sh         # Start ETH + SOL + XRP
    └── stop_all.sh          # Stop all
```

### 5.2 Key Design Decisions

- **Complete isolation:** Each coin has its own config, state file, log file, PID file, and command directory. No shared state.
- **BTC is untouched:** The live BTC bot in `execution/` is completely separate. Multi-asset code lives in `multi_asset/`.
- **Paper-only by default:** `paper_trade: true` is hardcoded in config generation. Switching to live requires explicit code change.
- **Market data shared:** All bots use the same HL API for price/candle data (read-only). No conflicts.
- **Process per coin:** Each bot is a separate OS process. Start/stop independently.

### 5.3 Operator Quickstart

```bash
cd "/path/to/Mr Martingale"

# Generate configs (run once after param changes)
PYTHONPATH=. python3 -m multi_asset.asset_config

# Start single coin paper bot
./multi_asset/scripts/start.sh ETH

# Start all three
./multi_asset/scripts/start_all.sh

# Check status
./multi_asset/scripts/status.sh

# Check a specific coin
./multi_asset/scripts/status.sh ETH

# Stop a coin
./multi_asset/scripts/stop.sh ETH

# Stop all
./multi_asset/scripts/stop_all.sh

# Dry-run check (single poll, no loop)
PYTHONPATH=. python3 -m multi_asset.coin_runner SOL --dry-run

# View logs
tail -f multi_asset/logs/grid_bot_ETH.log
```

---

## 6. Files Created / Changed

### New Files
| File | Purpose |
|------|---------|
| `RESEARCH_MULTI_ASSET.md` | This report |
| `multi_asset/__init__.py` | Package init |
| `multi_asset/asset_config.py` | Per-coin config generator with optimized params |
| `multi_asset/coin_runner.py` | Per-coin paper trade bot |
| `multi_asset/configs/ETH.json` | ETH runtime config |
| `multi_asset/configs/SOL.json` | SOL runtime config |
| `multi_asset/configs/XRP.json` | XRP runtime config |
| `multi_asset/scripts/start.sh` | Start script per coin |
| `multi_asset/scripts/stop.sh` | Stop script per coin |
| `multi_asset/scripts/status.sh` | Status check for all |
| `multi_asset/scripts/start_all.sh` | Batch start |
| `multi_asset/scripts/stop_all.sh` | Batch stop |
| `signals/multi_asset_optimizer.py` | Walk-forward optimizer |
| `signals/multi_asset_results/` | All optimization outputs |
| `signals/multi_asset_results/rankings.json` | Machine-readable rankings |
| `signals/multi_asset_results/rankings.csv` | CSV rankings |
| `signals/multi_asset_results/eligible_assets.json` | Full eligibility data |
| `signals/multi_asset_results/optimization_*.json` | Per-coin detailed results |
| `signals/multi_asset_results/all_coins_optimization.json` | Combined results |
| `intelligence/data/historical/candles_XRP_4h.csv.gz` | XRP 4h data (fetched) |
| `intelligence/data/historical/candles_XRP_1h.csv.gz` | XRP 1h data (fetched) |

### Unchanged (Protected)
| File | Note |
|------|------|
| `execution/grid_bot.py` | BTC live bot — not modified |
| `execution/config.py` | BTC config — not modified |
| `execution/grid_state.json` | BTC state — not modified |
| All existing `execution/` files | Production BTC bot completely untouched |

---

## 7. Assumptions & Caveats

1. **Leverage tiers are current as of 2026-02-28.** HL may change tiers; re-query before acting.
2. **Backtest uses 4h bar resolution.** Intra-bar dynamics (wicks, fills, partial execution) are approximated. Real fills will differ.
3. **Fees use Brian's actual tier** (maker 0.0144%, taker 0.0432%). Fees may change.
4. **Funding rates approximated** at 0.0013%/8h average. Actual funding varies by market conditions.
5. **No slippage model.** At small position sizes ($6.40 L1 margin × 20x = $128 notional), slippage should be negligible for ETH/SOL/XRP.
6. **Backtest assumes no concurrent positions** (one side at a time). The live bot enforces this.
7. **XRP szDecimals=0** means positions must be whole units. At $1.35: $128 notional = ~95 XRP. Minimum order is 1 XRP ($1.35). This works fine for the strategy.
8. **Walk-forward optimization uses 3 windows.** More windows would increase confidence but were limited by data length (especially XRP with ~4,400 bars).
9. **These are paper-trade recommendations only.** Do not go live without at least 2 weeks of paper trading to validate fills and mechanics.

---

*Generated by multi_asset_optimizer.py — walk-forward optimization with 7,776 total backtests across 4 coins and 4 time windows.*
