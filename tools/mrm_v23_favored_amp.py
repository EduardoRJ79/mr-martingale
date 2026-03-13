#!/usr/bin/env python3
"""
Mr Martingale v2.3 — Favored-Side Amplification Research
==========================================================
From the corrected v2.1 exact-liq champion baseline, test whether
the favored side should be run hotter than the baseline neutral 1.0×.

Favored-side amplification parameters tested:
  - fav_risk_scale: > 1.0 (bigger positions on favored side)
  - fav_trigger_scale: < 1.0 (easier entry on favored side)
  - fav_hold_scale: > 1.0 (hold longer on favored side)
  - fav_spacing_scale: < 1.0 (tighter spacing = more aggressive fills)
  - fav_mult_scale: > 1.0 (heavier deep fills on favored side)

All with exact liquidation simulation. Zero liquidations = hard requirement.
"""

import json
import math
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from itertools import product

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ─── Paths ────────────────────────────────────────────────────────────────
MRM_BASE = Path("/Users/assistant/Library/CloudStorage/OneDrive-VesselArchitecture&DesignInc/"
                "Documents/VW Family Office/portfolio-high-risk/strategies/Mr Martingale")
PARQUET = MRM_BASE / "signals" / "multi_asset_results" / "btcusdt_spot_5m_2018_plus_cached_with_ma.parquet"
REPORTS_DIR = MRM_BASE / "reports"
WS = Path("/Users/assistant/.openclaw/ws-731228")

# ─── v2.1 Champion Constants (DO NOT MODIFY — this is the baseline) ──────
INITIAL_EQUITY = 1000.0
NUM_LEVELS = 5
CHAMPION_LEVEL_GAPS = [0.5, 1.5, 9.0, 6.0]
CHAMPION_LEVEL_MULTIPLIERS = [2.0, 2.5, 2.5, 7.0]
CHAMPION_RISK_PCT = 0.25
CHAMPION_DMA_PERIOD = 440
CHAMPION_MAX_HOLD_BARS = 160
CHAMPION_LONG_TRIGGER_PCT = 0.5
CHAMPION_SHORT_TRIGGER_PCT = 1.5
CHAMPION_UNFAV_RISK_SCALE = 0.65
CHAMPION_UNFAV_SPACING_SCALE = 1.5
CHAMPION_UNFAV_TRIGGER_SCALE = 1.5
CHAMPION_UNFAV_HOLD_SCALE = 0.5

LONG_LEV = 20
SHORT_LEV = 15
TP_PCT = 0.5
TAKER_FEE = 0.000432
MAKER_FEE = 0.000144
FUND_8H_RATE = 0.0013 / 100
MAINT_RATE = 0.005
COOLDOWN_BARS = 1
MIN_EQUITY = 50.0


# ─── Data loading ─────────────────────────────────────────────────────────

def load_and_prepare(dma_period=440):
    print("Loading data...")
    df = pd.read_parquet(PARQUET)
    df['ts_dt'] = pd.to_datetime(df['ts'], utc=True)
    df.set_index('ts_dt', inplace=True)

    bars_4h = df.resample('4h').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna().copy()
    bars_4h['ema34'] = bars_4h['c'].ewm(span=34, adjust=False).mean()
    bars_4h['sma14'] = bars_4h['c'].rolling(14).mean()

    daily = df.resample('1D').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna().copy()
    daily[f'sma{dma_period}'] = daily['c'].rolling(dma_period).mean()

    return bars_4h, daily, dma_period


def classify_regime(row, sma_col):
    val = row.get(sma_col, np.nan)
    if pd.isna(val):
        return "unknown"
    return "bull" if row['c'] > val else "bear"


def broadcast_regime(bars_4h, daily, sma_col):
    regimes = pd.Series([classify_regime(row, sma_col) for _, row in daily.iterrows()], index=daily.index)
    shifted = regimes.shift(1)
    result = shifted.reindex(bars_4h.index, method='ffill')
    result = result.fillna(method='bfill')
    return result


# ─── Side behavior ────────────────────────────────────────────────────────

@dataclass
class SideBehavior:
    allowed: bool = True
    risk_scale: float = 1.0
    spacing_scale: float = 1.0
    trigger_scale: float = 1.0
    hold_scale: float = 1.0
    mult_scale: float = 1.0   # NEW: per-level multiplier amplification


@dataclass
class V23Config:
    """Full config for a v2.3 test candidate."""
    name: str
    risk_pct: float = CHAMPION_RISK_PCT
    level_gaps: list = field(default_factory=lambda: list(CHAMPION_LEVEL_GAPS))
    level_multipliers: list = field(default_factory=lambda: list(CHAMPION_LEVEL_MULTIPLIERS))
    dma_period: int = CHAMPION_DMA_PERIOD
    max_hold_bars: int = CHAMPION_MAX_HOLD_BARS
    long_trigger_pct: float = CHAMPION_LONG_TRIGGER_PCT
    short_trigger_pct: float = CHAMPION_SHORT_TRIGGER_PCT
    # unfavored side (keep v2.1 champion values)
    unfav_risk_scale: float = CHAMPION_UNFAV_RISK_SCALE
    unfav_spacing_scale: float = CHAMPION_UNFAV_SPACING_SCALE
    unfav_trigger_scale: float = CHAMPION_UNFAV_TRIGGER_SCALE
    unfav_hold_scale: float = CHAMPION_UNFAV_HOLD_SCALE
    # favored side amplification (NEW — v2.3 research knobs)
    fav_risk_scale: float = 1.0       # > 1.0 = bigger favored positions
    fav_trigger_scale: float = 1.0    # < 1.0 = easier entry on favored side
    fav_hold_scale: float = 1.0       # > 1.0 = hold longer on favored side
    fav_spacing_scale: float = 1.0    # < 1.0 = tighter spacing on favored side
    fav_mult_scale: float = 1.0       # > 1.0 = heavier deep fills on favored side

    def favored_behavior(self):
        return SideBehavior(
            allowed=True,
            risk_scale=self.fav_risk_scale,
            spacing_scale=self.fav_spacing_scale,
            trigger_scale=self.fav_trigger_scale,
            hold_scale=self.fav_hold_scale,
            mult_scale=self.fav_mult_scale,
        )

    def unfavored_behavior(self):
        return SideBehavior(
            allowed=True,
            risk_scale=self.unfav_risk_scale,
            spacing_scale=self.unfav_spacing_scale,
            trigger_scale=self.unfav_trigger_scale,
            hold_scale=self.unfav_hold_scale,
            mult_scale=1.0,
        )

    def get_behavior(self, regime, side):
        if regime == "bull":
            return self.favored_behavior() if side == "long" else self.unfavored_behavior()
        elif regime == "bear":
            return self.favored_behavior() if side == "short" else self.unfavored_behavior()
        return SideBehavior()

    def desc(self):
        parts = []
        if self.fav_risk_scale != 1.0:
            parts.append(f"fR={self.fav_risk_scale:.2f}")
        if self.fav_trigger_scale != 1.0:
            parts.append(f"fT={self.fav_trigger_scale:.2f}")
        if self.fav_hold_scale != 1.0:
            parts.append(f"fH={self.fav_hold_scale:.2f}")
        if self.fav_spacing_scale != 1.0:
            parts.append(f"fS={self.fav_spacing_scale:.2f}")
        if self.fav_mult_scale != 1.0:
            parts.append(f"fM={self.fav_mult_scale:.2f}")
        return ", ".join(parts) if parts else "baseline"


# ─── Grid and backtest engine ────────────────────────────────────────────

@dataclass
class Level:
    idx: int; target_px: float; notional: float; margin: float; qty: float
    filled: bool = False; fill_px: float = 0.0

@dataclass
class Grid:
    side: str; start_bar: int; trigger_px: float; leverage: int
    levels: List[Level] = field(default_factory=list)
    blended: float = 0.0; total_qty: float = 0.0; total_notional: float = 0.0
    tp_price: float = 0.0; max_hold: int = CHAMPION_MAX_HOLD_BARS

    def recalc(self):
        f = [l for l in self.levels if l.filled]
        if not f:
            return
        self.blended = sum(l.qty * l.fill_px for l in f) / sum(l.qty for l in f)
        self.total_qty = sum(l.qty for l in f)
        self.total_notional = sum(l.notional for l in f)
        self.tp_price = self.blended * (1 + TP_PCT / 100) if self.side == "long" else self.blended * (1 - TP_PCT / 100)


def make_cum_gaps(gaps):
    cum, acc = [], 0.0
    for g in gaps:
        acc += g
        cum.append(acc / 100.0)
    return cum


def build_grid(side, bar_idx, price, equity, risk_pct, gaps, multipliers):
    lev = LONG_LEV if side == "long" else SHORT_LEV
    cum = make_cum_gaps(gaps)
    g = Grid(side=side, start_bar=bar_idx, trigger_px=price, leverage=lev)

    l1_not = risk_pct * equity
    notional = l1_not
    for i in range(NUM_LEVELS):
        if i > 0:
            notional *= multipliers[i - 1]
        margin = notional / lev
        target = price if i == 0 else (price * (1 - cum[i - 1]) if side == "long" else price * (1 + cum[i - 1]))
        qty = notional / target
        lv = Level(idx=i, target_px=target, notional=notional, margin=margin, qty=qty)
        if i == 0:
            lv.filled = True
            lv.fill_px = price
        g.levels.append(lv)
    g.recalc()
    return g


def liq_price(grid, account):
    if grid.total_qty <= 0:
        return -1.0 if grid.side == "long" else 1e18
    maint = grid.total_notional * MAINT_RATE
    eff = max(maint + 0.01, account)
    delta = (eff - maint) / grid.total_qty
    return grid.blended - delta if grid.side == "long" else grid.blended + delta


def calc_pnl(grid, exit_px, bars_held):
    filled = [l for l in grid.levels if l.filled]
    if grid.side == "long":
        gross = sum(l.qty * (exit_px - l.fill_px) for l in filled)
    else:
        gross = sum(l.qty * (l.fill_px - exit_px) for l in filled)
    fees = 0.0
    for l in filled:
        fees += l.notional * (TAKER_FEE if l.idx == 0 else MAKER_FEE)
        fees += l.qty * exit_px * MAKER_FEE
    funding = grid.total_notional * FUND_8H_RATE * (bars_held / 2.0)
    return gross - fees - funding


def unrealized_pnl(grid, price):
    filled = [l for l in grid.levels if l.filled]
    if not filled:
        return 0.0
    if grid.side == "long":
        return sum(l.qty * (price - l.fill_px) for l in filled)
    else:
        return sum(l.qty * (l.fill_px - price) for l in filled)


@dataclass
class Result:
    name: str
    config: object
    final_equity: float
    cagr: float
    max_dd: float
    n_trades: int
    n_tp: int
    n_timeout: int
    n_liq: int
    win_rate: float
    years: float
    equity_ts: list
    time_ts: list
    long_trades: int = 0
    short_trades: int = 0
    fav_trades: int = 0
    unfav_trades: int = 0


def run_backtest(bars, regime_series, cfg: V23Config, verbose=False):
    cl_arr = bars['c'].values
    hi_arr = bars['h'].values
    lo_arr = bars['l'].values
    ema_arr = bars['ema34'].values
    sma_arr = bars['sma14'].values
    times = bars.index.values
    regimes = regime_series.values
    n = len(bars)

    equity = INITIAL_EQUITY
    grid = None
    last_exit = -99
    peak_mtm = INITIAL_EQUITY
    max_dd = 0.0
    trades = []
    eq_ts = []
    time_ts = []
    long_trades = 0
    short_trades = 0
    fav_trades = 0
    unfav_trades = 0

    for i in range(n):
        hi, lo, cl = hi_arr[i], lo_arr[i], cl_arr[i]
        ema, sma = ema_arr[i], sma_arr[i]
        reg = regimes[i]

        if pd.isna(ema) or pd.isna(sma):
            continue

        # Update grid fills
        if grid is not None:
            bh = i - grid.start_bar
            fc = sum(1 for l in grid.levels if l.filled)
            for li in range(fc, NUM_LEVELS):
                lv = grid.levels[li]
                if grid.side == "long" and lo <= lv.target_px:
                    lv.filled = True; lv.fill_px = lv.target_px; grid.recalc(); break
                elif grid.side == "short" and hi >= lv.target_px:
                    lv.filled = True; lv.fill_px = lv.target_px; grid.recalc(); break

            lp = liq_price(grid, equity)
            if (grid.side == "long" and lo <= lp) or (grid.side == "short" and hi >= lp):
                pnl = calc_pnl(grid, lp, bh)
                fn_levels = sum(1 for l in grid.levels if l.filled)
                trades.append({"side": grid.side, "reason": "LIQ", "pnl": pnl, "levels": fn_levels, "bars": bh})
                equity += pnl
                equity = max(equity, grid.total_notional * MAINT_RATE)
                grid = None; last_exit = i
            elif (grid.side == "long" and hi >= grid.tp_price) or (grid.side == "short" and lo <= grid.tp_price):
                pnl = calc_pnl(grid, grid.tp_price, bh)
                fn_levels = sum(1 for l in grid.levels if l.filled)
                trades.append({"side": grid.side, "reason": "TP", "pnl": pnl, "levels": fn_levels, "bars": bh})
                equity += pnl
                grid = None; last_exit = i
            elif bh >= grid.max_hold:
                pnl = calc_pnl(grid, cl, bh)
                fn_levels = sum(1 for l in grid.levels if l.filled)
                trades.append({"side": grid.side, "reason": "TIMEOUT", "pnl": pnl, "levels": fn_levels, "bars": bh})
                equity += pnl
                grid = None; last_exit = i

        # Open new grid
        if grid is None and (i - last_exit) >= COOLDOWN_BARS and equity >= MIN_EQUITY:
            pbe = (ema - cl) / ema * 100 if ema > 0 else 0
            pbs = (sma - cl) / sma * 100 if sma > 0 else 0
            pae = (cl - ema) / ema * 100 if ema > 0 else 0
            pas = (cl - sma) / sma * 100 if sma > 0 else 0

            lb = cfg.get_behavior(reg, "long")
            sb = cfg.get_behavior(reg, "short")

            if lb.allowed:
                lt = cfg.long_trigger_pct * lb.trigger_scale
                if pbe >= lt and pbs >= lt:
                    ar = cfg.risk_pct * lb.risk_scale
                    ag = [g * lb.spacing_scale for g in cfg.level_gaps]
                    am = [m * lb.mult_scale if idx > 0 else m for idx, m in enumerate(cfg.level_multipliers)]
                    # Keep first multiplier as-is (L1→L2), amplify rest
                    # Actually, mult_scale applies to all multipliers uniformly
                    am = [m * lb.mult_scale for m in cfg.level_multipliers]
                    ah = max(1, int(round(cfg.max_hold_bars * lb.hold_scale)))
                    grid = build_grid("long", i, cl, equity, ar, ag, am)
                    grid.max_hold = ah
                    long_trades += 1
                    if reg == "bull":
                        fav_trades += 1
                    else:
                        unfav_trades += 1

            if grid is None and sb.allowed:
                st = cfg.short_trigger_pct * sb.trigger_scale
                if pae >= st and pas >= st:
                    ar = cfg.risk_pct * sb.risk_scale
                    ag = [g * sb.spacing_scale for g in cfg.level_gaps]
                    am = [m * sb.mult_scale for m in cfg.level_multipliers]
                    ah = max(1, int(round(cfg.max_hold_bars * sb.hold_scale)))
                    grid = build_grid("short", i, cl, equity, ar, ag, am)
                    grid.max_hold = ah
                    short_trades += 1
                    if reg == "bear":
                        fav_trades += 1
                    else:
                        unfav_trades += 1

        # Mark-to-market MDD
        ur = unrealized_pnl(grid, cl) if grid else 0.0
        mtm = equity + ur
        if mtm > peak_mtm:
            peak_mtm = mtm
        dd = (peak_mtm - mtm) / peak_mtm * 100 if peak_mtm > 0 else 0
        max_dd = max(max_dd, dd)

        if i % 6 == 0:
            eq_ts.append(mtm)
            time_ts.append(times[i])

    # Close remaining
    if grid is not None:
        bh = n - 1 - grid.start_bar
        pnl = calc_pnl(grid, cl_arr[-1], bh)
        equity += pnl
        fn_levels = sum(1 for l in grid.levels if l.filled)
        trades.append({"side": grid.side, "reason": "END", "pnl": pnl, "levels": fn_levels, "bars": bh})

    start_t = pd.Timestamp(times[0])
    end_t = pd.Timestamp(times[-1])
    years = (end_t - start_t).total_seconds() / (365.25 * 86400)
    final = max(equity, 0.01)
    cagr = ((final / INITIAL_EQUITY) ** (1 / years) - 1) * 100 if years > 0 else 0
    n_tp = sum(1 for t in trades if t["reason"] == "TP")
    n_to = sum(1 for t in trades if t["reason"] == "TIMEOUT")
    n_liq = sum(1 for t in trades if t["reason"] == "LIQ")
    wr = n_tp / len(trades) * 100 if trades else 0

    return Result(
        name=cfg.name, config=cfg, final_equity=final, cagr=cagr, max_dd=max_dd,
        n_trades=len(trades), n_tp=n_tp, n_timeout=n_to, n_liq=n_liq, win_rate=wr,
        years=years, equity_ts=eq_ts, time_ts=time_ts,
        long_trades=long_trades, short_trades=short_trades,
        fav_trades=fav_trades, unfav_trades=unfav_trades,
    )


# ─── Research phases ─────────────────────────────────────────────────────

def phase1_single_axis_sweeps(bars_sim, regime_series):
    """Sweep each favored-side parameter independently."""
    print("\n" + "=" * 70)
    print("  PHASE 1: Single-axis sweeps")
    print("=" * 70)

    results = []

    # Baseline first
    baseline_cfg = V23Config(name="v2.1_baseline")
    print(f"\n  Running baseline...")
    r = run_backtest(bars_sim, regime_series, baseline_cfg)
    results.append(r)
    print(f"    CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}")

    # 1) fav_risk_scale sweep
    print(f"\n  Sweep: fav_risk_scale")
    for v in [1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.40, 1.50]:
        cfg = V23Config(name=f"fR={v:.2f}", fav_risk_scale=v)
        r = run_backtest(bars_sim, regime_series, cfg)
        results.append(r)
        liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
        print(f"    fav_risk_scale={v:.2f}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    # 2) fav_trigger_scale sweep (lower = easier entry)
    print(f"\n  Sweep: fav_trigger_scale")
    for v in [0.50, 0.60, 0.70, 0.80, 0.90, 0.95]:
        cfg = V23Config(name=f"fT={v:.2f}", fav_trigger_scale=v)
        r = run_backtest(bars_sim, regime_series, cfg)
        results.append(r)
        liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
        print(f"    fav_trigger_scale={v:.2f}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    # 3) fav_hold_scale sweep (higher = hold longer)
    print(f"\n  Sweep: fav_hold_scale")
    for v in [1.10, 1.20, 1.30, 1.50, 1.75, 2.00]:
        cfg = V23Config(name=f"fH={v:.2f}", fav_hold_scale=v)
        r = run_backtest(bars_sim, regime_series, cfg)
        results.append(r)
        liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
        print(f"    fav_hold_scale={v:.2f}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    # 4) fav_spacing_scale sweep (lower = tighter = more aggressive)
    print(f"\n  Sweep: fav_spacing_scale")
    for v in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]:
        cfg = V23Config(name=f"fS={v:.2f}", fav_spacing_scale=v)
        r = run_backtest(bars_sim, regime_series, cfg)
        results.append(r)
        liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
        print(f"    fav_spacing_scale={v:.2f}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    # 5) fav_mult_scale sweep (higher = heavier deep fills)
    print(f"\n  Sweep: fav_mult_scale")
    for v in [1.05, 1.10, 1.15, 1.20, 1.30, 1.50]:
        cfg = V23Config(name=f"fM={v:.2f}", fav_mult_scale=v)
        r = run_backtest(bars_sim, regime_series, cfg)
        results.append(r)
        liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
        print(f"    fav_mult_scale={v:.2f}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    return results


def phase2_interaction_grid(bars_sim, regime_series, phase1_results):
    """Based on phase 1 winners, test pairwise interactions of the best values."""
    print("\n" + "=" * 70)
    print("  PHASE 2: Interaction grid (best single-axis winners)")
    print("=" * 70)

    baseline = phase1_results[0]  # v2.1 baseline

    # Find the best zero-liq value for each axis
    axes = {
        'fav_risk_scale': [],
        'fav_trigger_scale': [],
        'fav_hold_scale': [],
        'fav_spacing_scale': [],
        'fav_mult_scale': [],
    }
    for r in phase1_results[1:]:
        cfg = r.config
        if r.n_liq > 0:
            continue
        if cfg.fav_risk_scale != 1.0 and cfg.fav_trigger_scale == 1.0 and cfg.fav_hold_scale == 1.0 and cfg.fav_spacing_scale == 1.0 and cfg.fav_mult_scale == 1.0:
            axes['fav_risk_scale'].append((cfg.fav_risk_scale, r.cagr))
        elif cfg.fav_trigger_scale != 1.0 and cfg.fav_risk_scale == 1.0 and cfg.fav_hold_scale == 1.0 and cfg.fav_spacing_scale == 1.0 and cfg.fav_mult_scale == 1.0:
            axes['fav_trigger_scale'].append((cfg.fav_trigger_scale, r.cagr))
        elif cfg.fav_hold_scale != 1.0 and cfg.fav_risk_scale == 1.0 and cfg.fav_trigger_scale == 1.0 and cfg.fav_spacing_scale == 1.0 and cfg.fav_mult_scale == 1.0:
            axes['fav_hold_scale'].append((cfg.fav_hold_scale, r.cagr))
        elif cfg.fav_spacing_scale != 1.0 and cfg.fav_risk_scale == 1.0 and cfg.fav_trigger_scale == 1.0 and cfg.fav_hold_scale == 1.0 and cfg.fav_mult_scale == 1.0:
            axes['fav_spacing_scale'].append((cfg.fav_spacing_scale, r.cagr))
        elif cfg.fav_mult_scale != 1.0 and cfg.fav_risk_scale == 1.0 and cfg.fav_trigger_scale == 1.0 and cfg.fav_hold_scale == 1.0 and cfg.fav_spacing_scale == 1.0:
            axes['fav_mult_scale'].append((cfg.fav_mult_scale, r.cagr))

    # Pick best 2 values per axis (must beat baseline)
    best_per_axis = {}
    for axis, vals in axes.items():
        above_baseline = [(v, c) for v, c in vals if c > baseline.cagr]
        if above_baseline:
            above_baseline.sort(key=lambda x: x[1], reverse=True)
            best_per_axis[axis] = [v for v, _ in above_baseline[:2]]
        else:
            best_per_axis[axis] = []

    print("\n  Best single-axis winners (zero-liq, beat baseline):")
    for axis, vals in best_per_axis.items():
        if vals:
            print(f"    {axis}: {vals}")
        else:
            print(f"    {axis}: (none beat baseline)")

    # Build interaction grid from axes that have winners
    active_axes = {k: v for k, v in best_per_axis.items() if v}
    if len(active_axes) < 2:
        print("  Not enough winning axes for interaction testing.")
        return []

    results = []
    axis_names = list(active_axes.keys())

    # Pairwise combinations
    from itertools import combinations
    for a1, a2 in combinations(axis_names, 2):
        for v1 in active_axes[a1]:
            for v2 in active_axes[a2]:
                kwargs = {a1: v1, a2: v2}
                name = f"{a1.replace('fav_', 'f').replace('_scale', '')}={v1:.2f}+{a2.replace('fav_', 'f').replace('_scale', '')}={v2:.2f}"
                cfg = V23Config(name=name, **kwargs)
                r = run_backtest(bars_sim, regime_series, cfg)
                results.append(r)
                liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
                print(f"    {name}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    # Triple combinations if 3+ axes are active
    if len(axis_names) >= 3:
        for combo in combinations(axis_names, 3):
            # Only use best value per axis
            kwargs = {ax: active_axes[ax][0] for ax in combo}
            name = "+".join(f"{ax.replace('fav_', 'f').replace('_scale', '')}={kwargs[ax]:.2f}" for ax in combo)
            cfg = V23Config(name=name, **kwargs)
            r = run_backtest(bars_sim, regime_series, cfg)
            results.append(r)
            liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
            print(f"    {name}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    # Full combination (all best values)
    if len(axis_names) >= 4:
        kwargs = {ax: active_axes[ax][0] for ax in axis_names}
        name = "ALL_BEST:" + "+".join(f"{ax.replace('fav_', 'f').replace('_scale', '')}={kwargs[ax]:.2f}" for ax in axis_names)
        cfg = V23Config(name=name, **kwargs)
        r = run_backtest(bars_sim, regime_series, cfg)
        results.append(r)
        liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
        print(f"    {name}: CAGR={r.cagr:.1f}% MDD={r.max_dd:.1f}% Liqs={r.n_liq} Eq=${r.final_equity:,.0f}{liq_flag}")

    return results


def phase3_fine_tune(bars_sim, regime_series, all_results):
    """Fine-tune around the best interaction result."""
    print("\n" + "=" * 70)
    print("  PHASE 3: Fine-tuning around best candidate")
    print("=" * 70)

    baseline = all_results[0]
    zero_liq = [r for r in all_results if r.n_liq == 0 and r.cagr > baseline.cagr]

    if not zero_liq:
        print("  No zero-liq candidate beats baseline. Nothing to fine-tune.")
        return []

    best = max(zero_liq, key=lambda r: r.cagr)
    cfg = best.config
    print(f"\n  Best candidate so far: {best.name}")
    print(f"    CAGR={best.cagr:.1f}% MDD={best.max_dd:.1f}% Liqs={best.n_liq}")
    print(f"    fR={cfg.fav_risk_scale:.2f} fT={cfg.fav_trigger_scale:.2f} fH={cfg.fav_hold_scale:.2f} fS={cfg.fav_spacing_scale:.2f} fM={cfg.fav_mult_scale:.2f}")

    results = []

    # Fine-tune each active parameter ±10-20%
    active_params = {}
    if cfg.fav_risk_scale != 1.0:
        active_params['fav_risk_scale'] = cfg.fav_risk_scale
    if cfg.fav_trigger_scale != 1.0:
        active_params['fav_trigger_scale'] = cfg.fav_trigger_scale
    if cfg.fav_hold_scale != 1.0:
        active_params['fav_hold_scale'] = cfg.fav_hold_scale
    if cfg.fav_spacing_scale != 1.0:
        active_params['fav_spacing_scale'] = cfg.fav_spacing_scale
    if cfg.fav_mult_scale != 1.0:
        active_params['fav_mult_scale'] = cfg.fav_mult_scale

    base_kwargs = {
        'fav_risk_scale': cfg.fav_risk_scale,
        'fav_trigger_scale': cfg.fav_trigger_scale,
        'fav_hold_scale': cfg.fav_hold_scale,
        'fav_spacing_scale': cfg.fav_spacing_scale,
        'fav_mult_scale': cfg.fav_mult_scale,
    }

    for param, val in active_params.items():
        # Generate fine-tune values around current best
        offsets = [-0.15, -0.10, -0.05, +0.05, +0.10, +0.15]
        for offset in offsets:
            new_val = val + offset
            # Sanity bounds
            if param == 'fav_risk_scale' and new_val <= 0.5:
                continue
            if param == 'fav_trigger_scale' and (new_val <= 0.1 or new_val >= 2.0):
                continue
            if param == 'fav_hold_scale' and new_val <= 0.3:
                continue
            if param == 'fav_spacing_scale' and (new_val <= 0.3 or new_val >= 2.0):
                continue
            if param == 'fav_mult_scale' and new_val <= 0.5:
                continue
            if abs(new_val - val) < 0.001:
                continue

            kwargs = dict(base_kwargs)
            kwargs[param] = new_val
            name = f"FT_{param.replace('fav_', 'f').replace('_scale', '')}={new_val:.2f}"
            test_cfg = V23Config(name=name, **kwargs)
            r = run_backtest(bars_sim, regime_series, test_cfg)
            results.append(r)
            liq_flag = " *** LIQ ***" if r.n_liq > 0 else ""
            delta = r.cagr - best.cagr
            print(f"    {name}: CAGR={r.cagr:.1f}% (Δ{delta:+.1f}%) MDD={r.max_dd:.1f}% Liqs={r.n_liq}{liq_flag}")

    return results


# ─── Charts ──────────────────────────────────────────────────────────────

def make_equity_chart(baseline, candidates, out_path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    fig = plt.figure(figsize=(18, 11), facecolor="#131722")
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.06)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)

    for ax in [ax1, ax2]:
        ax.set_facecolor("#131722"); ax.tick_params(colors="#b2b5be", labelsize=9)
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        for s in ["bottom", "left"]:
            ax.spines[s].set_color("#363a45")
        ax.grid(True, color="#1e222d", linewidth=0.6, alpha=0.7)

    all_to_plot = [baseline] + candidates
    colors = ["#ffb800", "#00ff88", "#00d4ff", "#a855f7", "#ff6b6b", "#f97316", "#22d3ee", "#e879f9"]

    for idx, r in enumerate(all_to_plot):
        ts = [pd.Timestamp(t) for t in r.time_ts]
        eq = r.equity_ts
        if not ts:
            continue

        color = colors[idx % len(colors)]
        lw = 2.5 if idx == 0 else 1.6
        ls = "-" if idx == 0 else "-"
        liq_tag = f" | {r.n_liq} liq" if r.n_liq > 0 else ""
        label = f"{r.name}: {r.cagr:.1f}%/yr | MDD {r.max_dd:.1f}%{liq_tag} | ${r.final_equity:,.0f}"
        ax1.plot(ts, eq, color=color, linewidth=lw, linestyle=ls, alpha=0.9, label=label)

        ea = np.array(eq)
        pk = np.maximum.accumulate(ea)
        dd = (pk - ea) / pk * 100
        ax2.fill_between(ts, 0, -dd, alpha=0.12, color=color)
        ax2.plot(ts, -dd, color=color, linewidth=lw * 0.7, alpha=0.85)

    ax1.set_yscale('log')
    ax1.yaxis.set_major_formatter(FuncFormatter(
        lambda x, _: f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}K" if x >= 1e3 else f"${x:.0f}"))
    ax1.axhline(INITIAL_EQUITY, color="#363a45", linewidth=1, linestyle="--", alpha=0.6)
    ax1.set_ylabel("Mark-to-Market Equity (log)", color="#b2b5be", fontsize=10)
    ax1.legend(loc="upper left", fontsize=7.5, facecolor="#131722", edgecolor="#363a45", labelcolor="#b2b5be")
    ax1.set_title("Mr Martingale v2.3 — Favored-Side Amplification Research  |  BTC 4h  |  $1K start",
                   color="#d1d4dc", fontsize=11, fontweight="bold", pad=10)
    ax2.set_ylabel("Drawdown (MTM)", color="#b2b5be", fontsize=10)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{-x:.0f}%"))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    plt.setp(ax2.xaxis.get_majorticklabels(), color="#b2b5be")
    plt.setp(ax1.xaxis.get_majorticklabels(), visible=False)
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor="#131722", edgecolor="none")
    plt.close(fig)
    print(f"  Chart: {out_path.name}")


def make_sweep_chart(phase1_results, baseline_cagr, out_path):
    """Bar chart of single-axis sweep results."""
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 5, figsize=(22, 6), facecolor="#131722")
    fig.suptitle("v2.3 Favored-Side Amplification — Single-Axis Sweep Results",
                 color="#d1d4dc", fontsize=12, fontweight="bold")

    sweep_groups = {
        'fav_risk_scale': {'label': 'Risk Scale', 'color': '#00ff88'},
        'fav_trigger_scale': {'label': 'Trigger Scale', 'color': '#00d4ff'},
        'fav_hold_scale': {'label': 'Hold Scale', 'color': '#a855f7'},
        'fav_spacing_scale': {'label': 'Spacing Scale', 'color': '#f97316'},
        'fav_mult_scale': {'label': 'Mult Scale', 'color': '#e879f9'},
    }

    for ax_idx, (param, info) in enumerate(sweep_groups.items()):
        ax = axes[ax_idx]
        ax.set_facecolor("#131722"); ax.tick_params(colors="#b2b5be", labelsize=7)
        for s in ax.spines.values():
            s.set_color("#363a45")
        ax.grid(True, axis='y', color="#1e222d", linewidth=0.5, alpha=0.5)

        vals, cagrs, has_liq = [], [], []
        for r in phase1_results[1:]:
            cfg = r.config
            is_this_axis = False
            param_val = getattr(cfg, param)
            other_params = ['fav_risk_scale', 'fav_trigger_scale', 'fav_hold_scale', 'fav_spacing_scale', 'fav_mult_scale']
            other_params.remove(param)
            if param_val != 1.0 and all(getattr(cfg, p) == 1.0 for p in other_params):
                is_this_axis = True
            if is_this_axis:
                vals.append(param_val)
                cagrs.append(r.cagr)
                has_liq.append(r.n_liq > 0)

        if vals:
            bar_colors = ['#ff4444' if liq else info['color'] for liq in has_liq]
            ax.bar(range(len(vals)), cagrs, color=bar_colors, alpha=0.8)
            ax.set_xticks(range(len(vals)))
            ax.set_xticklabels([f"{v:.2f}" for v in vals], fontsize=6, color="#b2b5be")
            ax.axhline(baseline_cagr, color="#ffb800", linewidth=1.5, linestyle="--", alpha=0.8, label="baseline")

        ax.set_title(info['label'], color="#d1d4dc", fontsize=9)
        ax.set_ylabel("CAGR %" if ax_idx == 0 else "", color="#b2b5be", fontsize=8)
        if ax_idx == 0:
            ax.legend(fontsize=6, facecolor="#131722", edgecolor="#363a45", labelcolor="#b2b5be")

    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor="#131722", edgecolor="none")
    plt.close(fig)
    print(f"  Sweep chart: {out_path.name}")


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print("=" * 70)
    print("  Mr Martingale v2.3 — Favored-Side Amplification Research")
    print(f"  Date: {today}")
    print(f"  Baseline: v2.1 champion (25% risk, DMA=440, corrected exact-liq)")
    print("=" * 70)

    bars_4h, daily, dma_period = load_and_prepare(CHAMPION_DMA_PERIOD)

    sma_col = f'sma{dma_period}'
    regime_series = broadcast_regime(bars_4h, daily, sma_col)

    sma_start = daily[daily[sma_col].notna()].index[0]
    bars_sim = bars_4h[bars_4h.index >= sma_start].copy()
    regime_sim = regime_series[bars_sim.index]

    print(f"Simulation: {len(bars_sim)} bars from {bars_sim.index[0]} to {bars_sim.index[-1]}")

    # Phase 1: Single-axis sweeps
    p1_results = phase1_single_axis_sweeps(bars_sim, regime_sim)

    # Phase 2: Interaction grid
    p2_results = phase2_interaction_grid(bars_sim, regime_sim, p1_results)

    # Phase 3: Fine-tune
    all_so_far = p1_results + p2_results
    p3_results = phase3_fine_tune(bars_sim, regime_sim, all_so_far)

    all_results = p1_results + p2_results + p3_results

    # ── Analysis ──
    print("\n" + "=" * 70)
    print("  ANALYSIS")
    print("=" * 70)

    baseline = all_results[0]
    print(f"\n  v2.1 Baseline: CAGR={baseline.cagr:.1f}% MDD={baseline.max_dd:.1f}% Eq=${baseline.final_equity:,.0f}")

    zero_liq = [r for r in all_results if r.n_liq == 0]
    zero_liq_better = [r for r in zero_liq if r.cagr > baseline.cagr]
    zero_liq_better.sort(key=lambda r: r.cagr, reverse=True)

    print(f"\n  Total configs tested: {len(all_results)}")
    print(f"  Zero-liq configs: {len(zero_liq)}")
    print(f"  Zero-liq configs that beat baseline: {len(zero_liq_better)}")

    if zero_liq_better:
        print(f"\n  Top 10 zero-liq candidates that beat baseline:")
        print(f"  {'Name':<55} {'CAGR':>8} {'MDD':>8} {'Eq':>14} {'Δ CAGR':>8} {'Trades':>7}")
        print("  " + "-" * 105)
        for r in zero_liq_better[:10]:
            delta = r.cagr - baseline.cagr
            print(f"  {r.name:<55} {r.cagr:>7.1f}% {r.max_dd:>7.1f}% ${r.final_equity:>12,.0f} {delta:>+7.1f}% {r.n_trades:>7}")

        champion = zero_liq_better[0]
        print(f"\n  CHAMPION: {champion.name}")
        print(f"    CAGR={champion.cagr:.1f}% (Δ{champion.cagr - baseline.cagr:+.1f}%)")
        print(f"    MDD={champion.max_dd:.1f}% (Δ{champion.max_dd - baseline.max_dd:+.1f}%)")
        print(f"    Final Equity=${champion.final_equity:,.0f}")
        print(f"    Config: {champion.config.desc()}")
    else:
        champion = None
        print(f"\n  NO candidate beats baseline with zero liquidations.")
        print(f"  RECOMMENDATION: Keep v2.1 as-is.")

    # ── Charts ──
    print(f"\n{'=' * 70}")
    print("  Generating outputs...")
    print(f"{'=' * 70}")

    # Equity chart: baseline + top candidates
    chart_candidates = zero_liq_better[:5] if zero_liq_better else []
    eq_path = REPORTS_DIR / f"mrm_v23_equity_{today}.png"
    make_equity_chart(baseline, chart_candidates, eq_path)

    # Sweep chart
    sweep_path = REPORTS_DIR / f"mrm_v23_sweep_{today}.png"
    make_sweep_chart(p1_results, baseline.cagr, sweep_path)

    # JSON data
    json_data = {
        "date": today,
        "baseline": {
            "cagr": round(baseline.cagr, 2),
            "final_equity": round(baseline.final_equity, 2),
            "max_dd": round(baseline.max_dd, 2),
            "n_liq": baseline.n_liq,
            "n_trades": baseline.n_trades,
        },
        "champion": None,
        "all_results": [],
    }

    if champion:
        cfg = champion.config
        json_data["champion"] = {
            "name": champion.name,
            "cagr": round(champion.cagr, 2),
            "final_equity": round(champion.final_equity, 2),
            "max_dd": round(champion.max_dd, 2),
            "n_liq": champion.n_liq,
            "n_trades": champion.n_trades,
            "fav_risk_scale": cfg.fav_risk_scale,
            "fav_trigger_scale": cfg.fav_trigger_scale,
            "fav_hold_scale": cfg.fav_hold_scale,
            "fav_spacing_scale": cfg.fav_spacing_scale,
            "fav_mult_scale": cfg.fav_mult_scale,
            "delta_cagr": round(champion.cagr - baseline.cagr, 2),
        }

    for r in all_results:
        cfg = r.config
        json_data["all_results"].append({
            "name": r.name,
            "cagr": round(r.cagr, 2),
            "final_equity": round(r.final_equity, 2),
            "max_dd": round(r.max_dd, 2),
            "n_liq": r.n_liq,
            "n_trades": r.n_trades,
            "n_tp": r.n_tp,
            "n_timeout": r.n_timeout,
            "long_trades": r.long_trades,
            "short_trades": r.short_trades,
            "fav_trades": r.fav_trades,
            "unfav_trades": r.unfav_trades,
            "fav_risk_scale": cfg.fav_risk_scale,
            "fav_trigger_scale": cfg.fav_trigger_scale,
            "fav_hold_scale": cfg.fav_hold_scale,
            "fav_spacing_scale": cfg.fav_spacing_scale,
            "fav_mult_scale": cfg.fav_mult_scale,
        })

    json_path = REPORTS_DIR / f"mrm_v23_results_{today}.json"
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=2)
    print(f"  Results: {json_path.name}")

    # ── Report ──
    report = generate_report(all_results, baseline, champion, zero_liq_better, today)
    report_path = REPORTS_DIR / f"mrm_v23_favored_amp_report_{today}.md"
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"  Report: {report_path.name}")

    print(f"\n{'=' * 70}")
    print("  DONE")
    print(f"{'=' * 70}")


def generate_report(all_results, baseline, champion, zero_liq_better, today):
    L = []
    A = L.append

    A("# Mr Martingale v2.3 — Favored-Side Amplification Report")
    A(f"**Date:** {today}")
    A("**Objective:** Test whether the favored side should be run hotter than baseline")
    A("**Method:** Systematic sweep of favored-side amplification parameters with exact-liq simulation")
    A("**Baseline:** v2.1 champion (25% risk, DMA=440, corrected exact-liq)")
    A("")
    A("---")
    A("")
    A("## 1. Executive Summary")
    A("")

    if champion and champion.cagr > baseline.cagr + 1.0:
        delta = champion.cagr - baseline.cagr
        cfg = champion.config
        A(f"**YES — Favored-side amplification improves CAGR by +{delta:.1f}% with zero liquidations.**")
        A("")
        A(f"| Metric | v2.1 Baseline | v2.3 Champion | Δ |")
        A(f"|--------|--------------|---------------|---|")
        A(f"| CAGR | {baseline.cagr:.1f}% | **{champion.cagr:.1f}%** | +{delta:.1f}% |")
        A(f"| Final Equity | ${baseline.final_equity:,.0f} | **${champion.final_equity:,.0f}** | {champion.final_equity/baseline.final_equity:.1f}× |")
        A(f"| Max DD (MTM) | {baseline.max_dd:.1f}% | {champion.max_dd:.1f}% | {champion.max_dd - baseline.max_dd:+.1f}% |")
        A(f"| Liquidations | {baseline.n_liq} | **{champion.n_liq}** | — |")
        A(f"| Trades | {baseline.n_trades} | {champion.n_trades} | {champion.n_trades - baseline.n_trades:+d} |")
        A("")
        A(f"**Recommended v2.3 favored-side parameters:**")
        A(f"```yaml")
        A(f"fav_risk_scale: {cfg.fav_risk_scale:.2f}")
        A(f"fav_trigger_scale: {cfg.fav_trigger_scale:.2f}")
        A(f"fav_hold_scale: {cfg.fav_hold_scale:.2f}")
        A(f"fav_spacing_scale: {cfg.fav_spacing_scale:.2f}")
        A(f"fav_mult_scale: {cfg.fav_mult_scale:.2f}")
        A(f"```")
    elif champion and champion.cagr > baseline.cagr:
        delta = champion.cagr - baseline.cagr
        A(f"**MARGINAL — Best candidate only +{delta:.1f}% CAGR over baseline.**")
        A("")
        A(f"The improvement is too small to justify the added complexity. **Recommend keeping v2.1 as-is.**")
    else:
        A("**NO — No favored-side amplification candidate beats the v2.1 baseline with zero liquidations.**")
        A("")
        A("**Recommend keeping v2.1 as-is.**")
    A("")
    A("---")
    A("")

    # Baseline reference
    A("## 2. Baseline Reference (v2.1 Champion)")
    A("")
    A("```yaml")
    A(f"risk_pct: {CHAMPION_RISK_PCT}")
    A(f"level_gaps: {CHAMPION_LEVEL_GAPS}")
    A(f"level_multipliers: {CHAMPION_LEVEL_MULTIPLIERS}")
    A(f"dma_period: {CHAMPION_DMA_PERIOD}")
    A(f"max_hold_bars: {CHAMPION_MAX_HOLD_BARS}")
    A(f"long_trigger_pct: {CHAMPION_LONG_TRIGGER_PCT}")
    A(f"short_trigger_pct: {CHAMPION_SHORT_TRIGGER_PCT}")
    A(f"unfav_risk_scale: {CHAMPION_UNFAV_RISK_SCALE}")
    A(f"unfav_spacing_scale: {CHAMPION_UNFAV_SPACING_SCALE}")
    A(f"unfav_trigger_scale: {CHAMPION_UNFAV_TRIGGER_SCALE}")
    A(f"unfav_hold_scale: {CHAMPION_UNFAV_HOLD_SCALE}")
    A("```")
    A("")
    A(f"**Baseline result:** CAGR={baseline.cagr:.1f}% | MDD={baseline.max_dd:.1f}% | Equity=${baseline.final_equity:,.0f} | Liqs={baseline.n_liq} | Trades={baseline.n_trades}")
    A("")
    A("---")
    A("")

    # Phase 1 results
    A("## 3. Phase 1: Single-Axis Sweep Results")
    A("")
    A("Each parameter swept independently while all others at 1.0 (baseline).")
    A("")
    A("| Parameter | Value | CAGR | MDD | Liqs | Δ CAGR | Trades |")
    A("|-----------|-------|------|-----|------|--------|--------|")
    A(f"| **baseline** | — | **{baseline.cagr:.1f}%** | {baseline.max_dd:.1f}% | {baseline.n_liq} | — | {baseline.n_trades} |")

    for r in all_results[1:]:
        cfg = r.config
        # Identify which single axis
        param = None
        val = None
        others_default = True
        for p in ['fav_risk_scale', 'fav_trigger_scale', 'fav_hold_scale', 'fav_spacing_scale', 'fav_mult_scale']:
            v = getattr(cfg, p)
            if v != 1.0:
                if param is not None:
                    others_default = False
                    break
                param = p
                val = v
        if param and others_default and val is not None:
            delta = r.cagr - baseline.cagr
            liq_mark = " ⚠" if r.n_liq > 0 else ""
            A(f"| {param} | {val:.2f} | {r.cagr:.1f}%{liq_mark} | {r.max_dd:.1f}% | {r.n_liq} | {delta:+.1f}% | {r.n_trades} |")

    A("")
    A("⚠ = liquidation(s) occurred (disqualified)")
    A("")
    A("---")
    A("")

    # Phase 2 results
    p2_results = [r for r in all_results if '+' in r.name or 'ALL_BEST' in r.name]
    if p2_results:
        A("## 4. Phase 2: Interaction Results")
        A("")
        A("Best single-axis winners combined pairwise and in triples/quads.")
        A("")
        A("| Combination | CAGR | MDD | Liqs | Δ CAGR | Trades |")
        A("|-------------|------|-----|------|--------|--------|")
        for r in sorted(p2_results, key=lambda x: x.cagr, reverse=True):
            delta = r.cagr - baseline.cagr
            liq_mark = " ⚠" if r.n_liq > 0 else ""
            A(f"| {r.name} | {r.cagr:.1f}%{liq_mark} | {r.max_dd:.1f}% | {r.n_liq} | {delta:+.1f}% | {r.n_trades} |")
        A("")
        A("---")
        A("")

    # Phase 3 results
    p3_results = [r for r in all_results if r.name.startswith('FT_')]
    if p3_results:
        A("## 5. Phase 3: Fine-Tuning Results")
        A("")
        A("| Tweak | CAGR | MDD | Liqs | Δ CAGR | Trades |")
        A("|-------|------|-----|------|--------|--------|")
        for r in sorted(p3_results, key=lambda x: x.cagr, reverse=True)[:15]:
            delta = r.cagr - baseline.cagr
            liq_mark = " ⚠" if r.n_liq > 0 else ""
            A(f"| {r.name} | {r.cagr:.1f}%{liq_mark} | {r.max_dd:.1f}% | {r.n_liq} | {delta:+.1f}% | {r.n_trades} |")
        A("")
        A("---")
        A("")

    # Top 10 overall
    A("## 6. Top 10 Overall (Zero-Liq, Beat Baseline)")
    A("")
    if zero_liq_better:
        A("| Rank | Name | CAGR | MDD | Δ CAGR | Equity | Trades |")
        A("|------|------|------|-----|--------|--------|--------|")
        for idx, r in enumerate(zero_liq_better[:10]):
            delta = r.cagr - baseline.cagr
            A(f"| {idx+1} | {r.name} | {r.cagr:.1f}% | {r.max_dd:.1f}% | +{delta:.1f}% | ${r.final_equity:,.0f} | {r.n_trades} |")
    else:
        A("*No candidate beat baseline with zero liquidations.*")
    A("")
    A("---")
    A("")

    # Analysis
    A("## 7. Analysis")
    A("")
    n_total = len(all_results) - 1  # exclude baseline
    n_liq = sum(1 for r in all_results[1:] if r.n_liq > 0)
    n_zero = n_total - n_liq
    n_better = len(zero_liq_better)
    A(f"- **Configs tested:** {n_total}")
    A(f"- **Caused liquidations:** {n_liq} ({n_liq/n_total*100:.0f}%)")
    A(f"- **Zero-liq and beat baseline:** {n_better} ({n_better/n_total*100:.0f}%)")
    A("")

    if champion and champion.cagr > baseline.cagr + 1.0:
        A("### Why favored-side amplification works (or doesn't)")
        A("")
        A("The key insight: the v2.1 champion already sits at the liquidation cliff (25% risk).")
        A("Any amplification on the favored side that increases effective notional pushes")
        A("the system closer to liquidation during unfavorable-side events within bull/bear regimes.")
        A("")
        A("The successful amplifications are ones that improve *trade efficiency* (more trades,")
        A("better timing, longer holds to capture more profit) without increasing peak notional exposure.")
    else:
        A("### Why the baseline is hard to beat")
        A("")
        A("The v2.1 champion sits at the **razor-sharp liquidation cliff** (25→26% = liq).")
        A("The favored side is already running at 100% capacity. Any amplification that")
        A("increases effective risk (risk_scale > 1, tighter spacing, heavier multipliers)")
        A("pushes toward the liq cliff. The geometry is already at its limit.")
        A("")
        A("Parameters that don't affect peak notional (trigger, hold) might help marginally,")
        A("but the compounding engine is already highly efficient at the baseline settings.")

    A("")
    A("---")
    A("")

    # Final recommendation
    A("## 8. Final Recommendation")
    A("")
    if champion and champion.cagr > baseline.cagr + 5.0:
        cfg = champion.config
        A(f"**ADOPT v2.3 favored-side amplification.**")
        A("")
        A(f"Add these parameters to the v2.1 champion config:")
        A(f"```yaml")
        A(f"# v2.3 favored-side amplification (added to v2.1 champion)")
        if cfg.fav_risk_scale != 1.0:
            A(f"fav_risk_scale: {cfg.fav_risk_scale}")
        if cfg.fav_trigger_scale != 1.0:
            A(f"fav_trigger_scale: {cfg.fav_trigger_scale}")
        if cfg.fav_hold_scale != 1.0:
            A(f"fav_hold_scale: {cfg.fav_hold_scale}")
        if cfg.fav_spacing_scale != 1.0:
            A(f"fav_spacing_scale: {cfg.fav_spacing_scale}")
        if cfg.fav_mult_scale != 1.0:
            A(f"fav_mult_scale: {cfg.fav_mult_scale}")
        A(f"```")
        A("")
        A(f"Expected improvement: +{champion.cagr - baseline.cagr:.1f}% CAGR, {champion.max_dd:.1f}% MDD, 0 liquidations.")
    elif champion and champion.cagr > baseline.cagr:
        A(f"**KEEP v2.1 as-is.** Best candidate is only +{champion.cagr - baseline.cagr:.1f}% CAGR — not worth the complexity.")
    else:
        A("**KEEP v2.1 as-is.** No favored-side amplification beats the baseline.")

    A("")
    A("---")
    A("")
    A("## 9. Files Created")
    A("")
    A("| File | Purpose |")
    A("|------|---------|")
    A(f"| `reports/mrm_v23_favored_amp_report_{today}.md` | This report |")
    A(f"| `reports/mrm_v23_equity_{today}.png` | Equity curves: baseline vs top candidates |")
    A(f"| `reports/mrm_v23_sweep_{today}.png` | Single-axis sweep bar chart |")
    A(f"| `reports/mrm_v23_results_{today}.json` | Full numeric results |")
    A("")
    A("*Research only — live bot untouched*")

    return "\n".join(L)


if __name__ == "__main__":
    main()
