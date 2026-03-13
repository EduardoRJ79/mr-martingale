"""
Dual-Sided Grid Backtest v2 — LONG + SHORT, Single Active Position
$400 account | $6.4 base margin | 5 levels | 2x | 20x leverage

Hyperliquid constraint: only one side open at a time.
If the opposite side triggers while a grid is active, the active grid
is force-closed at market before opening the new one.

Long  trigger: price 0.5%+ BELOW both EMA34 & SMA14
Short trigger: price 2.5%+ ABOVE both EMA34 & SMA14

Since long and short triggers are mutually exclusive (price can't be
both above and below MAs simultaneously), forced closes only happen
when the market reverses hard enough mid-hold to flip sides.
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
BASE_MARGIN        = 6.4
LEVERAGE           = 20
NUM_LEVELS         = 5
MULTIPLIER         = 2.0
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]
LONG_TRIGGER_PCT   = 0.5
SHORT_TRIGGER_PCT  = 2.5
TP_PCT             = 0.5
MAINT_MARGIN_RATE  = 0.005
FUNDING_PER_8H_PCT = 0.0013
MAX_HOLD_BARS      = 30        # 120h timeout
COOLDOWN_BARS      = 1

TAKER_FEE = 0.000432
MAKER_FEE = 0.000144

# Cumulative gaps from L1 trigger price
CUM_GAPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_GAPS.append(acc / 100)

MAX_MARGIN_PER_SIDE = BASE_MARGIN * sum(2**i for i in range(NUM_LEVELS))  # $198

# ─── Data structures ──────────────────────────────────────────────────────
@dataclass
class Level:
    idx:       int
    target_px: float
    margin:    float
    notional:  float
    btc_qty:   float
    filled:    bool  = False
    fill_px:   float = 0.0

@dataclass
class Grid:
    side:           str
    start_bar:      int
    trigger_px:     float
    levels:         List[Level] = field(default_factory=list)
    blended:        float = 0.0
    total_qty:      float = 0.0
    total_margin:   float = 0.0
    total_notional: float = 0.0
    tp_price:       float = 0.0
    max_lvl:        int   = 0
    exit_px:        float = 0.0
    exit_bar:       int   = 0
    exit_reason:    str   = ""
    pnl:            float = 0.0
    fee_cost:       float = 0.0
    funding_cost:   float = 0.0

    def recalc(self):
        filled = [l for l in self.levels if l.filled]
        if not filled:
            return
        tc = sum(l.btc_qty * l.fill_px for l in filled)
        tq = sum(l.btc_qty for l in filled)
        self.blended        = tc / tq
        self.total_qty      = tq
        self.total_margin   = sum(l.margin   for l in filled)
        self.total_notional = sum(l.notional for l in filled)
        self.max_lvl        = max(l.idx + 1  for l in filled)
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
    df['sma14'] = df['close'].rolling(14).mean()
    df['pct_below_ema34'] = (df['ema34'] - df['close']) / df['ema34'] * 100
    df['pct_below_sma14'] = (df['sma14'] - df['close']) / df['sma14'] * 100
    df['pct_above_ema34'] = (df['close'] - df['ema34']) / df['ema34'] * 100
    df['pct_above_sma14'] = (df['close'] - df['sma14']) / df['sma14'] * 100
    return df.dropna(subset=['ema34', 'sma14']).reset_index(drop=True)

def make_grid(side, bar_idx, trigger_px):
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px)
    for i in range(NUM_LEVELS):
        margin   = BASE_MARGIN * (MULTIPLIER ** i)
        notional = margin * LEVERAGE
        btc_qty  = notional / trigger_px
        if side == 'long':
            target = trigger_px if i == 0 else trigger_px * (1 - CUM_GAPS[i-1])
        else:
            target = trigger_px if i == 0 else trigger_px * (1 + CUM_GAPS[i-1])
        g.levels.append(Level(idx=i, target_px=target,
                               margin=margin, notional=notional, btc_qty=btc_qty))
    g.levels[0].filled  = True
    g.levels[0].fill_px = trigger_px
    g.recalc()
    return g

def unrealized(grid, price):
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
    filled_count = sum(1 for l in grid.levels if l.filled)
    for i in range(filled_count, NUM_LEVELS):
        l = grid.levels[i]
        if grid.side == 'long' and lo <= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            grid.recalc(); return True
        elif grid.side == 'short' and hi >= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            grid.recalc(); return True
    return False

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
    grid: Optional[Grid] = None
    last_exit_bar = -99
    cycles = []
    peak_account = ACCOUNT_USD
    max_drawdown = 0.0
    forced_closes = 0

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']

        long_signal  = (row['pct_below_ema34'] >= LONG_TRIGGER_PCT and
                        row['pct_below_sma14'] >= LONG_TRIGGER_PCT)
        short_signal = (row['pct_above_ema34'] >= SHORT_TRIGGER_PCT and
                        row['pct_above_sma14'] >= SHORT_TRIGGER_PCT)

        # ── Active grid management ────────────────────────────────────
        if grid is not None:
            bars_held = i - grid.start_bar

            # Fill next levels
            try_fill_levels(grid, lo, hi)

            # Liquidation check (worst-case price for open side)
            liq_price   = lo if grid.side == 'long' else hi
            equity      = account + unrealized(grid, liq_price)
            maint       = grid.total_notional * MAINT_MARGIN_RATE

            if equity <= maint:
                close_grid(grid, liq_price, i, "LIQUIDATED", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None; last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

            # TP check
            if check_tp(grid, lo, hi):
                close_grid(grid, grid.tp_price, i, "TP_HIT", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None; last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

            # Opposite trigger: force-close, then open new grid below
            opposite_triggered = (grid.side == 'long' and short_signal) or \
                                  (grid.side == 'short' and long_signal)
            if opposite_triggered:
                close_grid(grid, cl, i, "FORCE_CLOSE", bars_held)
                account += grid.pnl
                cycles.append(grid)
                forced_closes += 1
                grid = None; last_exit_bar = i - 1  # allow immediate re-entry
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                # fall through to open new grid this same bar

            # Timeout
            elif bars_held >= MAX_HOLD_BARS:
                close_grid(grid, cl, i, "TIMEOUT", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None; last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

        # ── Open new grid ─────────────────────────────────────────────
        if grid is None and i - last_exit_bar >= COOLDOWN_BARS:
            if long_signal:
                grid = make_grid('long', i, cl)
            elif short_signal:
                grid = make_grid('short', i, cl)

        # Track drawdown
        if account > peak_account: peak_account = account
        dd = (peak_account - account) / peak_account * 100
        if dd > max_drawdown: max_drawdown = dd

    # Close any open grid at end of data
    if grid is not None:
        bars_held = n - 1 - grid.start_bar
        close_grid(grid, df.iloc[-1]['close'], n-1, "END_OF_DATA", bars_held)
        account += grid.pnl
        cycles.append(grid)

    return cycles, df, account, max_drawdown, forced_closes

# ─── Reporting ────────────────────────────────────────────────────────────
def print_results(cycles, df, final_account, max_dd, forced_closes):
    months = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    years  = months / 12

    long_cycles  = [c for c in cycles if c.side == 'long']
    short_cycles = [c for c in cycles if c.side == 'short']
    all_tp   = [c for c in cycles if c.exit_reason == 'TP_HIT']
    all_liq  = [c for c in cycles if c.exit_reason == 'LIQUIDATED']
    all_to   = [c for c in cycles if c.exit_reason == 'TIMEOUT']
    all_fc   = [c for c in cycles if c.exit_reason == 'FORCE_CLOSE']
    all_eod  = [c for c in cycles if c.exit_reason == 'END_OF_DATA']

    total_pnl  = sum(c.pnl for c in cycles)
    total_fees = sum(c.fee_cost for c in cycles)
    total_fund = sum(c.funding_cost for c in cycles)
    ann_return = ((final_account / ACCOUNT_USD) ** (1/years) - 1) * 100 if final_account > 0 and years > 0 else float('nan')

    print(f"\n{'='*65}")
    print(f"  DUAL-SIDED GRID BACKTEST v2 — SINGLE ACTIVE POSITION")
    print(f"  $400 account | $6.4 base | 5L | 2x | 20x | 4H")
    print(f"  Spacing: [0.5, 1.5, 3.0, 3.0] | Trigger: ±0.5% | TP: 0.5%")
    print(f"  Hyperliquid constraint: only one side open at a time")
    print(f"  Period: {df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()} ({months:.1f} months)")
    print(f"{'='*65}")

    print(f"\n  OVERALL:")
    print(f"  Start: $400.00  →  End: ${final_account:.2f}  ({total_pnl/ACCOUNT_USD*100:+.1f}%)")
    print(f"  Annualized return: {ann_return:.1f}%")
    print(f"  Monthly profit: ${total_pnl/months:+.2f}/mo")
    print(f"  Max drawdown: {max_dd:.1f}%")
    print(f"  Total fees paid: ${total_fees:.2f}")
    print(f"  Total funding paid: ${total_fund:.2f}")

    print(f"\n  CYCLE BREAKDOWN:")
    print(f"  Total cycles: {len(cycles)}  ({len(long_cycles)} long / {len(short_cycles)} short)")
    print(f"  TP exits:     {len(all_tp):4d}  ({len(all_tp)/len(cycles)*100:.0f}%)")
    print(f"  Liquidated:   {len(all_liq):4d}  ({len(all_liq)/len(cycles)*100:.0f}%)")
    print(f"  Force-closed: {len(all_fc):4d}  (opposite side triggered mid-hold)")
    print(f"  Timeout:      {len(all_to):4d}")
    print(f"  End-of-data:  {len(all_eod):4d}")
    print(f"  Cycles/month: {len(cycles)/months:.1f}")

    # Level distribution on TP exits
    if all_tp:
        ldist = {}
        for c in all_tp:
            ldist[c.max_lvl] = ldist.get(c.max_lvl, 0) + 1
        avg_hold = np.mean([c.exit_bar - c.start_bar for c in all_tp]) * 4
        print(f"\n  TP EXIT DETAILS:")
        print(f"  Avg profit/TP: ${np.mean([c.pnl for c in all_tp]):.2f}  |  Avg hold: {avg_hold:.0f}h")
        print(f"  Level distribution:")
        for lvl in sorted(ldist):
            pct = ldist[lvl]/len(all_tp)*100
            bar = '█' * int(pct/2)
            print(f"    L{lvl}: {bar} {ldist[lvl]} ({pct:.0f}%)")

    # Force-close stats
    if all_fc:
        fc_pnl = sum(c.pnl for c in all_fc)
        print(f"\n  FORCE-CLOSE DETAILS:")
        print(f"  Total force-close PnL: ${fc_pnl:+.2f}  (avg ${fc_pnl/len(all_fc):+.2f}/close)")
        long_to_short = sum(1 for c in all_fc if c.side == 'long')
        short_to_long = sum(1 for c in all_fc if c.side == 'short')
        print(f"  Long→Short flips: {long_to_short}  |  Short→Long flips: {short_to_long}")

    # Per-side breakdown
    for label, side_cycles in [("LONG", long_cycles), ("SHORT", short_cycles)]:
        if not side_cycles: continue
        won  = [c for c in side_cycles if c.exit_reason == 'TP_HIT']
        liq  = [c for c in side_cycles if c.exit_reason == 'LIQUIDATED']
        fc   = [c for c in side_cycles if c.exit_reason == 'FORCE_CLOSE']
        to   = [c for c in side_cycles if c.exit_reason == 'TIMEOUT']
        pnl  = sum(c.pnl for c in side_cycles)
        print(f"\n  {label} GRID ({len(side_cycles)} cycles, {len(side_cycles)/months:.1f}/mo):")
        print(f"  PnL: ${pnl:+.2f} ({pnl/months:+.2f}/mo)")
        print(f"  TP: {len(won)} ({len(won)/len(side_cycles)*100:.0f}%)  Liq: {len(liq)}  Force: {len(fc)}  Timeout: {len(to)}")

    if all_liq:
        print(f"\n  ⚠️  LIQUIDATIONS: {len(all_liq)}")
        for c in all_liq[:20]:  # show first 20
            drop = abs(c.trigger_px - c.exit_px) / c.trigger_px * 100
            date = df.iloc[c.start_bar]['time'].strftime('%Y-%m-%d')
            print(f"    {date} [{c.side:5s}] trigger ${c.trigger_px:,.0f} → exit ${c.exit_px:,.0f} ({drop:.1f}%, L{c.max_lvl}) PnL: ${c.pnl:.2f}")
        if len(all_liq) > 20:
            print(f"    ... and {len(all_liq)-20} more")

    # Compare to long-only baseline
    print(f"\n  VS LONG-ONLY BASELINE ($400 / $6.4 base):")
    print(f"  Long-only:  +276.4%  ($400 → $1,505)  $32/mo  0 liquidations")
    print(f"  Dual-sided: {total_pnl/ACCOUNT_USD*100:+.1f}%  ($400 → ${final_account:.0f})  ${total_pnl/months:+.2f}/mo  {len(all_liq)} liquidations")


if __name__ == "__main__":
    print("Running dual-sided backtest v2 (single active position)...")
    cycles, df, final_account, max_dd, forced_closes = run()
    print_results(cycles, df, final_account, max_dd, forced_closes)
