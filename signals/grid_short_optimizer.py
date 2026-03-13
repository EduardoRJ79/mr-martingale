"""
Short Grid Parameter Optimizer
Scans trigger threshold × leverage combinations to find viable short parameters.
Runs full event-driven backtest (not just stat analysis) for each combo.
Long side is fixed at proven params. Short side is swept.
"""

import gzip, csv, pandas as pd, numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

# ─── Fixed params ─────────────────────────────────────────────────────────
ACCOUNT_USD        = 400.0
BASE_MARGIN        = 6.4
MULTIPLIER         = 2.0
NUM_LEVELS         = 5
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]
LONG_TRIGGER_PCT   = 0.5      # fixed — proven
TP_PCT             = 0.5
MAINT_MARGIN_RATE  = 0.005
FUNDING_PER_8H_PCT = 0.0013
MAX_HOLD_BARS      = 30
COOLDOWN_BARS      = 1
TAKER_FEE          = 0.000432
MAKER_FEE          = 0.000144

CUM_GAPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_GAPS.append(acc / 100)

# ─── Data structures ──────────────────────────────────────────────────────
@dataclass
class Level:
    idx: int; target_px: float; margin: float
    notional: float; btc_qty: float
    filled: bool = False; fill_px: float = 0.0

@dataclass
class Grid:
    side: str; start_bar: int; trigger_px: float; leverage: int
    levels: List[Level] = field(default_factory=list)
    blended: float = 0.0; total_qty: float = 0.0
    total_margin: float = 0.0; total_notional: float = 0.0
    tp_price: float = 0.0; max_lvl: int = 0
    exit_px: float = 0.0; exit_bar: int = 0
    exit_reason: str = ""; pnl: float = 0.0
    fee_cost: float = 0.0; funding_cost: float = 0.0

    def recalc(self):
        filled = [l for l in self.levels if l.filled]
        if not filled: return
        tc = sum(l.btc_qty * l.fill_px for l in filled)
        tq = sum(l.btc_qty for l in filled)
        self.blended = tc / tq
        self.total_qty = tq
        self.total_margin = sum(l.margin for l in filled)
        self.total_notional = sum(l.notional for l in filled)
        self.max_lvl = max(l.idx + 1 for l in filled)
        self.tp_price = self.blended * (1 - TP_PCT/100) if self.side=='short' else self.blended * (1 + TP_PCT/100)

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

def make_grid(side, bar_idx, trigger_px, leverage):
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px, leverage=leverage)
    for i in range(NUM_LEVELS):
        margin   = BASE_MARGIN * (MULTIPLIER ** i)
        notional = margin * leverage
        btc_qty  = notional / trigger_px
        if side == 'long':
            target = trigger_px if i == 0 else trigger_px * (1 - CUM_GAPS[i-1])
        else:
            target = trigger_px if i == 0 else trigger_px * (1 + CUM_GAPS[i-1])
        g.levels.append(Level(idx=i, target_px=target, margin=margin,
                               notional=notional, btc_qty=btc_qty))
    g.levels[0].filled = True; g.levels[0].fill_px = trigger_px
    g.recalc()
    return g

def try_fill(grid, lo, hi):
    fc = sum(1 for l in grid.levels if l.filled)
    for i in range(fc, NUM_LEVELS):
        l = grid.levels[i]
        if grid.side == 'long' and lo <= l.target_px:
            l.filled = True; l.fill_px = l.target_px; grid.recalc(); return
        elif grid.side == 'short' and hi >= l.target_px:
            l.filled = True; l.fill_px = l.target_px; grid.recalc(); return

def unrealized(grid, price):
    f = [l for l in grid.levels if l.filled]
    if grid.side == 'long':
        return sum(l.btc_qty * (price - l.fill_px) for l in f)
    return sum(l.btc_qty * (l.fill_px - price) for l in f)

def close_grid(grid, exit_px, exit_bar, reason, bars_held):
    fc  = grid.total_notional * (FUNDING_PER_8H_PCT/100) * (bars_held/2)
    fee = sum((TAKER_FEE if i==0 else MAKER_FEE) * l.notional +
              l.btc_qty * exit_px * MAKER_FEE
              for i, l in enumerate(grid.levels) if l.filled)
    if grid.side == 'long':
        gross = sum(l.btc_qty * (exit_px - l.fill_px) for l in grid.levels if l.filled)
    else:
        gross = sum(l.btc_qty * (l.fill_px - exit_px) for l in grid.levels if l.filled)
    grid.pnl = gross - fc - fee
    grid.fee_cost = fee; grid.funding_cost = fc
    grid.exit_px = exit_px; grid.exit_bar = exit_bar; grid.exit_reason = reason

def run_backtest(short_trigger, short_leverage):
    df = load_candles()
    n  = len(df)
    account = ACCOUNT_USD
    grid: Optional[Grid] = None
    last_exit = -99
    long_tp = long_liq = short_tp = short_liq = short_fc = 0
    long_pnl = short_pnl = 0.0
    peak = ACCOUNT_USD; max_dd = 0.0

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']
        long_sig  = row['pct_below_ema34'] >= LONG_TRIGGER_PCT and row['pct_below_ma21'] >= LONG_TRIGGER_PCT
        short_sig = row['pct_above_ema34'] >= short_trigger    and row['pct_above_ma21'] >= short_trigger

        if grid is not None:
            bh = i - grid.start_bar
            try_fill(grid, lo, hi)

            liq_px = lo if grid.side=='long' else hi
            if account + unrealized(grid, liq_px) <= grid.total_notional * MAINT_MARGIN_RATE:
                close_grid(grid, liq_px, i, 'LIQ', bh)
                account += grid.pnl
                if grid.side=='long': long_liq += 1; long_pnl += grid.pnl
                else:                 short_liq += 1; short_pnl += grid.pnl
                grid = None; last_exit = i
            elif (grid.side=='long' and hi >= grid.tp_price) or \
                 (grid.side=='short' and lo <= grid.tp_price):
                tp_px = grid.tp_price
                close_grid(grid, tp_px, i, 'TP', bh)
                account += grid.pnl
                if grid.side=='long': long_tp += 1; long_pnl += grid.pnl
                else:                 short_tp += 1; short_pnl += grid.pnl
                grid = None; last_exit = i
            elif (grid.side=='long' and short_sig) or (grid.side=='short' and long_sig):
                close_grid(grid, cl, i, 'FORCE', bh)
                account += grid.pnl
                if grid.side=='short': short_fc += 1; short_pnl += grid.pnl
                else:                  long_pnl += grid.pnl
                grid = None; last_exit = i - 1
            elif bh >= MAX_HOLD_BARS:
                close_grid(grid, cl, i, 'TO', bh)
                account += grid.pnl
                if grid.side=='long': long_pnl += grid.pnl
                else:                 short_pnl += grid.pnl
                grid = None; last_exit = i

        if grid is None and i - last_exit >= COOLDOWN_BARS:
            lev = 20 if long_sig else short_leverage
            if long_sig:
                grid = make_grid('long', i, cl, lev)
            elif short_sig:
                grid = make_grid('short', i, cl, lev)

        if account > peak: peak = account
        dd = (peak - account) / peak * 100
        if dd > max_dd: max_dd = dd

    months = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    total_pnl = long_pnl + short_pnl
    total_liq = long_liq + short_liq
    total_tp  = long_tp  + short_tp

    return {
        'final': account,
        'pnl': total_pnl,
        'pct': total_pnl / ACCOUNT_USD * 100,
        'monthly': total_pnl / months,
        'max_dd': max_dd,
        'total_liq': total_liq,
        'long_tp': long_tp, 'long_liq': long_liq, 'long_pnl': long_pnl,
        'short_tp': short_tp, 'short_liq': short_liq, 'short_pnl': short_pnl,
        'short_fc': short_fc,
        'months': months,
    }

# ─── Main scan ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Scanning short trigger threshold × leverage...")
    print("Long side: fixed at 0.5% trigger, 20x")
    print("Short side: swept across threshold [1.0–5.0%] × leverage [5–15x]")
    print()

    triggers  = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    leverages = [5, 7, 10, 12, 15]

    # Header
    print(f"{'Trigger':>8} {'Lev':>5} {'PnL%':>8} {'$/mo':>8} {'MaxDD':>7} {'Liqs':>6} "
          f"{'L.TP':>6} {'L.Liq':>7} {'S.TP':>6} {'S.Liq':>7} {'S.Force':>8}")
    print("─" * 95)

    best = None
    results = []
    for t in triggers:
        for lev in leverages:
            r = run_backtest(t, lev)
            results.append((t, lev, r))
            liq_flag = " !!!" if r['total_liq'] > 10 else ""
            print(f"{t:>7.1f}% {lev:>5}x {r['pct']:>+7.1f}% {r['monthly']:>+8.2f} "
                  f"{r['max_dd']:>6.1f}% {r['total_liq']:>6} "
                  f"{r['long_tp']:>6} {r['long_liq']:>7} "
                  f"{r['short_tp']:>6} {r['short_liq']:>7} "
                  f"{r['short_fc']:>8}{liq_flag}")

    print()
    print("─" * 95)
    # Best: max PnL with zero liquidations
    viable = [(t, lev, r) for t, lev, r in results if r['total_liq'] == 0]
    if viable:
        best = max(viable, key=lambda x: x[2]['pnl'])
        t, lev, r = best
        print(f"\n✅ BEST VIABLE (0 liquidations): trigger={t}%, short_lev={lev}x")
        print(f"   PnL: {r['pct']:+.1f}%  |  ${r['final']:.2f}  |  ${r['monthly']:+.2f}/mo  |  MaxDD: {r['max_dd']:.1f}%")
        print(f"   Long:  {r['long_tp']} TP / {r['long_liq']} liq  |  PnL ${r['long_pnl']:+.2f}")
        print(f"   Short: {r['short_tp']} TP / {r['short_liq']} liq / {r['short_fc']} force  |  PnL ${r['short_pnl']:+.2f}")
    else:
        low_liq = min(results, key=lambda x: x[2]['total_liq'])
        t, lev, r = low_liq
        print(f"\n⚠️  No zero-liq combo found. Lowest liquidations: trigger={t}%, lev={lev}x ({r['total_liq']} liqs)")
        print(f"   PnL: {r['pct']:+.1f}%  |  ${r['monthly']:+.2f}/mo  |  MaxDD: {r['max_dd']:.1f}%")

    # Long-only baseline for reference
    print()
    lo = run_backtest(999, 1)  # effectively long-only (short threshold unreachable)
    print(f"📊 Long-only baseline: {lo['pct']:+.1f}%  |  ${lo['monthly']:+.2f}/mo  |  {lo['total_liq']} liqs")
