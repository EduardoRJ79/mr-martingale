#!/usr/bin/env python3
"""Run HFT Analysis — all backtests, Monte Carlo, output JSON."""
from __future__ import annotations
import json, logging, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from intelligence.historical_data import load_funding_csv, load_candles_csv
from signals.hft_backtester import backtest, monte_carlo, HORIZONS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ASSETS = ["BTC", "ETH", "SOL"]
TFS = ["5m", "15m", "1h"]
PCTLS = [95.0, 97.0, 99.0]
MSS = [15.0, 25.0]
LEVS = [1.0, 2.0, 3.0]


def main():
    print("=" * 70)
    print("HFT TIMEFRAME + LEVERAGE ANALYSIS")
    print("=" * 70)

    # Load data
    data = {}
    for coin in ASSETS:
        data[coin] = {"funding": load_funding_csv(coin)}
        for tf in TFS:
            try:
                c = load_candles_csv(coin, tf)
                data[coin][tf] = c
                print(f"  {coin} {tf}: {len(c)} candles "
                      f"({c[0]['datetime_utc']} to {c[-1]['datetime_utc']})")
            except FileNotFoundError:
                data[coin][tf] = None
                print(f"  {coin} {tf}: NOT AVAILABLE")

    # Run all backtests
    print("\n--- Running backtests ---")
    all_rows = []
    for coin in ASSETS:
        funding = data[coin]["funding"]
        for tf in TFS:
            candles = data[coin].get(tf)
            if not candles:
                continue
            for pctl in PCTLS:
                for ms in MSS:
                    for fees in [True, False]:
                        res = backtest(coin, candles, funding, tf,
                                       pctl=pctl, ms=ms, levs=LEVS, fees=fees)
                        for r in res:
                            all_rows.append({
                                "coin": r.coin, "tf": r.base_tf,
                                "horizon": r.horizon_label, "hbars": r.horizon_bars,
                                "pctl": r.percentile, "ms": r.min_score,
                                "lev": r.leverage, "fees": r.include_fees,
                                "trades": r.total_trades, "hit": r.hit_rate,
                                "avg_ret": r.avg_return_pct, "med_ret": r.median_return_pct,
                                "sharpe": r.sharpe, "mdd": r.max_drawdown_pct,
                                "pf": r.profit_factor, "tot_ret": r.total_return_pct,
                                "ann_ret": r.ann_return_pct,
                                "sig_fired": r.signals_fired,
                                "_returns": r.returns,
                            })
                    n = len([r for r in all_rows if r["coin"] == coin and r["tf"] == tf
                             and r["pctl"] == pctl and r["ms"] == ms and r["fees"]
                             and r["lev"] == 1.0])
                    trades_str = ""
                    for r in all_rows:
                        if (r["coin"] == coin and r["tf"] == tf and r["pctl"] == pctl
                            and r["ms"] == ms and r["fees"] and r["lev"] == 1.0
                            and r["hbars"] == 1):
                            trades_str = f"{r['trades']} trades"
                            break
                    print(f"  {coin} {tf} P{pctl:.0f} ms={ms:.0f}: {trades_str}")

    # Monte Carlo on best configs (with fees, >= 20 trades)
    print("\n--- Monte Carlo ---")
    candidates = [r for r in all_rows if r["fees"] and r["trades"] >= 20
                  and r["avg_ret"] > 0]
    candidates.sort(key=lambda r: r["sharpe"], reverse=True)
    mc_results = {}
    for r in candidates[:15]:
        key = f"{r['coin']}_{r['tf']}_{r['horizon']}_P{r['pctl']:.0f}_ms{r['ms']:.0f}_{r['lev']:.0f}x"
        if key not in mc_results and len(r["_returns"]) >= 10:
            mc = monte_carlo(r["_returns"], n_sims=500)
            mc_results[key] = {"cfg": {k: v for k, v in r.items() if k != "_returns"}, "mc": mc}
            print(f"  {key}: med_ret={mc.get('med_ret%', 'N/A')}% "
                  f"prob+={mc.get('prob+', 'N/A')} ruin={mc.get('ruin15%', 'N/A')}")

    # Save raw JSON (without _returns for size)
    out = {"timestamp": datetime.now(timezone.utc).isoformat(),
           "results": [{k: v for k, v in r.items() if k != "_returns"} for r in all_rows],
           "monte_carlo": mc_results}
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"hft_analysis_{ts}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved: {path}")

    # Print summary tables
    print_summary(all_rows, mc_results)
    return all_rows, mc_results


def print_summary(rows, mc):
    print("\n" + "=" * 120)
    print("SUMMARY: Best configs WITH FEES (>= 10 trades, sorted by Sharpe)")
    print("=" * 120)
    print(f"{'Coin':<5} {'TF':<4} {'Hz':<5} {'P':>3} {'MS':>3} {'Lv':>3} "
          f"{'Trd':>5} {'Hit%':>6} {'AvgR%':>8} {'Shrp':>6} {'MDD%':>7} {'PF':>5} {'TotR%':>7}")
    print("-" * 120)
    good = sorted([r for r in rows if r["fees"] and r["trades"] >= 10],
                  key=lambda x: x["sharpe"], reverse=True)
    for r in good[:40]:
        pf = f"{r['pf']:.1f}" if r["pf"] < 100 else "inf"
        print(f"{r['coin']:<5} {r['tf']:<4} {r['horizon']:<5} {r['pctl']:>3.0f} "
              f"{r['ms']:>3.0f} {r['lev']:>2.0f}x {r['trades']:>5} {r['hit']:>5.1%} "
              f"{r['avg_ret']:>+7.3f} {r['sharpe']:>6.2f} {r['mdd']:>7.2f} {pf:>5} "
              f"{r['tot_ret']:>+7.2f}")

    # Trade count comparison
    print(f"\n{'='*70}")
    print("TRADE COUNT BY TIMEFRAME (P95 ms=15 lev=1x fees=True, shortest horizon)")
    print(f"{'='*70}")
    for coin in ASSETS:
        parts = []
        for tf in TFS:
            match = [r for r in rows if r["coin"] == coin and r["tf"] == tf
                     and r["pctl"] == 95 and r["ms"] == 15 and r["lev"] == 1.0
                     and r["fees"] and r["hbars"] == 1]
            t = match[0]["trades"] if match else 0
            parts.append(f"{tf}={t}")
        print(f"  {coin}: {', '.join(parts)}")


if __name__ == "__main__":
    main()
