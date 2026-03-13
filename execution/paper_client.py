"""
Paper trading shim — mirrors hl_client.py interface exactly.
Orders are simulated locally; no real API calls for trades.
Market data (price, candles) still comes from mainnet.

Usage: bot imports this instead of hl_client when PAPER_TRADE=True.
"""
import logging
import threading
import time
from typing import Optional, Tuple

from . import config as cfg

# Real market data still fetched from mainnet
from .hl_client import (
    get_mid_price,
    get_candles,
    set_leverage,  # simulated no-op wrapper below keeps interface clean
)

log = logging.getLogger("paper_client")

# ─── Paper order book ─────────────────────────────────────────────────────
_lock = threading.Lock()
_orders: dict[int, dict] = {}  # oid → order dict
_next_oid = 1_000_000
_paper_balance = 400.0         # simulated starting balance — updates on closes for compounding
_paper_peak_equity = _paper_balance


def _new_oid() -> int:
    global _next_oid
    _next_oid += 1
    return _next_oid


def get_account_state() -> dict:
    """Return a fake account state with paper balance."""
    return {
        "marginSummary": {
            "accountValue": str(_paper_balance),
            "totalNtlPos": "0.0",
            "totalRawUsd": str(_paper_balance),
            "totalMarginUsed": "0.0",
        },
        "crossMarginSummary": {
            "accountValue": str(_paper_balance),
            "totalNtlPos": "0.0",
            "totalRawUsd": str(_paper_balance),
            "totalMarginUsed": "0.0",
        },
        "withdrawable": str(_paper_balance),
        "assetPositions": [],
        "time": int(time.time() * 1000),
        "_paper": True,
    }


def get_account_balance() -> float:
    return _paper_balance


def get_equity_snapshot(unrealized_pnl: float = 0.0) -> dict:
    """Return paper balance/equity snapshot with rolling peak drawdown."""
    global _paper_peak_equity

    balance = float(_paper_balance)
    unrealized = float(unrealized_pnl or 0.0)
    equity = balance + unrealized

    if equity > _paper_peak_equity:
        _paper_peak_equity = equity

    drawdown_pct = 0.0
    if _paper_peak_equity > 0:
        drawdown_pct = (equity / _paper_peak_equity - 1.0) * 100.0

    return {
        "balance": balance,
        "equity": equity,
        "peak_equity": float(_paper_peak_equity),
        "drawdown_pct": drawdown_pct,
    }


def get_position(coin: str = cfg.COIN) -> dict:
    # Lightweight shim; paper mode reconciliation is skipped in grid_bot.
    return {"size": 0.0, "entry_px": 0.0, "liq_px": None, "raw": None}


def update_paper_balance(pnl: float):
    """Called by grid_bot after a TP/timeout close to compound paper balance."""
    global _paper_balance
    _paper_balance = max(_paper_balance + pnl, 0.01)
    snap = get_equity_snapshot(unrealized_pnl=0.0)
    log.info(
        "[PAPER] Balance updated: $%s (%+.2f) | stop=NA | unrealized_pnl=$+0.00 | drawdown=%+.2f%%",
        f"{_paper_balance:,.2f}",
        pnl,
        snap["drawdown_pct"],
    )


def get_open_orders(coin: str = cfg.COIN) -> list:
    with _lock:
        return [
            {
                "oid": o["oid"],
                "coin": o["coin"],
                "is_buy": o["is_buy"],
                "reduce_only": o["reduce_only"],
                "limitPx": str(o["price"]),
                "sz": str(o["size"]),
            }
            for o in _orders.values()
            if o["coin"] == coin and o["status"] == "open"
        ]


# ─── Simulated order placement ────────────────────────────────────────────

def _place(coin: str, is_buy: bool, size: float, price: float,
           reduce_only: bool = False, label: str = "") -> dict:
    oid = _new_oid()
    order = {
        "oid": oid,
        "coin": coin,
        "is_buy": is_buy,
        "size": float(size),
        "price": float(price),
        "reduce_only": reduce_only,
        "status": "open",     # open | filled | canceled
        "fill_px": None,
        "fill_qty": 0.0,
        "label": label,
        "created_ms": int(time.time() * 1000),
        "filled_ms": None,
    }
    with _lock:
        _orders[oid] = order
    side = "BUY" if is_buy else "SELL"
    log.info(f"[PAPER] {label} {side} {size:.5f} {coin} @ ${price:,.1f} oid={oid}")
    return {
        "response": {
            "data": {
                "statuses": [{"resting": {"oid": oid}}]
            }
        }
    }


def _market_fill(coin: str, is_buy: bool, size: float, label: str = "") -> dict:
    """Simulate instant market fill at current mid price."""
    price = get_mid_price(coin)
    slippage = 1.001 if is_buy else 0.999   # 0.1% slippage
    fill_px = round(price * slippage, 1)
    oid = _new_oid()
    log.info(f"[PAPER] {label} MARKET {'BUY' if is_buy else 'SELL'} {size:.5f} {coin} → filled @ ${fill_px:,.1f}")
    return {
        "response": {
            "data": {
                "statuses": [{
                    "filled": {
                        "totalSz": str(size),
                        "avgPx": str(fill_px),
                        "oid": oid,
                    }
                }]
            }
        }
    }


# ─── Order status / fill summary helpers (for reconciliation safety) ─────

def query_order(oid: int) -> dict:
    with _lock:
        o = _orders.get(int(oid))
    if not o:
        return {"status": "unknownOid"}

    return {
        "status": "order",
        "order": {
            "order": {
                "coin": o["coin"],
                "side": "B" if o["is_buy"] else "A",
                "limitPx": str(o["price"]),
                "sz": str(0.0 if o["status"] != "open" else o["size"]),
                "oid": o["oid"],
                "timestamp": o["created_ms"],
                "reduceOnly": o["reduce_only"],
                "orderType": "Limit",
                "origSz": str(o["size"]),
                "tif": "Gtc",
            },
            "status": "filled" if o["status"] == "filled" else ("canceled" if o["status"] == "canceled" else "open"),
            "statusTimestamp": o["filled_ms"] or o["created_ms"],
        },
    }


def get_order_status(oid: int) -> str:
    r = query_order(oid)
    if r.get("status", "").lower() == "unknownoid":
        return "unknownoid"
    return str(r.get("order", {}).get("status", "")).lower()


def get_order_fill_summary(oid: int, coin: str = cfg.COIN) -> Tuple[float, Optional[float]]:
    with _lock:
        o = _orders.get(int(oid))
    if not o or o.get("coin") != coin or o.get("status") != "filled":
        return 0.0, None
    return float(o.get("fill_qty", 0.0)), float(o.get("fill_px") or 0.0)


# ─── Long order API ───────────────────────────────────────────────────────

def market_buy(coin: str, size: float) -> dict:
    return _market_fill(coin, True, size, label="MARKET-BUY")


def limit_buy(coin: str, size: float, price: float) -> Optional[int]:
    r = _place(coin, True, size, price, label="LIMIT-BUY")
    return r["response"]["data"]["statuses"][0]["resting"]["oid"]


def limit_sell_tp(coin: str, size: float, price: float) -> Optional[int]:
    r = _place(coin, False, size, price, reduce_only=True, label="TP-SELL")
    return r["response"]["data"]["statuses"][0]["resting"]["oid"]


def market_sell_close(coin: str, size: float) -> dict:
    return _market_fill(coin, False, size, label="MARKET-SELL-CLOSE")


# ─── Short order API ──────────────────────────────────────────────────────

def market_sell(coin: str, size: float) -> dict:
    return _market_fill(coin, False, size, label="MARKET-SELL")


def limit_sell(coin: str, size: float, price: float) -> Optional[int]:
    r = _place(coin, False, size, price, label="LIMIT-SELL")
    return r["response"]["data"]["statuses"][0]["resting"]["oid"]


def limit_buy_tp(coin: str, size: float, price: float) -> Optional[int]:
    r = _place(coin, True, size, price, reduce_only=True, label="TP-BUY")
    return r["response"]["data"]["statuses"][0]["resting"]["oid"]


def market_buy_close(coin: str, size: float) -> dict:
    return _market_fill(coin, True, size, label="MARKET-BUY-CLOSE")


# ─── Order management ─────────────────────────────────────────────────────

def cancel_order(coin: str, oid: int):
    with _lock:
        o = _orders.get(int(oid))
        if not o:
            return
        if o["coin"] != coin:
            return
        if o["status"] == "open":
            o["status"] = "canceled"
            o["filled_ms"] = int(time.time() * 1000)
            log.info(f"[PAPER] Cancelled oid={oid}")


def cancel_orders(coin: str, oids):
    for oid in sorted({int(o) for o in oids if o is not None}):
        cancel_order(coin, oid)


def cancel_all_orders(coin: str = cfg.COIN):
    with _lock:
        for o in _orders.values():
            if o["coin"] == coin and o["status"] == "open":
                o["status"] = "canceled"
                o["filled_ms"] = int(time.time() * 1000)
                log.info(f"[PAPER] Cancelled oid={o['oid']}")


def check_limit_fills(price: float):
    """
    Called each poll loop with current price.
    Simulates limit order fills when price crosses the order level.
    """
    with _lock:
        for o in _orders.values():
            if o["status"] != "open":
                continue

            filled = False
            # Buy limit fills when price drops to or below limit
            if o["is_buy"] and not o["reduce_only"] and price <= o["price"]:
                filled = True
            # Sell limit (long TP) fills when price rises to or above limit
            elif not o["is_buy"] and o["reduce_only"] and price >= o["price"]:
                filled = True
            # Short sell limit fills when price rises to or above limit
            elif not o["is_buy"] and not o["reduce_only"] and price >= o["price"]:
                filled = True
            # Short TP buy fills when price drops to or below limit
            elif o["is_buy"] and o["reduce_only"] and price <= o["price"]:
                filled = True

            if filled:
                o["status"] = "filled"
                o["fill_px"] = float(o["price"])
                o["fill_qty"] = float(o["size"])
                o["filled_ms"] = int(time.time() * 1000)
                label = o.get("label", "ORDER")
                log.info(f"[PAPER] {label} filled: oid={o['oid']} @ ${o['fill_px']:,.1f} (price=${price:,.1f})")


def set_leverage(coin: str = cfg.COIN, leverage: int = cfg.LEVERAGE):
    log.info(f"[PAPER] Leverage set {leverage}x for {coin} (simulated)")
