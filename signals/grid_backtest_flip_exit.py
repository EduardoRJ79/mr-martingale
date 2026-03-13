"""
Mr Martingale — Flip Exit Backtest
Exit condition: hold until opposite signal fires (instead of fixed 0.5% TP)

Long grid exits when: price rises 2.5%+ above BOTH MAs (short trigger)
Short grid exits when: price drops 0.5%+ below BOTH MAs (long trigger)

Compare vs baseline: fixed 0.5% TP exit
Both run on same $400 account / $6.4 base / 5L / 2x / 4H params.
"""

import gzip, csv, pandas as pd, numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

ACCOUNT_USD        = 400.0
BASE_MARGIN        = 6.4
MULTIPLIER         = 2.0
NUM_LEVELS         = 5
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]
LONG_TRIGGER_PCT   = 0.5
SHORT_TRIGGER_PCT  = 2.5
LONG_LEVERAGE      = 20
SHORT_LEVERAGE     = 15
MAINT_MARGIN_RATE  = 0.005
FUNDING_PER_8H_PCT = 0.0013
MAX_HOLD_BARS      = 30        # 5-day timeout (shared by both modes)
COOLDOWN_BARS      = 1
TAKER_FEE          = 0.000432
MAKER_FEE          = 0.000144

CUM_GAPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_GAPS.append(acc / 100)

@dataclass
class Level:
    idx: int; target_px: float; margin: float
    notional: float; btc_qty: float
    filled: bool = False; fill_px: float = 0.0

@dataclass
class Grid:
    side: str; start_bar: int; trigger_px: float
    levels: List[Level] = field(default_factory=list)
    blended: float = 0.0; total_qty: float = 0.0
    total_margin: float = 0.0; total_notional: float = 0.0
    tp_price: float = 0.0; max_lvl: int = 0
    exit_px: float = 0.0; exit_bar: int = 0
    exit_reason: str = ""; pnl: float = 0.0
    fee_cost: float = 0.0; funding_cost: float = 0.0

    def recalc(self, tp_mode: str, tp_pct: float = 0.5):
        filled = [l for l in self.levels if l.filled]
        if not filled: return
        tc = sum(l.btc_qty * l.fill_px for l in filled)
        tq = sum(l.btc_qty for l in filled)
        self.blended        = tc / tq
        self.total_qty      = tq
        self.total_margin   = sum(l.margin   for l in filled)
        self.total_notional = sum(l.notional for l in filled)
        self.max_lvl        = max(l.idx + 1  for l in filled)
        # TP price only used in fixed-tp mode
        if tp_mode == 'fixed':
            if self.side == 'long':
                self.tp_price = self.blended * (1 + tp_pct / 100)
            else:
                self.tp_price = self.blended * (1 - tp_pct / 100)

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
    df = df.dropna(subset=['ema34','ma21']).reset_index(drop=True)
    df['pct_below_ema34'] = (df['ema34'] - df['close']) / df['ema34'] * 100
    df['pct_below_ma21']  = (df['ma21']  - df['close']) / df['ma21']  * 100
    df['pct_above_ema34'] = (df['close'] - df['ema34']) / df['ema34'] * 100
    df['pct_above_ma21']  = (df['close'] - df['ma21'])  / df['ma21']  * 100
    return df

def make_grid(side, bar_idx, trigger_px, tp_mode):
    leverage = LONG_LEVERAGE if side == 'long' else SHORT_LEVERAGE
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px)
    for i in range(NUM_LEVELS):
        margin   = BASE_MARGIN * (MULTIPLIER ** i)
        notional = margin * leverage
        btc_qty  = notional / trigger_px
        target   = trigger_px if i == 0 else (
            trigger_px * (1 - CUM_GAPS[i-1]) if side == 'long'
            else trigger_px * (1 + CUM_GAPS[i-1])
        )
        g.levels.append(Level(idx=i, target_px=target, margin=margin,
                               notional=notional, btc_qty=btc_qty))
    g.levels[0].filled = True; g.levels[0].fill_px = trigger_px
    g.recalc(tp_mode)
    return g

def try_fill(grid, lo, hi, tp_mode):
    fc = sum(1 for l in grid.levels if l.filled)
    for i in range(fc, NUM_LEVELS):
        l = grid.levels[i]
        if grid.side == 'long' and lo <= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            grid.recalc(tp_mode); return True
        elif grid.side == 'short' and hi >= l.target_px:
            l.filled = True; l.fill_px = l.target_px
            grid.recalc(tp_mode); return True
    return False

def unrealized(grid, price):
    f = [l for l in grid.levels if l.filled]
    if grid.side == 'long':
        return sum(l.btc_qty * (price - l.fill_px) for l in f)
    return sum(l.btc_qty * (l.fill_px - price) for l in f)

def close_grid(grid, exit_px, exit_bar, reason, bars_held):
    fc  = grid.total_notional * (FUNDING_PER_8H_PCT / 100) * (bars_held / 2)
    fee = sum((TAKER_FEE if i == 0 else MAKER_FEE) * l.notional +
              l.btc_qty * exit_px * MAKER_FEE
              for i, l in enumerate(grid.levels) if l.filled)
    gross = (sum(l.btc_qty * (exit_px - l.fill_px) for l in grid.levels if l.filled)
             if grid.side == 'long' else
             sum(l.btc_qty * (l.fill_px - exit_px) for l in grid.levels if l.filled))
    grid.pnl = gross - fc - fee
    grid.fee_cost = fee; grid.funding_cost = fc
    grid.exit_px = exit_px; grid.exit_bar = exit_bar; grid.exit_reason = reason

def run(tp_mode: str):
    """
    tp_mode: 'fixed'  — exit at 0.5% above blended entry
             'flip'   — exit when opposite MA signal fires
    """
    df = load_candles()
    n  = len(df)
    account   = ACCOUNT_USD
    grid: Optional[Grid] = None
    last_exit = -99
    cycles    = []
    peak      = ACCOUNT_USD
    max_dd    = 0.0

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']
        long_sig  = row['pct_below_ema34'] >= LONG_TRIGGER_PCT  and row['pct_below_ma21'] >= LONG_TRIGGER_PCT
        short_sig = row['pct_above_ema34'] >= SHORT_TRIGGER_PCT and row['pct_above_ma21'] >= SHORT_TRIGGER_PCT

        if grid is not None:
            bh = i - grid.start_bar
            try_fill(grid, lo, hi, tp_mode)

            # ── Liquidation ───────────────────────────────────────────
            liq_px = lo if grid.side == 'long' else hi
            if account + unrealized(grid, liq_px) <= grid.total_notional * MAINT_MARGIN_RATE:
                close_grid(grid, liq_px, i, 'LIQUIDATED', bh)
                account += grid.pnl; cycles.append(grid)
                grid = None; last_exit = i

            # ── Exit condition ────────────────────────────────────────
            elif tp_mode == 'fixed':
                # Fixed TP: price reaches 0.5% above blended
                tp_hit = (grid.side == 'long' and hi >= grid.tp_price) or \
                         (grid.side == 'short' and lo <= grid.tp_price)
                if tp_hit:
                    close_grid(grid, grid.tp_price, i, 'TP_HIT', bh)
                    account += grid.pnl; cycles.append(grid)
                    grid = None; last_exit = i

            elif tp_mode == 'flip':
                # Flip exit: hold until opposite signal
                flip = (grid.side == 'long'  and short_sig) or \
                       (grid.side == 'short' and long_sig)
                if flip:
                    # Exit at close (signal confirmed this bar)
                    close_grid(grid, cl, i, 'FLIP_EXIT', bh)
                    account += grid.pnl; cycles.append(grid)
                    # Allow immediate re-entry on opposite side this bar
                    last_exit = i - 1
                    grid = None

            # ── Timeout — only enforced in fixed-TP mode ──────────────
            if grid is not None and tp_mode == 'fixed' and bh >= MAX_HOLD_BARS:
                close_grid(grid, cl, i, 'TIMEOUT', bh)
                account += grid.pnl; cycles.append(grid)
                grid = None; last_exit = i

        # ── Open new grid ─────────────────────────────────────────────
        if grid is None and i - last_exit >= COOLDOWN_BARS:
            if long_sig:
                grid = make_grid('long', i, cl, tp_mode)
            elif short_sig:
                grid = make_grid('short', i, cl, tp_mode)

        if account > peak: peak = account
        dd = (peak - account) / peak * 100
        if dd > max_dd: max_dd = dd

    # Close open grid at end
    if grid is not None:
        bh = n - 1 - grid.start_bar
        close_grid(grid, df.iloc[-1]['close'], n-1, 'END_OF_DATA', bh)
        account += grid.pnl; cycles.append(grid)

    return cycles, df, account, max_dd


def report(label, tp_mode, cycles, df, final_account, max_dd):
    months  = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    years   = months / 12
    won     = [c for c in cycles if c.exit_reason in ('TP_HIT', 'FLIP_EXIT')]
    liq     = [c for c in cycles if c.exit_reason == 'LIQUIDATED']
    timeout = [c for c in cycles if c.exit_reason == 'TIMEOUT']
    eod     = [c for c in cycles if c.exit_reason == 'END_OF_DATA']
    long_c  = [c for c in cycles if c.side == 'long']
    short_c = [c for c in cycles if c.side == 'short']

    total_pnl  = sum(c.pnl for c in cycles)
    total_fees = sum(c.fee_cost for c in cycles)
    total_fund = sum(c.funding_cost for c in cycles)
    ann = ((final_account / ACCOUNT_USD) ** (1/years) - 1) * 100 if final_account > 0 else float('nan')

    avg_hold_bars = np.mean([c.exit_bar - c.start_bar for c in won]) if won else 0
    avg_pnl_won   = np.mean([c.pnl for c in won]) if won else 0
    max_pnl_won   = max((c.pnl for c in won), default=0)

    # Level distribution
    ldist = {}
    for c in won:
        ldist[c.max_lvl] = ldist.get(c.max_lvl, 0) + 1

    print(f"\n{'='*65}")
    print(f"  {label}")
    print(f"{'='*65}")
    print(f"  Start: $400  →  End: ${final_account:.2f}  ({total_pnl/ACCOUNT_USD*100:+.1f}%)")
    print(f"  Annualized: {ann:.1f}%  |  Monthly: ${total_pnl/months:+.2f}/mo")
    print(f"  Max drawdown: {max_dd:.1f}%")
    print(f"  Total cycles: {len(cycles)}  ({len(long_c)} long / {len(short_c)} short)  {len(cycles)/months:.1f}/mo")
    print(f"  Exits — Win: {len(won)} ({len(won)/len(cycles)*100:.0f}%)  Liq: {len(liq)}  Timeout: {len(timeout)}  EOD: {len(eod)}")
    print(f"  Avg profit/win: ${avg_pnl_won:.2f}  |  Best: ${max_pnl_won:.2f}")
    print(f"  Avg hold (wins): {avg_hold_bars*4:.0f}h  ({avg_hold_bars:.1f} bars)")
    print(f"  Fees: ${total_fees:.2f}  |  Funding: ${total_fund:.2f}")
    if ldist:
        print(f"  Level fills (winning exits):")
        for lvl in sorted(ldist):
            pct = ldist[lvl]/len(won)*100
            bar = '█' * int(pct/2)
            print(f"    L{lvl}: {bar} {ldist[lvl]} ({pct:.0f}%)")
    if liq:
        print(f"  ⚠️  Liquidations:")
        for c in liq[:5]:
            drop = abs(c.trigger_px - c.exit_px) / c.trigger_px * 100
            date = df.iloc[c.start_bar]['time'].strftime('%Y-%m-%d')
            print(f"    {date} [{c.side}] {drop:.1f}% move, L{c.max_lvl}, PnL ${c.pnl:.2f}")

    # Profit distribution for flip mode
    if tp_mode == 'flip' and won:
        pnls = sorted([c.pnl for c in won])
        print(f"  PnL distribution (wins):")
        print(f"    p10: ${np.percentile(pnls,10):.2f}  p25: ${np.percentile(pnls,25):.2f}  "
              f"median: ${np.median(pnls):.2f}  p75: ${np.percentile(pnls,75):.2f}  p90: ${np.percentile(pnls,90):.2f}")
        neg = [p for p in pnls if p < 0]
        print(f"    Losing exits: {len(neg)} ({len(neg)/len(won)*100:.0f}% of wins — exited at loss before TP)")


if __name__ == "__main__":
    print("Mr Martingale — Flip Exit vs Fixed TP Backtest")
    print("$400 | $6.4 base | 5L | 2x | Long 0.5%/20x | Short 2.5%/15x | 4H")
    print("Timeout: 120h (30 bars) for both modes\n")

    cycles_fixed, df, final_fixed, dd_fixed = run('fixed')
    cycles_flip,  df, final_flip,  dd_flip  = run('flip')

    report("BASELINE — Fixed 0.5% TP",     'fixed', cycles_fixed, df, final_fixed, dd_fixed)
    report("FLIP EXIT — Hold for opposite signal", 'flip',  cycles_flip,  df, final_flip,  dd_flip)

    # Summary comparison
    months = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    pnl_fixed = sum(c.pnl for c in cycles_fixed)
    pnl_flip  = sum(c.pnl for c in cycles_flip)
    print(f"\n{'='*65}")
    print(f"  HEAD-TO-HEAD")
    print(f"{'='*65}")
    print(f"  {'Metric':<30} {'Fixed TP':>12} {'Flip Exit':>12}")
    print(f"  {'-'*54}")
    print(f"  {'Total return':<30} {pnl_fixed/ACCOUNT_USD*100:>+11.1f}% {pnl_flip/ACCOUNT_USD*100:>+11.1f}%")
    print(f"  {'Monthly profit':<30} ${pnl_fixed/months:>+10.2f} ${pnl_flip/months:>+10.2f}")
    print(f"  {'Max drawdown':<30} {dd_fixed:>11.1f}% {dd_flip:>11.1f}%")
    print(f"  {'Total cycles':<30} {len(cycles_fixed):>12} {len(cycles_flip):>12}")
    print(f"  {'Cycles/month':<30} {len(cycles_fixed)/months:>12.1f} {len(cycles_flip)/months:>12.1f}")
    print(f"  {'Liquidations':<30} {sum(1 for c in cycles_fixed if c.exit_reason=='LIQUIDATED'):>12} {sum(1 for c in cycles_flip if c.exit_reason=='LIQUIDATED'):>12}")
    print(f"  {'Timeouts':<30} {sum(1 for c in cycles_fixed if c.exit_reason=='TIMEOUT'):>12} {sum(1 for c in cycles_flip if c.exit_reason=='TIMEOUT'):>12}")
    wins_fixed = [c for c in cycles_fixed if c.exit_reason in ('TP_HIT','FLIP_EXIT')]
    wins_flip  = [c for c in cycles_flip  if c.exit_reason in ('TP_HIT','FLIP_EXIT')]
    all_flip   = cycles_flip  # all cycles for hold time

    avg_hold_fixed = np.mean([c.exit_bar - c.start_bar for c in wins_fixed]) * 4 if wins_fixed else 0
    avg_hold_flip  = np.mean([c.exit_bar - c.start_bar for c in wins_flip])  * 4 if wins_flip  else 0
    max_hold_flip  = max((c.exit_bar - c.start_bar for c in all_flip), default=0) * 4
    avg_hold_all_flip = np.mean([c.exit_bar - c.start_bar for c in all_flip]) * 4 if all_flip else 0

    print(f"  {'Avg hold (wins, hours)':<30} {avg_hold_fixed:>12.0f} {avg_hold_flip:>12.0f}")
    print(f"  {'Avg hold (wins, days)':<30} {avg_hold_fixed/24:>12.1f} {avg_hold_flip/24:>12.1f}")
    print(f"  {'Avg hold (all cycles, days)':<30} {'—':>12} {avg_hold_all_flip/24:>12.1f}")
    print(f"  {'Max hold (any cycle, days)':<30} {'—':>12} {max_hold_flip/24:>12.1f}")

    avg_pnl_fixed = np.mean([c.pnl for c in wins_fixed]) if wins_fixed else 0
    avg_pnl_flip  = np.mean([c.pnl for c in wins_flip])  if wins_flip  else 0
    print(f"  {'Avg profit/win':<30} ${avg_pnl_fixed:>+10.2f} ${avg_pnl_flip:>+10.2f}")
    total_fees_fixed = sum(c.fee_cost for c in cycles_fixed)
    total_fees_flip  = sum(c.fee_cost for c in cycles_flip)
    total_fund_fixed = sum(c.funding_cost for c in cycles_fixed)
    total_fund_flip  = sum(c.funding_cost for c in cycles_flip)
    print(f"  {'Total fees':<30} ${total_fees_fixed:>+10.2f} ${total_fees_flip:>+10.2f}")
    print(f"  {'Total funding':<30} ${total_fund_fixed:>+10.2f} ${total_fund_flip:>+10.2f}")

    # Flip exit: hold time distribution
    if all_flip:
        hold_days = [(c.exit_bar - c.start_bar) * 4 / 24 for c in all_flip]
        print(f"\n  FLIP EXIT — Hold time distribution (all cycles):")
        for bucket, label in [(1,'<1d'), (3,'1–3d'), (7,'3–7d'), (14,'7–14d'), (30,'14–30d'), (999,'>30d')]:
            prev = [0,1,3,7,14,30][([1,3,7,14,30,999]).index(bucket)]
            count = sum(1 for d in hold_days if prev <= d < bucket)
            pct   = count / len(hold_days) * 100
            bar   = '█' * int(pct / 3)
            print(f"    {label:>7}: {bar} {count} ({pct:.0f}%)")
