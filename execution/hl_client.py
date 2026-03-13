"""
Hyperliquid trading client — supports both LONG and SHORT grids.
Adds order-status and position helpers for safer state reconciliation.
"""
import logging
import time
import requests
from typing import Optional, Tuple

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
import eth_account

try:
    from . import config as cfg
except ImportError:
    import config as cfg

log = logging.getLogger("hl_client")

_URL = constants.TESTNET_API_URL if cfg.HL_TESTNET else constants.MAINNET_API_URL


def _build():
    wallet = eth_account.Account.from_key(cfg.HL_PRIVATE_KEY)
    info = Info(_URL, skip_ws=True)
    exchange = Exchange(wallet, _URL, account_address=cfg.HL_MAIN_ADDRESS)
    return info, exchange


info_client, exchange_client = _build()


# ─── Market data ──────────────────────────────────────────────────────────

def get_mid_price(coin: str = cfg.COIN) -> float:
    return float(info_client.all_mids()[coin])


def get_candles(coin: str = cfg.COIN, interval: str = "4h", n: int = 60) -> list:
    end_ms = int(time.time() * 1000)
    interval_ms = {"1h": 3_600_000, "4h": 14_400_000}[interval]
    start_ms = end_ms - n * interval_ms
    resp = requests.post(
        f"{_URL}/info",
        json={
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_account_state() -> dict:
    return info_client.user_state(cfg.HL_MAIN_ADDRESS)


def get_account_balance() -> float:
    return float(get_account_state()["marginSummary"]["accountValue"])


def get_open_orders(coin: str = cfg.COIN) -> list:
    return [o for o in info_client.open_orders(cfg.HL_MAIN_ADDRESS) if o["coin"] == coin]


def get_position(coin: str = cfg.COIN) -> dict:
    """
    Returns a normalized position snapshot for a coin.

    Output schema:
      {
        "size": float,      # signed qty (+long, -short)
        "entry_px": float,
        "liq_px": Optional[float],
        "raw": dict|None,
      }
    """
    state = get_account_state()
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", ap)
        if pos.get("coin") != coin:
            continue
        size = float(pos.get("szi") or pos.get("sz") or pos.get("positionSize") or 0.0)
        entry_px = float(pos.get("entryPx") or 0.0)
        liq_px_raw = pos.get("liquidationPx") or pos.get("liqPx")
        liq_px = float(liq_px_raw) if liq_px_raw not in (None, "", "0") else None
        return {
            "size": size,
            "entry_px": entry_px,
            "liq_px": liq_px,
            "raw": pos,
        }
    return {"size": 0.0, "entry_px": 0.0, "liq_px": None, "raw": None}


# ─── Order helpers ────────────────────────────────────────────────────────

def _extract_oid(result: dict) -> Optional[int]:
    try:
        return result["response"]["data"]["statuses"][0]["resting"]["oid"]
    except (KeyError, IndexError, TypeError):
        return None


def _extract_fill_px(result: dict) -> Optional[float]:
    try:
        return float(result["response"]["data"]["statuses"][0]["filled"]["avgPx"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _normalize_order_status(status: Optional[str]) -> str:
    return status.lower().strip() if isinstance(status, str) else ""


def query_order(oid: int) -> dict:
    return info_client.query_order_by_oid(cfg.HL_MAIN_ADDRESS, int(oid))


def get_order_status(oid: int) -> str:
    """
    Returns normalized order status:
      - filled / canceled / open ...
      - unknownoid when exchange cannot find order
      - "" on parse failure
    """
    try:
        resp = query_order(oid)
    except Exception as e:
        log.error(f"query_order failed oid={oid}: {e}")
        return ""

    root_status = _normalize_order_status(resp.get("status"))
    if root_status == "unknownoid":
        return "unknownoid"

    order_obj = resp.get("order", {})
    nested_status = _normalize_order_status(order_obj.get("status"))
    return nested_status or root_status


def get_order_fill_summary(oid: int, coin: str = cfg.COIN) -> Tuple[float, Optional[float]]:
    """
    Aggregate fills for a given oid from recent user fills.
    Returns: (filled_qty, avg_fill_px)
    """
    try:
        fills = info_client.user_fills(cfg.HL_MAIN_ADDRESS)
    except Exception as e:
        log.error(f"user_fills failed while checking oid={oid}: {e}")
        return 0.0, None

    matched = []
    for f in fills:
        try:
            if int(f.get("oid", -1)) != int(oid):
                continue
        except (TypeError, ValueError):
            continue
        if coin and f.get("coin") != coin:
            continue
        matched.append(f)

    if not matched:
        return 0.0, None

    qty = sum(float(f.get("sz", 0.0)) for f in matched)
    if qty <= 0:
        return 0.0, None

    notional = sum(float(f.get("px", 0.0)) * float(f.get("sz", 0.0)) for f in matched)
    avg_px = notional / qty if qty > 0 else None
    return qty, avg_px


# ─── Long orders ──────────────────────────────────────────────────────────

def market_buy(coin: str, size: float) -> dict:
    """IOC limit well above market → guaranteed taker fill."""
    price = round(get_mid_price(coin) * 1.03)
    log.info(f"Market BUY {size:.5f} {coin} @ ~{price:,.0f}")
    return exchange_client.order(coin, True, size, price,
                                 {"limit": {"tif": "Ioc"}}, reduce_only=False)


def limit_buy(coin: str, size: float, price: float) -> Optional[int]:
    """GTC resting limit buy (maker). Returns oid."""
    price = round(price)
    log.info(f"Limit BUY {size:.5f} {coin} @ {price:,.0f}")
    result = exchange_client.order(coin, True, size, price,
                                   {"limit": {"tif": "Gtc"}}, reduce_only=False)
    return _extract_oid(result)


def limit_sell_tp(coin: str, size: float, price: float) -> Optional[int]:
    """GTC reduce-only limit sell for long TP."""
    price = round(price)
    log.info(f"Limit SELL (TP) {size:.5f} {coin} @ {price:,.0f}")
    result = exchange_client.order(coin, False, size, price,
                                   {"limit": {"tif": "Gtc"}}, reduce_only=True)
    return _extract_oid(result)


def market_buy_close(coin: str, size: float) -> dict:
    """Close short position — IOC limit well above market."""
    price = round(get_mid_price(coin) * 1.03)
    log.info(f"Market BUY (close short) {size:.5f} {coin} @ ~{price:,.0f}")
    return exchange_client.order(coin, True, size, price,
                                 {"limit": {"tif": "Ioc"}}, reduce_only=True)


# ─── Short orders ─────────────────────────────────────────────────────────

def market_sell(coin: str, size: float) -> dict:
    """IOC limit well below market → guaranteed taker fill (opens short)."""
    price = round(get_mid_price(coin) * 0.97)
    log.info(f"Market SELL {size:.5f} {coin} @ ~{price:,.0f}")
    return exchange_client.order(coin, False, size, price,
                                 {"limit": {"tif": "Ioc"}}, reduce_only=False)


def limit_sell(coin: str, size: float, price: float) -> Optional[int]:
    """GTC resting limit sell above market (adds to short grid, maker)."""
    price = round(price)
    log.info(f"Limit SELL {size:.5f} {coin} @ {price:,.0f}")
    result = exchange_client.order(coin, False, size, price,
                                   {"limit": {"tif": "Gtc"}}, reduce_only=False)
    return _extract_oid(result)


def limit_buy_tp(coin: str, size: float, price: float) -> Optional[int]:
    """GTC reduce-only limit buy for short TP."""
    price = round(price)
    log.info(f"Limit BUY (TP) {size:.5f} {coin} @ {price:,.0f}")
    result = exchange_client.order(coin, True, size, price,
                                   {"limit": {"tif": "Gtc"}}, reduce_only=True)
    return _extract_oid(result)


def market_sell_close(coin: str, size: float) -> dict:
    """Close long position — IOC limit well below market."""
    price = round(get_mid_price(coin) * 0.97)
    log.info(f"Market SELL (close long) {size:.5f} {coin} @ ~{price:,.0f}")
    return exchange_client.order(coin, False, size, price,
                                 {"limit": {"tif": "Ioc"}}, reduce_only=True)


# ─── Utilities ────────────────────────────────────────────────────────────

def cancel_order(coin: str, oid: int):
    log.info(f"Cancel {oid}")
    return exchange_client.cancel(coin, int(oid))


def cancel_orders(coin: str, oids):
    unique_oids = sorted({int(o) for o in oids if o is not None})
    for oid in unique_oids:
        try:
            cancel_order(coin, oid)
            time.sleep(0.2)
        except Exception as e:
            log.error(f"Cancel failed {oid}: {e}")


def cancel_all_orders(coin: str = cfg.COIN):
    cancel_orders(coin, [o["oid"] for o in get_open_orders(coin)])


def set_leverage(coin: str = cfg.COIN, leverage: int = cfg.LEVERAGE):
    result = exchange_client.update_leverage(leverage, coin, is_cross=True)
    log.info(f"Leverage set {leverage}x cross for {coin}: {result}")
    return result
