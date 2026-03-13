"""
Real Data Backtester — v2

Tests signal predictions against REAL Hyperliquid historical data.

CRITICAL FINDING: Hyperliquid funding rates are hourly, not 8-hourly like
most exchanges. Individual rates are ~1/8th of what Binance reports.
- BTC median abs rate: 0.0000125 (1.25 bps)
- P95 abs rate: ~0.00007 (7 bps)
- P99: ~0.00013 (13 bps)

This means our original 0.001 (100 bps) threshold was absurdly high for HL.
We need to adapt thresholds to actual data distribution.

Approach: Use percentile-based thresholds (P90, P95, P99 of abs funding rate)
to define "extreme" dynamically.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from intelligence.historical_data import load_funding_csv, load_candles_csv
from signals.signal_definitions import Direction

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class BacktestResult:
    signal_name: str
    asset: str
    horizon_bars: int
    horizon_label: str
    total_predictions: int
    hit_rate: float
    avg_return_pct: float
    median_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: float
    returns: list[float] = field(default_factory=list, repr=False)


def align_funding_to_candles(funding: list[dict], candles: list[dict]) -> dict[int, list[dict]]:
    """Map funding records into 4h candle windows."""
    aligned: dict[int, list[dict]] = {}
    f_idx = 0
    for c in candles:
        o, cl = c["open_time_ms"], c["close_time_ms"]
        period = []
        while f_idx < len(funding) and funding[f_idx]["timestamp_ms"] < o:
            f_idx += 1
        j = f_idx
        while j < len(funding) and funding[j]["timestamp_ms"] <= cl:
            period.append(funding[j])
            j += 1
        if period:
            aligned[o] = period
    return aligned


def run_funding_backtest(
    coin: str,
    candles: list[dict],
    aligned_funding: dict[int, list[dict]],
    all_funding: list[dict],
    horizons: list[int],
    percentile_threshold: float = 90.0,
    mode: str = "contrarian",
    use_rolling_threshold: bool = True,
    rolling_window: int = 2000,
) -> list[BacktestResult]:
    """
    Backtest funding rate signal on real data.
    
    Uses percentile-based thresholds: a rate is "extreme" if it exceeds
    the Nth percentile of recent absolute rates.
    
    mode: "contrarian" = trade WITH funding direction
          "classic" = trade AGAINST funding direction
    """
    horizon_labels = {1: "4h", 2: "8h", 3: "12h", 6: "24h", 12: "48h"}
    results_list: list[BacktestResult] = []
    
    # Pre-compute all abs rates for static threshold
    all_rates = [abs(r["funding_rate"]) for r in all_funding]
    static_threshold = float(np.percentile(all_rates, percentile_threshold))
    
    returns_by_horizon: dict[int, list[float]] = {h: [] for h in horizons}
    signal_count = 0
    all_funding_so_far: list[float] = []
    
    for i, candle in enumerate(candles):
        open_ms = candle["open_time_ms"]
        period_funding = aligned_funding.get(open_ms, [])
        
        if not period_funding:
            continue
        
        # Add to rolling window
        for f in period_funding:
            all_funding_so_far.append(f["funding_rate"])
        
        # Use last rate in period
        current_rate = period_funding[-1]["funding_rate"]
        
        # Determine threshold
        if use_rolling_threshold and len(all_funding_so_far) >= rolling_window:
            recent = all_funding_so_far[-rolling_window:]
            threshold = float(np.percentile([abs(r) for r in recent], percentile_threshold))
        else:
            threshold = static_threshold
        
        # Is this extreme?
        if abs(current_rate) < threshold:
            continue
        
        signal_count += 1
        
        # Determine direction
        if mode == "contrarian":
            direction = Direction.LONG if current_rate > 0 else Direction.SHORT
        else:
            direction = Direction.SHORT if current_rate > 0 else Direction.LONG
        
        # Confidence: how extreme relative to threshold (simple linear scale, capped)
        confidence = min(1.0, abs(current_rate) / threshold - 0.5)
        confidence = max(0.1, confidence)  # Floor at 0.1
        
        for h in horizons:
            future_idx = i + h
            if future_idx >= len(candles):
                continue
            
            future_price = candles[future_idx]["close"]
            current_price = candle["close"]
            price_return = (future_price - current_price) / current_price
            
            if direction == Direction.LONG:
                signed_return = price_return
            else:
                signed_return = -price_return
            
            returns_by_horizon[h].append(signed_return)
    
    logger.info("  %s %s: %d signals fired (threshold=%.8f at P%d)",
               coin, mode, signal_count, static_threshold, int(percentile_threshold))
    
    for h in horizons:
        rets = returns_by_horizon[h]
        if not rets:
            results_list.append(BacktestResult(
                f"funding_{mode}", coin, h, horizon_labels.get(h, f"{h*4}h"),
                0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            ))
            continue
        
        hits = sum(1 for r in rets if r > 0)
        avg_ret = float(np.mean(rets)) * 100
        median_ret = float(np.median(rets)) * 100
        std_ret = float(np.std(rets)) if len(rets) > 1 else 1.0
        periods_per_year = 365 * 24 / (h * 4)
        sharpe = (float(np.mean(rets)) / std_ret * math.sqrt(periods_per_year)) if std_ret > 0 else 0.0
        
        cumulative = np.cumsum(rets)
        peak = np.maximum.accumulate(cumulative)
        drawdown = cumulative - peak
        max_dd = float(np.min(drawdown)) * 100 if len(drawdown) > 0 else 0.0
        
        # Profit factor
        gains = sum(r for r in rets if r > 0)
        losses = abs(sum(r for r in rets if r < 0))
        pf = gains / losses if losses > 0 else float('inf') if gains > 0 else 0.0
        
        results_list.append(BacktestResult(
            f"funding_{mode}", coin, h, horizon_labels.get(h, f"{h*4}h"),
            len(rets), round(hits / len(rets), 4),
            round(avg_ret, 4), round(median_ret, 4),
            round(sharpe, 2), round(max_dd, 2),
            round(pf, 2), rets,
        ))
    
    return results_list


def run_monte_carlo(returns: list[float], n_sims: int = 1000, n_trades: int = 100) -> dict:
    if len(returns) < 10:
        return {"error": "Insufficient data", "n_actual": len(returns)}
    rng = np.random.RandomState(42)
    final_returns = []
    max_drawdowns = []
    sharpes = []
    for _ in range(n_sims):
        sampled = rng.choice(returns, size=min(n_trades, len(returns)), replace=True)
        cumulative = np.cumsum(sampled)
        final_returns.append(float(cumulative[-1]) * 100)
        peak = np.maximum.accumulate(cumulative)
        dd = cumulative - peak
        max_drawdowns.append(float(np.min(dd)) * 100)
        mean_r = float(np.mean(sampled))
        std_r = float(np.std(sampled))
        sharpes.append(mean_r / std_r * math.sqrt(252 * 6) if std_r > 0 else 0)
    return {
        "n_sims": n_sims,
        "n_trades": min(n_trades, len(returns)),
        "median_return_pct": round(float(np.median(final_returns)), 2),
        "mean_return_pct": round(float(np.mean(final_returns)), 2),
        "p5_return_pct": round(float(np.percentile(final_returns, 5)), 2),
        "p95_return_pct": round(float(np.percentile(final_returns, 95)), 2),
        "median_max_dd_pct": round(float(np.median(max_drawdowns)), 2),
        "worst_max_dd_pct": round(float(np.min(max_drawdowns)), 2),
        "median_sharpe": round(float(np.median(sharpes)), 2),
        "prob_positive": round(sum(1 for r in final_returns if r > 0) / n_sims, 3),
        "prob_ruin_15pct": round(sum(1 for dd in max_drawdowns if dd < -15) / n_sims, 3),
    }


def print_results(results: list[BacktestResult]) -> None:
    print(f"\n{'='*110}")
    print(f"  {'Signal':<22} {'Asset':<6} {'Horiz':<7} {'Trades':<7} {'Hit%':<8} "
          f"{'AvgRet%':<10} {'MedRet%':<10} {'Sharpe':<8} {'MaxDD%':<8} {'PF':<6}")
    print(f"{'='*110}")
    for r in sorted(results, key=lambda x: (x.signal_name, x.asset, x.horizon_bars)):
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 100 else "inf"
        print(f"  {r.signal_name:<22} {r.asset:<6} {r.horizon_label:<7} {r.total_predictions:<7} "
              f"{r.hit_rate:>6.1%}  {r.avg_return_pct:>+9.4f}  {r.median_return_pct:>+9.4f}  "
              f"{r.sharpe:>6.2f}  {r.max_drawdown_pct:>7.2f}  {pf_str:>5}")
    print(f"{'='*110}")


def save_results(results: list[BacktestResult], label: str = "real_backtest") -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{label}_{ts}.json"
    out = []
    for r in results:
        out.append({
            "signal": r.signal_name, "asset": r.asset,
            "horizon": r.horizon_label, "horizon_bars": r.horizon_bars,
            "trades": r.total_predictions, "hit_rate": r.hit_rate,
            "avg_return_pct": r.avg_return_pct, "median_return_pct": r.median_return_pct,
            "sharpe": r.sharpe, "max_drawdown_pct": r.max_drawdown_pct,
            "profit_factor": r.profit_factor,
        })
    path.write_text(json.dumps(out, indent=2))
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=" * 70)
    print("REAL DATA BACKTEST v2 — Hyperliquid Historical Data")
    print("Adaptive percentile-based thresholds")
    print("=" * 70)

    assets = ["BTC", "ETH", "SOL"]
    horizons = [1, 2, 3, 6]  # 4h, 8h, 12h, 24h
    all_results: list[BacktestResult] = []

    for percentile in [90, 95, 99]:
        print(f"\n{'#'*70}")
        print(f"# PERCENTILE THRESHOLD: P{percentile}")
        print(f"{'#'*70}")

        contrarian_results = []
        classic_results = []

        for coin in assets:
            try:
                funding = load_funding_csv(coin)
                candles = load_candles_csv(coin, "4h")
            except FileNotFoundError as e:
                logger.warning("Skipping %s: %s", coin, e)
                continue

            aligned = align_funding_to_candles(funding, candles)

            for mode, result_list in [("contrarian", contrarian_results), ("classic", classic_results)]:
                res = run_funding_backtest(
                    coin, candles, aligned, funding, horizons,
                    percentile_threshold=percentile,
                    mode=mode,
                )
                result_list.extend(res)

        print(f"\n--- P{percentile} CONTRARIAN (trade WITH funding) ---")
        print_results(contrarian_results)
        print(f"\n--- P{percentile} CLASSIC (trade AGAINST funding) ---")
        print_results(classic_results)

        all_results.extend(contrarian_results)
        all_results.extend(classic_results)

    # Monte Carlo on P90 contrarian (most trades)
    print(f"\n{'#'*70}")
    print("# MONTE CARLO ANALYSIS — P90 Contrarian, 24h horizon")
    print(f"{'#'*70}")

    for coin in assets:
        try:
            funding = load_funding_csv(coin)
            candles = load_candles_csv(coin, "4h")
            aligned = align_funding_to_candles(funding, candles)
            res = run_funding_backtest(coin, candles, aligned, funding, [6],
                                       percentile_threshold=90, mode="contrarian")
            for r in res:
                if r.returns and len(r.returns) >= 10:
                    mc = run_monte_carlo(r.returns, n_sims=1000, n_trades=100)
                    print(f"\n  {coin} ({r.total_predictions} trades):")
                    for k, v in mc.items():
                        print(f"    {k}: {v}")
                else:
                    print(f"\n  {coin}: {r.total_predictions} trades — insufficient for MC")
        except FileNotFoundError:
            continue

    # Stability: out-of-sample split
    print(f"\n{'#'*70}")
    print("# STABILITY: IN-SAMPLE vs OUT-OF-SAMPLE (50/50 split)")
    print(f"{'#'*70}")

    for coin in assets:
        try:
            funding = load_funding_csv(coin)
            candles = load_candles_csv(coin, "4h")
        except FileNotFoundError:
            continue

        mid = len(candles) // 2
        candles_is = candles[:mid]
        candles_oos = candles[mid:]

        aligned_is = align_funding_to_candles(funding, candles_is)
        aligned_oos = align_funding_to_candles(funding, candles_oos)

        for label, c, a in [("IN-SAMPLE", candles_is, aligned_is),
                            ("OUT-OF-SAMPLE", candles_oos, aligned_oos)]:
            res = run_funding_backtest(coin, c, a, funding, [6],
                                       percentile_threshold=90, mode="contrarian")
            for r in res:
                print(f"  {coin} {label}: {r.total_predictions} trades, "
                      f"hit={r.hit_rate:.1%}, avg={r.avg_return_pct:+.4f}%, "
                      f"sharpe={r.sharpe:.2f}")

    path = save_results(all_results, "real_data_backtest_v2")
    print(f"\nAll results saved to {path}")


if __name__ == "__main__":
    main()