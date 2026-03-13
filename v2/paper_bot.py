"""
Mr Martingale v2 — Paper Trade Bot
====================================
True compounding | No stop-loss | dynamic long-DMA soft-bias regime filter
5-level ladder | optimized spacing/multipliers from exact-liq winner

PAPER TRADE ONLY — no real orders are placed.

Run:
    cd "/path/to/Mr Martingale"
    python -m v2.paper_bot

Or:
    bash v2/scripts/run_v2_paper.sh
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config as cfg
from .data_fetch import get_candles, get_mid_price, compute_regime
from . import notifier as notify

# ─── Logging setup ────────────────────────────────────────────────────────

def _setup_logging():
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = logging.FileHandler(str(cfg.LOG_FILE))
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if sys.stdout.isatty():
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)


log = logging.getLogger("mrm_v2.bot")

# ─── Heartbeat / reporting intervals ──────────────────────────────────────
HEARTBEAT_EVERY   = 12     # polls (~1 hour at 5-min)
DISCORD_RPT_EVERY = 72     # polls (~6 hours)

# ─── State helpers ────────────────────────────────────────────────────────

def _load_state() -> dict:
    cfg.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if cfg.STATE_FILE.exists():
        try:
            with open(cfg.STATE_FILE) as f:
                return json.load(f)
        except Exception as e:
            log.error(f"State load failed: {e} — starting fresh")
    return {
        "version":    cfg.BOT_VERSION,
        "equity":     cfg.INITIAL_EQUITY,
        "grid":       None,
        "trade_log":  [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "poll_count": 0,
    }


def _save_state(state: dict):
    cfg.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = cfg.STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(cfg.STATE_FILE)


# ─── Market state ─────────────────────────────────────────────────────────

def _fetch_market(coin: str) -> tuple:
    """Returns (price, ema34, sma14). Uses closed bars for MA calculation."""
    candles = get_candles(coin, cfg.CANDLE_INTERVAL, 60)
    closed  = candles[:-1]   # exclude in-progress bar
    closes  = pd.Series([float(c["c"]) for c in closed])

    ema34 = closes.ewm(span=cfg.EMA_SPAN, adjust=False).mean().iloc[-1]
    sma14 = closes.rolling(cfg.MA_PERIOD).mean().iloc[-1]
    price = get_mid_price(coin)
    return float(price), float(ema34), float(sma14)


def _side_is_favored(side: str, regime: str) -> bool:
    if regime == "unknown":
        return True
    return (side == "long" and regime == "bull") or (side == "short" and regime == "bear")


def _side_params(side: str, regime: str) -> dict:
    favored = _side_is_favored(side, regime)
    if cfg.REGIME_MODE == "hard" and not favored and regime != "unknown":
        return {"allowed": False, "favored": False}

    if favored or regime == "unknown":
        return {
            "allowed": True,
            "favored": favored or regime == "unknown",
            "risk_pct": cfg.RISK_PCT,
            "trigger_scale": 1.0,
            "hold_bars": cfg.MAX_HOLD_BARS,
            "level_gaps": list(cfg.LEVEL_GAPS),
        }

    return {
        "allowed": True,
        "favored": False,
        "risk_pct": cfg.RISK_PCT * cfg.UNFAV_RISK_SCALE,
        "trigger_scale": cfg.UNFAV_EDGE_K_SCALE,
        "hold_bars": max(1, int(round(cfg.MAX_HOLD_BARS * cfg.UNFAV_HOLD_SCALE))),
        "level_gaps": [g * cfg.UNFAV_SPACING_SCALE for g in cfg.LEVEL_GAPS],
    }


def _cum_drops(level_gaps: list) -> list:
    out = []
    acc = 0.0
    for g in level_gaps:
        acc += g
        out.append(acc / 100.0)
    return out


# ─── Grid construction ────────────────────────────────────────────────────

def _build_levels(side: str, price: float, equity: float, risk_pct: float = None, level_gaps: list = None) -> list:
    """
    Build all 5 ladder levels for the given side.

    True compounding: L1 notional = RISK_PCT × equity.
    Convex per-step multipliers [1.5, 2.0, 3.0, 5.0].
    Spacing may be regime-adjusted (soft bias) via level_gaps.
    """
    lev = cfg.LEVERAGE_LONG if side == "long" else cfg.LEVERAGE_SHORT
    levels = []

    risk_pct = cfg.RISK_PCT if risk_pct is None else risk_pct
    level_gaps = cfg.LEVEL_GAPS if level_gaps is None else level_gaps
    cum_drops = _cum_drops(level_gaps)

    l1_notional = risk_pct * equity
    notional = l1_notional

    for i in range(cfg.NUM_LEVELS):
        if i > 0:
            notional = notional * cfg.LEVEL_MULTIPLIERS[i - 1]

        margin = notional / lev

        if i == 0:
            target_px = price
        else:
            drop = cum_drops[i - 1]
            if side == "long":
                target_px = price * (1.0 - drop)
            else:
                target_px = price * (1.0 + drop)

        qty = notional / target_px

        levels.append({
            "level":     i + 1,
            "target_px": target_px,
            "notional":  notional,
            "margin":    margin,
            "qty":       round(qty, cfg.SZ_DECIMALS),
            "filled":    i == 0,          # L1 fills immediately
            "fill_px":   price if i == 0 else 0.0,
        })

    return levels


def _grid_from_levels(side: str, levels: list, price: float, bar_index: int) -> dict:
    """Compute blended entry and TP from filled levels, return grid dict."""
    filled = [l for l in levels if l["filled"]]
    total_qty = sum(l["qty"] for l in filled)
    blended   = sum(l["qty"] * l["fill_px"] for l in filled) / total_qty

    if side == "long":
        tp_px = blended * (1.0 + cfg.TP_PCT / 100.0)
    else:
        tp_px = blended * (1.0 - cfg.TP_PCT / 100.0)

    return {
        "side":          side,
        "levels":        levels,
        "blended_entry": blended,
        "tp_price":      tp_px,
        "total_qty":     total_qty,
        "open_bar":      bar_index,
        "opened_at":     datetime.now(timezone.utc).isoformat(),
        "max_hold_bars": cfg.MAX_HOLD_BARS,
        "risk_pct":      cfg.RISK_PCT,
        "favored":       True,
        "trigger_scale": 1.0,
        "level_gaps":    list(cfg.LEVEL_GAPS),
    }


# ─── Grid update logic ────────────────────────────────────────────────────

def _update_grid(grid: dict, price: float, bar_index: int) -> tuple:
    """
    Check price against pending level targets, TP, and max_hold timeout.

    Returns:
        (should_close: bool, close_reason: str, pnl: float)
    """
    side   = grid["side"]
    levels = grid["levels"]

    # 1. Check for new level fills
    new_fills = []
    for lv in levels:
        if not lv["filled"]:
            if side == "long"  and price <= lv["target_px"]:
                lv["filled"]  = True
                lv["fill_px"] = lv["target_px"]
                new_fills.append(lv)
            elif side == "short" and price >= lv["target_px"]:
                lv["filled"]  = True
                lv["fill_px"] = lv["target_px"]
                new_fills.append(lv)

    # 2. Recompute blended / TP
    filled = [l for l in levels if l["filled"]]
    if not filled:
        return False, "", 0.0

    total_qty = sum(l["qty"] for l in filled)
    blended   = sum(l["qty"] * l["fill_px"] for l in filled) / total_qty
    grid["blended_entry"] = blended
    grid["total_qty"]     = total_qty

    if side == "long":
        grid["tp_price"] = blended * (1.0 + cfg.TP_PCT / 100.0)
    else:
        grid["tp_price"] = blended * (1.0 - cfg.TP_PCT / 100.0)

    # Notify level fills
    for lv in new_fills:
        log.info(
            f"  L{lv['level']} filled @ ${lv['fill_px']:,.2f} | "
            f"blended=${blended:,.2f} | levels_in={len(filled)}"
        )
        notify.level_filled(side, lv["level"], lv["fill_px"], blended, len(filled))

    # 3. Check TP
    if side == "long"  and price >= grid["tp_price"]:
        pnl = _calc_pnl(grid, grid["tp_price"])
        return True, "tp", pnl

    if side == "short" and price <= grid["tp_price"]:
        pnl = _calc_pnl(grid, grid["tp_price"])
        return True, "tp", pnl

    # 4. Check max hold timeout
    bars_held = bar_index - grid["open_bar"]
    if bars_held >= grid.get("max_hold_bars", cfg.MAX_HOLD_BARS):
        pnl = _calc_pnl(grid, price)
        return True, "timeout", pnl

    return False, "", 0.0


def _calc_pnl(grid: dict, exit_px: float) -> float:
    """Compute simulated PnL for the exit price (paper trade)."""
    filled = [l for l in grid["levels"] if l["filled"]]
    total_qty  = sum(l["qty"] for l in filled)
    blended    = sum(l["qty"] * l["fill_px"] for l in filled) / total_qty
    total_notional = total_qty * blended

    if grid["side"] == "long":
        gross = total_qty * (exit_px - blended)
    else:
        gross = total_qty * (blended - exit_px)

    fees = total_notional * (cfg.TAKER_FEE + cfg.MAKER_FEE)
    return gross - fees


def _record_trade(state: dict, grid: dict, exit_px: float, reason: str, pnl: float):
    """Append trade to state trade log."""
    filled = [l for l in grid["levels"] if l["filled"]]
    state["trade_log"].append({
        "side":          grid["side"],
        "opened_at":     grid["opened_at"],
        "closed_at":     datetime.now(timezone.utc).isoformat(),
        "open_bar":      grid["open_bar"],
        "levels_filled": len(filled),
        "blended_entry": grid["blended_entry"],
        "exit_px":       exit_px,
        "close_reason":  reason,
        "pnl":           round(pnl, 4),
        "equity_after":  round(state["equity"], 4),
    })
    # Keep last 500 trades to avoid unbounded state growth
    if len(state["trade_log"]) > 500:
        state["trade_log"] = state["trade_log"][-500:]


# ─── Main loop ────────────────────────────────────────────────────────────

def run():
    _setup_logging()

    log.info("=" * 60)
    log.info(f"Mr Martingale {cfg.BOT_VERSION}  |  PAPER TRADE")
    log.info(f"Coin: {cfg.COIN} | Interval: {cfg.CANDLE_INTERVAL}")
    log.info(
        f"Ladder: {cfg.NUM_LEVELS}L | Gaps: {cfg.LEVEL_GAPS} | "
        f"Mult: {cfg.LEVEL_MULTIPLIERS}"
    )
    log.info(f"Risk: {cfg.RISK_PCT*100:.0f}% equity | TP: {cfg.TP_PCT}% | MaxHold: {cfg.MAX_HOLD_BARS}b")
    log.info(f"Regime filter: {cfg.REGIME_MA_PERIOD}d SMA ({cfg.REGIME_MODE} bias) | Long lev: {cfg.LEVERAGE_LONG}× | Short lev: {cfg.LEVERAGE_SHORT}×")
    log.info("=" * 60)

    state     = _load_state()
    bar_index = state.get("bar_index", 0)
    poll_count = state.get("poll_count", 0)
    last_regime_refresh = 0
    regime    = state.get("last_regime", "unknown")
    regime_sma = state.get("regime_sma", None)

    log.info(f"State loaded: equity=${state['equity']:.2f} | trades={len(state['trade_log'])}")

    # Initial regime fetch
    regime, regime_sma, _price = compute_regime(cfg.COIN, cfg.REGIME_MA_PERIOD, cfg.REGIME_FETCH_N)
    state["last_regime"] = regime
    state["regime_sma"]  = regime_sma
    _save_state(state)

    notify.bot_started(state["equity"], regime, regime_sma)

    while True:
        try:
            poll_count += 1
            bar_index  += 1

            # ── Refresh regime every ~6 hours (72 polls) ──────────────────
            if poll_count - last_regime_refresh >= DISCORD_RPT_EVERY:
                regime, regime_sma, _p = compute_regime(
                    cfg.COIN, cfg.REGIME_MA_PERIOD, cfg.REGIME_FETCH_N
                )
                state["last_regime"] = regime
                state["regime_sma"]  = regime_sma
                last_regime_refresh  = poll_count

            # ── Fetch market state ────────────────────────────────────────
            price, ema34, sma14 = _fetch_market(cfg.COIN)
            pct_below_ema = (ema34 - price) / ema34 * 100
            pct_below_sma = (sma14 - price) / sma14 * 100
            pct_above_ema = (price - ema34) / ema34 * 100
            pct_above_sma = (price - sma14) / sma14 * 100

            grid = state.get("grid")

            log.info(
                f"[PAPER] {cfg.COIN} ${price:,.2f} | "
                f"↓EMA34 {pct_below_ema:+.2f}% ↓SMA14 {pct_below_sma:+.2f}% | "
                f"regime={regime.upper()} | "
                f"{'GRID:' + grid['side'].upper() if grid else 'flat'} | "
                f"eq=${state['equity']:.2f}"
            )

            # ── Check active grid ─────────────────────────────────────────
            if grid is not None:
                bars_held = bar_index - grid["open_bar"]
                should_close, reason, pnl = _update_grid(grid, price, bar_index)

                if should_close:
                    exit_px = grid["tp_price"] if reason == "tp" else price
                    state["equity"] += pnl
                    _record_trade(state, grid, exit_px, reason, pnl)

                    if reason == "tp":
                        log.info(
                            f"✅ {grid['side'].upper()} TP HIT @ ${exit_px:,.2f} | "
                            f"PnL: ${pnl:+.4f} | equity=${state['equity']:.2f}"
                        )
                        notify.tp_hit(grid["side"], exit_px, grid["blended_entry"],
                                      pnl, state["equity"])
                    else:
                        log.info(
                            f"⏰ {grid['side'].upper()} TIMEOUT ({bars_held}b) @ ${exit_px:,.2f} | "
                            f"PnL: ${pnl:+.4f} | equity=${state['equity']:.2f}"
                        )
                        notify.timeout_close(grid["side"], exit_px, pnl,
                                             state["equity"], bars_held)

                    state["grid"] = None
                else:
                    state["grid"] = grid    # save updated fills

            # ── Open new grid ─────────────────────────────────────────────
            if state["grid"] is None:
                long_cfg = _side_params("long", regime)
                short_cfg = _side_params("short", regime)

                long_trigger = cfg.LONG_TRIGGER_PCT * long_cfg.get("trigger_scale", 1.0)
                short_trigger = cfg.SHORT_TRIGGER_PCT * short_cfg.get("trigger_scale", 1.0)

                long_signal = (
                    long_cfg.get("allowed", False) and
                    pct_below_ema >= long_trigger and
                    pct_below_sma >= long_trigger
                )
                short_signal = (
                    short_cfg.get("allowed", False) and
                    pct_above_ema >= short_trigger and
                    pct_above_sma >= short_trigger
                )

                if long_signal:
                    levels = _build_levels("long", price, state["equity"], risk_pct=long_cfg["risk_pct"], level_gaps=long_cfg["level_gaps"])
                    grid   = _grid_from_levels("long", levels, price, bar_index)
                    grid["max_hold_bars"] = long_cfg["hold_bars"]
                    grid["risk_pct"] = long_cfg["risk_pct"]
                    grid["favored"] = long_cfg["favored"]
                    grid["trigger_scale"] = long_cfg["trigger_scale"]
                    grid["level_gaps"] = list(long_cfg["level_gaps"])
                    state["grid"] = grid

                    l1_notional = long_cfg["risk_pct"] * state["equity"]
                    tp_px = grid["tp_price"]
                    bias = "FAV" if long_cfg["favored"] else "UNFAV"
                    log.info(
                        f"📈 LONG opened ({bias}) @ ${price:,.2f} | TP: ${tp_px:,.2f} | "
                        f"L1 notional: ${l1_notional:.2f} | trig={long_trigger:.2f}% | hold={long_cfg['hold_bars']}b | eq=${state['equity']:.2f}"
                    )
                    notify.grid_opened("long", price, tp_px, l1_notional, state["equity"])

                elif short_signal:
                    levels = _build_levels("short", price, state["equity"], risk_pct=short_cfg["risk_pct"], level_gaps=short_cfg["level_gaps"])
                    grid   = _grid_from_levels("short", levels, price, bar_index)
                    grid["max_hold_bars"] = short_cfg["hold_bars"]
                    grid["risk_pct"] = short_cfg["risk_pct"]
                    grid["favored"] = short_cfg["favored"]
                    grid["trigger_scale"] = short_cfg["trigger_scale"]
                    grid["level_gaps"] = list(short_cfg["level_gaps"])
                    state["grid"] = grid

                    l1_notional = short_cfg["risk_pct"] * state["equity"]
                    tp_px = grid["tp_price"]
                    bias = "FAV" if short_cfg["favored"] else "UNFAV"
                    log.info(
                        f"📉 SHORT opened ({bias}) @ ${price:,.2f} | TP: ${tp_px:,.2f} | "
                        f"L1 notional: ${l1_notional:.2f} | trig={short_trigger:.2f}% | hold={short_cfg['hold_bars']}b | eq=${state['equity']:.2f}"
                    )
                    notify.grid_opened("short", price, tp_px, l1_notional, state["equity"])

                else:
                    if pct_below_ema >= cfg.LONG_TRIGGER_PCT and pct_below_sma >= cfg.LONG_TRIGGER_PCT and not long_cfg.get("favored", True):
                        log.info(f"[SOFT BIAS] LONG dislocation seen in {regime} regime but filtered by stricter unfavored settings")
                    if pct_above_ema >= cfg.SHORT_TRIGGER_PCT and pct_above_sma >= cfg.SHORT_TRIGGER_PCT and not short_cfg.get("favored", True):
                        log.info(f"[SOFT BIAS] SHORT dislocation seen in {regime} regime but filtered by stricter unfavored settings")

            # ── Persist state ─────────────────────────────────────────────
            state["poll_count"] = poll_count
            state["bar_index"]  = bar_index
            _save_state(state)

            # ── Heartbeat ─────────────────────────────────────────────────
            if poll_count % HEARTBEAT_EVERY == 0:
                grid_side   = state["grid"]["side"] if state["grid"] else None
                bars_held_h = (bar_index - state["grid"]["open_bar"]) if state["grid"] else None
                notify.heartbeat(state["equity"], regime, grid_side, bars_held_h)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — saving state and exiting")
            state["poll_count"] = poll_count
            state["bar_index"]  = bar_index
            _save_state(state)
            break

        except Exception as exc:
            log.exception(f"Loop error: {exc}")
            notify.error_alert(str(exc)[:200])

        time.sleep(cfg.POLL_SECONDS)


# ─── Entry point ──────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mr Martingale v2 paper trade bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Single market check, no loop")
    args = parser.parse_args()

    if args.dry_run:
        _setup_logging()
        price, ema34, sma14 = _fetch_market(cfg.COIN)
        regime, sma400, _ = compute_regime(
            cfg.COIN, cfg.REGIME_MA_PERIOD, cfg.REGIME_FETCH_N
        )
        log.info(f"DRY-RUN: {cfg.COIN} ${price:,.2f}")
        log.info(f"  EMA34: ${ema34:,.2f}  SMA14: ${sma14:,.2f}")
        sma_str = f"${sma400:,.0f}" if sma400 else "n/a"
        log.info(f"  {cfg.REGIME_MA_PERIOD}d SMA: {sma_str}  Regime: {regime.upper()} ({cfg.REGIME_MODE})")
        log.info(f"  Base cum depths: {[f'{d*100:.1f}%' for d in cfg.CUM_DROPS]}")
        log.info(f"  Unfav scales: risk={cfg.UNFAV_RISK_SCALE} spacing={cfg.UNFAV_SPACING_SCALE} trigger={cfg.UNFAV_EDGE_K_SCALE} hold={cfg.UNFAV_HOLD_SCALE}")
        return

    run()


if __name__ == "__main__":
    main()
