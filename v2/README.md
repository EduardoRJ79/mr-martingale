# Mr Martingale v2 — Paper Trade Bot

**Status:** Paper trade only | NOT the live bot  
**Version:** 3.0.0-paper  
**Versioned from:** `MR_MARTINGALE_V2_SPEC.md` + exact-liq optimization research (2026-03-12)

---

## What This Is

v2 is a **separate paper-trade implementation** of the researched next-gen config.  
The v1 live bot in `execution/` is completely untouched.

Key v2 design:
- **True compounding** — every new grid recalculates size from current equity
- **No stop-loss** — spacing + regime filter are the only risk controls
- **5-level ladder** with **late_expand spacing**
- **440-day SMA soft-bias regime filter** — favored side full strength, unfavored side degraded (not disabled)
- **Convex multipliers** `[1.5, 2.0, 3.0, 5.0]` (accelerating ladder)
- **30% risk per entry** (L1 notional = 30% of equity)
- **96-bar max hold** timeout (16 days at 4h bars)

---

## Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Coin | BTC | |
| Candle interval | 4h | |
| Regime filter | 440-day SMA soft bias | Favored side full / unfavored side degraded |
| Long trigger | 0.5% below EMA34+SMA14 | |
| Short trigger | 2.5% above EMA34+SMA14 | |
| Level gaps | [0.5, 1.5, 9.0, 6.0]% | exact-liq optimized profile |
| Cumulative depths | L2=0.5%, L3=2.0%, L4=10.0%, L5=17.0% | |
| Multipliers | [2.0, 2.5, 2.5, 7.0] | exact-liq optimized |
| Risk per entry | 25% | L1 notional = 25% × equity |
| Leverage (long) | 20× | |
| Leverage (short) | 15× | |
| TP | 0.5% | from blended entry |
| Max hold | 160 bars | ~26.7 days |
| Starting equity | $1,000 | |

### Soft-bias regime model (v2.1)

The current paper bot no longer uses a hard on/off regime gate.

Instead:
- **Favored side** (longs in bull / shorts in bear) uses the full baseline config
- **Unfavored side** remains tradable, but is degraded:
  - risk = **65%** of normal
  - spacing = **1.5x wider**
  - trigger threshold = **1.5x stricter**
  - max hold = **50%** of normal

This remains the v2.1 regime model, but with corrected exact-liq optimized parameters from the 2026-03-12 re-optimization pass.

### Spacing interpretation (late_expand)

"Late expand" means the ladder stays tight early (normal mean-reversion fills)
and expands massively at L3→L4 and L4→L5, preventing liquidation on adverse moves:

```
Level | Step gap | Cumulative depth from trigger
L1    |   —      |  0.0% (entry at trigger)
L2    |   0.5%   |  0.5%
L3    |   1.5%   |  2.0%
L4    |   9.0%   | 11.0%   ← deeper protection moved earlier
L5    |   6.0%   | 17.0%   ← final deep extension
```

Resolved from `reports/mrm_asymmetric_compounding_study_2026-03-10.md` and
`reports/l4l5_spacing_sweep_report_2026-03-10.md` — the `[8,7]` symmetric config
was the only zero-liquidation survivor in the 2018–2026 stress test.

---

## File Structure

```
v2/
├── __init__.py
├── config.py       — all parameters
├── data_fetch.py   — standalone market data (no v1 dependency)
├── notifier.py     — Discord notifications tagged [MrM-v2 PAPER]
├── paper_bot.py    — main loop
├── state/
│   └── v2_paper_state.json   (created on first run)
├── logs/
│   └── v2_paper_bot.log      (created on first run)
├── scripts/
│   └── run_v2_paper.sh       (launch/stop helper)
└── README.md
```

---

## Running It

### Dry-run check (no loop, just market + regime status):
```bash
cd "/path/to/Mr Martingale"
bash v2/scripts/run_v2_paper.sh --dry-run
```

### Foreground (Ctrl+C to stop):
```bash
bash v2/scripts/run_v2_paper.sh --fg
```

### Background (production):
```bash
bash v2/scripts/run_v2_paper.sh
# Monitor: tail -f v2/logs/v2_paper_bot.log
# Stop:    kill $(cat v2/state/v2_paper_bot.pid)
```

### Directly via Python:
```bash
cd "/path/to/Mr Martingale"
source ~/.openclaw/ws-731228/.secrets/hyperliquid.env
python3 -m v2.paper_bot
python3 -m v2.paper_bot --dry-run
```

---

## Monitoring

**Logs:** `v2/logs/v2_paper_bot.log`  
**State:** `v2/state/v2_paper_state.json`  
**Discord:** All events tagged `[MrM-v2 PAPER]` — same webhook as v1

State JSON keys:
- `equity` — current paper equity
- `grid` — active grid (null if flat)
- `trade_log` — last 500 trades with PnL
- `last_regime` — `bull` / `bear` / `unknown`

---

## v1 vs v2 Side-by-Side

| Aspect | v1 (live) | v2 (paper) |
|--------|-----------|------------|
| Files | `execution/` | `v2/` |
| Mode | LIVE | PAPER ONLY |
| Sizing | Fixed 1.6% per level | True compounding 30% L1 notional |
| Multiplier | 2.0× uniform | [1.5, 2.0, 3.0, 5.0] convex |
| Spacing | [0.5, 1.5, 3.0, 3.0]% | [0.5, 1.5, 8.0, 7.0]% (late_expand) |
| Regime filter | None | 400-day SMA |
| Max hold | 120h | 96 bars (16 days) |
| Stop-loss | None | None |

---

## Caveats

1. **Daily candle availability**: 400d SMA requires ~14 months of daily data from Hyperliquid.
   If fewer bars are available, regime defaults to `unknown` (both directions allowed).
2. **True compounding at small equity**: At $400 starting equity, L4+L5 margin can exceed
   account size if all levels fill simultaneously. This is by design — the late_expand
   spacing makes simultaneous deep fills extremely unlikely.
3. **BTC-only**: v2 is BTC-only initially (same as v1 live bot).
4. **Backtest caveat**: 320.5% CAGR backtest was run 2019–2026 with BTC. Results are
   path-dependent and influenced by 2021. Do not treat as a guarantee.

---

*v2.1 is experimental. The v1 live bot remains the production system until deliberate cutover.*
