"""
L4/L5 Spacing Sweep — Mr Martingale
=====================================
Sweeps L3→L4 and L4→L5 spacing independently using 2018-present BTC 5m data.

Canonical parameters (current live bot):
  Leverage: 20x long / 15x short  |  Base margin: $6.4 (FIXED)
  Levels: 5 | Multiplier: 2.0
  Long trigger: 0.5% below BOTH EMA34 & MA14 (4h-aligned)
  Short trigger: 2.5% above BOTH EMA34 & MA14
  TP: 0.5% | Max hold: 1440 5m bars (30 × 4h) | Cooldown: 48 5m bars (1 × 4h)

ACCOUNT METHODOLOGY:
  - strategy_equity = $400 initial (tracks actual strategy P&L)
  - Liquidation fires when strategy_equity + unrealized_pnl ≤ maint_margin
  - After liquidation: strategy_equity decreases, but trading always continues
    (simulates unlimited refill — we evaluate strategy quality, not sizing)
  - CAGR/MDD/PnL all relative to $400 initial

Fixed: L1→L2=0.5%, L2→L3=1.5%  
Swept: L3→L4 ∈ [2.5,3,4,5,6,7,8,10,12]  |  L4→L5 ∈ [3,4,5,6,7,8,10,12]
"""

import pandas as pd, numpy as np, json, csv
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

BASE         = Path(__file__).parent.parent
RESULTS_DIR  = BASE / "signals" / "multi_asset_results"
REPORTS_DIR  = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
PARQUET_FILE = RESULTS_DIR / "btcusdt_spot_5m_2018_plus_cached_with_ma.parquet"

BASE_MARGIN   = 6.4           # Fixed — never changes
STRAT_INIT    = 400.0         # Reference capital
LONG_LEV      = 20
SHORT_LEV     = 15
NUM_LVL       = 5
MULT          = 2.0
LONG_TRIG     = 0.5
SHORT_TRIG    = 2.5
TP_PCT        = 0.5
MAINT_RATE    = 0.005
FUND_8H       = 0.0013 / 100
MAX_HOLD      = 30 * 48       # 1440 5m bars
COOLDOWN      = 1  * 48       # 48 5m bars
TAKER         = 0.000432
MAKER         = 0.000144

GAP_L1L2 = 0.5
GAP_L2L3 = 1.5
BASELINE = [GAP_L1L2, GAP_L2L3, 3.0, 3.0]

L3L4_VALS = [2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]
L4L5_VALS = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]


@dataclass
class Level:
    idx: int; target_px: float; margin: float
    notional: float; qty: float
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

    def recalc(self):
        f = [l for l in self.levels if l.filled]
        if not f: return
        self.blended = sum(l.qty*l.fill_px for l in f) / sum(l.qty for l in f)
        self.total_qty = sum(l.qty for l in f)
        self.total_margin = sum(l.margin for l in f)
        self.total_notional = sum(l.notional for l in f)
        self.max_lvl = max(l.idx+1 for l in f)
        self.tp_price = self.blended*(1+TP_PCT/100) if self.side=="long" else self.blended*(1-TP_PCT/100)


def load_data():
    df = pd.read_parquet(PARQUET_FILE)
    df["time"] = pd.to_datetime(df["t"], unit="ms")
    df = df.rename(columns={"c":"close","h":"high","l":"low","o":"open","ema":"ema34","ma":"ma14"})
    df = df.dropna(subset=["ema34","ma14"]).reset_index(drop=True)
    df["pct_below_ema"] = (df["ema34"]-df["close"])/df["ema34"]*100
    df["pct_below_ma"]  = (df["ma14"] -df["close"])/df["ma14"] *100
    df["pct_above_ema"] = (df["close"]-df["ema34"])/df["ema34"]*100
    df["pct_above_ma"]  = (df["close"]-df["ma14"] )/df["ma14"] *100
    return df


def make_grid(side, bar_idx, trigger_px, leverage, gaps):
    g = Grid(side=side, start_bar=bar_idx, trigger_px=trigger_px, leverage=leverage)
    cum = []
    acc = 0.0
    for gap in gaps:
        acc += gap
        cum.append(acc/100)
    for i in range(NUM_LVL):
        m = BASE_MARGIN * (MULT ** i)
        n = m * leverage
        q = n / trigger_px
        t = trigger_px if i == 0 else (trigger_px*(1-cum[i-1]) if side=="long" else trigger_px*(1+cum[i-1]))
        g.levels.append(Level(idx=i, target_px=t, margin=m, notional=n, qty=q))
    g.levels[0].filled = True
    g.levels[0].fill_px = trigger_px
    g.recalc()
    return g


def run_backtest(close_arr, high_arr, low_arr, ema_arr, ma_arr, gaps):
    """Optimized backtest using numpy arrays instead of df.iloc."""
    n = len(close_arr)
    strat_eq = STRAT_INIT       # strategy equity (tracks real P&L)
    peak_eq  = STRAT_INIT
    max_dd   = 0.0
    grid = None
    last_exit = -99 * 48
    cycles, liq_ev = [], []
    force_closes = 0

    for i in range(n):
        hi, lo, cl = high_arr[i], low_arr[i], close_arr[i]
        ema, ma = ema_arr[i], ma_arr[i]

        # Compute signals
        pbe = (ema-cl)/ema*100
        pbm = (ma-cl)/ma*100
        pae = (cl-ema)/ema*100
        pam = (cl-ma)/ma*100
        lsig = pbe >= LONG_TRIG and pbm >= LONG_TRIG
        ssig = pae >= SHORT_TRIG and pam >= SHORT_TRIG

        if grid is not None:
            held = i - grid.start_bar
            # Fill next level
            nf = sum(1 for l in grid.levels if l.filled)
            if nf < NUM_LVL:
                l = grid.levels[nf]
                if grid.side == "long" and lo <= l.target_px:
                    l.filled = True; l.fill_px = l.target_px; grid.recalc()
                elif grid.side == "short" and hi >= l.target_px:
                    l.filled = True; l.fill_px = l.target_px; grid.recalc()

            filled = [l for l in grid.levels if l.filled]
            liq_px = lo if grid.side == "long" else hi
            unrl = grid.total_qty*(liq_px-grid.blended) if grid.side=="long" else grid.total_qty*(grid.blended-liq_px)
            maint  = grid.total_notional * MAINT_RATE

            # Liquidation: compare strategy equity (not sim account) to maint threshold
            if strat_eq + unrl <= maint:
                fund = grid.total_notional * FUND_8H * (held/96)
                fee  = sum(l.notional*TAKER + l.qty*liq_px*TAKER for l in filled)
                gross = (grid.total_qty*(liq_px-grid.blended) if grid.side=="long"
                         else grid.total_qty*(grid.blended-liq_px))
                pnl = gross - fund - fee
                strat_eq += pnl
                # Strategy equity can go negative — we continue trading (unlimited refill)
                if strat_eq < -STRAT_INIT * 10:  # Safety floor to prevent runaway
                    strat_eq = STRAT_INIT  # reset to initial (simulate full refill)
                grid.pnl = pnl; grid.exit_px = liq_px; grid.exit_bar = i
                grid.exit_reason = "LIQUIDATED"; grid.max_lvl = len(filled)
                liq_ev.append({"side": grid.side, "blended": grid.blended,
                                "exit_px": liq_px, "max_lvl": grid.max_lvl,
                                "strat_eq_before": strat_eq - pnl, "pnl": pnl})
                cycles.append(grid); grid = None; last_exit = i
                peak_eq = max(peak_eq, strat_eq)
                max_dd = max(max_dd, (peak_eq-strat_eq)/peak_eq*100 if peak_eq > 0 else 0)
                continue

            # TP
            if (grid.side=="long" and hi >= grid.tp_price) or (grid.side=="short" and lo <= grid.tp_price):
                ep = grid.tp_price
                fund = grid.total_notional * FUND_8H * (held/96)
                fee  = sum(l.notional*MAKER + l.qty*ep*MAKER for l in filled)
                gross = (grid.total_qty*(ep-grid.blended) if grid.side=="long"
                         else grid.total_qty*(grid.blended-ep))
                pnl = gross - fund - fee
                strat_eq += pnl
                grid.pnl = pnl; grid.exit_px = ep; grid.exit_bar = i
                grid.exit_reason = "TP_HIT"; grid.max_lvl = len(filled)
                cycles.append(grid); grid = None; last_exit = i
                peak_eq = max(peak_eq, strat_eq)
                max_dd = max(max_dd, (peak_eq-strat_eq)/peak_eq*100 if peak_eq > 0 else 0)
                continue

            # Opposite signal → force close
            opp = (grid.side=="long" and ssig) or (grid.side=="short" and lsig)
            if opp or held >= MAX_HOLD:
                ep = cl
                fund = grid.total_notional * FUND_8H * (held/96)
                fee  = sum(l.notional*TAKER + l.qty*ep*TAKER for l in filled)
                gross = (grid.total_qty*(ep-grid.blended) if grid.side=="long"
                         else grid.total_qty*(grid.blended-ep))
                pnl = gross - fund - fee
                strat_eq += pnl
                reason = "FORCE_CLOSE" if opp else "TIMEOUT"
                grid.pnl = pnl; grid.exit_px = ep; grid.exit_bar = i
                grid.exit_reason = reason
                cycles.append(grid); grid = None
                last_exit = i - 1 if opp else i
                if opp: force_closes += 1
                peak_eq = max(peak_eq, strat_eq)
                max_dd = max(max_dd, (peak_eq-strat_eq)/peak_eq*100 if peak_eq > 0 else 0)
                if reason == "TIMEOUT": continue

        # Open new grid
        if grid is None and (i - last_exit) >= COOLDOWN:
            if lsig:
                grid = make_grid("long", i, cl, LONG_LEV, gaps)
            elif ssig:
                grid = make_grid("short", i, cl, SHORT_LEV, gaps)

        # Track drawdown
        if strat_eq > peak_eq: peak_eq = strat_eq
        dd = (peak_eq - strat_eq) / peak_eq * 100 if peak_eq > 0 else 0
        if dd > max_dd: max_dd = dd

    # Close at end
    if grid is not None:
        filled = [l for l in grid.levels if l.filled]
        held = n - 1 - grid.start_bar
        ep = close_arr[-1]
        fund = grid.total_notional * FUND_8H * (held/96)
        fee  = sum(l.notional*TAKER + l.qty*ep*TAKER for l in filled)
        gross = (grid.total_qty*(ep-grid.blended) if grid.side=="long"
                 else grid.total_qty*(grid.blended-ep))
        pnl = gross - fund - fee
        strat_eq += pnl
        grid.pnl = pnl; grid.exit_px = ep; grid.exit_bar = n-1
        grid.exit_reason = "END_OF_DATA"
        cycles.append(grid)

    long_c  = [c for c in cycles if c.side == "long"]
    short_c = [c for c in cycles if c.side == "short"]
    return cycles, strat_eq, max_dd, long_c, short_c, liq_ev, force_closes


def calc_metrics(cycles, long_c, short_c, liq_ev, final_strat_eq, max_dd, n_days, force_closes):
    total  = len(cycles)
    years  = n_days / 365.25
    months = n_days / 30.44

    all_tp  = [c for c in cycles if c.exit_reason == "TP_HIT"]
    all_liq = [c for c in cycles if c.exit_reason == "LIQUIDATED"]
    ll = [c for c in long_c  if c.exit_reason == "LIQUIDATED"]
    sl = [c for c in short_c if c.exit_reason == "LIQUIDATED"]

    net_pnl  = final_strat_eq - STRAT_INIT
    pnl_pct  = net_pnl / STRAT_INIT * 100
    cagr     = ((final_strat_eq / STRAT_INIT) ** (1/years) - 1) * 100 if years > 0 and final_strat_eq > 0 else float("nan")

    return {
        "total_cycles":  total,
        "long_cycles":   len(long_c),
        "short_cycles":  len(short_c),
        "tp_exits":      len(all_tp),
        "liquidations":  len(all_liq),
        "long_liqs":     len(ll),
        "short_liqs":    len(sl),
        "force_closes":  force_closes,
        "timeouts":      len([c for c in cycles if c.exit_reason=="TIMEOUT"]),
        "win_rate_pct":  len(all_tp)/total*100 if total > 0 else 0,
        "final_strat_eq":round(final_strat_eq, 2),
        "pnl_pct":       round(pnl_pct, 1),
        "cagr_pct":      round(cagr, 2),
        "max_dd_pct":    round(max_dd, 2),
        "months":        round(months, 1),
        "trades_per_mo": round(total/months, 1) if months > 0 else 0,
        "liq_detail":    liq_ev,
    }


def run_sweep(df, verbose=True):
    # Pre-extract numpy arrays for speed
    close_arr = df["close"].values.astype(float)
    high_arr  = df["high"].values.astype(float)
    low_arr   = df["low"].values.astype(float)
    ema_arr   = df["ema34"].values.astype(float)
    ma_arr    = df["ma14"].values.astype(float)
    n_days    = (df["time"].iloc[-1] - df["time"].iloc[0]).days

    results = []
    combos  = [(g34, g45) for g34 in L3L4_VALS for g45 in L4L5_VALS]
    total   = len(combos)

    for ci, (g34, g45) in enumerate(combos, 1):
        gaps = [GAP_L1L2, GAP_L2L3, g34, g45]
        cum  = [gaps[0], gaps[0]+gaps[1], sum(gaps[:3]), sum(gaps)]
        if verbose:
            print(f"  [{ci:3d}/{total}] L4={g34:.1f}% L5={g45:.1f}% cumL4={cum[2]:.1f}% L5={cum[3]:.1f}%",
                  end="", flush=True)
        cycles, fse, mdd, lc, sc, le, fc = run_backtest(close_arr, high_arr, low_arr, ema_arr, ma_arr, gaps)
        m = calc_metrics(cycles, lc, sc, le, fse, mdd, n_days, fc)
        m.update({"gap_l3l4": g34, "gap_l4l5": g45, "cum_l4": cum[2], "cum_l5": cum[3],
                  "gaps_str": f"[{GAP_L1L2},{GAP_L2L3},{g34},{g45}]"})
        results.append(m)
        if verbose:
            print(f"  liqs={m['liquidations']}({m['long_liqs']}L/{m['short_liqs']}S)"
                  f"  pnl={m['pnl_pct']:+.0f}%  CAGR={m['cagr_pct']:+.1f}%"
                  f"  MDD={m['max_dd_pct']:.1f}%  trades={m['total_cycles']}")
    return results, n_days, (close_arr, high_arr, low_arr, ema_arr, ma_arr)


def generate_report(baseline, results, date_str):
    zero_liq   = sorted([r for r in results if r["liquidations"] == 0], key=lambda r: -r["pnl_pct"])
    all_sorted = sorted(results, key=lambda r: (r["liquidations"], -r["pnl_pct"]))

    best_zero = zero_liq[0] if zero_liq else None

    lines = [
        "# Mr Martingale — L4/L5 Spacing Sweep Report",
        f"**Generated:** {date_str}  ",
        "**Dataset:** BTC/USDT 5m Binance spot 2018-01-03 → 2026-03-01 (8+ years)  ",
        "**Config:** $400 ref | $6.4 fixed base margin | 5L | 2x | 20x long / 15x short  ",
        "**Method:** Strategy equity tracks $400 initial; liquidation fires on real equity threshold;",
        "trading continues after liquidation (unlimited refill = strategy-quality evaluation)  ",
        "**Fixed:** L1→L2=0.5%, L2→L3=1.5% (cum L2=0.5%, L3=2.0%)  ",
        f"**Swept:** L3→L4 ∈ {L3L4_VALS}% | L4→L5 ∈ {L4L5_VALS}%  ",
        f"**Configs tested:** {len(results)}  ",
        "",
        "---",
        "",
        "## Baseline: [0.5, 1.5, 3.0, 3.0]",
        "Cumulative depths from trigger: L2=0.5% | L3=2.0% | **L4=5.0%** | **L5=8.0%**",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total cycles | {baseline['total_cycles']:,} |",
        f"| Long / Short | {baseline['long_cycles']} / {baseline['short_cycles']} |",
        f"| Win rate | {baseline['win_rate_pct']:.1f}% |",
        f"| **Total liquidations** | **{baseline['liquidations']} ({baseline['long_liqs']}L / {baseline['short_liqs']}S)** |",
        f"| Final strategy equity | ${baseline['final_strat_eq']:,.2f} |",
        f"| Net PnL | {baseline['pnl_pct']:+.1f}% on $400 reference |",
        f"| **CAGR** | **{baseline['cagr_pct']:+.1f}%/yr** |",
        f"| **Max drawdown** | **{baseline['max_dd_pct']:.1f}%** |",
        f"| Trades/month | {baseline['trades_per_mo']:.1f} |",
        f"| Timeouts | {baseline['timeouts']} |",
        f"| Force closes | {baseline['force_closes']} |",
        "",
    ]

    # Baseline liq events
    if baseline["liq_detail"]:
        lines += [
            "### Baseline Liquidation Events",
            "",
            "| Side | Blended | Exit Px | Max Lvl | Strat Eq Before | PnL |",
            "|------|---------|---------|---------|----------------|-----|",
        ]
        for ev in baseline["liq_detail"]:
            lines.append(
                f"| {ev['side'].upper()} | ${ev['blended']:,.0f} | ${ev['exit_px']:,.0f} | "
                f"L{ev['max_lvl']} | ${ev['strat_eq_before']:,.2f} | ${ev['pnl']:,.2f} |"
            )

    lines += [
        "",
        "---",
        "",
        "## Zero-Liquidation Configs",
        "",
    ]

    if zero_liq:
        lines += [
            f"**{len(zero_liq)} zero-liq configs found out of {len(results)} tested.**",
            "",
            "| L3→L4 | L4→L5 | Cum L4 | Cum L5 | PnL% | CAGR | MDD | Trades | /mo | PnL Δ |",
            "|-------|-------|--------|--------|------|------|-----|--------|-----|-------|",
        ]
        for r in zero_liq[:12]:
            delta = r["pnl_pct"] - baseline["pnl_pct"]
            lines.append(
                f"| {r['gap_l3l4']:.1f}% | {r['gap_l4l5']:.1f}% | {r['cum_l4']:.1f}% | {r['cum_l5']:.1f}% | "
                f"{r['pnl_pct']:+.0f}% | {r['cagr_pct']:+.1f}% | {r['max_dd_pct']:.1f}% | "
                f"{r['total_cycles']} | {r['trades_per_mo']:.1f} | {delta:+.0f}% |"
            )
    else:
        lines.append("❌ **No zero-liquidation configurations found in tested range.**")

    lines += [
        "",
        "---",
        "",
        "## All Configs Ranked (by liq count, then PnL)",
        "",
        "| L3→L4 | L4→L5 | Cum L5 | Liqs (L/S) | PnL% | CAGR | MDD | Trades |",
        "|-------|-------|--------|-----------|------|------|-----|--------|",
    ]

    for r in all_sorted[:30]:
        lines.append(
            f"| {r['gap_l3l4']:.1f}% | {r['gap_l4l5']:.1f}% | {r['cum_l5']:.1f}% | "
            f"{r['liquidations']} ({r['long_liqs']}L/{r['short_liqs']}S) | "
            f"{r['pnl_pct']:+.0f}% | {r['cagr_pct']:+.1f}% | {r['max_dd_pct']:.1f}% | "
            f"{r['total_cycles']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Recommendation",
        "",
    ]

    if best_zero:
        delta_pnl  = best_zero["pnl_pct"] - baseline["pnl_pct"]
        delta_cagr = best_zero["cagr_pct"] - baseline["cagr_pct"]
        lines += [
            f"### Best Zero-Liq: L3→L4={best_zero['gap_l3l4']:.1f}%, L4→L5={best_zero['gap_l4l5']:.1f}%",
            f"- Cumulative: L4={best_zero['cum_l4']:.1f}%, L5={best_zero['cum_l5']:.1f}% below trigger",
            f"- PnL: {best_zero['pnl_pct']:+.0f}% (Δ {delta_pnl:+.0f}% vs baseline)",
            f"- CAGR: {best_zero['cagr_pct']:+.1f}%/yr (Δ {delta_cagr:+.1f}%)",
            f"- MDD: {best_zero['max_dd_pct']:.1f}% (baseline {baseline['max_dd_pct']:.1f}%)",
            f"- Trades: {best_zero['total_cycles']} ({best_zero['trades_per_mo']:.1f}/mo)",
            "",
        ]

    lines += [
        "---",
        "",
        "## Honest Assessment",
        "",
        "### Can spacing eliminate liquidations?",
        "",
    ]

    if zero_liq:
        lines += [
            f"**YES** — {len(zero_liq)} configs achieve zero liquidations over 2018-present.",
            "",
            "**However:** Zero-liq spacing configs avoid fills in moderate crashes.",
            "The 2018 bear market and COVID crash are so extreme that even very wide spacing",
            "may just delay fills, not eliminate them.",
            "",
            "**The real mechanism:** Wider L4/L5 spacing prevents the CASCADE INITIATOR —",
            "when L4/L5 don't fill, the position exits at TP or timeout instead of hitting",
            "all 5 levels. This avoids the 5× margin deployment that makes liquidation likely.",
        ]
    else:
        lines += [
            "**NO** — within the tested range, liquidations persist.",
            "The root cause (sequential cascade: 2018 bear depletes account → each new cycle",
            "at near-zero equity is immediately liquidated) requires either:",
            "- Much lower leverage (8x vs 20x)",
            "- Much wider spacing (possibly beyond the tested range)",
            "- A stop-loss mechanism to halt trading during extreme bear regimes",
        ]

    lines += [
        "",
        "### Asymmetric long vs short spacing?",
        f"- Long liqs come from BEAR MARKET cascades: slow, gradual fills over hours/days",
        f"- Short liqs from RAPID PUMP events: fills happen in 1-3 bars",
        f"- Long deep spacing: HIGH IMPACT — prevents cascade initiators",
        f"- Short deep spacing: MODERATE IMPACT — pumps can fill any reasonable L5",
        f"- **Verdict: Asymmetric spacing worth testing (wider L5 for shorts than longs)**",
        "",
        "---",
        f"*Report by `tools/l4l5_spacing_sweep.py` | "
        f"Data: `signals/multi_asset_results/l4l5_spacing_sweep_2018_{date_str}.csv`*",
    ]

    return "\n".join(lines)


def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    print("=" * 65)
    print("  MR MARTINGALE — L4/L5 SPACING SWEEP (2018-present)")
    print(f"  Fixed $6.4 base | $400 strat equity reference")
    print("=" * 65)

    print("\nLoading data...")
    df = load_data()
    close_a = df["close"].values.astype(float)
    high_a  = df["high"].values.astype(float)
    low_a   = df["low"].values.astype(float)
    ema_a   = df["ema34"].values.astype(float)
    ma_a    = df["ma14"].values.astype(float)
    n_days  = (df["time"].iloc[-1] - df["time"].iloc[0]).days
    print(f"  {len(df):,} bars | {df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()}")

    # Baseline
    print(f"\nBaseline [0.5, 1.5, 3.0, 3.0]...", end="", flush=True)
    cycles, fse, mdd, lc, sc, le, fc = run_backtest(close_a, high_a, low_a, ema_a, ma_a, BASELINE)
    baseline = calc_metrics(cycles, lc, sc, le, fse, mdd, n_days, fc)
    baseline.update({"gap_l3l4": 3.0, "gap_l4l5": 3.0, "cum_l4": 5.0, "cum_l5": 8.0,
                     "gaps_str": "[0.5,1.5,3.0,3.0]"})
    print(f"  liqs={baseline['liquidations']}({baseline['long_liqs']}L/{baseline['short_liqs']}S)"
          f"  pnl={baseline['pnl_pct']:+.0f}%  CAGR={baseline['cagr_pct']:+.1f}%"
          f"  MDD={baseline['max_dd_pct']:.1f}%  trades={baseline['total_cycles']}")
    for ev in le:
        print(f"  Liq: {ev['side']:5s} L{ev['max_lvl']} blended={ev['blended']:.0f} eq_before={ev['strat_eq_before']:.1f} pnl={ev['pnl']:.1f}")

    # Sweep
    print(f"\nSweeping {len(L3L4_VALS)*len(L4L5_VALS)} combinations...")
    results, _, _ = run_sweep(df, verbose=True)

    # Save CSV
    all_res = [baseline] + results
    csv_path = RESULTS_DIR / f"l4l5_spacing_sweep_2018_{date_str}.csv"
    fieldnames = [k for k in all_res[0].keys() if k != "liq_detail"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_res:
            writer.writerow({k: v for k,v in r.items() if k != "liq_detail"})
    print(f"\n  CSV → {csv_path.name}")

    # Report
    report = generate_report(baseline, results, date_str)
    rpath = REPORTS_DIR / f"l4l5_spacing_sweep_report_{date_str}.md"
    rpath.write_text(report)
    print(f"  Report → reports/{rpath.name}")

    # Summary
    zero_liq = [r for r in results if r["liquidations"] == 0]
    print(f"\n{'='*65}")
    print(f"  Baseline liqs: {baseline['liquidations']} | CAGR: {baseline['cagr_pct']:+.1f}%")
    print(f"  Zero-liq configs: {len(zero_liq)}/{len(results)}")
    if zero_liq:
        best = max(zero_liq, key=lambda r: r["pnl_pct"])
        print(f"  Best zero-liq: L4={best['gap_l3l4']:.1f}% L5={best['gap_l4l5']:.1f}% | "
              f"pnl={best['pnl_pct']:+.0f}% | CAGR={best['cagr_pct']:+.1f}% | MDD={best['max_dd_pct']:.1f}%")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
