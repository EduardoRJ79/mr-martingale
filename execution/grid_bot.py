"""
Mr Martingale — dual-sided (LONG + SHORT) main loop.

Long grid:  opens when BTC price is 0.5%+ below both EMA34 & SMA14
Short grid: opens when BTC price is 2.5%+ above both EMA34 & SMA14

Runtime safety model (v1.2.0):
- One active side at a time (long OR short)
- Exchange-truth order checks (no "missing order == fill" inference)
- Startup reconciliation against exchange state

Run:  python -m execution.grid_bot
"""

import logging
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from . import config as cfg
from . import grid_state as gs_mod
from .grid_state import LONG, SHORT, GridState, BotState
from . import notifier
from . import command_bus
from . import binance_data

if cfg.PAPER_TRADE:
    from . import paper_client as hl
    import logging as _l
    _l.getLogger("grid_bot").info("🗒️  PAPER TRADE MODE — no real orders will be placed")
else:
    from . import hl_client as hl

_root = logging.getLogger()
# Only configure if not already set up
if not _root.handlers:
    _fmt = logging.Formatter("%(asctime)s %(name)-16s %(levelname)-8s %(message)s")
    _log_path = cfg.STATE_FILE.parent / "grid_bot.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _fh = logging.FileHandler(str(_log_path))
    _fh.setFormatter(_fmt)
    _root.setLevel(logging.INFO)
    _root.addHandler(_fh)
    # Only add stdout handler if stdout is a real terminal (not redirected to log file)
    if sys.stdout.isatty():
        _sh = logging.StreamHandler(sys.stdout)
        _sh.setFormatter(_fmt)
        _root.addHandler(_sh)
log = logging.getLogger("grid_bot")

HEARTBEAT_EVERY = 12         # polls (~1 hour at 5-min interval)
WEBHOOK_REPORT_EVERY = 24    # polls (~2 hours at 5-min interval)

CANCEL_STATUSES = {"canceled", "cancelled", "rejected", "expired"}
UNKNOWN_STATUS = "unknownoid"
QTY_TOL = max(10 ** (-cfg.SZ_DECIMALS), 1e-6)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _status_lower(s) -> str:
    return str(s).strip().lower() if s is not None else ""


def parse_market_fill_result(result: dict):
    """
    Returns (filled_qty, avg_px, error_msg).
    error_msg is None when parse indicates a fill.
    """
    try:
        status = result["response"]["data"]["statuses"][0]
    except Exception as e:
        return 0.0, None, f"unparseable result: {e} | raw={result}"

    if "filled" in status:
        try:
            fill = status["filled"]
            qty = float(fill.get("totalSz") or fill.get("sz") or 0.0)
            px = float(fill.get("avgPx") or fill.get("px"))
            return qty, px, None
        except Exception as e:
            return 0.0, None, f"filled status parse error: {e} | raw={status}"

    err = status.get("error") if isinstance(status, dict) else str(status)
    return 0.0, None, f"not filled: {err}"


def grid_resting_oids(grid: GridState):
    """Only unfilled ladder oids (+optional tp separately)."""
    return [lv.oid for lv in grid.levels if (not lv.filled and lv.oid is not None)]


def cancel_grid_orders(grid: GridState, include_tp: bool = True):
    oids = grid_resting_oids(grid)
    if include_tp and grid.tp_oid is not None:
        oids.append(grid.tp_oid)
    if not oids:
        return
    if hasattr(hl, "cancel_orders"):
        hl.cancel_orders(cfg.COIN, oids)
    else:
        for oid in sorted(set(oids)):
            try:
                hl.cancel_order(cfg.COIN, oid)
                time.sleep(0.2)
            except Exception as e:
                log.error(f"Cancel failed {oid}: {e}")


def signed_qty_for_state(bs: BotState) -> float:
    q = 0.0
    if bs.long_grid.active:
        q += float(bs.long_grid.total_qty)
    if bs.short_grid.active:
        q -= float(bs.short_grid.total_qty)
    return q


def paper_unrealized_pnl(grid: GridState, mark_px: float) -> float:
    if (grid is None) or (not grid.active):
        return 0.0

    pnl = 0.0
    for lv in grid.levels:
        if not lv.filled:
            continue
        qty = float(lv.fill_qty or 0.0)
        entry = float(lv.fill_px or lv.target_px or 0.0)
        if qty <= 0 or entry <= 0:
            continue
        if grid.side == LONG:
            pnl += qty * (mark_px - entry)
        else:
            pnl += qty * (entry - mark_px)
    return pnl


def paper_stop_proxy(grid: GridState):
    if (grid is None) or (not grid.active) or (not grid.levels):
        return None
    ladder = [float(lv.target_px) for lv in grid.levels if lv.target_px]
    if not ladder:
        return None
    return min(ladder) if grid.side == LONG else max(ladder)


def paper_runtime_metrics(bs: BotState, mark_px: float) -> dict:
    active_grid = None
    if bs.long_grid.active:
        active_grid = bs.long_grid
    elif bs.short_grid.active:
        active_grid = bs.short_grid

    unrealized = paper_unrealized_pnl(active_grid, mark_px) if active_grid else 0.0
    stop_px = paper_stop_proxy(active_grid)

    drawdown = None
    if hasattr(hl, "get_equity_snapshot"):
        try:
            snap = hl.get_equity_snapshot(unrealized_pnl=unrealized)
            drawdown = snap.get("drawdown_pct")
        except Exception as e:
            log.warning(f"Paper telemetry snapshot failed: {e}")

    return {
        "unrealized_pnl": unrealized,
        "stop": stop_px,
        "drawdown": drawdown,
    }


def _signed_position_size() -> float:
    """Exchange-truth signed BTC position size (+long / -short)."""
    try:
        pos = hl.get_position(cfg.COIN)
        return float(pos.get("size", 0.0))
    except Exception as e:
        log.error(f"Could not fetch position size: {e}")
        return 0.0


def _side_position_matches(side: str, signed_size: float) -> bool:
    if side == LONG:
        return signed_size > QTY_TOL
    return signed_size < -QTY_TOL


def reconcile_grid_from_exchange(bs: BotState, side: str, open_oids: set[int]) -> BotState:
    """
    Startup safety:
    - Reconcile missing ladder oids using order status/fills
    - Ensure TP oid consistency
    - Never infer fills from missing open order alone
    """
    grid = bs.long_grid if side == LONG else bs.short_grid
    if not grid.active:
        return bs

    changed = False

    # Reconcile unfilled levels
    for lv in grid.levels:
        if lv.filled or lv.oid is None:
            continue
        if lv.oid in open_oids:
            continue

        status = _status_lower(hl.get_order_status(lv.oid))
        if status == "filled":
            qty, fill_px = hl.get_order_fill_summary(lv.oid, cfg.COIN)
            lv.filled = True
            if qty > 0:
                lv.fill_qty = round(qty, cfg.SZ_DECIMALS)
            if fill_px is not None:
                lv.fill_px = float(fill_px)
            else:
                lv.fill_px = lv.target_px
            changed = True
            log.warning(
                f"Startup reconcile: marked {side.upper()} L{lv.level} filled "
                f"@ ${lv.fill_px:,.1f} qty={lv.fill_qty:.5f}"
            )
        elif status in CANCEL_STATUSES or status == UNKNOWN_STATUS:
            signed_sz = _signed_position_size()
            if abs(signed_sz) <= QTY_TOL:
                log.warning(
                    f"Startup reconcile: {side.upper()} L{lv.level} oid={lv.oid} status={status}, "
                    "but position is flat. Auto-resetting stale grid."
                )
                return gs_mod.reset_grid(bs, side)

            # Manual intervention likely changed ladder order. Stop tracking this resting level
            # instead of halting the bot.
            log.warning(
                f"Startup reconcile: {side.upper()} L{lv.level} oid={lv.oid} status={status}; "
                "dropping this resting level from managed state."
            )
            lv.oid = None
            changed = True
        else:
            raise RuntimeError(
                f"Startup reconcile failed: {side.upper()} L{lv.level} oid={lv.oid} "
                f"unexpected status={status!r}."
            )

    if changed:
        grid.recalc()

    # Reconcile TP
    if grid.tp_oid is not None and grid.tp_oid not in open_oids:
        tp_status = _status_lower(hl.get_order_status(grid.tp_oid))
        if tp_status == "filled":
            log.warning(f"Startup reconcile: {side.upper()} TP already filled while bot was down; resetting {side} grid")
            bs = gs_mod.reset_grid(bs, side)
        elif tp_status in CANCEL_STATUSES or tp_status == UNKNOWN_STATUS:
            signed_sz = _signed_position_size()

            if abs(signed_sz) <= QTY_TOL:
                log.warning(
                    f"Startup reconcile: {side.upper()} TP oid={grid.tp_oid} status={tp_status}, "
                    "but position is flat. Auto-resetting stale grid."
                )
                return gs_mod.reset_grid(bs, side)

            if _side_position_matches(side, signed_sz):
                # Re-create missing TP for current live position size.
                qty = round(abs(signed_sz), cfg.SZ_DECIMALS)
                if qty <= 0:
                    qty = round(grid.total_qty, cfg.SZ_DECIMALS)
                if side == LONG:
                    new_tp_oid = hl.limit_sell_tp(cfg.COIN, qty, grid.tp_price)
                else:
                    new_tp_oid = hl.limit_buy_tp(cfg.COIN, qty, grid.tp_price)
                if new_tp_oid is None:
                    raise RuntimeError(
                        f"Startup reconcile failed: could not recreate {side.upper()} TP after status={tp_status}."
                    )
                log.warning(
                    f"Startup reconcile: recreated {side.upper()} TP oid={new_tp_oid} "
                    f"qty={qty:.5f} after missing TP status={tp_status}."
                )
                grid.tp_oid = new_tp_oid
                changed = True
            else:
                raise RuntimeError(
                    f"Startup reconcile failed: {side.upper()} TP oid={grid.tp_oid} status={tp_status}, "
                    f"but live position sign does not match side (size={signed_sz:.5f})."
                )
        else:
            raise RuntimeError(
                f"Startup reconcile failed: {side.upper()} TP oid={grid.tp_oid} unexpected status={tp_status!r}."
            )

    if changed:
        gs_mod.save(bs)

    return bs


def reconcile_startup_state(bs: BotState) -> BotState:
    """Validate and reconcile local JSON state against exchange truth."""
    if cfg.PAPER_TRADE:
        return bs  # paper mode has no reliable exchange position truth

    if bs.long_grid.active and bs.short_grid.active:
        raise RuntimeError("Invalid local state: both long_grid and short_grid are active")

    open_orders = hl.get_open_orders(cfg.COIN)
    open_oids = {o["oid"] for o in open_orders}

    bs = reconcile_grid_from_exchange(bs, LONG, open_oids)
    bs = reconcile_grid_from_exchange(bs, SHORT, open_oids)

    # Position-size truth check
    pos = hl.get_position(cfg.COIN)
    actual_signed_qty = float(pos.get("size", 0.0))
    expected_signed_qty = signed_qty_for_state(bs)

    if abs(actual_signed_qty - expected_signed_qty) > QTY_TOL:
        # Auto-heal case: local JSON says active, but exchange is fully flat and no working orders.
        if abs(actual_signed_qty) <= QTY_TOL and not open_oids:
            log.warning(
                "Startup reconcile: local state mismatched while exchange is flat with no open orders. "
                "Auto-resetting both grids."
            )
            bs = gs_mod.reset_grid(bs, LONG)
            bs = gs_mod.reset_grid(bs, SHORT)
            return bs

        raise RuntimeError(
            "Startup reconcile failed: exchange position mismatch. "
            f"expected={expected_signed_qty:.5f}, actual={actual_signed_qty:.5f}."
        )

    return bs


def _manual_close_any_active(bs: BotState, reason: str = "MANUAL_CLOSE") -> BotState:
    """Close whichever side is active; no-op if flat."""
    if bs.long_grid.active:
        ok = force_close(bs, bs.long_grid, reason)
        if ok:
            bs = gs_mod.reset_grid(bs, LONG)
        else:
            raise RuntimeError("Manual close failed for LONG")
    elif bs.short_grid.active:
        ok = force_close(bs, bs.short_grid, reason)
        if ok:
            bs = gs_mod.reset_grid(bs, SHORT)
        else:
            raise RuntimeError("Manual close failed for SHORT")
    else:
        log.info("Manual close requested, but no active grid")
    return bs


def process_pending_commands(bs: BotState, state: dict) -> BotState:
    """
    Handle queued manual commands from local console.
    Supported actions:
      - manual_long
      - manual_short
      - manual_close
    """
    pending = command_bus.list_pending()
    if not pending:
        return bs

    for path, cmd in pending:
        action = str(cmd.get("action", "")).strip().lower()
        cmd_id = cmd.get("id", path.stem)
        src = cmd.get("source", "unknown")

        log.info(f"Processing command {cmd_id} action={action} source={src}")

        try:
            if action == "manual_long" and not bs.long_grid.active and not bs.short_grid.active:
                risk = cfg.RISK_PCT
                is_bull = state["is_bull"] if state["is_bull"] is not None else True
                grid = open_grid(bs, LONG, state, risk_pct=risk, entry_gate="manual", is_favored=is_bull)
                if grid is None:
                    raise RuntimeError("open_grid(LONG) returned None")
                msg = f"Manual LONG opened @ ${grid.blended_entry:,.1f}"

            elif action == "manual_short" and not bs.short_grid.active and not bs.long_grid.active:
                risk = cfg.RISK_PCT
                is_bull = state["is_bull"] if state["is_bull"] is not None else True
                grid = open_grid(bs, SHORT, state, risk_pct=risk, entry_gate="manual", is_favored=not is_bull)
                if grid is None:
                    raise RuntimeError("open_grid(SHORT) returned None")
                msg = f"Manual SHORT opened @ ${grid.blended_entry:,.1f}"

            elif action == "manual_close":
                bs = _manual_close_any_active(bs, reason="MANUAL_CLOSE")
                msg = "Manual close processed"

            else:
                raise RuntimeError(f"Unknown action: {action}")

            command_bus.mark_processed(path, cmd, "ok", msg)
            log.info(f"Command done {cmd_id}: {msg}")

        except Exception as e:
            err = str(e)
            command_bus.mark_processed(path, cmd, "error", err)
            log.error(f"Command failed {cmd_id}: {err}")
            notifier.error(f"Command failed ({action}): {err}")

    return bs


def sleep_with_command_watch(bs: BotState, seconds: int) -> BotState:
    """Sleep in small increments so manual console commands feel instant."""
    end = time.time() + max(0, int(seconds))
    while time.time() < end:
        remaining = end - time.time()
        if remaining <= 0:
            break

        pending = command_bus.list_pending()
        if pending:
            try:
                state = fetch_market_state()
                bs = process_pending_commands(bs, state)
            except Exception as e:
                log.exception(f"Command watch error: {e}")
                notifier.error(f"Command watch error: {e}")

        time.sleep(min(1.0, max(0.1, remaining)))

    return bs


# ─── MA calculations ──────────────────────────────────────────────────────

def fetch_market_state() -> dict:
    """
    Return dict with all v3.0 indicators:
    price, ema34, sma14, ema20, sma440, high_20d, rsi14, is_bull
    """
    # 4H candles (need 500 for RSI warmup + high_20d window)
    candles = hl.get_candles(cfg.COIN, cfg.CANDLE_INTERVAL, n=500)
    closed = candles[:-1]  # exclude current open bar
    closes = pd.Series([float(c["c"]) for c in closed])
    highs = pd.Series([float(c["h"]) for c in closed])

    ema34 = float(closes.ewm(span=cfg.EMA_SPAN, adjust=False).mean().iloc[-1])
    sma14 = float(closes.rolling(cfg.MA_PERIOD).mean().iloc[-1])
    ema20 = float(closes.ewm(span=cfg.EMA20_SPAN, adjust=False).mean().iloc[-1])

    # High of last 20 days (120 × 4H bars)
    high_20d = float(highs.rolling(cfg.HIGH_20D_BARS).max().iloc[-1])

    # RSI(14) on 4H closes (Wilder SMA)
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(cfg.RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(cfg.RSI_PERIOD).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi_series = 100 - (100 / (1 + rs))
    rsi14 = float(rsi_series.iloc[-1])

    # SMA440 from Binance daily candles
    daily_closes = binance_data.fetch_daily_closes(limit=500)
    sma440 = None
    is_bull = None
    if len(daily_closes) >= cfg.SMA440_SPAN:
        sma440_val = sum(daily_closes[-cfg.SMA440_SPAN:]) / cfg.SMA440_SPAN
        sma440 = sma440_val
        price = hl.get_mid_price(cfg.COIN)
        is_bull = price > sma440_val
    else:
        price = hl.get_mid_price(cfg.COIN)

    return {
        "price": price,
        "ema34": ema34,
        "sma14": sma14,
        "ema20": ema20,
        "sma440": sma440,
        "high_20d": high_20d,
        "rsi14": rsi14,
        "is_bull": is_bull,
    }


def send_2h_report(bs: 'BotState', state: dict):
    """Send a 2-hour status webhook with grid details."""
    import requests
    if not cfg.DISCORD_WEBHOOK:
        return
    price = state["price"]
    ema34 = state["ema34"]
    sma14 = state["sma14"]
    bal = hl.get_account_balance()
    lines = [f"📊 **2h Status Report** — BTC ${price:,.1f} | EMA34 ${ema34:,.1f} | SMA14 ${sma14:,.1f} | Bal: ${bal:,.2f}"]

    for label, grid in [("LONG", bs.long_grid), ("SHORT", bs.short_grid)]:
        if not grid.active:
            lines.append(f"\n**{label}:** idle")
            continue
        hold = grid.hold_hours()
        mark = price
        lines.append(f"\n**{label} Grid** (held {hold:.1f}h) | Blended: ${grid.blended_entry:,.1f} | TP: ${grid.tp_price:,.1f}")
        for lv in grid.levels:
            status = "✅ filled" if lv.filled else (f"⏳ resting @ ${lv.target_px:,.0f}" if lv.oid else "—")
            sz = lv.fill_qty or 0
            entry = lv.fill_px or lv.target_px
            if lv.filled:
                if grid.side == LONG:
                    pnl = sz * (mark - entry)
                else:
                    pnl = sz * (entry - mark)
                lines.append(f"  L{lv.level}: {sz:.5f} @ ${entry:,.0f} | mark ${mark:,.0f} | PnL ${pnl:+.2f} | {status}")
            else:
                lines.append(f"  L{lv.level}: {sz:.5f} @ ${entry:,.0f} | {status}")

    msg = "\n".join(lines)
    try:
        requests.post(cfg.DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except Exception as e:
        log.error(f"2h report webhook error: {e}")


def pct_above(price, ma):
    return (price - ma) / ma * 100


def pct_below(price, ma):
    return (ma - price) / ma * 100


def long_triggered(state: dict) -> tuple:
    """
    v3.0 entry logic for LONG.
    Returns (triggered: bool, gate: str, risk_pct: float).
    """
    price = state["price"]
    is_bull = state["is_bull"]

    # Regime: if SMA440 unavailable, skip entirely
    if is_bull is None:
        return (False, "no_sma440", 0.0)

    is_favored = is_bull  # bull = favored for longs
    trigger_scale = 1.0 if is_favored else cfg.UNFAV_TRIGGER_SCALE

    # Gate 1: v28 (EMA34 + SMA14)
    v28_gate = (pct_below(price, state["ema34"]) >= cfg.LONG_TRIGGER_PCT * trigger_scale and
                pct_below(price, state["sma14"]) >= cfg.LONG_TRIGGER_PCT * trigger_scale)

    # Gate 2: EMA20
    ema20_gate = pct_below(price, state["ema20"]) >= cfg.EMA20_TRIGGER_PCT * trigger_scale

    if not (v28_gate or ema20_gate):
        return (False, "no_trigger", 0.0)

    # dd20d filter
    dd_from_high = (price / state["high_20d"]) - 1.0
    if dd_from_high < cfg.DD20D_THRESHOLD:
        # RSI rescue
        if state["rsi14"] <= cfg.RSI_RESCUE_THRESHOLD:
            risk = cfg.RESCUE_RISK_PCT * (1.0 if is_favored else cfg.UNFAV_RISK_SCALE)
            return (True, "rescued", risk)
        else:
            return (False, "dd20d_blocked", 0.0)

    # Normal entry
    risk = cfg.RISK_PCT * (1.0 if is_favored else cfg.UNFAV_RISK_SCALE)
    gate = "v28" if v28_gate else "ema20"
    return (True, gate, risk)


def short_triggered(state: dict) -> tuple:
    """
    v3.0 entry logic for SHORT.
    Returns (triggered: bool, gate: str, risk_pct: float).
    No dd20d/RSI filter for shorts.
    """
    price = state["price"]
    is_bull = state["is_bull"]

    if is_bull is None:
        return (False, "no_sma440", 0.0)

    # For shorts: bear = favored
    is_favored = not is_bull
    trigger_scale = 1.0 if is_favored else cfg.UNFAV_TRIGGER_SCALE

    triggered = (pct_above(price, state["ema34"]) >= cfg.SHORT_TRIGGER_PCT * trigger_scale and
                 pct_above(price, state["sma14"]) >= cfg.SHORT_TRIGGER_PCT * trigger_scale)

    if not triggered:
        return (False, "no_trigger", 0.0)

    risk = cfg.RISK_PCT * (1.0 if is_favored else cfg.UNFAV_RISK_SCALE)
    return (True, "short", risk)


# ─── Grid open ────────────────────────────────────────────────────────────

def open_grid(bs: BotState, side: str, state: dict,
              risk_pct: float, entry_gate: str, is_favored: bool) -> GridState:
    leverage = cfg.LEVERAGE if side == LONG else cfg.SHORT_LEVERAGE
    price = state["price"]

    balance = hl.get_account_balance()
    hold_bars = cfg.MAX_HOLD_BARS if is_favored else int(cfg.MAX_HOLD_BARS * cfg.UNFAV_HOLD_SCALE)
    max_hold_h = hold_bars * 4

    log.info(
        f"TRIGGER {side.upper()} [{entry_gate}]: BTC ${price:,.1f} "
        f"| EMA34 ${state['ema34']:,.1f} | SMA14 ${state['sma14']:,.1f} "
        f"| EMA20 ${state['ema20']:,.1f} | RSI14 {state['rsi14']:.1f} "
        f"| {'FAVORED' if is_favored else 'UNFAVORED'} "
        f"| risk={risk_pct:.2%} | {leverage}x | bal=${balance:.2f}"
    )

    # Exchange-level leverage guardrail for this side
    try:
        hl.set_leverage(cfg.COIN, leverage)
    except Exception as e:
        log.warning(f"Could not set leverage {leverage}x for {side}: {e}")

    grid = GridState()
    grid.side = side
    grid.active = True
    grid.trigger_px = price
    grid.ema34 = state["ema34"]
    grid.sma14 = state["sma14"]
    grid.opened_at = datetime.now(timezone.utc).isoformat()
    grid.is_favored = is_favored
    grid.entry_gate = entry_gate
    grid.risk_pct = risk_pct
    grid.max_hold_hours = max_hold_h
    grid.levels = gs_mod.build_levels(
        price, side, risk_pct=risk_pct, balance=balance, is_favored=is_favored
    )

    l1 = grid.levels[0]
    qty = round(l1.notional / price, cfg.SZ_DECIMALS)

    # ── L1 market order ────────────────────────────────────────────────
    if side == LONG:
        result = hl.market_buy(cfg.COIN, qty)
    else:
        result = hl.market_sell(cfg.COIN, qty)

    fill_qty, fill_px, err = parse_market_fill_result(result)
    if err:
        log.error(f"Market order NOT filled for {side.upper()} L1: {err}")
        notifier.error(f"{side.upper()} L1 market order rejected: {err}")
        return None

    l1.filled = True
    l1.fill_px = fill_px
    l1.fill_qty = round(fill_qty if fill_qty > 0 else qty, cfg.SZ_DECIMALS)
    grid.recalc()

    pct_dev = pct_below(price, state["ema34"]) if side == LONG else pct_above(price, state["ema34"])
    notifier.grid_opened(side, 1, fill_px, state["ema34"], state["sma14"], l1.margin, pct_dev)

    placed_oids = []
    try:
        # ── L2-L5 resting limit orders ───────────────────────────────
        for lv in grid.levels[1:]:
            lv_qty = round(lv.notional / lv.target_px, cfg.SZ_DECIMALS)
            if side == LONG:
                oid = hl.limit_buy(cfg.COIN, lv_qty, lv.target_px)
            else:
                oid = hl.limit_sell(cfg.COIN, lv_qty, lv.target_px)

            if oid is None:
                raise RuntimeError(f"Failed placing {side.upper()} L{lv.level} resting order")

            lv.oid = oid
            lv.fill_qty = lv_qty
            placed_oids.append(oid)
            time.sleep(0.3)

        # ── TP resting limit ──────────────────────────────────────────
        if side == LONG:
            tp_oid = hl.limit_sell_tp(cfg.COIN, grid.total_qty, grid.tp_price)
        else:
            tp_oid = hl.limit_buy_tp(cfg.COIN, grid.total_qty, grid.tp_price)

        if tp_oid is None:
            raise RuntimeError(f"Failed placing {side.upper()} TP order")

        grid.tp_oid = tp_oid

    except Exception as e:
        # Emergency rollback: cancel placed ladders and close L1
        log.error(f"{side.upper()} grid open failed post-L1: {e}")
        notifier.error(f"{side.upper()} grid open failed post-L1; rolling back. {e}")

        try:
            if placed_oids:
                if hasattr(hl, "cancel_orders"):
                    hl.cancel_orders(cfg.COIN, placed_oids)
                else:
                    for oid in placed_oids:
                        hl.cancel_order(cfg.COIN, oid)
                        time.sleep(0.2)
        except Exception as ce:
            log.error(f"Rollback cancel failed: {ce}")

        try:
            if side == LONG:
                hl.market_sell_close(cfg.COIN, round(grid.total_qty, cfg.SZ_DECIMALS))
            else:
                hl.market_buy_close(cfg.COIN, round(grid.total_qty, cfg.SZ_DECIMALS))
        except Exception as ce:
            log.error(f"Rollback close failed: {ce}")

        return None

    # Attach to bot state
    if side == LONG:
        bs.long_grid = grid
    else:
        bs.short_grid = grid

    gs_mod.save(bs)
    log.info(f"{side.upper()} grid open. Blended: ${grid.blended_entry:,.1f} | TP: ${grid.tp_price:,.1f}")
    return grid


# ─── Grid management ──────────────────────────────────────────────────────

def check_fills(bs: BotState, grid: GridState):
    """Check if any resting ladder orders have filled. Update TP if so."""
    open_oids = {o["oid"] for o in hl.get_open_orders(cfg.COIN)}
    changed = False

    for lv in grid.levels:
        if lv.filled or lv.oid is None:
            continue

        if lv.oid in open_oids:
            continue

        status = _status_lower(hl.get_order_status(lv.oid))

        if status == "filled":
            qty, fill_px = hl.get_order_fill_summary(lv.oid, cfg.COIN)
            lv.filled = True
            if qty > 0:
                lv.fill_qty = round(qty, cfg.SZ_DECIMALS)
            lv.fill_px = float(fill_px) if fill_px is not None else lv.target_px
            changed = True

            log.info(f"{grid.side.upper()} L{lv.level} filled @ ${lv.fill_px:,.1f}")
            grid.recalc()

            pct_f = abs(grid.trigger_px - lv.fill_px) / grid.trigger_px * 100
            notifier.level_filled(grid.side, lv.level, lv.fill_px,
                                  grid.blended_entry, grid.total_margin, pct_f)

            # Cancel old TP (if still open), then place new TP for full size
            if grid.tp_oid:
                latest_open = {o["oid"] for o in hl.get_open_orders(cfg.COIN)}
                if grid.tp_oid in latest_open:
                    try:
                        hl.cancel_order(cfg.COIN, grid.tp_oid)
                    except Exception as e:
                        raise RuntimeError(f"Could not cancel old TP {grid.tp_oid}: {e}")
                    time.sleep(0.3)
                else:
                    tp_status = _status_lower(hl.get_order_status(grid.tp_oid))
                    if tp_status == "filled":
                        # TP won the race; main loop will clear grid on check_tp_hit
                        log.info(f"TP oid={grid.tp_oid} already filled while processing level fill")
                        continue
                    if tp_status in CANCEL_STATUSES or tp_status == UNKNOWN_STATUS:
                        raise RuntimeError(
                            f"TP oid={grid.tp_oid} disappeared with status={tp_status} while processing fills"
                        )

                grid.tp_oid = None

            if grid.side == LONG:
                grid.tp_oid = hl.limit_sell_tp(cfg.COIN, grid.total_qty, grid.tp_price)
            else:
                grid.tp_oid = hl.limit_buy_tp(cfg.COIN, grid.total_qty, grid.tp_price)

            if grid.tp_oid is None:
                raise RuntimeError(f"Failed to place updated TP for {grid.side.upper()} grid")

            log.info(f"Updated TP: ${grid.tp_price:,.1f} qty={grid.total_qty:.5f}")

        elif status in CANCEL_STATUSES or status == UNKNOWN_STATUS:
            signed_sz = _signed_position_size()

            if abs(signed_sz) <= QTY_TOL:
                # Trade appears closed externally; clear stale local grid state.
                log.warning(
                    f"{grid.side.upper()} L{lv.level} oid={lv.oid} status={status} while position is flat; "
                    "auto-resetting grid."
                )
                bs = gs_mod.reset_grid(bs, grid.side)
                return

            # Manual ladder edits are allowed: stop tracking this resting level and continue.
            log.warning(
                f"{grid.side.upper()} L{lv.level} oid={lv.oid} status={status}; "
                "dropping this resting level from managed state."
            )
            lv.oid = None
            changed = True
            continue
        else:
            raise RuntimeError(
                f"{grid.side.upper()} L{lv.level} oid={lv.oid} missing from open orders with unexpected status={status!r}."
            )

    if changed:
        gs_mod.save(bs)


def check_tp_hit(bs: BotState, grid: GridState) -> bool:
    """Returns True when grid should be reset (TP filled or externally closed)."""
    if grid.tp_oid is None:
        return False

    open_oids = {o["oid"] for o in hl.get_open_orders(cfg.COIN)}
    if grid.tp_oid in open_oids:
        return False

    tp_status = _status_lower(hl.get_order_status(grid.tp_oid))

    if tp_status == "filled":
        tp_qty, tp_px = hl.get_order_fill_summary(grid.tp_oid, cfg.COIN)
        exit_px = float(tp_px) if tp_px is not None else grid.tp_price

        pnl = grid.total_qty * abs(exit_px - grid.blended_entry)
        pnl -= grid.total_margin * cfg.MAKER_FEE * 2
        hold = grid.hold_hours()

        if tp_qty > 0 and abs(tp_qty - grid.total_qty) > QTY_TOL:
            log.warning(
                f"TP fill qty mismatch: expected={grid.total_qty:.5f} got={tp_qty:.5f}; "
                f"using state qty for PnL"
            )

        log.info(f"{grid.side.upper()} TP HIT @ ${exit_px:,.1f} | ~${pnl:+.2f}")
        notifier.tp_hit(grid.side, exit_px, grid.blended_entry,
                        pnl, grid.max_level_hit(), hold)

        # Cancel only this grid's residual ladder orders (not global coin orders)
        cancel_grid_orders(grid, include_tp=False)

        if cfg.PAPER_TRADE:
            hl.update_paper_balance(pnl)

        return True

    if tp_status in CANCEL_STATUSES or tp_status == UNKNOWN_STATUS:
        signed_sz = _signed_position_size()

        if abs(signed_sz) <= QTY_TOL:
            # Position already flat; likely manually closed. Reset grid safely.
            log.warning(
                f"{grid.side.upper()} TP oid={grid.tp_oid} status={tp_status} while position is flat; "
                "treating as external close and resetting grid."
            )
            cancel_grid_orders(grid, include_tp=False)
            return True

        if _side_position_matches(grid.side, signed_sz):
            # Rebuild TP for live size and continue.
            qty = round(abs(signed_sz), cfg.SZ_DECIMALS)
            if qty <= 0:
                qty = round(grid.total_qty, cfg.SZ_DECIMALS)
            if grid.side == LONG:
                new_tp_oid = hl.limit_sell_tp(cfg.COIN, qty, grid.tp_price)
            else:
                new_tp_oid = hl.limit_buy_tp(cfg.COIN, qty, grid.tp_price)
            if new_tp_oid is None:
                raise RuntimeError(
                    f"{grid.side.upper()} TP missing with status={tp_status}; failed to recreate TP"
                )
            log.warning(
                f"{grid.side.upper()} TP missing with status={tp_status}; recreated TP oid={new_tp_oid} "
                f"qty={qty:.5f}."
            )
            grid.tp_oid = new_tp_oid
            gs_mod.save(bs)
            return False

        raise RuntimeError(
            f"{grid.side.upper()} TP oid={grid.tp_oid} status={tp_status}, "
            f"but live position sign mismatch (size={signed_sz:.5f})."
        )

    return False


def force_close(bs: BotState, grid: GridState, reason: str = "TIMEOUT") -> bool:
    log.warning(f"Force closing {grid.side.upper()} grid ({reason})")

    # Cancel this grid's working orders first
    cancel_grid_orders(grid, include_tp=True)
    time.sleep(0.5)

    if grid.total_qty <= 0:
        log.warning(f"force_close called with zero qty for {grid.side.upper()} grid")
        return True

    close_qty = round(grid.total_qty, cfg.SZ_DECIMALS)
    if grid.side == LONG:
        result = hl.market_sell_close(cfg.COIN, close_qty)
    else:
        result = hl.market_buy_close(cfg.COIN, close_qty)

    filled_qty, fill_px, err = parse_market_fill_result(result)
    if err or fill_px is None:
        msg = f"FORCE_CLOSE FAILED for {grid.side.upper()} qty={close_qty:.5f}: {err}"
        log.error(msg)
        notifier.error(msg)
        return False

    if filled_qty > 0 and abs(filled_qty - close_qty) > QTY_TOL:
        msg = (
            f"FORCE_CLOSE PARTIAL for {grid.side.upper()}: expected={close_qty:.5f} filled={filled_qty:.5f}. "
            "State not reset."
        )
        log.error(msg)
        notifier.error(msg)
        return False

    pnl = grid.total_qty * abs(fill_px - grid.blended_entry)
    pnl = pnl if (grid.side == LONG and fill_px > grid.blended_entry) or \
                 (grid.side == SHORT and fill_px < grid.blended_entry) else -pnl
    hold = grid.hold_hours()

    notifier.timeout_close(grid.side, fill_px, grid.blended_entry,
                           pnl, grid.max_level_hit(), hold)

    if cfg.PAPER_TRADE:
        hl.update_paper_balance(pnl)

    return True


# ─── Main loop ────────────────────────────────────────────────────────────

def run():
    mode = "📝 PAPER TRADE" if cfg.PAPER_TRADE else "🔴 LIVE"
    log.info("=" * 60)
    log.info(f"Mr Martingale v{cfg.BOT_VERSION} — LONG + SHORT [{mode}]")
    log.info(f"Coin: {cfg.COIN} | {cfg.NUM_LEVELS}L | "
             f"Long trigger: {cfg.LONG_TRIGGER_PCT}%/{cfg.EMA20_TRIGGER_PCT}% EMA20 | "
             f"Short trigger: {cfg.SHORT_TRIGGER_PCT}% | TP: {cfg.TP_PCT}%")
    log.info(f"Risk: {cfg.RISK_PCT:.0%} | Rescue: {cfg.RESCUE_RISK_PCT:.0%} | "
             f"Mults: {cfg.LEVEL_MULTS_SEQ} | Gaps: {cfg.LEVEL_GAPS}")
    log.info(f"Timeout: {cfg.MAX_HOLD_BARS} bars favored | "
             f"{int(cfg.MAX_HOLD_BARS * cfg.UNFAV_HOLD_SCALE)} bars unfavored")
    log.info("=" * 60)

    # Set leverage ceiling high enough for either side at startup
    try:
        hl.set_leverage(cfg.COIN, max(cfg.LEVERAGE, cfg.SHORT_LEVERAGE))
    except Exception as e:
        log.error(f"Could not set startup leverage: {e}")

    command_bus.ensure_dirs()

    bs = gs_mod.load()

    # Startup state reconciliation (live only)
    try:
        bs = reconcile_startup_state(bs)
    except Exception as e:
        log.exception(f"Startup reconcile failed: {e}")
        notifier.error(f"Startup reconcile failed; bot halted. {e}")
        return

    # Process any queued manual commands immediately on startup
    try:
        state0 = fetch_market_state()
        bs = process_pending_commands(bs, state0)
    except Exception as e:
        log.error(f"Startup command processing error: {e}")

    poll_count = 0

    while True:
        try:
            poll_count += 1
            state = fetch_market_state()
            price = state["price"]
            ema34 = state["ema34"]
            sma14 = state["sma14"]
            pb = pct_below(price, ema34)   # positive = price below EMA34
            pa = pct_above(price, ema34)   # positive = price above EMA34
            pb_ma = pct_below(price, sma14)
            pa_ma = pct_above(price, sma14)

            # In paper mode, simulate limit order fills at current price
            if cfg.PAPER_TRADE:
                hl.check_limit_fills(price)

            mode_tag = "[PAPER] " if cfg.PAPER_TRADE else ""
            if cfg.PAPER_TRADE:
                metrics = paper_runtime_metrics(bs, price)
                stop_px = metrics.get("stop")
                stop_text = f"${stop_px:,.1f}" if isinstance(stop_px, (int, float)) else "NA"
                drawdown = metrics.get("drawdown")
                drawdown_text = f"{drawdown:+.2f}%" if isinstance(drawdown, (int, float)) else "NA"
                log.info(
                    f"{mode_tag}BTC ${price:,.1f} | "
                    f"↓EMA34 {pct_below(price, ema34):+.2f}% ↓SMA14 {pct_below(price, sma14):+.2f}% "
                    f"↓EMA20 {pct_below(price, state['ema20']):+.2f}% | "
                    f"RSI {state['rsi14']:.0f} | "
                    f"{'BULL' if state['is_bull'] else 'BEAR' if state['is_bull'] is not None else 'N/A'} | "
                    f"Long: {'OPEN' if bs.long_grid.active else 'idle'} | "
                    f"Short: {'OPEN' if bs.short_grid.active else 'idle'} | "
                    f"stop={stop_text} | unrealized_pnl=${metrics['unrealized_pnl']:+.2f} | drawdown={drawdown_text}"
                )
            else:
                log.info(
                    f"{mode_tag}BTC ${price:,.1f} | "
                    f"↓EMA34 {pct_below(price, ema34):+.2f}% ↓SMA14 {pct_below(price, sma14):+.2f}% "
                    f"↓EMA20 {pct_below(price, state['ema20']):+.2f}% | "
                    f"RSI {state['rsi14']:.0f} | "
                    f"{'BULL' if state['is_bull'] else 'BEAR' if state['is_bull'] is not None else 'N/A'} | "
                    f"Long: {'OPEN' if bs.long_grid.active else 'idle'} | "
                    f"Short: {'OPEN' if bs.short_grid.active else 'idle'}"
                )

            # One-side invariant
            if bs.long_grid.active and bs.short_grid.active:
                raise RuntimeError("Invariant violation: both long_grid and short_grid active")

            # Manual console commands (processed before trigger logic)
            bs = process_pending_commands(bs, state)

            # ── Heartbeat ─────────────────────────────────────────────
            if poll_count % HEARTBEAT_EVERY == 0:
                bal = hl.get_account_balance()
                notifier.heartbeat(price, state["ema34"], state["sma14"], pa, pb,
                                   bs.long_grid.active, bs.short_grid.active, bal)

            # ── 2h webhook report ─────────────────────────────────────
            if poll_count % WEBHOOK_REPORT_EVERY == 0:
                send_2h_report(bs, state)

            # ── LONG GRID ─────────────────────────────────────────────
            if bs.long_grid.active:
                check_fills(bs, bs.long_grid)
                if check_tp_hit(bs, bs.long_grid):
                    bs = gs_mod.reset_grid(bs, LONG)
                elif bs.long_grid.hold_hours() >= bs.long_grid.max_hold_hours:
                    ok = force_close(bs, bs.long_grid, "TIMEOUT")
                    if ok:
                        bs = gs_mod.reset_grid(bs, LONG)
                    else:
                        raise RuntimeError("LONG force_close failed; state not reset")

            elif not bs.short_grid.active:
                triggered, gate, risk = long_triggered(state)
                if triggered:
                    is_favored = state["is_bull"]
                    if open_grid(bs, LONG, state, risk_pct=risk, entry_gate=gate, is_favored=is_favored) is None:
                        log.error("open_grid(LONG) failed — skipping this poll")

            # ── SHORT GRID ────────────────────────────────────────────
            if bs.short_grid.active:
                check_fills(bs, bs.short_grid)
                if check_tp_hit(bs, bs.short_grid):
                    bs = gs_mod.reset_grid(bs, SHORT)
                elif bs.short_grid.hold_hours() >= bs.short_grid.max_hold_hours:
                    ok = force_close(bs, bs.short_grid, "TIMEOUT")
                    if ok:
                        bs = gs_mod.reset_grid(bs, SHORT)
                    else:
                        raise RuntimeError("SHORT force_close failed; state not reset")

            elif not bs.long_grid.active:
                triggered, gate, risk = short_triggered(state)
                if triggered:
                    is_favored = not state["is_bull"]
                    if open_grid(bs, SHORT, state, risk_pct=risk, entry_gate=gate, is_favored=is_favored) is None:
                        log.error("open_grid(SHORT) failed — skipping this poll")

        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.exception(f"Main loop error: {e}")
            notifier.error(str(e))
            time.sleep(30)

        bs = sleep_with_command_watch(bs, cfg.POLL_SECONDS)


if __name__ == "__main__":
    run()
