"""Discord notifier — posts to webhook targets."""
import os, json, requests, logging, time
from datetime import datetime
from . import config as cfg

log = logging.getLogger("notifier")

SIDE_ICON = {"long": "🟢", "short": "🔴"}
SIDE_LABEL = {"long": "LONG", "short": "SHORT"}

def _build_webhooks():
    """Build webhook list from config + env var EXTRA_WEBHOOKS (JSON array)."""
    hooks = []

    if cfg.DISCORD_WEBHOOK:
        hooks.append({"url": cfg.DISCORD_WEBHOOK, "thread_id": None, "label": "primary"})

    # Extra webhooks: env var JSON array of {"url": ..., "thread_id": ..., "label": ...}
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

def _send(msg: str):
    if getattr(cfg, 'PAPER_TRADE', False):
        msg = f"📝 {msg}"
    
    webhooks = _build_webhooks()
    
    for wh in webhooks:
        if not wh["url"]:
            continue
        try:
            url = wh["url"]
            if wh["thread_id"] and "thread_id" not in url:
                url = f"{url}?thread_id={wh['thread_id']}"
            r = requests.post(url, json={"content": msg}, timeout=10)
            if r.status_code == 429:
                retry_after = r.json().get("retry_after", 1)
                log.warning(f"[NOTIFY] rate-limited on {wh['label']}, retry in {retry_after}s")
                time.sleep(retry_after)
                r = requests.post(url, json={"content": msg}, timeout=10)
            if r.status_code not in (200, 204):
                log.warning(f"[NOTIFY] {wh['label']} ({wh['url'][:50]}...) {r.status_code}: {r.text[:200]}")
            else:
                log.debug(f"[NOTIFY] {wh['label']} OK ({r.status_code})")
        except Exception as e:
            log.error(f"[NOTIFY] {wh['label']} ({wh['url'][:50]}...) error: {e}")
    
    log.info(f"[NOTIFY] {msg}")

def _ts():
    return datetime.utcnow().strftime("%H:%M UTC")

def grid_opened(side: str, level: int, entry: float,
                ema34: float, sma14: float, margin: float, pct_dev: float):
    # Suppress — only notify on level fills, TP, timeout, and errors
    pass

def level_filled(side: str, level: int, fill_px: float,
                 blended: float, total_margin: float, pct_from_l1: float):
    _send(
        f"🔶 **{SIDE_LABEL[side]} L{level} filled** | "
        f"**${fill_px:,.1f}** ({pct_from_l1:.1f}% from L1) | "
        f"Blended: ${blended:,.1f} | Margin in: ${total_margin:.0f} | {_ts()}"
    )

def tp_hit(side: str, exit_px: float, blended: float,
           pnl: float, max_lvl: int, hold_h: float):
    _send(
        f"✅ **{SIDE_LABEL[side]} TP** | "
        f"Exit: **${exit_px:,.1f}** | PnL: **${pnl:+.2f}** | "
        f"L{max_lvl} max | {hold_h:.1f}h held | {_ts()}"
    )

def timeout_close(side: str, exit_px: float, blended: float,
                  pnl: float, max_lvl: int, hold_h: float):
    _send(
        f"⏱️ **{SIDE_LABEL[side]} timeout closed** | "
        f"${exit_px:,.1f} | PnL: ${pnl:+.2f} | {hold_h:.1f}h | {_ts()}"
    )

def liq_warning(side: str, equity: float, maint: float, price: float):
    _send(
        f"🚨 **{SIDE_LABEL[side]} LIQ WARNING** | "
        f"Equity: ${equity:.2f} | Maint: ${maint:.2f} | BTC: ${price:,.0f} | {_ts()}"
    )

def error(msg: str):
    _send(f"⚠️ **Bot error:** {msg[:200]} | {_ts()}")

def heartbeat(price: float, ema34: float, sma14: float,
              pct_above: float, pct_below: float,
              long_active: bool, short_active: bool, balance: float):
    # Suppress heartbeats — silent unless a level fires
    pass
