# Mr. Martingale Changelog

## v3.0.0-proposed — 2026-03-12

### Canonical strategy memorialized as v3.0
- Promoted the current winning research branch to `MR_MARTINGALE_V3_SPEC.md` and `MR_MARTINGALE_V3_STRATEGY.md`.
- v3.0 reflects the latest exact-liq validated winner:
  - true compounding
  - no stop-loss
  - 5 levels
  - 440d SMA soft-bias regime model
  - level gaps `[0.5, 1.5, 9.0, 6.0]`
  - level multipliers `[2.0, 2.5, 2.5, 7.0]`
  - risk per entry 25%
  - short trigger 1.5%
  - max hold 160 bars
- Explicitly records that:
  - earlier optimistic 30% risk results were invalidated by exact-liq simulation
  - v2.2 regime machine was rejected
  - v2.3 favored-side amplification was rejected
- This is a documentary / canonical-label update only. Live bot remains unchanged.

## v2.1.1-paper — 2026-03-12

### v2.1 corrected to exact-liq winner (paper target updated)
- Updated v2 docs and paper target from the stale 30% risk / 400DMA / `[0.5,1.5,8,7]` / `[1.5,2.0,3.0,5.0]` branch.
- New exact-liq winner memorialized and targeted for paper bot:
  - risk: 25%
  - DMA: 440
  - soft bias scales: risk×0.65, spacing×1.50, trigger×1.50, hold×0.50
  - level gaps: `[0.5, 1.5, 9.0, 6.0]`
  - level multipliers: `[2.0, 2.5, 2.5, 7.0]`
  - short trigger: 1.5%
  - max hold: 160 bars
- Research basis: exact liquidation simulation; 0 liquidations, ~189% CAGR fair comparison, ~194% at optimized DMA.
- Live bot remains untouched.

## v2.0.0-paper — 2026-03-11

### Mr Martingale v2 paper-trade bot launched (separate from live v1)
- **Files created:** `v2/__init__.py`, `v2/config.py`, `v2/data_fetch.py`, `v2/notifier.py`, `v2/paper_bot.py`
- **Launch script:** `v2/scripts/run_v2_paper.sh`
- **State:** `v2/state/v2_paper_state.json` | **Log:** `v2/logs/v2_paper_bot.log`
- **v1 live bot: UNTOUCHED** (all `execution/` files unchanged)

#### v2 parameters:
- true compounding: L1 notional = 30% × equity (recomputed per grid open)
- no stop-loss
- 5 levels, late_expand spacing: `[0.5, 1.5, 8.0, 7.0]`
  - cumulative depths: L2=0.5%, L3=2.0%, L4=10.0%, L5=17.0%
  - resolved from asymmetric_compounding_sweep [8,7] best zero-liq config
- convex per-step multipliers: `[1.5, 2.0, 3.0, 5.0]`
- 400-day SMA regime filter: bull→longs only, bear→shorts only
- max_hold_bars=96 (16 days at 4h)
- initial equity: $400

#### First run snapshot (2026-03-11 ~21:01 UTC):
- BTC: $69,218 | EMA34: $69,181 | SMA14: $69,859
- Regime: BEAR (price -29.2% below 400d SMA $97,716)
- Status: flat/idle (awaiting SHORT trigger)

## v2.0.0-proposed — 2026-03-11

### Proposed strategy branch updated after full iterative optimization (not deployed)
- Updated `MR_MARTINGALE_V2_SPEC.md` and `MR_MARTINGALE_V2_STRATEGY.md` to reflect the new leading candidate.
- Replaced the older 365DMA / 4-level / flat-2.5x branch with the current optimized branch:
  - true compounding
  - no stop-loss
  - 5 ladder levels
  - 400DMA regime filter
  - `late_expand` spacing profile
  - convex per-level multipliers `[1.5, 2.0, 3.0, 5.0]`
  - risk per entry: 30%
  - max hold: 96 bars
- Updated memorialized research result to: 2019-01-03 → 2026-03-09 backtest, 320.5% CAGR, $1,000 → $26.4M, zero liquidations, zero timeout losses, 67.4% max drawdown.
- This changelog entry remains documentary only. The live bot/version was **not** changed by this step.

## v1.3.1 — 2026-02-27

### Performance reporting accuracy
- Added fill classification to separate **strategy closes** from **operational scratches** (micro round-trip artifacts during startup/process churn).
- Console headline now shows **Strategy Win %** (instead of blended close-fill win%).
- Trade history table now includes `category` (strategy vs operational).
- Analytics tab adds **Outcome Classification** panel:
  - strategy closes/wins/losses/win rate/net
  - operational scratch count/net

## v1.3.0 — 2026-02-26

### Local Trading Console (new)
- Added Bloomberg-style local dashboard: `execution/console_app.py`.
- Includes:
  - live account/grid status
  - active orders panel
  - trade history + performance history
  - expense analysis (fees + funding) + expense ratio
  - price chart with candlesticks, EMA34/SMA14, trigger bands, trade markers, active order overlays
  - historical equity + projected PnL (dashed), with projected expenses derived from historical data

### Manual control plane (new)
- Added file-based command bus: `execution/command_bus.py`.
- Console buttons now queue manual actions for the live bot:
  - `manual_long`
  - `manual_short`
  - `manual_close`
- Bot processes command results with audit trail (`execution/commands/pending|processed`).

### Bot runtime improvements
- `grid_bot.py` now watches for queued manual commands during sleep intervals, enabling near-instant manual control without waiting full 5-minute poll.
- Added startup processing for pending commands.

### Versioning
- Bumped `BOT_VERSION` to `1.3.0`.

## v1.2.0 — 2026-02-26

### Safety & reliability hardening
- Enforced **single-side invariant** in live loop (no simultaneous long+short grids).
- Added **startup reconciliation** against exchange truth:
  - validates local state vs exchange position size
  - reconciles missing ladder order fills via explicit order status/fills
  - halts on ambiguous/mismatched state
- Replaced "missing open order == filled" behavior with **explicit order-status verification**.
- Replaced "missing TP order == TP hit" behavior with **explicit TP fill verification**.
- Updated force-close flow to **verify market close fill** before resetting state.
- Scoped cancellation from global coin-level behavior to **grid-owned OIDs**.

### Configuration & versioning
- Added `BOT_VERSION = "1.2.0"` in `execution/config.py`.
- Added root `VERSION` file.

### Security hygiene
- Removed hardcoded Discord webhook fallback from `execution/notifier.py`.

### API helpers
- Added in `execution/hl_client.py`:
  - `get_position()`
  - `query_order()`
  - `get_order_status()`
  - `get_order_fill_summary()`
  - `cancel_orders()`
- Added matching safety shims in `execution/paper_client.py`.
