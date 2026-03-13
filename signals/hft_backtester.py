"""
HFT Backtester — Lower Timeframes + Leverage

Data constraints (Hyperliquid API caps at ~5000 candles per interval):
- 5m: ~17 days of data (~5000 candles)
- 15m: ~52 days of data (~5000 candles)  
- 1h: ~7 months of data (~5000 candles)
- Funding: hourly, 2+ years
"""
from __future__ import annotations
import json, logging, math, sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from intelligence.historical_data import load_funding_csv, load_candles_csv
from signals.signal_definitions import (
    Direction, FundingRateExtremeSignal, OIDivergenceSignal,
    LiquidationCascadeProxySignal)
from signals.confluence_engine import ConfluenceEngine

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).parent / "results"

TAKER_FEE = 0.00025  # 0.025% per side
RT_FEE = 2 * TAKER_FEE  # 0.05% round trip
HOURLY_FUND = 0.00001  # avg abs funding rate

HORIZONS = {
    "5m":  {1: "5m", 3: "15m", 6: "30m", 12: "1h", 24: "2h"},
    "15m": {1: "15m", 2: "30m", 4: "1h", 8: "2h", 16: "4h"},
    "1h":  {1: "1h", 4: "4h", 8: "8h", 12: "12h", 24: "24h"},
}

TF = {
    "5m":  {"hpb": 5/60, "vw": 288, "clb": 6, "lb": 300, "pt": 0.001},
    "15m": {"hpb": 0.25, "vw": 96, "clb": 4, "lb": 100, "pt": 0.002},
    "1h":  {"hpb": 1.0,  "vw": 24, "clb": 3, "lb": 48,  "pt": 0.005},
}


@dataclass
class HFTResult:
    coin: str; base_tf: str; horizon_label: str; horizon_bars: int
    percentile: float; min_score: float; leverage: float; include_fees: bool
    total_trades: int; hit_rate: float; avg_return_pct: float
    median_return_pct: float; sharpe: float; max_drawdown_pct: float
    profit_factor: float; total_return_pct: float; ann_return_pct: float
    returns: list[float] = field(default_factory=list, repr=False)
    signals_fired: dict = field(default_factory=dict)


def backtest(coin, candles, funding, base_tf, pctl=95.0, ms=15.0,
             ma=2, levs=None, warmup=100, fees=True):
    """Core backtest loop. Returns list of HFTResult."""
    if levs is None:
        levs = [1.0, 2.0, 3.0]
    p = TF[base_tf]; hz = HORIZONS[base_tf]
    fs = FundingRateExtremeSignal(percentile_threshold=pctl, mode="classic")
    oi = OIDivergenceSignal(volume_spike_percentile=90.0,
                            volume_window=p["vw"], price_move_threshold=p["pt"])
    lq = LiquidationCascadeProxySignal(wick_ratio_threshold=0.80,
         volatility_window=p["vw"], volatility_spike_percentile=95.0,
         cascade_lookback=p["clb"])
    eng = ConfluenceEngine(min_score=ms, min_active_signals=ma)

    sf = sorted(funding, key=lambda r: r["timestamp_ms"])
    ar = [r["funding_rate"] for r in sf]
    at = [r["timestamp_ms"] for r in sf]
    mh = max(hz.keys())
    rmap = {(h, l): [] for h in hz for l in levs}
    sfired = {"funding_extreme": 0, "oi_divergence": 0, "liquidation_cascade": 0}
    fp = 0

    for i in range(warmup, len(candles) - mh):
        c = candles[i]
        rec = candles[max(0, i - p["lb"]):i]
        if len(rec) < 20:
            continue
        while fp < len(at) and at[fp] <= c["close_time_ms"]:
            fp += 1
        if fp == 0:
            continue
        cr = ar[fp - 1]
        rw = ar[max(0, fp - 2000):fp]
        r_f = fs.evaluate_from_history(cr, rw)
        r_o = oi.evaluate_from_candles(c, rec)
        r_l = lq.evaluate_from_candles(c, rec)
        if r_f.is_active: sfired["funding_extreme"] += 1
        if r_o.is_active: sfired["oi_divergence"] += 1
        if r_l.is_active: sfired["liquidation_cascade"] += 1
        cf = eng.score({"funding_extreme": r_f, "oi_divergence": r_o,
                        "liquidation_cascade": r_l})
        if not cf.is_tradeable:
            continue
        for hb, hl in hz.items():
            fi = i + hb
            if fi >= len(candles):
                continue
            rr = (candles[fi]["close"] - c["close"]) / c["close"]
            if cf.direction == Direction.SHORT:
                rr = -rr
            for lv in levs:
                ret = rr * lv
                if fees:
                    ret -= RT_FEE * lv
                    ret -= lv * HOURLY_FUND * hb * p["hpb"]
                rmap[(hb, lv)].append(ret)

    dh = (candles[-1]["close_time_ms"] - candles[0]["open_time_ms"]) / 3.6e6 if len(candles) > 1 else 1.0
    out = []
    for (hb, lv), rets in rmap.items():
        hl = hz[hb]
        if not rets:
            out.append(HFTResult(coin, base_tf, hl, hb, pctl, ms, lv, fees,
                                 0, 0, 0, 0, 0, 0, 0, 0, 0, [], sfired))
            continue
        n = len(rets); hits = sum(1 for r in rets if r > 0)
        am = float(np.mean(rets)); md = float(np.median(rets))
        sd = float(np.std(rets)) if n > 1 else 1.0
        tpy = 365 * 24 / (hb * p["hpb"]) if hb * p["hpb"] > 0 else 1
        sh = am / sd * math.sqrt(tpy) if sd > 0 else 0.0
        tr = float(np.sum(rets))
        cum = np.cumsum(rets); pk = np.maximum.accumulate(cum)
        mdd = float(np.min(cum - pk)) * 100
        g = sum(r for r in rets if r > 0)
        lo = abs(sum(r for r in rets if r < 0))
        pf = g / lo if lo > 0 else (999 if g > 0 else 0)
        yr = dh / (365 * 24)
        anr = ((1 + tr) ** (1 / yr) - 1) * 100 if yr > 0 and tr > -1 else -100
        out.append(HFTResult(coin, base_tf, hl, hb, pctl, ms, lv, fees,
                             n, round(hits/n, 4), round(am*100, 4), round(md*100, 4),
                             round(sh, 2), round(mdd, 2), round(min(pf, 999), 2),
                             round(tr*100, 2), round(anr, 2), rets, sfired))
    return out


def monte_carlo(returns, n_sims=500, n_trades=200):
    if len(returns) < 10:
        return {"error": "Insufficient", "n": len(returns)}
    rng = np.random.RandomState(42)
    F, D, S = [], [], []
    nt = min(n_trades, len(returns))
    for _ in range(n_sims):
        s = rng.choice(returns, size=nt, replace=True)
        cum = np.cumsum(s); F.append(float(cum[-1]) * 100)
        pk = np.maximum.accumulate(cum)
        D.append(float(np.min(cum - pk)))
        m = float(np.mean(s)); st = float(np.std(s))
        S.append(m / st * math.sqrt(252 * 6) if st > 0 else 0)
    return {
        "n_sims": n_sims, "n_trades": nt,
        "med_ret%": round(float(np.median(F)), 2),
        "mean_ret%": round(float(np.mean(F)), 2),
        "p5_ret%": round(float(np.percentile(F, 5)), 2),
        "p95_ret%": round(float(np.percentile(F, 95)), 2),
        "med_dd%": round(float(np.median(D)) * 100, 2),
        "worst_dd%": round(float(np.min(D)) * 100, 2),
        "med_sharpe": round(float(np.median(S)), 2),
        "prob+": round(sum(1 for r in F if r > 0) / n_sims, 3),
        "ruin15%": round(sum(1 for d in D if d < -0.15) / n_sims, 3),
    }
