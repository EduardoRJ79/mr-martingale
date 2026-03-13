"""
MA Parameter Grid Search Optimizer
Sweeps EMA span × MA period × MA type for the dual-sided grid bot backtest.

Strategy: same event-driven logic as grid_backtest_dual_v2.py, parameterized.
  - 9 EMA spans × 9 MA periods × 2 MA types = 162 combinations

Fixed params:
  Account: $400 | Base margin: $6.4 | 5 levels | 2x | 20x long / 15x short
  Long trigger: 0.5% below BOTH MAs | Short trigger: 2.5% above BOTH MAs
  TP: 0.5% | Timeout: 30 bars | Cooldown: 1 bar
  Level gaps: [0.5, 1.5, 3.0, 3.0]
  Fees: taker 0.000432, maker 0.000144 | Funding: 0.0013%/8h | Maint: 0.5%
"""

import pandas as pd
import numpy as np
import gzip, csv, itertools, time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"
OUT_CSV  = Path(__file__).parent / "ma_optimization_results.csv"

# ─── Fixed Strategy Params ────────────────────────────────────────────────
ACCOUNT_USD         = 400.0
BASE_MARGIN         = 6.4
LEVERAGE_LONG       = 20
LEVERAGE_SHORT      = 15
NUM_LEVELS          = 5
MULTIPLIER          = 2.0
LEVEL_GAPS          = [0.5, 1.5, 3.0, 3.0]
LONG_TRIGGER_PCT    = 0.5    # % below BOTH MAs
SHORT_TRIGGER_PCT   = 2.5    # % above BOTH MAs
TP_PCT              = 0.5
MAINT_MARGIN_RATE   = 0.005
FUNDING_PER_8H_PCT  = 0.0013
MAX_HOLD_BARS       = 30
COOLDOWN_BARS       = 1
TAKER_FEE           = 0.000432
MAKER_FEE           = 0.000144

# Precompute cumulative gap fractions from trigger price
CUM_GAPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_GAPS.append(acc / 100)

# ─── Sweep Space ──────────────────────────────────────────────────────────
EMA_SPANS  = [10, 14, 20, 21, 26, 34, 50, 55, 89]
MA_PERIODS = [10, 14, 20, 21, 26, 34, 50, 55, 89]
MA_TYPES   = ['SMA', 'EMA']

# ─── Data Structures ──────────────────────────────────────────────────────
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
    leverage:       int
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
def load_candles() -> pd.DataFrame:
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
    return df

def add_indicators(df: pd.DataFrame, ema_span: int, ma_period: int, ma_type: str) -> pd.DataFrame:
    """Add EMA and second MA columns. Returns copy with added signals."""
    df = df.copy()
    df['ema1'] = df['close'].ewm(span=ema_span, adjust=False).mean()
    if ma_type == 'SMA':
        df['ma2'] = df['close'].rolling(ma_period).mean()
    else:
        df['ma2'] = df['close'].ewm(span=ma_period, adjust=False).mean()
    df['pct_below_ema1'] = (df['ema1'] - df['close']) / df['ema1'] * 100
    df['pct_below_ma2']  = (df['ma2']  - df['close']) / df['ma2']  * 100
    df['pct_above_ema1'] = (df['close'] - df['ema1']) / df['ema1'] * 100
    df['pct_above_ma2']  = (df['close'] - df['ma2'])  / df['ma2']  * 100
    return df.dropna(subset=['ema1', 'ma2']).reset_index(drop=True)

def make_grid(side: str, bar_idx: int, trigger_px: float) -> Grid:
    leverage = LEVERAGE_LONG if side == 'long' else LEVERAGE_SHORT
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px, leverage=leverage)
    for i in range(NUM_LEVELS):
        margin   = BASE_MARGIN * (MULTIPLIER ** i)
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
    g.recalc()
    return g

def unrealized(grid: Grid, price: float) -> float:
    filled = [l for l in grid.levels if l.filled]
    if grid.side == 'long':
        return sum(l.btc_qty * (price - l.fill_px) for l in filled)
    else:
        return sum(l.btc_qty * (l.fill_px - price) for l in filled)

def calc_funding(grid: Grid, bars_held: int) -> float:
    return grid.total_notional * (FUNDING_PER_8H_PCT / 100) * (bars_held / 2)

def calc_fees(grid: Grid, exit_price: float) -> float:
    fee = 0.0
    for i, l in enumerate(grid.levels):
        if not l.filled:
            continue
        entry_rate = TAKER_FEE if i == 0 else MAKER_FEE
        fee += l.notional * entry_rate
        fee += l.btc_qty * exit_price * MAKER_FEE
    return fee

def try_fill_levels(grid: Grid, lo: float, hi: float) -> bool:
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

def check_tp(grid: Grid, lo: float, hi: float) -> bool:
    if grid.side == 'long':
        return hi >= grid.tp_price
    else:
        return lo <= grid.tp_price

def close_grid(grid: Grid, exit_px: float, exit_bar: int, reason: str, bars_held: int) -> Grid:
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

# ─── Single Backtest Run ──────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame) -> Tuple[List[Grid], float, float, int]:
    """
    Run the event-driven backtest on a pre-prepared DataFrame with
    'pct_below_ema1', 'pct_below_ma2', 'pct_above_ema1', 'pct_above_ma2' columns.

    Returns: (cycles, final_account, max_drawdown, liq_count)
    """
    n       = len(df)
    account = ACCOUNT_USD
    grid: Optional[Grid] = None
    last_exit_bar = -99
    cycles = []
    peak_account = ACCOUNT_USD
    max_drawdown = 0.0

    hi_arr  = df['high'].values
    lo_arr  = df['low'].values
    cl_arr  = df['close'].values
    pbe1    = df['pct_below_ema1'].values
    pbm2    = df['pct_below_ma2'].values
    pae1    = df['pct_above_ema1'].values
    pam2    = df['pct_above_ma2'].values

    for i in range(n):
        hi = hi_arr[i]
        lo = lo_arr[i]
        cl = cl_arr[i]

        long_signal  = (pbe1[i] >= LONG_TRIGGER_PCT  and pbm2[i] >= LONG_TRIGGER_PCT)
        short_signal = (pae1[i] >= SHORT_TRIGGER_PCT and pam2[i] >= SHORT_TRIGGER_PCT)

        if grid is not None:
            bars_held = i - grid.start_bar

            try_fill_levels(grid, lo, hi)

            # Liquidation check
            liq_price = lo if grid.side == 'long' else hi
            equity    = account + unrealized(grid, liq_price)
            maint     = grid.total_notional * MAINT_MARGIN_RATE

            if equity <= maint:
                close_grid(grid, liq_price, i, "LIQUIDATED", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None
                last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

            # TP check
            if check_tp(grid, lo, hi):
                close_grid(grid, grid.tp_price, i, "TP_HIT", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None
                last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

            # Opposite trigger: force-close, then open new grid this bar
            opposite = (grid.side == 'long' and short_signal) or \
                       (grid.side == 'short' and long_signal)
            if opposite:
                close_grid(grid, cl, i, "FORCE_CLOSE", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None
                last_exit_bar = i - 1   # allow immediate re-entry
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                # fall through to open new grid

            elif bars_held >= MAX_HOLD_BARS:
                close_grid(grid, cl, i, "TIMEOUT", bars_held)
                account += grid.pnl
                cycles.append(grid)
                grid = None
                last_exit_bar = i
                if account > peak_account: peak_account = account
                dd = (peak_account - account) / peak_account * 100
                if dd > max_drawdown: max_drawdown = dd
                continue

        # Open new grid
        if grid is None and i - last_exit_bar >= COOLDOWN_BARS:
            if long_signal:
                grid = make_grid('long', i, cl)
            elif short_signal:
                grid = make_grid('short', i, cl)

        if account > peak_account: peak_account = account
        dd = (peak_account - account) / peak_account * 100
        if dd > max_drawdown: max_drawdown = dd

    # Close open grid at end
    if grid is not None:
        bars_held = n - 1 - grid.start_bar
        close_grid(grid, cl_arr[-1], n - 1, "END_OF_DATA", bars_held)
        account += grid.pnl
        cycles.append(grid)

    liq_count = sum(1 for c in cycles if c.exit_reason == 'LIQUIDATED')
    return cycles, account, max_drawdown, liq_count

# ─── Metrics Computation ──────────────────────────────────────────────────
def compute_metrics(cycles: List[Grid], df: pd.DataFrame, final_account: float,
                    max_dd: float, liq_count: int) -> dict:
    if not cycles:
        return dict(
            total_return_pct=0, monthly_profit=0, max_drawdown=max_dd,
            sharpe_proxy=0, liquidations=liq_count, total_cycles=0, win_rate=0
        )

    t0     = df['time'].iloc[0]
    t1     = df['time'].iloc[-1]
    months = (t1 - t0).days / 30.0

    total_pnl    = sum(c.pnl for c in cycles)
    total_return = total_pnl / ACCOUNT_USD * 100
    monthly_pnl  = total_pnl / months if months > 0 else 0

    tp_cycles  = [c for c in cycles if c.exit_reason == 'TP_HIT']
    win_rate   = len(tp_cycles) / len(cycles) * 100 if cycles else 0

    # Build monthly PnL series from cycle exit bars
    monthly_pnl_series = {}
    for c in cycles:
        bar_ts = df['time'].iloc[min(c.exit_bar, len(df)-1)]
        key = (bar_ts.year, bar_ts.month)
        monthly_pnl_series[key] = monthly_pnl_series.get(key, 0) + c.pnl

    if len(monthly_pnl_series) >= 2:
        vals = list(monthly_pnl_series.values())
        std  = float(np.std(vals, ddof=1))
        sharpe = monthly_pnl / std if std > 1e-9 else 0.0
    else:
        sharpe = 0.0

    return dict(
        total_return_pct  = round(total_return, 2),
        monthly_profit    = round(monthly_pnl, 4),
        max_drawdown      = round(max_dd, 2),
        sharpe_proxy      = round(sharpe, 4),
        liquidations      = liq_count,
        total_cycles      = len(cycles),
        win_rate          = round(win_rate, 2),
    )

# ─── Main Grid Search ─────────────────────────────────────────────────────
def run_grid_search():
    print("Loading candle data...")
    raw_df = load_candles()
    t0 = raw_df['time'].min()
    t1 = raw_df['time'].max()
    months = (t1 - t0).days / 30.0
    print(f"Data: {t0.date()} → {t1.date()} ({months:.1f} months, {len(raw_df)} bars)")

    combos = list(itertools.product(EMA_SPANS, MA_PERIODS, MA_TYPES))
    total  = len(combos)
    print(f"\nSweeping {total} combinations ({len(EMA_SPANS)} EMA × {len(MA_PERIODS)} MA × {len(MA_TYPES)} types)...")
    print(f"Long trigger: ≥{LONG_TRIGGER_PCT}% below both MAs (20x)")
    print(f"Short trigger: ≥{SHORT_TRIGGER_PCT}% above both MAs (15x)")
    print()

    results = []
    t_start = time.time()

    for idx, (ema_span, ma_period, ma_type) in enumerate(combos):
        # Prepare indicators
        df = add_indicators(raw_df, ema_span, ma_period, ma_type)

        # Run backtest
        cycles, final_account, max_dd, liq_count = run_backtest(df)

        # Compute metrics
        m = compute_metrics(cycles, df, final_account, max_dd, liq_count)

        row = dict(
            ema_span   = ema_span,
            ma_period  = ma_period,
            ma_type    = ma_type,
            combo_label= f"EMA{ema_span}+{ma_type}{ma_period}",
            **m,
        )
        results.append(row)

        # Progress every 10
        if (idx + 1) % 10 == 0 or (idx + 1) == total:
            elapsed = time.time() - t_start
            eta     = elapsed / (idx + 1) * (total - idx - 1)
            print(f"  [{idx+1:3d}/{total}] EMA{ema_span}+{ma_type}{ma_period:2d} → "
                  f"ret={m['total_return_pct']:+6.1f}%  sharpe={m['sharpe_proxy']:5.2f}  "
                  f"liq={m['liquidations']}  ETA {eta:.0f}s")

    elapsed = time.time() - t_start
    print(f"\nCompleted {total} backtests in {elapsed:.1f}s ({elapsed/total:.2f}s each)")

    # Save CSV
    res_df = pd.DataFrame(results)
    res_df.to_csv(OUT_CSV, index=False)
    print(f"Results saved to: {OUT_CSV}")

    return res_df, months

# ─── Report Printing ──────────────────────────────────────────────────────
def print_report(res_df: pd.DataFrame, months: float):
    W = 72

    def separator(char='─'):
        print(char * W)

    def header(title):
        separator('═')
        print(f"  {title}")
        separator('═')

    def table_row(rank, row, highlight=False):
        marker = ' ★' if highlight else '  '
        print(f"{marker}{rank:2d}. {row['combo_label']:<20s}"
              f"  ret={row['total_return_pct']:+7.1f}%"
              f"  sharpe={row['sharpe_proxy']:5.2f}"
              f"  ${row['monthly_profit']:+6.2f}/mo"
              f"  dd={row['max_drawdown']:4.1f}%"
              f"  cyc={int(row['total_cycles']):3d}"
              f"  wr={row['win_rate']:4.1f}%"
              f"  liq={int(row['liquidations'])}")

    # ── Zero-liq subset ─────────────────────────────────────────────
    zero_liq = res_df[res_df['liquidations'] == 0].copy()
    print(f"\n  Total combos: {len(res_df)}  |  Zero-liquidation combos: {len(zero_liq)}")
    print()

    # ── Baseline ────────────────────────────────────────────────────
    bl = res_df[(res_df['ema_span'] == 34) & (res_df['ma_period'] == 21) & (res_df['ma_type'] == 'SMA')]
    if len(bl) == 1:
        brow = bl.iloc[0]
    else:
        brow = None

    # ── Top 15 by Sharpe (zero-liq) ─────────────────────────────────
    header("TOP 15 COMBOS BY SHARPE PROXY  (zero-liquidation only)")
    print(f"  {'Rank':<4} {'Combo':<20} {'Return':>8} {'Sharpe':>7} {'$/mo':>7}"
          f" {'MaxDD':>6} {'Cyc':>4} {'WinR':>5} {'Liq':>4}")
    separator()

    top_sharpe = zero_liq.sort_values('sharpe_proxy', ascending=False).head(15)
    for rank, (_, row) in enumerate(top_sharpe.iterrows(), 1):
        is_bl = (brow is not None and
                 row['ema_span'] == 34 and row['ma_period'] == 21 and row['ma_type'] == 'SMA')
        table_row(rank, row, highlight=is_bl)

    # ── Top 15 by Return (zero-liq) ──────────────────────────────────
    print()
    header("TOP 15 COMBOS BY TOTAL RETURN  (zero-liquidation only)")
    print(f"  {'Rank':<4} {'Combo':<20} {'Return':>8} {'Sharpe':>7} {'$/mo':>7}"
          f" {'MaxDD':>6} {'Cyc':>4} {'WinR':>5} {'Liq':>4}")
    separator()

    top_return = zero_liq.sort_values('total_return_pct', ascending=False).head(15)
    for rank, (_, row) in enumerate(top_return.iterrows(), 1):
        is_bl = (brow is not None and
                 row['ema_span'] == 34 and row['ma_period'] == 21 and row['ma_type'] == 'SMA')
        table_row(rank, row, highlight=is_bl)

    # ── Baseline comparison ──────────────────────────────────────────
    print()
    header("BASELINE: EMA34 + SMA21")
    separator()
    if brow is not None:
        table_row(0, brow)
        # Rank among zero-liq
        if brow['liquidations'] == 0:
            sr_rank = (zero_liq['sharpe_proxy'] > brow['sharpe_proxy']).sum() + 1
            rt_rank = (zero_liq['total_return_pct'] > brow['total_return_pct']).sum() + 1
            print(f"  Baseline ranks: #{sr_rank} by Sharpe, #{rt_rank} by Return  (among zero-liq combos)")
        else:
            print(f"  ⚠️  Baseline had {int(brow['liquidations'])} liquidation(s) — excluded from zero-liq tables")
    else:
        print("  [Baseline EMA34+SMA21 not found in results]")

    # ── Heatmap: best MA period per EMA span ─────────────────────────
    print()
    header("HEATMAP: BEST MA PERIOD PER EMA SPAN  (by Sharpe, zero-liq)")
    separator()
    print(f"  {'EMA Span':<10} {'Best Combo':<22} {'Sharpe':>7} {'Return':>8} {'$/mo':>7} {'Liq':>4}")
    separator()

    for ema in sorted(EMA_SPANS):
        subset = zero_liq[zero_liq['ema_span'] == ema]
        if subset.empty:
            # Fall back to all combos for this span
            subset = res_df[res_df['ema_span'] == ema]
        if subset.empty:
            print(f"  EMA{ema:<7}  [no data]")
            continue
        best = subset.sort_values('sharpe_proxy', ascending=False).iloc[0]
        print(f"  EMA{ema:<7}  {best['combo_label']:<22}  "
              f"{best['sharpe_proxy']:5.2f}  "
              f"{best['total_return_pct']:+7.1f}%  "
              f"${best['monthly_profit']:+6.2f}/mo  "
              f"liq={int(best['liquidations'])}")

    # ── Best vs Baseline delta ────────────────────────────────────────
    print()
    header("BEST COMBO vs BASELINE DELTA")
    separator()

    if len(zero_liq) > 0:
        best_sharpe = zero_liq.sort_values('sharpe_proxy', ascending=False).iloc[0]
        best_return = zero_liq.sort_values('total_return_pct', ascending=False).iloc[0]

        print(f"  Best Sharpe: {best_sharpe['combo_label']}")
        print(f"    Sharpe:  {best_sharpe['sharpe_proxy']:.2f}", end="")
        if brow is not None:
            print(f"  (baseline: {brow['sharpe_proxy']:.2f},  Δ{best_sharpe['sharpe_proxy']-brow['sharpe_proxy']:+.2f})", end="")
        print()
        print(f"    Return:  {best_sharpe['total_return_pct']:+.1f}%", end="")
        if brow is not None:
            print(f"  (baseline: {brow['total_return_pct']:+.1f}%,  Δ{best_sharpe['total_return_pct']-brow['total_return_pct']:+.1f}%)", end="")
        print()
        print(f"    $/mo:    ${best_sharpe['monthly_profit']:+.2f}", end="")
        if brow is not None:
            print(f"  (baseline: ${brow['monthly_profit']:+.2f},  Δ${best_sharpe['monthly_profit']-brow['monthly_profit']:+.2f})", end="")
        print()
        print(f"    Max DD:  {best_sharpe['max_drawdown']:.1f}%  Cycles: {int(best_sharpe['total_cycles'])}")

        print()
        print(f"  Best Return: {best_return['combo_label']}")
        print(f"    Return:  {best_return['total_return_pct']:+.1f}%", end="")
        if brow is not None:
            print(f"  (baseline: {brow['total_return_pct']:+.1f}%,  Δ{best_return['total_return_pct']-brow['total_return_pct']:+.1f}%)", end="")
        print()
        print(f"    Sharpe:  {best_return['sharpe_proxy']:.2f}  $/mo: ${best_return['monthly_profit']:+.2f}  Max DD: {best_return['max_drawdown']:.1f}%")

    # ── Distribution stats ───────────────────────────────────────────
    print()
    header("DISTRIBUTION SUMMARY  (all 162 combos)")
    separator()
    print(f"  Combos with 0 liquidations:  {(res_df['liquidations']==0).sum()}")
    print(f"  Combos with 1+ liquidations: {(res_df['liquidations']>0).sum()}")
    print(f"  Positive return combos:      {(res_df['total_return_pct']>0).sum()}")
    if len(zero_liq) > 0:
        print(f"  Zero-liq sharpe range:       {zero_liq['sharpe_proxy'].min():.2f} → {zero_liq['sharpe_proxy'].max():.2f}")
        print(f"  Zero-liq return range:       {zero_liq['total_return_pct'].min():+.1f}% → {zero_liq['total_return_pct'].max():+.1f}%")
    print()
    separator('═')
    print(f"  Full results saved to: {OUT_CSV}")
    separator('═')

# ─── Entry Point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 72)
    print("  MR MARTINGALE GRID BOT — MA PARAMETER OPTIMIZER")
    print(f"  Sweep: {len(EMA_SPANS)} EMA spans × {len(MA_PERIODS)} MA periods × {len(MA_TYPES)} types = {len(EMA_SPANS)*len(MA_PERIODS)*len(MA_TYPES)} combos")
    print("=" * 72)

    res_df, months = run_grid_search()
    print_report(res_df, months)
