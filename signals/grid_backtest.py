"""
Grid Strategy Backtester v2
Variable spacing | Discord notifications | Optimal config

Optimal spacings found via grid search:
  L1→L2: 0.5% | L2→L3: 1.5% | L3→L4: 3.0% | L4→L5: 3.0%
  (cumulative from L1 trigger: 0.5% / 2.0% / 5.0% / 8.0%)
"""

import pandas as pd
import numpy as np
import gzip, csv, json, os, requests
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Callable

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

# ─── Strategy Parameters ───────────────────────────────────────────────────
ACCOUNT_USD        = 500.0
LEVERAGE           = 20
NUM_LEVELS         = 5
BASE_MARGIN        = 8.0

# Variable spacing: gap from previous level (%)
# [L1→L2, L2→L3, L3→L4, L4→L5]
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]

TRIGGER_PCT        = 0.5      # % below BOTH MAs to open grid
TP_PCT             = 0.5      # % above blended entry to exit
MULTIPLIER         = 2.0
MAINT_MARGIN_RATE  = 0.005
FUNDING_PER_8H_PCT = 0.0013
MAX_HOLD_BARS      = 30
COOLDOWN_BARS      = 1

# ─── Discord Notification Config ───────────────────────────────────────────
# In live mode: set DISCORD_WEBHOOK_URL env var or use the message tool
# In backtest mode: notifications are logged to console only
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
NOTIFY_CHANNEL_ID   = "1474189306536001659"   # #ideas channel

# ─── Derived: cumulative drops ─────────────────────────────────────────────
CUM_DROPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_DROPS.append(acc / 100)   # as fraction e.g. 0.005

# ─── Data Structures ───────────────────────────────────────────────────────
@dataclass
class Position:
    level:      int
    entry:      float
    margin:     float
    notional:   float
    btc_qty:    float
    bar_opened: int

@dataclass
class Cycle:
    start_bar:      int
    start_price:    float
    positions:      List[Position] = field(default_factory=list)
    blended_entry:  float = 0.0
    exit_price:     Optional[float] = None
    exit_bar:       Optional[int] = None
    exit_reason:    str = ""
    pnl:            float = 0.0
    funding_cost:   float = 0.0
    max_levels_hit: int = 0

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

# ─── Notification ──────────────────────────────────────────────────────────
def notify(msg: str, live: bool = False):
    """Send notification. In backtest mode just prints. In live mode posts to Discord."""
    if live and DISCORD_WEBHOOK_URL:
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=5)
        except Exception as e:
            print(f"  [notify error] {e}")
    else:
        print(f"  [NOTIFY] {msg}")

def fmt_level_fill(level: int, entry: float, blended: float,
                   total_margin: float, positions: list, live: bool = False):
    """Format and send a level-fill notification."""
    pct_from_l1 = (positions[0].entry - entry) / positions[0].entry * 100
    msg = (
        f"🔶 **Grid L{level} filled** | "
        f"Entry: ${entry:,.0f} ({pct_from_l1:.1f}% below L1) | "
        f"Blended avg: ${blended:,.0f} | "
        f"Margin deployed: ${total_margin:.0f} | "
        f"Levels open: {level}"
    )
    notify(msg, live)

def fmt_exit(reason: str, exit_price: float, blended: float,
             pnl: float, max_lvl: int, hold_bars: int, live: bool = False):
    """Format and send an exit notification."""
    icon = {"TP_HIT": "✅", "LIQUIDATED": "💀", "TIMEOUT": "⏱️"}.get(reason, "❓")
    hold_h = hold_bars * 4
    msg = (
        f"{icon} **Grid closed ({reason})** | "
        f"Exit: ${exit_price:,.0f} | Blended was: ${blended:,.0f} | "
        f"PnL: ${pnl:+.2f} | Max level: L{max_lvl} | Held: {hold_h}h"
    )
    notify(msg, live)

# ─── Helpers ───────────────────────────────────────────────────────────────
def load_candles(coin: str, interval: str) -> pd.DataFrame:
    path = DATA_DIR / f"candles_{coin}_{interval}.csv.gz"
    rows = []
    with gzip.open(path, 'rt') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    df = pd.DataFrame(rows)
    df['time']  = pd.to_datetime(df['open_time_ms'].astype(float), unit='ms')
    df['close'] = df['close'].astype(float)
    df['high']  = df['high'].astype(float)
    df['low']   = df['low'].astype(float)
    df = df.sort_values('time').reset_index(drop=True)
    df['ema34'] = df['close'].ewm(span=34, adjust=False).mean()
    df['ma21']  = df['close'].rolling(21).mean()
    df['pct_below_ema34'] = (df['ema34'] - df['close']) / df['ema34'] * 100
    df['pct_below_ma21']  = (df['ma21']  - df['close']) / df['ma21']  * 100
    return df.dropna(subset=['ema34','ma21']).reset_index(drop=True)

def blended_entry(positions):
    tc = sum(p.btc_qty * p.entry for p in positions)
    tq = sum(p.btc_qty for p in positions)
    return tc / tq if tq else 0.0

def unrealized_pnl(positions, price):
    return sum(p.btc_qty * (price - p.entry) for p in positions)

def funding_cost(positions, bars_held):
    return sum(p.notional for p in positions) * (FUNDING_PER_8H_PCT / 100) * (bars_held / 2)

def level_params(level_idx, entry_price):
    margin   = BASE_MARGIN * (MULTIPLIER ** level_idx)
    notional = margin * LEVERAGE
    btc_qty  = notional / entry_price
    return margin, notional, btc_qty

# ─── Backtester ────────────────────────────────────────────────────────────
def run_backtest(live: bool = False, notify_fills: bool = True):
    df      = load_candles("BTC", "4h")
    n       = len(df)
    account = ACCOUNT_USD
    cycles  = []
    current = None
    last_exit_bar = -99

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']

        # ── IDLE ─────────────────────────────────────────────────────────
        if current is None:
            if i - last_exit_bar < COOLDOWN_BARS:
                continue
            if (row['pct_below_ema34'] >= TRIGGER_PCT and
                    row['pct_below_ma21'] >= TRIGGER_PCT):
                m, n_, q = level_params(0, cl)
                p = Position(level=1, entry=cl, margin=m, notional=n_,
                             btc_qty=q, bar_opened=i)
                current = Cycle(start_bar=i, start_price=cl,
                                blended_entry=cl, max_levels_hit=1)
                current.positions.append(p)
                if notify_fills:
                    notify(f"🟢 **Grid opened L1** | Entry: ${cl:,.0f} | "
                           f"EMA34: ${row['ema34']:,.0f} | MA21: ${row['ma21']:,.0f} | "
                           f"Margin: ${m:.0f}", live)
            continue

        # ── ACTIVE ───────────────────────────────────────────────────────
        positions  = current.positions
        levels_hit = len(positions)

        # 1) Fill next level if price hits target
        for lvl_idx in range(levels_hit, NUM_LEVELS):
            target = current.start_price * (1 - CUM_DROPS[lvl_idx - 1])
            if lo <= target:
                m, n_, q = level_params(lvl_idx, target)
                p = Position(level=lvl_idx+1, entry=target, margin=m,
                             notional=n_, btc_qty=q, bar_opened=i)
                positions.append(p)
                current.max_levels_hit = lvl_idx + 1
                be = blended_entry(positions)
                current.blended_entry = be
                if notify_fills:
                    fmt_level_fill(lvl_idx+1, target, be,
                                   sum(p.margin for p in positions), positions, live)
                break   # one fill per bar

        be             = blended_entry(positions)
        current.blended_entry  = be
        total_margin   = sum(p.margin   for p in positions)
        total_notional = sum(p.notional for p in positions)
        bars_held      = i - current.start_bar

        # 2) Liquidation check (at candle low)
        equity = account + unrealized_pnl(positions, lo)
        maint  = total_notional * MAINT_MARGIN_RATE
        if equity <= maint:
            fc  = funding_cost(positions, bars_held)
            pnl = -total_margin - fc
            current.update(exit_price=lo, exit_bar=i, exit_reason="LIQUIDATED",
                           pnl=pnl, funding_cost=fc)
            account += pnl
            if notify_fills:
                fmt_exit("LIQUIDATED", lo, be, pnl, current.max_levels_hit, bars_held, live)
            cycles.append(current)
            current = None; last_exit_bar = i
            continue

        # 3) Take-profit check (at candle high)
        tp_price = be * (1 + TP_PCT / 100)
        if hi >= tp_price:
            fc    = funding_cost(positions, bars_held)
            gross = unrealized_pnl(positions, tp_price)
            pnl   = gross - fc
            current.update(exit_price=tp_price, exit_bar=i, exit_reason="TP_HIT",
                           pnl=pnl, funding_cost=fc)
            account += pnl
            if notify_fills:
                fmt_exit("TP_HIT", tp_price, be, pnl, current.max_levels_hit, bars_held, live)
            cycles.append(current)
            current = None; last_exit_bar = i
            continue

        # 4) Timeout
        if bars_held >= MAX_HOLD_BARS:
            fc    = funding_cost(positions, bars_held)
            gross = unrealized_pnl(positions, cl)
            pnl   = gross - fc
            current.update(exit_price=cl, exit_bar=i, exit_reason="TIMEOUT",
                           pnl=pnl, funding_cost=fc)
            account += pnl
            if notify_fills:
                fmt_exit("TIMEOUT", cl, be, pnl, current.max_levels_hit, bars_held, live)
            cycles.append(current)
            current = None; last_exit_bar = i

    # Close open cycle at end
    if current is not None:
        positions  = current.positions
        bars_held  = n - 1 - current.start_bar
        fc         = funding_cost(positions, bars_held)
        gross      = unrealized_pnl(positions, df.iloc[-1]['close'])
        pnl        = gross - fc
        current.update(exit_price=df.iloc[-1]['close'], exit_bar=n-1,
                       exit_reason="END_OF_DATA", pnl=pnl, funding_cost=fc)
        account += pnl
        cycles.append(current)

    return cycles, df, account

# ─── Results Reporter ──────────────────────────────────────────────────────
def print_results(cycles, df, final_account):
    sep = "=" * 65
    gaps_str = " / ".join(f"{g}%" for g in LEVEL_GAPS)
    print(f"\n{sep}")
    print("  BACKTEST RESULTS v2 — VARIABLE SPACING + NOTIFY")
    print(f"  Gaps: {gaps_str} | 5L | 2x | $8 base | 20x | $500 | 4H")
    print(f"  Period: {df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()}")
    print(sep)

    won     = [c for c in cycles if c.exit_reason == "TP_HIT"]
    lost    = [c for c in cycles if c.exit_reason == "LIQUIDATED"]
    timeout = [c for c in cycles if c.exit_reason == "TIMEOUT"]
    eod     = [c for c in cycles if c.exit_reason == "END_OF_DATA"]
    months  = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    total_pnl = sum(c.pnl for c in cycles)

    print(f"\n  CYCLE COUNTS:")
    print(f"    Total:          {len(cycles)}")
    print(f"    TP exits (✓):   {len(won):3d}  ({len(won)/len(cycles)*100:.0f}%)")
    print(f"    Liquidated (✗): {len(lost):3d}  ({len(lost)/len(cycles)*100:.0f}%)")
    print(f"    Timeout (~):    {len(timeout):3d}")
    print(f"    End-of-data:    {len(eod):3d}")

    if won:
        avg_bars = np.mean([c.exit_bar - c.start_bar for c in won])
        ldist = {}
        for c in won:
            ldist[c.max_levels_hit] = ldist.get(c.max_levels_hit, 0) + 1
        print(f"\n  WINNING CYCLES:")
        print(f"    Avg profit:     ${np.mean([c.pnl for c in won]):.2f}")
        print(f"    Total profit:   ${sum(c.pnl for c in won):.2f}")
        print(f"    Avg hold:       {avg_bars:.1f} bars ({avg_bars*4:.0f}h)")
        print(f"    Levels filled:")
        for lvl in sorted(ldist):
            pct = ldist[lvl]/len(won)*100
            bar = '█' * int(pct/2)
            print(f"      L{lvl}: {bar} ({ldist[lvl]}, {pct:.0f}%)")

    if lost:
        print(f"\n  LIQUIDATED:")
        for c in lost:
            drop = (c.start_price - c.exit_price) / c.start_price * 100
            date = df.iloc[c.start_bar]['time'].strftime('%Y-%m-%d')
            print(f"    {date}  ${c.start_price:,.0f}→${c.exit_price:,.0f} "
                  f"({drop:.1f}% drop, L{c.max_levels_hit}) PnL:${c.pnl:.2f}")

    print(f"\n  OVERALL:")
    print(f"    Start:          $500.00")
    print(f"    End:            ${final_account:.2f}")
    print(f"    Total PnL:      ${total_pnl:+.2f}  ({total_pnl/500*100:+.1f}%)")
    print(f"    Monthly avg:    ${total_pnl/months:+.2f}")
    print(f"    Cycles/month:   {len(cycles)/months:.1f}")

    print(f"\n  CUMULATIVE LEVEL SPACINGS:")
    cum = 0
    for i, g in enumerate(LEVEL_GAPS):
        cum += g
        print(f"    L{i+2}: {g:.1f}% below L{i+1} (cumulative {cum:.1f}% below trigger)")

if __name__ == "__main__":
    print("Running backtest with variable spacing + notification hooks...")
    print("(Notifications suppressed in backtest mode — will fire live in production)\n")
    cycles, df, final_account = run_backtest(live=False, notify_fills=False)
    print_results(cycles, df, final_account)
