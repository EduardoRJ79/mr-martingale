# Mr Martingale — Active Strategy (Paper Trade)

> **Bot name:** Mr Martingale  
> **Status:** Paper trading as of 2026-02-20

---

# Strategy v3 — Multi-Signal Confluence — ⏳ DATA COLLECTION PHASE

*Updated: 2026-02-16*

## Status: Awaiting Forward Data

The 3-signal confluence (funding + OI proxy + liquidation proxy) tested against real
Hyperliquid data shows **marginal positive expectancy but insufficient edge for live trading**.

### HFT / Lower Timeframe Analysis: ❌ REJECTED
Tested 5m and 15m base timeframes with 2-3x leverage to amplify the thin edge.
**Result:** Hit rates degrade at lower timeframes (46.8% at 5m vs 50.6% at 1h).
Fees eat the edge at sub-2h horizons. Leverage amplifies drawdowns proportionally.
The frequency-play thesis is invalidated — signals are hourly in nature and don't
benefit from faster evaluation. See `signals/results/go_no_go_hft.md`.

The fourth signal (Order Book Imbalance) requires forward L2 data collection before
the full thesis can be evaluated.

## Core Thesis

Individual microstructure signals in crypto are weak. But confluence of multiple weak
signals can produce a tradeable edge — IF the signals capture different aspects of
market state (leading vs lagging, positioning vs flow).

## The Four Signals

### 1. Funding Rate P99 Mean-Reversion (Historically Validated)
- When funding hits P99 extreme positive → expect pullback → SHORT
- When funding hits P99 extreme negative → expect bounce → LONG
- BTC: 61% hit at 4h, 67 trades in 2+ years
- ETH: 56% hit at 4h, 91 trades
- **Weak alone, provides value as confluence input**

### 2. OI Divergence Proxy (Historically Validated)
- Volume spike + directional price move = new positions entering (momentum confirmation)
- Low volume + directional move = weak move (likely reversion)
- Fires frequently (~1500-2000 times per asset over 2+ years)
- **Marginal standalone edge, valuable for confirmation**

### 3. Liquidation Cascade Proxy (Historically Validated)
- Long-wick candles during high vol = forced liquidation exhaustion → mean-revert
- V-shaped price patterns = cascade then recovery
- ~100-400 signals per asset over 2+ years
- **Detects market structure patterns but unclear standalone edge**

### 4. Order Book Imbalance ⏳ (Needs Forward Data)
- L2 bid/ask volume imbalance at top N levels
- Leading indicator — shows intent before execution
- The only LEADING signal in the set (others are all lagging)
- Collector built: `intelligence/orderbook_collector.py`
- **Cannot validate until 2-4 weeks of data collected**

## 3-Signal Confluence Results (Real Data)

Best configuration: P95 funding threshold, min_score=15, min_active=2

| Asset | Trades | 1h Hit | 4h Avg Ret | 12h Hit | MC Prob+ |
|-------|--------|--------|------------|---------|----------|
| BTC | 175 | 58.3% | ~flat | 54.3% | — |
| ETH | 256 | 53.1% | +0.10% | 52.7% | — |
| SOL | 186 | 52.1% | +0.29% | 53.2% | 90% |

**Verdict: Not enough edge for live trading. Average returns are tiny, risk metrics unacceptable.**

## What Needs to Happen

1. ✅ Signal definitions built for all 4 types
2. ✅ Confluence engine built and tested
3. ✅ Forward data collectors built (L2 book + trade/liquidation WS)
4. ⏳ Collect 2-4 weeks of L2 book data
5. ⏳ Re-run confluence with 4th signal
6. ❓ Decision point: 4-signal confluence edge sufficient for paper trading?

## Phase 5: Agent Board Architecture (Future)

Once signal quality is validated and a paper-trading execution layer exists, the
decision-making layer upgrades from a single confluence score to a **multi-agent board**.

### Why a Board, Not a Single Agent
- A single agent making leveraged trades unsupervised is how accounts blow up
- Multiple agents with different cognitive roles force explicit reasoning
- Built-in adversarial checks prevent overconfidence
- Every decision is traceable — when a trade fails, you can see which agent was wrong

### The Board (4 Seats)

**1. Analyst Agent** — *"What does the data say?"*
- Consumes the 4 signal feeds (funding, OI, liquidations, order book)
- Runs confluence scoring
- Presents a market state summary: trend, momentum, stress level
- No opinion on whether to trade — just reports the facts
- Model: fast reasoning (Grok 4.1 or similar)

**2. Sentiment Agent** — *"What does the crowd think?"*
- Monitors Crypto Twitter, news feeds, social signals
- Detects narrative shifts, hype cycles, fear spikes
- Contrarian flag: when sentiment is extreme, flag it
- Adds the human-behavioral layer the data feeds miss
- Model: fast reasoning with web access

**3. Risk Agent** — *"Why we should NOT do this."*
- Structurally adversarial — its job is to find reasons to say no
- Evaluates portfolio exposure, correlation, max drawdown proximity
- Checks: Are we sized right? Are we correlated? Is this revenge trading?
- Has VETO power — if risk says no, the trade doesn't happen
- Model: strong reasoning (Opus-tier for critical thinking)

**4. Executor Agent** — *"How and when to enter."*
- No opinion on direction — only cares about execution quality
- Optimal entry timing, limit vs market, slippage minimization
- Position sizing based on Risk Agent's parameters
- Manages open positions: trailing stops, partial exits, time-based exits
- Model: fast, low-latency

### Decision Flow

```
Market State Change
       │
       ▼
┌─────────────┐
│   Analyst    │ ── "Confluence score: 18/25, BTC funding P99, OI diverging"
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Sentiment   │ ── "CT is max bearish, fear index 15, contrarian long signal"
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    Risk      │ ── "Portfolio 40% exposed, drawdown at 8%. APPROVE at 2% size max."
└──────┬──────┘    (or: "VETO — too correlated with existing SOL long")
       │
       ▼
┌─────────────┐
│  Executor    │ ── Places limit order, sets stops, monitors fill
└─────────────┘
```

### Consensus Rules
- Analyst + Sentiment must agree on direction (or one neutral + one strong)
- Risk Agent can VETO any trade, no override
- Executor only acts on approved trades
- All reasoning logged to `meta/board_decisions/` for post-mortem review

### Implementation Notes
- Each agent is a sub-agent spawn (OpenClaw sessions_spawn)
- Board convenes on signal triggers, not on a timer
- Shared context via files in `meta/board_state/`
- Start with paper trading — board runs but executor submits to simulation
- Graduate to live only after 4+ weeks of positive paper results

### Prerequisites
- ✅ Phase 1: Intelligence layer (data feeds working)
- ⏳ Phase 2: Signal validation (4-signal confluence proven)
- ❓ Phase 3: Execution layer (Hyperliquid order placement)
- ❓ Phase 4: Paper trading infrastructure
- ❓ **Phase 5: Agent Board** (this section)

---

## Risk Rules (Unchanged)
- Max position size: 3% of portfolio per trade
- Max drawdown before halt: 15%
- Stop loss: Always. 2-3% from entry.
- Target: 4-6% (R:R minimum 1.5:1)
- Max concurrent positions: 2
- Paper trade FIRST. Minimum 2 weeks before live.

---
*v3: 2026-02-16 — confluence thesis, awaiting L2 book data*
*v2: 2026-02-16 — contrarian funding, invalidated by real data*
*v1: 2026-02-14 — original mean-reversion, invalidated by inversion analysis*
