# HL Manager — V1 Portfolio Manager Spec

Version: v0.5 draft (2026-02-28)
Owner: Brian + Winnie
Status: Direction approved; ready for paper-mode implementation

## 1) Plain-English behavior (the important part)

HL Manager is the capital allocator above Mr. Martingale.

- Mr. Martingale trades BTC perps.
- Spot sleeve (UBTC/USDC) is the primary lower-risk investment sleeve **and** liquidity backstop.

Every 5 minutes, HL Manager checks grid stress, perp balances, and spot balances.

### During stress (L4 hit on either side)
Manager enters **Defense Mode** and "slams" collateral into perp:
1. Compute current deployed perp margin.
2. Target top-up = **2x deployed margin**.
3. Use spot USDC first.
4. If spot USDC is short, sell UBTC -> USDC for remainder.
5. Transfer USDC to perp.

Example: if deployed margin is ~$200 at L4, manager targets ~$400 top-up.

### During profits
When realized perp PnL increases, manager automatically sweeps:
- **25% of each positive realized-profit increment** to spot sleeve (USDC), tweakable via config.

### Capital health alert
Manager also monitors spot-vs-perp coverage:
- If **spot sleeve notional < 100% of perp equity** (ratio < 1.0), notify Brian to add funds.
- Example: perp = $400 -> alert if spot drops below $400.

So behavior is:
- Stress -> spot funds defend perp aggressively.
- Recovery/profit -> perp pays spot back continuously.
- Low spot coverage -> proactive add-funds alert.

## 2) V1 Scope
- One account, BTC only.
- Perp symbol: `BTC` (existing Mr. Martingale).
- Spot symbol: `UBTC/USDC`.
- Spot sleeve may hold either UBTC or USDC (actively traded).
- Transfers use Hyperliquid `usdClassTransfer`.
- Decision loop every 5 minutes.
- All manager notifications mirrored to all configured webhooks.

## 3) V1 Non-goals
- Multi-asset allocation.
- Sub-accounts.
- Full policy optimizer/backtester.
- Regime classifier.

## 4) Core parameters (V1 defaults)

### Global
- `PM_ENABLED = true`
- `PM_PAPER_MODE = true` (initial observation period)
- `PM_LOOP_SECONDS = 300`
- `PM_STATUS_HEARTBEAT_MIN = 120`
- `PM_MIN_NOTIONAL_USDC = 12`

### Defense Mode (Spot -> Perp)
- `PM_DEFENSE_TRIGGER_LEVEL = 4`
- `PM_DEFENSE_SIDES = ["long", "short"]`
- `PM_DEFENSE_CONFIRM_LOOPS = 1` (trigger on hit)
- `PM_DEFENSE_MULTIPLIER = 2.0` (target top-up = 2x deployed margin)
- `PM_DEFENSE_MAX_ACTIONS_PER_CYCLE = 1`
- **No hard daily cap**
- `PM_SPOT_RESERVE_FLOOR_PCT = 0.00` (all-in defense; spot sleeve can be fully deployed when needed)

### Profit Sweep (Perp -> Spot)
- `PM_SWEEP_ENABLED = true`
- `PM_SWEEP_PCT = 0.25` (25% of each positive realized increment)
- `PM_SWEEP_MIN_NOTIONAL_USDC = 12`
- `PM_SWEEP_COOLDOWN_MIN = 0` (event-driven)
- `PM_PERP_FREE_FLOOR_USDC = 220` (never sweep below this)

### Coverage Alert (notify to add funds)
- `PM_SPOT_TO_PERP_MIN_RATIO = 1.00`
- `PM_SPOT_TO_PERP_ALERT_COOLDOWN_MIN = 60`

## 5) No daily cap policy (as requested)
V1 does **not** impose a daily movement cap.

Control is through:
- one defense action per cycle,
- spot reserve floor (0% all-in),
- perp free floor,
- kill-switch,
- explicit underfunded-defense alerts,
- and spot/perp coverage alerts.

## 6) Decision order (per loop)
1. Build snapshot: grid state, deployed margin, side, balances, realized PnL.
2. Run invariants; if failed -> kill-switch.
3. Compute spot/perp coverage ratio and emit add-funds alert if below threshold.
4. Evaluate Defense Mode first (priority).
5. If no defense action, evaluate profit sweep.
6. Record `noop` with reason if no action.
7. Emit 2h status heartbeat.
8. Append audit row.

## 7) Action logic details

### A) Defense action
Inputs:
- `deployed_margin_usdc`
- `spot_usdc_free`
- `spot_ubtc_notional`

Compute:
- `target_topup = deployed_margin_usdc * PM_DEFENSE_MULTIPLIER`
- `spot_total_notional = spot_usdc_free + spot_ubtc_notional`
- `max_draw_from_spot = spot_total_notional * (1 - PM_SPOT_RESERVE_FLOOR_PCT)`

Execution:
- use spot USDC first,
- then sell UBTC for shortfall,
- then transfer available USDC spot -> perp,
- never exceed `max_draw_from_spot`.

If full target is not possible within reserve floor:
- perform partial top-up up to allowed amount,
- emit critical alert: `DEFENSE_UNDERFUNDED` with shortfall amount.

### B) Profit sweep action
Inputs:
- `realized_pnl_total`
- `last_realized_seen`

Compute:
- `delta = realized_pnl_total - last_realized_seen`
- if `delta > 0`, `sweep = delta * PM_SWEEP_PCT`

Execution:
- transfer `sweep` (or capped by perp free floor) perp -> spot,
- keep proceeds in spot sleeve as USDC (spot strategy decides UBTC conversion).

If `delta <= 0`:
- no sweep,
- update realized marker.

### C) Coverage alert action
Inputs:
- `spot_total_notional`
- `perp_equity_usdc`

Compute:
- `coverage_ratio = spot_total_notional / perp_equity_usdc` (if perp_equity > 0)

If `coverage_ratio < PM_SPOT_TO_PERP_MIN_RATIO`:
- emit `SPOT_COVERAGE_LOW` alert (respect alert cooldown),
- include `recommended_add_funds = max(0, perp_equity_usdc - spot_total_notional)`.

## 8) Kill-switch (hard stop)
Enter `KILLED` mode (no fund movement) on:
- 3 consecutive action failures,
- reconciliation mismatch > 5 USDC,
- spot execution slippage > 60 bps,
- any post-action floor/invariant breach,
- manual flag `PM_KILL_SWITCH=true`.

When killed:
- continue monitoring and heartbeat,
- send immediate alert,
- require explicit manual reset.

## 9) State + idempotency
`execution/pm_state.json`:
- `kill_switch`
- `last_topup_ts`
- `last_sweep_ts`
- `last_realized_seen`
- `last_spot_coverage_alert_ts`
- `cycle_topup_done` (keyed by `cycle_id`)
- `consecutive_failures`
- rolling counters/metrics (diagnostics)

Idempotency key:
- `defense:{cycle_id}:{trigger_level}`

## 10) Audit log contract
`execution/pm_audit.jsonl` append-only rows with:
- `ts`, `mode`, `decision` (`defense|sweep|coverage_alert|noop|kill`), `reason_code`
- `cycle_id`, `deepest_level`, `side`, `deployed_margin_usdc`
- `spot_total_notional`, `perp_equity_usdc`, `coverage_ratio`
- requested vs executed notional
- pre/post balances (perp + spot)
- slippage bps
- webhook delivery results

## 11) Notification policy (ALL mirrored)
Send for:
- manager start/stop,
- defense planned/executed/partial/skipped/failed,
- sweep planned/executed/skipped/failed,
- `SPOT_COVERAGE_LOW` (add-funds alert),
- kill-switch engaged/reset,
- 2h status heartbeat.

Prefix:
- paper: `🧪 PM`
- live: `⚙️ PM`

## 12) Rollout
- Phase A: paper mode for 7–14 days.
- Phase B: guarded live enablement.
- Phase C: tune multiplier/floors/sweep % from real logs.

## 13) Implementation checklist
- Add `execution/portfolio_manager.py`
- Add `execution/pm_state.py`
- Add PM config block in `execution/config.py`
- Extend notifier helpers for PM reason codes
- Add paper simulation for spot sell + class transfer
- Add structured JSONL audit writer
- Add runner: `python -m execution.portfolio_manager`

---

Defaults frozen per latest direction:
- Spot reserve floor: 0% (all-in defense)
- Spot/perp minimum coverage for alerting: 1.0x
- Profit sweep: 25%
