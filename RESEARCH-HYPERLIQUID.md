# Hyperliquid API Research
*Compiled: 2026-02-16*

## 1. API Architecture

Hyperliquid uses a simple REST + WebSocket architecture:
- **REST:** All info endpoints are `POST https://api.hyperliquid.xyz/info` with `{"type": "..."}` body
- **WebSocket:** `wss://api.hyperliquid.xyz/ws` with JSON subscription messages
- **No auth required** for public data (reading market state)
- **Auth required** only for trading actions (placing/canceling orders)
- Rate limits are generous; we throttle at 100ms intervals to be safe

## 2. Available Data Endpoints

### Market State (Real-time)
| Endpoint | Type | What We Get |
|----------|------|-------------|
| `allMids` | REST/WS | Mid prices for all assets |
| `metaAndAssetCtxs` | REST | **Funding rate, open interest, mark price, oracle price, premium, day volume** per asset |
| `l2Book` | REST/WS | Full L2 order book (bids/asks with sizes and order counts) |
| `meta` | REST | Universe of all assets, max leverage per asset, margin tables |
| `activeAssetCtx` | WS | Streaming funding/OI/price updates per asset |

### Historical Data
| Endpoint | Type | What We Get |
|----------|------|-------------|
| `fundingHistory` | REST | Historical funding rates per coin with timestamps (paginated, 500 per page) |
| `candleSnapshot` | REST/WS | OHLCV candles: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d, 3d, 1w, 1M |
| `trades` | WS | Real-time trade stream with buyer/seller addresses, price, size |
| `userFills` / `userFillsByTime` | REST | Per-user trade history (up to 10000 most recent) |

### User-Level Data (Requires Address)
| Endpoint | Type | What We Get |
|----------|------|-------------|
| `clearinghouseState` | REST/WS | Positions, leverage, margin, liquidation prices, PnL |
| `openOrders` | REST/WS | Open limit/trigger orders |
| `userFunding` | REST/WS | Funding payments received/paid |
| `userEvents` | WS | **Fills, funding, LIQUIDATIONS, cancels** — the liquidation events include liquidated user address, notional, method (market/backstop) |

### Key WebSocket Subscriptions for Our Strategy
| Subscription | Relevance |
|-------------|-----------|
| `trades` | Real-time trade flow — can detect large trades, sweep patterns |
| `l2Book` | Order book depth — detect walls, thin liquidity zones |
| `candle` | OHLCV at various intervals |
| `activeAssetCtx` | Streaming funding + OI changes |
| `userEvents` | **CRITICAL: `WsLiquidation` events broadcast liquidations in real-time** |

## 3. What Historical Data Depth Exists?

- **Funding rates:** Available from exchange launch (April 2023). Paginated in 500-record chunks via `startTime`/`endTime` millisecond params. We can build a complete history.
- **Candles:** Available from exchange launch. All standard intervals supported.
- **Trades:** Real-time streaming only via WebSocket. No bulk historical trade download endpoint.
- **Open Interest:** No historical OI endpoint. Only current snapshot via `metaAndAssetCtxs`. Must build our own time series by polling.
- **Liquidations:** No historical liquidation endpoint. Must capture real-time via `userEvents` WebSocket. Community projects like `thunderhead-labs/hyperliquid-stats` have built their own databases.
- **Order book:** Snapshot only. No historical order book data. Must build our own.

## 4. What Our Code Currently Uses vs What's Available

### Currently Using (intelligence layer)
| Module | Endpoint | Real Data? |
|--------|----------|-----------|
| `funding_monitor.py` | `metaAndAssetCtxs` + `fundingHistory` | ✅ **YES** — real API calls |
| `liquidation_tracker.py` | `metaAndAssetCtxs` + `allMids` | ⚠️ **ESTIMATED** — calculates theoretical liq zones from leverage tiers, NOT actual liquidation data |
| `oi_tracker.py` | `metaAndAssetCtxs` + `allMids` | ✅ **YES** — real API calls for current OI, delta requires previous snapshot |
| `hyperliquid_client.py` | Multiple | ✅ Real client wrapper, correctly implemented |

### Backtester Data
| Component | Real Data? |
|-----------|-----------|
| `backtester.py generate_synthetic_data()` | ❌ **NO** — 100% synthetic GBM with random funding/OI |
| Price series | ❌ Simulated (geometric Brownian motion) |
| Funding rates | ❌ Simulated (random walk with mean reversion) |
| OI data | ❌ Simulated (correlated random) |
| Liquidation zones | ❌ Simulated (estimated from leverage tiers) |

**CRITICAL FINDING:** All backtests run on synthetic data. The negative performance of funding_extreme may partly reflect a mismatch between the synthetic funding dynamics and real market behavior. However, the INVERSION result is still mathematically valid for the synthetic data — it proves the signal logic is directionally inverted.

## 5. What We're Missing (Opportunity Gaps)

### Available via API but NOT yet used:
1. **Real-time liquidation events** (`WsLiquidation` via WebSocket) — this is the biggest gap. We estimate liquidation zones theoretically but could track ACTUAL liquidations.
2. **L2 order book depth** — we have the endpoint but don't use it for signals. Book imbalance (bid vs ask depth) is a well-known short-term signal.
3. **Trade flow data** (`trades` WebSocket) — individual trades with buyer/seller addresses. Could detect whale activity, aggressive buying/selling patterns.
4. **Candle data** — could build proper historical backtests from real OHLCV instead of synthetic GBM.
5. **User position tracking** — if we know whale addresses, we can monitor their positions, leverage, and proximity to liquidation via `clearinghouseState`.

### NOT available:
1. **Aggregate liquidation history** — no endpoint. Must build from real-time stream.
2. **Historical order book** — no endpoint. Must snapshot ourselves.
3. **Position breakdown by leverage** — only aggregate OI, no breakdown by leverage tier.
4. **Liquidation prices for non-tracked users** — only available if you know the user's address.

## 6. Recommendations for Next Steps

### Priority 1: Real Data Backtesting
- Use `candleSnapshot` to pull real BTC/ETH 5m candles going back to 2023
- Use `fundingHistory` to pull real funding rates
- Rebuild backtester to use real data instead of synthetic

### Priority 2: Live Liquidation Tracking
- Subscribe to `userEvents` WebSocket for real-time liquidation events
- Build a liquidation database
- Replace estimated liquidation zones with actual liquidation flow data

### Priority 3: Order Book Signal
- Add `l2Book` subscription
- Build order book imbalance signal (bid_depth vs ask_depth at various levels)
- This is a proven short-term alpha source in crypto

### Priority 4: Whale Tracking
- Identify known whale/MM addresses from public data
- Track their positions via `clearinghouseState`
- Build a "smart money" positioning signal

---
*This research confirms the API has significantly more capability than we currently use. The biggest immediate win is replacing synthetic backtesting with real historical data.*
