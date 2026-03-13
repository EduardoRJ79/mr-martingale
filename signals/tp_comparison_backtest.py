"""
TP Comparison Backtest: Fixed 0.5% vs MA-Band Reversion TP
==========================================================
Compares two take-profit strategies:
  A) TP = blended_entry * (1 + 0.5%)   [current strategy]
  B) TP = upper_MA_band + 0.1%         [full mean-reversion]

For LONG grids:  TP_B = max(ema34, sma14) * 1.001
For SHORT grids: TP_B = min(ema34, sma14) * 0.999

Uses SMA14 (v1.1 config) and 20x long / 15x short leverage.
Runs over: full period + bear period (Nov 2025 - Feb 2026).
"""

import pandas as pd
import numpy as np
import gzip, csv, json, sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from copy import deepcopy

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

# ─── Config ───────────────────────────────────────────────────────────────
ACCOUNT_USD        = 400.0
BASE_MARGIN_PCT    = 0.016       # 1.6% of account balance (compounds)
LONG_LEVERAGE      = 20
SHORT_LEVERAGE     = 15
NUM_LEVELS         = 5
MULTIPLIER         = 2.0
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]
LONG_TRIGGER_PCT   = 0.5
SHORT_TRIGGER_PCT  = 2.5
MAINT_MARGIN_RATE  = 0.005
FUNDING_PER_8H_PCT = 0.0013
MAX_HOLD_BARS      = 30         # 120h / 4h = 30 bars
COOLDOWN_BARS      = 1

TAKER_FEE = 0.000432
MAKER_FEE = 0.000144

# Cumulative gaps from L1 trigger price
CUM_GAPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_GAPS.append(acc / 100)

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
    tp_pct_from_entry: float = 0.0  # for analysis

    def recalc(self, tp_mode='fixed', ema34=0, sma14=0):
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

        if tp_mode == 'fixed':
            if self.side == 'long':
                self.tp_price = self.blended * (1 + 0.5 / 100)
            else:
                self.tp_price = self.blended * (1 - 0.5 / 100)
        elif tp_mode == 'ma_reversion':
            if self.side == 'long':
                # TP at upper MA band + 0.1%
                upper_ma = max(ema34, sma14)
                self.tp_price = upper_ma * 1.001
                # But never set TP below blended + 0.1% (safety floor)
                floor = self.blended * 1.001
                if self.tp_price < floor:
                    self.tp_price = floor
            else:
                # TP at lower MA band - 0.1%
                lower_ma = min(ema34, sma14)
                self.tp_price = lower_ma * 0.999
                # Safety ceiling: never above blended - 0.1%
                ceil = self.blended * 0.999
                if self.tp_price > ceil:
                    self.tp_price = ceil

        self.tp_pct_from_entry = abs(self.tp_price - self.blended) / self.blended * 100

# ─── Helpers ──────────────────────────────────────────────────────────────
def load_candles(start_date=None, end_date=None):
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
    df = df.dropna(subset=['ema34','sma14']).reset_index(drop=True)
    if start_date:
        df = df[df['time'] >= start_date].reset_index(drop=True)
    if end_date:
        df = df[df['time'] <= end_date].reset_index(drop=True)
    return df

def make_grid(side, bar_idx, trigger_px, account_balance, tp_mode='fixed', ema34=0, sma14=0):
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px)
    leverage = LONG_LEVERAGE if side == 'long' else SHORT_LEVERAGE
    base_margin = account_balance * BASE_MARGIN_PCT

    for i in range(NUM_LEVELS):
        margin   = base_margin * (MULTIPLIER ** i)
        notional = margin * leverage
        btc_qty  = notional / trigger_px
        if side == 'long':
            target = trigger_px if i == 0 else trigger_px * (1 - CUM_GAPS[i-1])
        else:
            target = trigger_px if i == 0 else trigger_px * (1 + CUM_GAPS[i-1])
        g.levels.append(Level(idx=i, target_px=target,
                               margin=margin, notional=notional, btc_qty=btc_qty))
    g.levels[0].filled  = True
    g.levels[0].fill_px = trigger_px
    g.recalc(tp_mode=tp_mode, ema34=ema34, sma14=sma14)
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

def try_fill_levels(grid, lo, hi, tp_mode, ema34, sma14):
    filled_any = False
    filled_count = sum(1 for l in grid.levels if l.filled)
    for i in range(filled_count, NUM_LEVELS):
        l = grid.levels[i]
        if grid.side == 'long' and lo <= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            grid.recalc(tp_mode=tp_mode, ema34=ema34, sma14=sma14)
            filled_any = True
        elif grid.side == 'short' and hi >= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            grid.recalc(tp_mode=tp_mode, ema34=ema34, sma14=sma14)
            filled_any = True
        else:
            break  # levels are ordered; if this one didn't fill, none below will
    return filled_any

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
def run_backtest(df, tp_mode='fixed', label=''):
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
        ema34, sma14 = row['ema34'], row['sma14']

        long_signal  = (row['pct_below_ema34'] >= LONG_TRIGGER_PCT and
                        row['pct_below_sma14'] >= LONG_TRIGGER_PCT)
        short_signal = (row['pct_above_ema34'] >= SHORT_TRIGGER_PCT and
                        row['pct_above_sma14'] >= SHORT_TRIGGER_PCT)

        # ── Active grid management ────────────────────────────────────
        if grid is not None:
            bars_held = i - grid.start_bar

            # Update TP for MA reversion mode (MAs move each bar)
            if tp_mode == 'ma_reversion':
                grid.recalc(tp_mode='ma_reversion', ema34=ema34, sma14=sma14)

            # Fill next levels
            try_fill_levels(grid, lo, hi, tp_mode, ema34, sma14)

            # Liquidation check
            liq_price = lo if grid.side == 'long' else hi
            equity    = account + unrealized(grid, liq_price)
            maint     = grid.total_notional * MAINT_MARGIN_RATE

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

            # Opposite trigger: force-close then open new
            opposite_triggered = (grid.side == 'long' and short_signal) or \
                                  (grid.side == 'short' and long_signal)
            if opposite_triggered:
                close_grid(grid, cl, i, "FORCE_CLOSE", bars_held)
                account += grid.pnl
                cycles.append(grid)
                forced_closes += 1
                grid = None; last_exit_bar = i - 1
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd

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
                grid = make_grid('long', i, cl, account, tp_mode, ema34, sma14)
            elif short_signal:
                grid = make_grid('short', i, cl, account, tp_mode, ema34, sma14)

        # Track drawdown
        if account > peak_account: peak_account = account
        dd = (peak_account - account) / peak_account * 100
        if dd > max_drawdown: max_drawdown = dd

    # Close any open grid at end
    if grid is not None:
        bars_held = n - 1 - grid.start_bar
        close_grid(grid, df.iloc[-1]['close'], n-1, "END_OF_DATA", bars_held)
        account += grid.pnl
        cycles.append(grid)

    return cycles, account, max_drawdown, forced_closes

# ─── Analysis ─────────────────────────────────────────────────────────────
def analyze(cycles, df, label, final_account, max_dd):
    months = max((df['time'].iloc[-1] - df['time'].iloc[0]).days / 30, 0.1)
    years  = months / 12

    tp_cycles   = [c for c in cycles if c.exit_reason == 'TP_HIT']
    timeout_cyc = [c for c in cycles if c.exit_reason == 'TIMEOUT']
    force_cyc   = [c for c in cycles if c.exit_reason == 'FORCE_CLOSE']
    liq_cyc     = [c for c in cycles if c.exit_reason == 'LIQUIDATED']

    long_cycles  = [c for c in cycles if c.side == 'long']
    short_cycles = [c for c in cycles if c.side == 'short']

    total_pnl  = sum(c.pnl for c in cycles)
    total_fees = sum(c.fee_cost for c in cycles)
    total_fund = sum(c.funding_cost for c in cycles)
    win_count  = sum(1 for c in cycles if c.pnl > 0)
    winrate    = win_count / len(cycles) * 100 if cycles else 0

    avg_tp_pnl   = np.mean([c.pnl for c in tp_cycles]) if tp_cycles else 0
    avg_loss_pnl = np.mean([c.pnl for c in cycles if c.pnl <= 0]) if any(c.pnl <= 0 for c in cycles) else 0

    avg_hold_tp  = np.mean([c.exit_bar - c.start_bar for c in tp_cycles]) * 4 if tp_cycles else 0
    avg_hold_all = np.mean([c.exit_bar - c.start_bar for c in cycles]) * 4 if cycles else 0

    # TP target distance stats
    tp_pcts = [c.tp_pct_from_entry for c in tp_cycles] if tp_cycles else [0]
    avg_tp_pct = np.mean(tp_pcts) if tp_pcts else 0
    
    # R:R calculation
    avg_win = np.mean([c.pnl for c in cycles if c.pnl > 0]) if any(c.pnl > 0 for c in cycles) else 0
    avg_loss = abs(np.mean([c.pnl for c in cycles if c.pnl <= 0])) if any(c.pnl <= 0 for c in cycles) else 1
    rr = avg_win / avg_loss if avg_loss > 0 else float('inf')

    ann_return = ((final_account / ACCOUNT_USD) ** (1/years) - 1) * 100 if final_account > 0 and years > 0 else 0

    return {
        'label': label,
        'period_months': months,
        'total_cycles': len(cycles),
        'long_cycles': len(long_cycles),
        'short_cycles': len(short_cycles),
        'tp_exits': len(tp_cycles),
        'timeout_exits': len(timeout_cyc),
        'force_close_exits': len(force_cyc),
        'liquidations': len(liq_cyc),
        'winrate': winrate,
        'cycles_per_month': len(cycles) / months,
        'tp_per_month': len(tp_cycles) / months,
        'final_account': final_account,
        'total_pnl': total_pnl,
        'total_return_pct': total_pnl / ACCOUNT_USD * 100,
        'ann_return_pct': ann_return,
        'monthly_pnl': total_pnl / months,
        'max_drawdown': max_dd,
        'total_fees': total_fees,
        'total_funding': total_fund,
        'avg_tp_pnl': avg_tp_pnl,
        'avg_loss_pnl': avg_loss_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'rr_ratio': rr,
        'avg_tp_pct_from_entry': avg_tp_pct,
        'avg_hold_tp_hours': avg_hold_tp,
        'avg_hold_all_hours': avg_hold_all,
    }

def print_comparison(results_list):
    """Print side-by-side comparison table"""
    print(f"\n{'='*90}")
    print(f"  TP STRATEGY COMPARISON")
    print(f"{'='*90}")

    for r in results_list:
        period = r['label'].split(' — ')[1] if ' — ' in r['label'] else r['label']
        tp_name = r['label'].split(' — ')[0] if ' — ' in r['label'] else r['label']
        print(f"\n  [{tp_name}] Period: {period} ({r['period_months']:.1f} months)")
        print(f"  {'─'*60}")
        print(f"  Account: $400 → ${r['final_account']:.2f}  ({r['total_return_pct']:+.1f}%)")
        print(f"  Annualized: {r['ann_return_pct']:.1f}%  |  Monthly: ${r['monthly_pnl']:+.2f}")
        print(f"  Max DD: {r['max_drawdown']:.2f}%")
        print(f"  ")
        print(f"  Cycles: {r['total_cycles']} ({r['long_cycles']}L / {r['short_cycles']}S) → {r['cycles_per_month']:.1f}/mo")
        print(f"  TP hits: {r['tp_exits']} ({r['tp_per_month']:.1f}/mo)  Timeout: {r['timeout_exits']}  Force: {r['force_close_exits']}  Liq: {r['liquidations']}")
        print(f"  Win rate: {r['winrate']:.1f}%  |  R:R = {r['rr_ratio']:.2f}")
        print(f"  Avg TP PnL: ${r['avg_tp_pnl']:.3f}  |  Avg loss PnL: ${r['avg_loss_pnl']:.3f}")
        print(f"  Avg TP distance: {r['avg_tp_pct_from_entry']:.2f}% from blended entry")
        print(f"  Avg hold (TP): {r['avg_hold_tp_hours']:.0f}h  |  Avg hold (all): {r['avg_hold_all_hours']:.0f}h")
        print(f"  Fees: ${r['total_fees']:.2f}  |  Funding: ${r['total_funding']:.2f}")

    # Direct comparison table
    if len(results_list) >= 2:
        print(f"\n{'='*90}")
        print(f"  HEAD-TO-HEAD COMPARISON")
        print(f"{'='*90}")

        # Group by period
        periods = {}
        for r in results_list:
            parts = r['label'].split(' — ')
            period = parts[1] if len(parts) > 1 else 'unknown'
            tp = parts[0] if len(parts) > 1 else r['label']
            if period not in periods:
                periods[period] = {}
            periods[period][tp] = r

        for period, variants in periods.items():
            if len(variants) < 2:
                continue
            keys = list(variants.keys())
            a, b = variants[keys[0]], variants[keys[1]]

            print(f"\n  Period: {period}")
            print(f"  {'Metric':<30s} {'TP=0.5% Fixed':>18s} {'TP=MA+0.1%':>18s} {'Delta':>12s}")
            print(f"  {'─'*78}")

            metrics = [
                ('Final Account', 'final_account', '${:.2f}', False),
                ('Total Return %', 'total_return_pct', '{:+.1f}%', False),
                ('Ann. Return %', 'ann_return_pct', '{:.1f}%', False),
                ('Monthly PnL', 'monthly_pnl', '${:+.2f}', False),
                ('Max Drawdown', 'max_drawdown', '{:.2f}%', True),
                ('Total Cycles', 'total_cycles', '{:.0f}', False),
                ('TP Hits', 'tp_exits', '{:.0f}', False),
                ('TP Hits/Month', 'tp_per_month', '{:.1f}', False),
                ('Timeouts', 'timeout_exits', '{:.0f}', True),
                ('Force Closes', 'force_close_exits', '{:.0f}', True),
                ('Win Rate', 'winrate', '{:.1f}%', False),
                ('R:R Ratio', 'rr_ratio', '{:.2f}', False),
                ('Avg TP PnL', 'avg_tp_pnl', '${:.3f}', False),
                ('Avg TP %', 'avg_tp_pct_from_entry', '{:.2f}%', False),
                ('Avg Hold (TP)', 'avg_hold_tp_hours', '{:.0f}h', True),
                ('Avg Hold (All)', 'avg_hold_all_hours', '{:.0f}h', True),
                ('Total Fees', 'total_fees', '${:.2f}', True),
                ('Total Funding', 'total_funding', '${:.2f}', True),
            ]

            for name, key, fmt, lower_is_better in metrics:
                va = a[key]
                vb = b[key]
                diff = vb - va
                diff_str = f'{diff:+.2f}'
                if key == 'total_return_pct' or key == 'ann_return_pct' or key == 'winrate':
                    diff_str = f'{diff:+.1f}pp'
                elif 'pnl' in key or 'account' in key or 'fee' in key or 'fund' in key:
                    diff_str = f'${diff:+.2f}'
                elif key == 'max_drawdown':
                    diff_str = f'{diff:+.2f}pp'

                print(f"  {name:<30s} {fmt.format(va):>18s} {fmt.format(vb):>18s} {diff_str:>12s}")


def print_cycle_detail_sample(cycles, label, n=10):
    """Print sample of individual cycles for inspection"""
    tp_cycles = [c for c in cycles if c.exit_reason == 'TP_HIT']
    timeout_cycles = [c for c in cycles if c.exit_reason == 'TIMEOUT']

    print(f"\n  SAMPLE CYCLES — {label}")
    print(f"  {'─'*80}")

    if tp_cycles:
        print(f"  First {min(n, len(tp_cycles))} TP exits:")
        for c in tp_cycles[:n]:
            hold = (c.exit_bar - c.start_bar) * 4
            tp_dist = abs(c.tp_price - c.blended) / c.blended * 100
            print(f"    {c.side:5s} L{c.max_lvl} | entry ${c.blended:,.0f} → TP ${c.tp_price:,.0f} ({tp_dist:.2f}%) | hold {hold}h | PnL ${c.pnl:.3f}")

    if timeout_cycles:
        print(f"  First {min(n, len(timeout_cycles))} Timeouts:")
        for c in timeout_cycles[:n]:
            hold = (c.exit_bar - c.start_bar) * 4
            print(f"    {c.side:5s} L{c.max_lvl} | entry ${c.blended:,.0f} → exit ${c.exit_px:,.0f} | hold {hold}h | PnL ${c.pnl:.3f}")


def run_backtest_tp_sweep(df, fixed_tp_pct):
    """Run backtest with a specific fixed TP percentage"""
    n       = len(df)
    account = ACCOUNT_USD
    grid_s: Optional[Grid] = None
    last_exit_bar = -99
    cycles = []
    peak_account = ACCOUNT_USD
    max_drawdown = 0.0
    forced_closes = 0

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']
        ema34, sma14 = row['ema34'], row['sma14']

        long_signal  = (row['pct_below_ema34'] >= LONG_TRIGGER_PCT and
                        row['pct_below_sma14'] >= LONG_TRIGGER_PCT)
        short_signal = (row['pct_above_ema34'] >= SHORT_TRIGGER_PCT and
                        row['pct_above_sma14'] >= SHORT_TRIGGER_PCT)

        if grid_s is not None:
            bars_held = i - grid_s.start_bar

            filled_count = sum(1 for l in grid_s.levels if l.filled)
            for li in range(filled_count, NUM_LEVELS):
                l = grid_s.levels[li]
                if grid_s.side == 'long' and lo <= l.target_px:
                    l.filled = True; l.fill_px = l.target_px
                elif grid_s.side == 'short' and hi >= l.target_px:
                    l.filled = True; l.fill_px = l.target_px
                else:
                    break

            filled = [l for l in grid_s.levels if l.filled]
            if filled:
                tc = sum(l.btc_qty * l.fill_px for l in filled)
                tq = sum(l.btc_qty for l in filled)
                grid_s.blended = tc / tq
                grid_s.total_qty = tq
                grid_s.total_margin = sum(l.margin for l in filled)
                grid_s.total_notional = sum(l.notional for l in filled)
                grid_s.max_lvl = max(l.idx + 1 for l in filled)
                if grid_s.side == 'long':
                    grid_s.tp_price = grid_s.blended * (1 + fixed_tp_pct / 100)
                else:
                    grid_s.tp_price = grid_s.blended * (1 - fixed_tp_pct / 100)
                grid_s.tp_pct_from_entry = fixed_tp_pct

            liq_price = lo if grid_s.side == 'long' else hi
            equity = account + unrealized(grid_s, liq_price)
            maint = grid_s.total_notional * MAINT_MARGIN_RATE
            if equity <= maint:
                close_grid(grid_s, liq_price, i, "LIQUIDATED", bars_held)
                account += grid_s.pnl; cycles.append(grid_s)
                grid_s = None; last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

            if check_tp(grid_s, lo, hi):
                close_grid(grid_s, grid_s.tp_price, i, "TP_HIT", bars_held)
                account += grid_s.pnl; cycles.append(grid_s)
                grid_s = None; last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

            opposite = (grid_s.side == 'long' and short_signal) or (grid_s.side == 'short' and long_signal)
            if opposite:
                close_grid(grid_s, cl, i, "FORCE_CLOSE", bars_held)
                account += grid_s.pnl; cycles.append(grid_s)
                forced_closes += 1
                grid_s = None; last_exit_bar = i - 1
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd

            elif bars_held >= MAX_HOLD_BARS:
                close_grid(grid_s, cl, i, "TIMEOUT", bars_held)
                account += grid_s.pnl; cycles.append(grid_s)
                grid_s = None; last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

        if grid_s is None and i - last_exit_bar >= COOLDOWN_BARS:
            if long_signal:
                grid_s = make_grid('long', i, cl, account, 'fixed', 0, 0)
            elif short_signal:
                grid_s = make_grid('short', i, cl, account, 'fixed', 0, 0)

        if account > peak_account: peak_account = account
        dd = (peak_account - account) / peak_account * 100
        if dd > max_drawdown: max_drawdown = dd

    if grid_s is not None:
        bars_held = n - 1 - grid_s.start_bar
        close_grid(grid_s, df.iloc[-1]['close'], n-1, "END_OF_DATA", bars_held)
        account += grid_s.pnl; cycles.append(grid_s)

    return cycles, account, max_drawdown, forced_closes


# ─── Main ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Loading 4H BTC candles (SMA14 / EMA34)...")
    df_full = load_candles()
    print(f"Full period: {df_full['time'].iloc[0].date()} → {df_full['time'].iloc[-1].date()} ({len(df_full)} bars)")

    # Bear period: Nov 2025 - Feb 2026
    df_bear = load_candles(start_date='2025-11-01')
    print(f"Bear period: {df_bear['time'].iloc[0].date()} → {df_bear['time'].iloc[-1].date()} ({len(df_bear)} bars)")

    # Verify MA distance stats
    print(f"\n  MA Distance Stats (full period):")
    long_trigger_mask = (df_full['pct_below_ema34'] >= LONG_TRIGGER_PCT) & (df_full['pct_below_sma14'] >= LONG_TRIGGER_PCT)
    short_trigger_mask = (df_full['pct_above_ema34'] >= SHORT_TRIGGER_PCT) & (df_full['pct_above_sma14'] >= SHORT_TRIGGER_PCT)
    print(f"  Long trigger bars: {long_trigger_mask.sum()} / {len(df_full)} ({long_trigger_mask.sum()/len(df_full)*100:.1f}%)")
    print(f"  Short trigger bars: {short_trigger_mask.sum()} / {len(df_full)} ({short_trigger_mask.sum()/len(df_full)*100:.1f}%)")

    # When price is below both MAs, how far below typically?
    below_both = df_full[long_trigger_mask]
    if len(below_both) > 0:
        avg_below_ema = below_both['pct_below_ema34'].mean()
        avg_below_sma = below_both['pct_below_sma14'].mean()
        max_below_ema = below_both['pct_below_ema34'].max()
        print(f"  When long triggered: avg {avg_below_ema:.2f}% below EMA34, {avg_below_sma:.2f}% below SMA14")
        print(f"  Max distance below EMA34: {max_below_ema:.2f}%")

    # What % of the time does price return to the MA band?
    # (This is the key assumption being tested)
    print(f"\n  Mean-reversion distance analysis:")
    print(f"  If price is 0.5% below MAs at entry, it needs to travel:")
    print(f"    Fixed TP (0.5%): entry + 0.5% = ~1.0% total move")
    print(f"    MA TP: back to MA band = ~0.5% + whatever distance to MA ≈ 1-3% total move")

    all_results = []

    # ─── Run backtests ────────────────────────────────────────────────
    for period_label, df_period in [("Full (Nov23-Feb26)", df_full), ("Bear (Nov25-Feb26)", df_bear)]:
        for tp_mode, tp_label in [("fixed", "TP=0.5% Fixed"), ("ma_reversion", "TP=MA+0.1%")]:
            print(f"\n{'─'*50}")
            print(f"  Running: {tp_label} — {period_label} ...")
            cycles, final, max_dd, fc = run_backtest(df_period, tp_mode=tp_mode)
            result = analyze(cycles, df_period, f"{tp_label} — {period_label}", final, max_dd)
            all_results.append(result)
            print(f"  Done: {len(cycles)} cycles, ${final:.2f} final, {max_dd:.2f}% max DD")

    # ─── Print comparison ─────────────────────────────────────────────
    print_comparison(all_results)

    # Print sample cycles for detailed inspection
    for period_label, df_period in [("Full (Nov23-Feb26)", df_full)]:
        for tp_mode, tp_label in [("fixed", "TP=0.5% Fixed"), ("ma_reversion", "TP=MA+0.1%")]:
            cycles, _, _, _ = run_backtest(df_period, tp_mode=tp_mode)
            print_cycle_detail_sample(cycles, f"{tp_label} — {period_label}", n=5)

    # ─── Additional analysis: TP distance distribution ────────────────
    print(f"\n{'='*90}")
    print(f"  TP DISTANCE DISTRIBUTION (MA Reversion mode, Full period)")
    print(f"{'='*90}")
    cycles_ma, _, _, _ = run_backtest(df_full, tp_mode='ma_reversion')
    tp_ma_cycles = [c for c in cycles_ma if c.exit_reason == 'TP_HIT']
    if tp_ma_cycles:
        distances = [c.tp_pct_from_entry for c in tp_ma_cycles]
        print(f"  N = {len(tp_ma_cycles)} TP exits")
        print(f"  Min TP distance: {min(distances):.3f}%")
        print(f"  P25 TP distance: {np.percentile(distances, 25):.3f}%")
        print(f"  Median TP distance: {np.median(distances):.3f}%")
        print(f"  P75 TP distance: {np.percentile(distances, 75):.3f}%")
        print(f"  Max TP distance: {max(distances):.3f}%")
        print(f"  Mean TP distance: {np.mean(distances):.3f}%")

    # ─── Sensitivity sweep ───────────────────────────────────────
    print(f"\n{'='*90}")
    print(f"  SENSITIVITY: TP at various fixed levels (Full period)")
    print(f"{'='*90}")
    print(f"  {'TP %':>6s} {'Cycles':>8s} {'TP':>5s} {'TO':>5s} {'FC':>5s} {'Liq':>5s} {'WR%':>7s} {'Final$':>10s} {'Ret%':>10s} {'MaxDD':>7s} {'$/mo':>8s} {'AvgH':>6s}")
    print(f"  {'─'*88}")
    for tp_pct_test in [0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]:
        cycles_sweep, final_sweep, dd_sweep, _ = run_backtest_tp_sweep(df_full, tp_pct_test)
        months = max((df_full['time'].iloc[-1] - df_full['time'].iloc[0]).days / 30, 0.1)
        tp_hits = sum(1 for c in cycles_sweep if c.exit_reason == 'TP_HIT')
        timeouts = sum(1 for c in cycles_sweep if c.exit_reason == 'TIMEOUT')
        force_c = sum(1 for c in cycles_sweep if c.exit_reason == 'FORCE_CLOSE')
        liqs = sum(1 for c in cycles_sweep if c.exit_reason == 'LIQUIDATED')
        total = len(cycles_sweep)
        wr = sum(1 for c in cycles_sweep if c.pnl > 0) / total * 100 if total else 0
        avg_hold = np.mean([c.exit_bar - c.start_bar for c in cycles_sweep]) * 4 if cycles_sweep else 0
        pnl = sum(c.pnl for c in cycles_sweep)
        ret = pnl / ACCOUNT_USD * 100
        monthly = pnl / months
        print(f"  {tp_pct_test:>5.1f}% {total:>8d} {tp_hits:>5d} {timeouts:>5d} {force_c:>5d} {liqs:>5d} {wr:>6.1f}% ${final_sweep:>9.2f} {ret:>+9.1f}% {dd_sweep:>6.2f}% ${monthly:>7.2f} {avg_hold:>5.0f}h")

    print(f"\n  BEAR PERIOD (Nov25-Feb26) sensitivity sweep:")
    print(f"  {'TP %':>6s} {'Cycles':>8s} {'TP':>5s} {'TO':>5s} {'FC':>5s} {'Liq':>5s} {'WR%':>7s} {'Final$':>10s} {'Ret%':>10s} {'MaxDD':>7s} {'$/mo':>8s} {'AvgH':>6s}")
    print(f"  {'─'*88}")
    for tp_pct_test in [0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0]:
        cycles_sweep, final_sweep, dd_sweep, _ = run_backtest_tp_sweep(df_bear, tp_pct_test)
        months = max((df_bear['time'].iloc[-1] - df_bear['time'].iloc[0]).days / 30, 0.1)
        tp_hits = sum(1 for c in cycles_sweep if c.exit_reason == 'TP_HIT')
        timeouts = sum(1 for c in cycles_sweep if c.exit_reason == 'TIMEOUT')
        force_c = sum(1 for c in cycles_sweep if c.exit_reason == 'FORCE_CLOSE')
        liqs = sum(1 for c in cycles_sweep if c.exit_reason == 'LIQUIDATED')
        total = len(cycles_sweep)
        wr = sum(1 for c in cycles_sweep if c.pnl > 0) / total * 100 if total else 0
        avg_hold = np.mean([c.exit_bar - c.start_bar for c in cycles_sweep]) * 4 if cycles_sweep else 0
        pnl = sum(c.pnl for c in cycles_sweep)
        ret = pnl / ACCOUNT_USD * 100
        monthly = pnl / months
        print(f"  {tp_pct_test:>5.1f}% {total:>8d} {tp_hits:>5d} {timeouts:>5d} {force_c:>5d} {liqs:>5d} {wr:>6.1f}% ${final_sweep:>9.2f} {ret:>+9.1f}% {dd_sweep:>6.2f}% ${monthly:>7.2f} {avg_hold:>5.0f}h")

