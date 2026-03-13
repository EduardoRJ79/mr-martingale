"""
Dual-Sided Grid Backtest: LONG + SHORT
$400 account | $6.4 base margin | 5 levels | 2x | 20x leverage
Variable spacing: [0.5, 1.5, 3.0, 3.0]

Long  trigger: price 0.5%+ BELOW both EMA34 & MA21
Short trigger: price 0.5%+ ABOVE both EMA34 & MA21

Both grids can be open simultaneously (they hedge each other at max draw).
Cross-margin: full $400 account backs both sides.
Liquidation check uses combined unrealized PnL across both open grids.
"""

import pandas as pd
import numpy as np
import gzip, csv
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

# ─── Config ───────────────────────────────────────────────────────────────
ACCOUNT_USD        = 400.0
BASE_MARGIN        = 6.4       # $6.4 × (1+2+4+8+16) = $198 max per side
LEVERAGE           = 20
NUM_LEVELS         = 5
MULTIPLIER         = 2.0
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]   # gaps between levels (%)
TRIGGER_PCT        = 0.5       # % above/below BOTH MAs to trigger
TP_PCT             = 0.5       # % from blended entry to TP
MAINT_MARGIN_RATE  = 0.005     # 0.5% maintenance margin
FUNDING_PER_8H_PCT = 0.0013    # avg funding per 8h (annualized ~0.57%)
MAX_HOLD_BARS      = 30        # 120h = 5 days at 4H bars
COOLDOWN_BARS      = 1

TAKER_FEE = 0.000432
MAKER_FEE = 0.000144

# Cumulative drops/rises from L1 trigger
CUM_GAPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_GAPS.append(acc / 100)

# Max margin deployed per side if all 5 levels fill
MAX_MARGIN_PER_SIDE = BASE_MARGIN * sum(2**i for i in range(NUM_LEVELS))  # 31x

# ─── Data structures ──────────────────────────────────────────────────────
@dataclass
class Level:
    idx:       int
    target_px: float
    margin:    float
    notional:  float
    btc_qty:   float
    filled:    bool = False
    fill_px:   float = 0.0

@dataclass
class Grid:
    side:          str        # 'long' or 'short'
    start_bar:     int
    trigger_px:    float
    levels:        List[Level] = field(default_factory=list)
    blended:       float = 0.0
    total_qty:     float = 0.0
    total_margin:  float = 0.0
    total_notional:float = 0.0
    tp_price:      float = 0.0
    max_lvl:       int   = 0
    exit_px:       float = 0.0
    exit_bar:      int   = 0
    exit_reason:   str   = ""
    pnl:           float = 0.0
    fee_cost:      float = 0.0
    funding_cost:  float = 0.0

    def recalc(self):
        filled = [l for l in self.levels if l.filled]
        if not filled:
            return
        tc = sum(l.btc_qty * l.fill_px for l in filled)
        tq = sum(l.btc_qty for l in filled)
        self.blended       = tc / tq
        self.total_qty     = tq
        self.total_margin  = sum(l.margin for l in filled)
        self.total_notional= sum(l.notional for l in filled)
        self.max_lvl       = max(l.idx + 1 for l in filled)
        if self.side == 'long':
            self.tp_price = self.blended * (1 + TP_PCT / 100)
        else:
            self.tp_price = self.blended * (1 - TP_PCT / 100)

# ─── Helpers ──────────────────────────────────────────────────────────────
def load_candles():
    path = DATA_DIR / "candles_BTC_4h.csv.gz"
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
    df['pct_above_ema34'] = (df['close'] - df['ema34']) / df['ema34'] * 100
    df['pct_above_ma21']  = (df['close'] - df['ma21'])  / df['ma21']  * 100
    return df.dropna(subset=['ema34','ma21']).reset_index(drop=True)

def make_grid(side, bar_idx, trigger_px):
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px)
    for i in range(NUM_LEVELS):
        margin   = BASE_MARGIN * (MULTIPLIER ** i)
        notional = margin * LEVERAGE
        btc_qty  = notional / trigger_px
        if side == 'long':
            # L1 fills at trigger, L2+ fill below
            target = trigger_px if i == 0 else trigger_px * (1 - CUM_GAPS[i-1])
        else:
            # L1 fills at trigger, L2+ fill above
            target = trigger_px if i == 0 else trigger_px * (1 + CUM_GAPS[i-1])
        g.levels.append(Level(idx=i, target_px=target,
                               margin=margin, notional=notional, btc_qty=btc_qty))
    # Fill L1 immediately
    g.levels[0].filled  = True
    g.levels[0].fill_px = trigger_px
    g.recalc()
    return g

def unrealized(grid, price):
    """Unrealized PnL of all filled levels at given price."""
    filled = [l for l in grid.levels if l.filled]
    if grid.side == 'long':
        return sum(l.btc_qty * (price - l.fill_px) for l in filled)
    else:
        return sum(l.btc_qty * (l.fill_px - price) for l in filled)

def calc_funding(grid, bars_held):
    return grid.total_notional * (FUNDING_PER_8H_PCT / 100) * (bars_held / 2)

def calc_fees(grid, exit_price):
    fee = 0.0
    for i, l in enumerate(grid.levels):
        if not l.filled:
            continue
        entry_rate = TAKER_FEE if i == 0 else MAKER_FEE
        fee += l.notional * entry_rate
        fee += l.btc_qty * exit_price * MAKER_FEE
    return fee

def try_fill_levels(grid, lo, hi):
    """Fill any unfilled levels touched this bar. Returns True if anything filled."""
    changed = False
    filled_count = sum(1 for l in grid.levels if l.filled)
    for i in range(filled_count, NUM_LEVELS):
        l = grid.levels[i]
        if grid.side == 'long' and lo <= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            changed = True; break   # one fill per bar
        elif grid.side == 'short' and hi >= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            changed = True; break
    if changed:
        grid.recalc()
    return changed

def check_tp(grid, lo, hi):
    if grid.side == 'long':
        return hi >= grid.tp_price
    else:
        return lo <= grid.tp_price

def close_grid(grid, exit_px, exit_bar, reason, bars_held):
    fc  = calc_funding(grid, bars_held)
    fee = calc_fees(grid, exit_px)
    if grid.side == 'long':
        gross = sum(l.btc_qty * (exit_px - l.fill_px) for l in grid.levels if l.filled)
    else:
        gross = sum(l.btc_qty * (l.fill_px - exit_px) for l in grid.levels if l.filled)
    grid.pnl          = gross - fc - fee
    grid.fee_cost     = fee
    grid.funding_cost = fc
    grid.exit_px      = exit_px
    grid.exit_bar     = exit_bar
    grid.exit_reason  = reason
    return grid

# ─── Main backtester ──────────────────────────────────────────────────────
def run():
    df      = load_candles()
    n       = len(df)
    account = ACCOUNT_USD
    long_grid:  Optional[Grid] = None
    short_grid: Optional[Grid] = None
    long_last_exit  = -99
    short_last_exit = -99
    long_cycles  = []
    short_cycles = []
    peak_account = ACCOUNT_USD
    max_drawdown = 0.0

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']

        # ── Fill pending levels ────────────────────────────────────────
        if long_grid:
            try_fill_levels(long_grid, lo, hi)
        if short_grid:
            try_fill_levels(short_grid, lo, hi)

        # ── Liquidation: combined unrealized PnL across both grids ────
        combined_upnl = 0.0
        combined_notional = 0.0
        if long_grid:
            combined_upnl     += unrealized(long_grid, lo)
            combined_notional += long_grid.total_notional
        if short_grid:
            combined_upnl     += unrealized(short_grid, hi)   # worst case for short
            combined_notional += short_grid.total_notional

        equity = account + combined_upnl
        maint  = combined_notional * MAINT_MARGIN_RATE

        if combined_notional > 0 and equity <= maint:
            # Liquidation — close everything at worst price
            if long_grid:
                close_grid(long_grid, lo, i, "LIQUIDATED", i - long_grid.start_bar)
                account += long_grid.pnl
                long_cycles.append(long_grid); long_grid = None; long_last_exit = i
            if short_grid:
                close_grid(short_grid, hi, i, "LIQUIDATED", i - short_grid.start_bar)
                account += short_grid.pnl
                short_cycles.append(short_grid); short_grid = None; short_last_exit = i
            if account > peak_account: peak_account = account
            dd = (peak_account - account) / peak_account * 100
            if dd > max_drawdown: max_drawdown = dd
            continue

        # ── TP checks ─────────────────────────────────────────────────
        if long_grid and check_tp(long_grid, lo, hi):
            close_grid(long_grid, long_grid.tp_price, i, "TP_HIT",
                       i - long_grid.start_bar)
            account += long_grid.pnl
            long_cycles.append(long_grid); long_grid = None; long_last_exit = i

        if short_grid and check_tp(short_grid, lo, hi):
            close_grid(short_grid, short_grid.tp_price, i, "TP_HIT",
                       i - short_grid.start_bar)
            account += short_grid.pnl
            short_cycles.append(short_grid); short_grid = None; short_last_exit = i

        # ── Timeout checks ────────────────────────────────────────────
        if long_grid and (i - long_grid.start_bar) >= MAX_HOLD_BARS:
            close_grid(long_grid, cl, i, "TIMEOUT", i - long_grid.start_bar)
            account += long_grid.pnl
            long_cycles.append(long_grid); long_grid = None; long_last_exit = i

        if short_grid and (i - short_grid.start_bar) >= MAX_HOLD_BARS:
            close_grid(short_grid, cl, i, "TIMEOUT", i - short_grid.start_bar)
            account += short_grid.pnl
            short_cycles.append(short_grid); short_grid = None; short_last_exit = i

        # ── New trigger checks ────────────────────────────────────────
        if (long_grid is None and
                i - long_last_exit >= COOLDOWN_BARS and
                row['pct_below_ema34'] >= TRIGGER_PCT and
                row['pct_below_ma21']  >= TRIGGER_PCT):
            long_grid = make_grid('long', i, cl)

        if (short_grid is None and
                i - short_last_exit >= COOLDOWN_BARS and
                row['pct_above_ema34'] >= TRIGGER_PCT and
                row['pct_above_ma21']  >= TRIGGER_PCT):
            short_grid = make_grid('short', i, cl)

        # ── Track drawdown ────────────────────────────────────────────
        if account > peak_account: peak_account = account
        dd = (peak_account - account) / peak_account * 100
        if dd > max_drawdown: max_drawdown = dd

    # Close open grids at end of data
    for grid, cycles in [(long_grid, long_cycles), (short_grid, short_cycles)]:
        if grid is not None:
            bars_held = n - 1 - grid.start_bar
            close_grid(grid, df.iloc[-1]['close'], n-1, "END_OF_DATA", bars_held)
            account += grid.pnl
            cycles.append(grid)

    return long_cycles, short_cycles, df, account, max_drawdown


# ─── Reporting ────────────────────────────────────────────────────────────
def side_report(label, cycles, df):
    if not cycles: return
    won     = [c for c in cycles if c.exit_reason == "TP_HIT"]
    lost    = [c for c in cycles if c.exit_reason == "LIQUIDATED"]
    timeout = [c for c in cycles if c.exit_reason == "TIMEOUT"]
    eod     = [c for c in cycles if c.exit_reason == "END_OF_DATA"]
    months  = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    total_pnl = sum(c.pnl for c in cycles)
    total_fees = sum(c.fee_cost for c in cycles)

    ldist = {}
    for c in won:
        ldist[c.max_lvl] = ldist.get(c.max_lvl, 0) + 1

    avg_hold = np.mean([c.exit_bar - c.start_bar for c in won]) * 4 if won else 0

    print(f"\n  ── {label} ──")
    print(f"  Cycles: {len(cycles)}  TP: {len(won)} ({len(won)/len(cycles)*100:.0f}%)"
          f"  Liq: {len(lost)}  Timeout: {len(timeout)}  EOD: {len(eod)}")
    print(f"  Total PnL: ${total_pnl:+.2f}  |  Monthly: ${total_pnl/months:+.2f}/mo")
    print(f"  Avg profit/cycle: ${np.mean([c.pnl for c in won]):.2f}  |  Avg hold: {avg_hold:.0f}h")
    print(f"  Total fees: ${total_fees:.2f}")
    if won:
        print(f"  Level distribution (TP exits):")
        for lvl in sorted(ldist):
            pct = ldist[lvl]/len(won)*100
            bar = '█' * int(pct/2)
            print(f"    L{lvl}: {bar} {ldist[lvl]} ({pct:.0f}%)")
    if lost:
        print(f"  ⚠️  LIQUIDATIONS: {len(lost)}")
        for c in lost:
            drop = abs(c.trigger_px - c.exit_px) / c.trigger_px * 100
            date = df.iloc[c.start_bar]['time'].strftime('%Y-%m-%d')
            print(f"    {date} trigger ${c.trigger_px:,.0f} → exit ${c.exit_px:,.0f} ({drop:.1f}% move, L{c.max_lvl})")


def print_results(long_cycles, short_cycles, df, final_account, max_dd):
    all_cycles = long_cycles + short_cycles
    months = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    total_pnl = sum(c.pnl for c in all_cycles)
    liq_count = sum(1 for c in all_cycles if c.exit_reason == "LIQUIDATED")
    years = months / 12
    ann_return = ((final_account / ACCOUNT_USD) ** (1/years) - 1) * 100 if years > 0 else 0

    # Simultaneous open tracking
    simultaneous = 0
    for i in range(len(df)):
        l_open = any(c.start_bar <= i < c.exit_bar for c in long_cycles)
        s_open = any(c.start_bar <= i < c.exit_bar for c in short_cycles)
        if l_open and s_open:
            simultaneous += 1
    simultaneous_hrs = simultaneous * 4

    print(f"\n{'='*65}")
    print(f"  DUAL-SIDED GRID BACKTEST — LONG + SHORT")
    print(f"  $400 account | $6.4 base margin | 5L | 2x | 20x | 4H")
    print(f"  Spacing: [0.5, 1.5, 3.0, 3.0] | Trigger: ±0.5% | TP: 0.5%")
    print(f"  Period: {df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()} ({months:.1f} months)")
    print(f"{'='*65}")

    print(f"\n  COMBINED RESULTS:")
    print(f"  Start: $400.00  →  End: ${final_account:.2f}  ({total_pnl/ACCOUNT_USD*100:+.1f}%)")
    print(f"  Annualized return: {ann_return:.1f}%")
    print(f"  Monthly profit: ${total_pnl/months:+.2f}/mo")
    print(f"  Total cycles: {len(all_cycles)}  ({len(long_cycles)} long + {len(short_cycles)} short)")
    print(f"  Cycles/month: {len(all_cycles)/months:.1f}")
    print(f"  Max drawdown: {max_dd:.1f}%")
    print(f"  Liquidations: {liq_count}")
    print(f"  Both sides open simultaneously: {simultaneous} bars ({simultaneous_hrs:.0f}h)")
    print(f"  Max margin ever deployed: ${MAX_MARGIN_PER_SIDE:.0f}/side")

    side_report("LONG GRID", long_cycles, df)
    side_report("SHORT GRID", short_cycles, df)

    # Compare vs long-only $400
    long_only_pnl = sum(c.pnl for c in long_cycles)
    short_only_pnl = sum(c.pnl for c in short_cycles)
    print(f"\n  CONTRIBUTION BREAKDOWN:")
    print(f"  Long PnL:  ${long_only_pnl:+.2f}  ({long_only_pnl/ACCOUNT_USD*100:+.1f}% on capital)")
    print(f"  Short PnL: ${short_only_pnl:+.2f}  ({short_only_pnl/ACCOUNT_USD*100:+.1f}% on capital)")
    print(f"  Combined:  ${total_pnl:+.2f}  ({total_pnl/ACCOUNT_USD*100:+.1f}% on capital)")
    print(f"\n  Long cycles/mo:  {len(long_cycles)/months:.1f}")
    print(f"  Short cycles/mo: {len(short_cycles)/months:.1f}")
    print(f"  Combined/mo:     {len(all_cycles)/months:.1f}")


if __name__ == "__main__":
    print("Running dual-sided backtest (LONG + SHORT)...")
    long_cycles, short_cycles, df, final_account, max_dd = run()
    print_results(long_cycles, short_cycles, df, final_account, max_dd)
