"""
Mr Martingale v2/v3 paper Discord notifier.
Fans out to the primary DISCORD_WEBHOOK plus any EXTRA_WEBHOOKS entries.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

from . import config as cfg

log = logging.getLogger("mrm_v2.notifier")

PREFIX = f"🧪 **[{cfg.BOT_NAME} PAPER]**"


def _build_webhooks():
    hooks = []

    if cfg.DISCORD_WEBHOOK:
        hooks.append({"url": cfg.DISCORD_WEBHOOK, "thread_id": None, "label": "primary"})

    extra = os.environ.get("EXTRA_WEBHOOKS", "")
    if extra:
        try:
            for wh in json.loads(extra):
                url = wh.get("url")
                if not url:
                    continue
                hooks.append({
                    "url": url,
                    "thread_id": wh.get("thread_id"),
                    "label": wh.get("label", "extra"),
                })
        except Exception as e:
            log.error(f"EXTRA_WEBHOOKS parse error: {e}")

    return hooks


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _send(msg: str):
    full = f"{PREFIX} {msg}"
    webhooks = _build_webhooks()
    if not webhooks:
        log.debug(f"[NOTIFY no-webhook] {msg}")
        return

    for wh in webhooks:
        try:
            url = wh["url"]
            if wh.get("thread_id") and "thread_id" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}thread_id={wh['thread_id']}"
            r = requests.post(url, json={"content": full}, timeout=10)
            if r.status_code == 429:
                retry = r.json().get("retry_after", 1)
                log.warning(f"[NOTIFY] rate-limited on {wh['label']}, retry in {retry}s")
                time.sleep(float(retry))
                r = requests.post(url, json={"content": full}, timeout=10)
            if r.status_code not in (200, 204):
                log.warning(f"[NOTIFY] {wh['label']} {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.error(f"[NOTIFY] {wh.get('label','?')} send error: {e}")


def bot_started(equity: float, regime: str, sma):
    sma_str = f"${sma:,.0f}" if sma else "n/a"
    _send(
        f"🚀 **Bot started** | Equity: ${equity:.2f} | "
        f"Regime: {regime.upper()} | 400d SMA: {sma_str} | {_ts()}"
    )


def grid_opened(side: str, price: float, tp: float, base_notional: float, equity: float):
    icon = "📈" if side == "long" else "📉"
    _send(
        f"{icon} **{side.upper()} grid opened** | "
        f"Entry: ${price:,.1f} | TP: ${tp:,.1f} | "
        f"L1 notional: ${base_notional:.0f} | Equity: ${equity:.2f} | {_ts()}"
    )


def level_filled(side: str, level: int, fill_px: float, blended: float, levels_in: int):
    _send(
        f"🔶 **{side.upper()} L{level} filled** | "
        f"${fill_px:,.1f} | Blended: ${blended:,.1f} | "
        f"{levels_in}/{cfg.NUM_LEVELS} levels in | {_ts()}"
    )


def tp_hit(side: str, exit_px: float, blended: float, pnl: float, equity: float):
    icon = "✅"
    _send(
        f"{icon} **{side.upper()} TP HIT** | "
        f"${exit_px:,.1f} | Blended was: ${blended:,.1f} | "
        f"PnL: ${pnl:+.2f} | Equity: ${equity:.2f} | {_ts()}"
    )


def timeout_close(side: str, price: float, pnl: float, equity: float, bars_held: int):
    _send(
        f"⏰ **{side.upper()} TIMEOUT** ({bars_held} bars) | "
        f"${price:,.1f} | PnL: ${pnl:+.2f} | Equity: ${equity:.2f} | {_ts()}"
    )


def regime_block(side: str, regime: str):
    """Sent when a trade signal is blocked by the regime filter."""
    _send(
        f"🚫 **Regime block** — {side.upper()} signal blocked "
        f"(regime={regime.upper()}) | {_ts()}"
    )


def heartbeat(equity: float, regime: str, side, bars_held):
    pos = f"{side.upper()} open ({bars_held}b)" if side else "flat"
    _send(
        f"💓 **Heartbeat** | Equity: ${equity:.2f} | "
        f"Regime: {regime.upper()} | {pos} | {_ts()}"
    )


def error_alert(msg: str):
    _send(f"🔴 **ERROR** | {msg} | {_ts()}")
