"""
Confluence Backtester — Real Data

Tests the multi-signal confluence strategy against REAL Hyperliquid data.
Signals that CAN be backtested historically:
  1. Funding Rate Extremes (P99 mean-reversion)
  2. OI Divergence (volume+price proxy from candles)
  3. Liquidation Cascade Proxy (price-action patterns from candles)

Signal that CANNOT be backtested (needs forward data):
  4. Order Book Imbalance (L2 data — no historical source)

The backtester runs the three historical signals at each 1h candle,
feeds them through the confluence engine, and evaluates the resulting
predictions against actual future price movements.
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
    Direction, SignalResult,
    FundingRateExtremeSignal, OIDivergenceSignal, LiquidationCascadeProxySignal
)
from signals.confluence_engine import ConfluenceEngine, ConfluenceResult

logger = logging.getLogger(__name__)
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class TradeRecord:
    timestamp_ms: int
    direction: str
    confluence_score: float
    n_active: int
    agreement: float
    entry_price: float
    returns: dict[str, float] = field(default_factory=dict)  # horizon -> return
    active_signals: list[str] = field(default_factory=list)


def build_funding_lookup(funding_records: list[dict]) -> dict[int, list[dict]]:
    """Group funding records by hour timestamp for O(1) lookup."""
    lookup = {}
    for r in funding_records:
        hour_ts = (r["timestamp_ms"] // 3600000) * 3600000
        lookup.setdefault(hour_ts, []).append(r)
    return lookup


def run_confluence_backtest(
    coin: str,
    candles_1h: list[dict],
    funding_records: list[dict],
    horizons_hours: list[int] = None,
    funding_percentile: float = 99.0,
    min_confluence_score: float = 35.0,
    min_active_signals: int = 2,
    warmup_bars: int = 100,
) -> dict:
    """
    Run confluence backtest on real historical data.

    Uses 1h candles as the base timeframe. At each bar:
    1. Compute funding signal from recent funding history
    2. Compute OI divergence from volume+price
    3. Compute liquidation cascade proxy from price action
    4. Feed all three through confluence engine
    5. If confluence fires, record prediction and check future prices
    """
    if horizons_hours is None:
        horizons_hours = [1, 4, 8, 12, 24]

    # Initialize signals
    funding_sig = FundingRateExtremeSignal(
        percentile_threshold=funding_percentile, mode="classic")
    oi_sig = OIDivergenceSignal(
        volume_spike_percentile=90.0, volume_window=24, price_move_threshold=0.005)
    liq_sig = LiquidationCascadeProxySignal(
        wick_ratio_threshold=0.80, volatility_window=24,
        volatility_spike_percentile=95.0, cascade_lookback=3)
    engine = ConfluenceEngine(
        min_score=min_confluence_score, min_active_signals=min_active_signals)

    # Build funding lookup and pre-sort rates by timestamp
    funding_lookup = build_funding_lookup(funding_records)
    all_rates_sorted = sorted(funding_records, key=lambda r: r["timestamp_ms"])
    all_rate_values = [r["funding_rate"] for r in all_rates_sorted]
    all_rate_times = [r["timestamp_ms"] for r in all_rates_sorted]

    trades: list[TradeRecord] = []
    signals_fired = {"funding_extreme": 0, "oi_divergence": 0, "liquidation_cascade": 0}
    total_bars = 0

    max_horizon = max(horizons_hours)
    funding_ptr = 0  # Pointer into sorted funding records (monotonically advancing)

    for i in range(warmup_bars, len(candles_1h) - max_horizon):
        total_bars += 1
        candle = candles_1h[i]
        recent = candles_1h[max(0, i - 48):i]  # 48h lookback for context

        # 1. Funding signal — advance pointer to current candle time
        while funding_ptr < len(all_rate_times) and all_rate_times[funding_ptr] <= candle["close_time_ms"]:
            funding_ptr += 1
        # rates_so_far is all_rate_values[:funding_ptr] — use slice for rolling window
        window_start = max(0, funding_ptr - 2000)
        rates_window = all_rate_values[window_start:funding_ptr]

        current_rate = all_rate_values[funding_ptr - 1] if funding_ptr > 0 else 0.0
        hour_ts = (candle["open_time_ms"] // 3600000) * 3600000
        hour_funding = funding_lookup.get(hour_ts, [])
        if hour_funding:
            current_rate = hour_funding[-1]["funding_rate"]

        funding_result = funding_sig.evaluate_from_history(current_rate, rates_window)

        # 2. OI divergence (volume+price proxy)
        oi_result = oi_sig.evaluate_from_candles(candle, recent)

        # 3. Liquidation cascade proxy
        liq_result = liq_sig.evaluate_from_candles(candle, recent)

        # Track individual signal fires
        if funding_result.is_active: signals_fired["funding_extreme"] += 1
        if oi_result.is_active: signals_fired["oi_divergence"] += 1
        if liq_result.is_active: signals_fired["liquidation_cascade"] += 1

        # 4. Confluence scoring
        sig_dict = {
            "funding_extreme": funding_result,
            "oi_divergence": oi_result,
            "liquidation_cascade": liq_result,
        }
        confluence = engine.score(sig_dict)

        if not confluence.is_tradeable:
            continue

        # 5. Record trade and evaluate future returns
        trade = TradeRecord(
            timestamp_ms=candle["open_time_ms"],
            direction=confluence.direction.value,
            confluence_score=confluence.score,
            n_active=confluence.n_signals_active,
            agreement=confluence.signal_agreement,
            entry_price=candle["close"],
            active_signals=confluence.metadata.get("active", []),
        )

        for h in horizons_hours:
            future_idx = i + h
            if future_idx < len(candles_1h):
                future_price = candles_1h[future_idx]["close"]
                price_return = (future_price - candle["close"]) / candle["close"]
                if confluence.direction == Direction.SHORT:
                    price_return = -price_return
                trade.returns[f"{h}h"] = price_return

        trades.append(trade)

    return {
        "coin": coin,
        "total_bars": total_bars,
        "total_trades": len(trades),
        "signals_fired": signals_fired,
        "trades": trades,
        "params": {
            "funding_percentile": funding_percentile,
            "min_confluence_score": min_confluence_score,
            "min_active_signals": min_active_signals,
        }
    }


def analyze_results(result, horizons=None):
    """Compute statistics from backtest results."""
    trades = result["trades"]
    if not trades:
        return {"error": "No trades", "coin": result["coin"]}
    if horizons is None:
        horizons = ["1h", "4h", "8h", "12h", "24h"]
    stats = {"coin": result["coin"], "total_trades": len(trades),
             "params": result["params"], "signals_fired": result["signals_fired"]}
    for h in horizons:
        rets = [t.returns.get(h) for t in trades if t.returns.get(h) is not None]
        if not rets:
            stats[h] = {"trades": 0}; continue
        hits = sum(1 for r in rets if r > 0)
        avg = float(np.mean(rets)); med = float(np.median(rets))
        std = float(np.std(rets)) if len(rets) > 1 else 1.0
        ppy = 365 * 24 / int(h.replace("h", ""))
        sharpe = (avg / std * math.sqrt(ppy)) if std > 0 else 0
        cum = np.cumsum(rets); pk = np.maximum.accumulate(cum)
        dd = cum - pk; max_dd = float(np.min(dd)) * 100 if len(dd) > 0 else 0
        gains = sum(r for r in rets if r > 0)
        losses = abs(sum(r for r in rets if r < 0))
        pf = gains / losses if losses > 0 else (999 if gains > 0 else 0)
        stats[h] = {"trades": len(rets), "hit_rate": round(hits/len(rets), 4),
            "avg_return_pct": round(avg*100, 4), "median_return_pct": round(med*100, 4),
            "sharpe": round(sharpe, 2), "max_drawdown_pct": round(max_dd, 2),
            "profit_factor": round(min(pf, 999), 2)}
    scores = [t.confluence_score for t in trades]
    stats["score_dist"] = {"min": round(min(scores),1), "max": round(max(scores),1),
        "mean": round(float(np.mean(scores)),1)}
    longs = sum(1 for t in trades if t.direction == "long")
    stats["dir_split"] = {"long": longs, "short": len(trades)-longs}
    return stats


def run_monte_carlo(trades, horizon="4h", n_sims=1000, n_trades=100):
    rets = [t.returns.get(horizon) for t in trades if t.returns.get(horizon) is not None]
    if len(rets) < 10:
        return {"error": "Insufficient", "n_actual": len(rets)}
    rng = np.random.RandomState(42)
    finals=[]; dds=[]; sharpes=[]
    for _ in range(n_sims):
        s = rng.choice(rets, size=min(n_trades, len(rets)), replace=True)
        cum = np.cumsum(s); finals.append(float(cum[-1])*100)
        pk = np.maximum.accumulate(cum); dds.append(float(np.min(cum-pk))*100)
        mn=float(np.mean(s)); st=float(np.std(s))
        sharpes.append(mn/st*math.sqrt(252*6) if st>0 else 0)
    return {"n_sims": n_sims, "n_trades": min(n_trades, len(rets)),
        "median_return_pct": round(float(np.median(finals)),2),
        "p5_return_pct": round(float(np.percentile(finals,5)),2),
        "p95_return_pct": round(float(np.percentile(finals,95)),2),
        "median_max_dd_pct": round(float(np.median(dds)),2),
        "worst_max_dd_pct": round(float(np.min(dds)),2),
        "median_sharpe": round(float(np.median(sharpes)),2),
        "prob_positive": round(sum(1 for r in finals if r>0)/n_sims,3),
        "prob_ruin_15pct": round(sum(1 for d in dds if d<-15)/n_sims,3)}


def run_stability(coin, c1h, fund, **kw):
    mid = len(c1h) // 2
    is_r = run_confluence_backtest(coin, c1h[:mid], fund, **kw)
    oos_r = run_confluence_backtest(coin, c1h[mid:], fund, **kw)
    return {"in_sample": analyze_results(is_r), "out_of_sample": analyze_results(oos_r)}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("=" * 80)
    print("CONFLUENCE BACKTESTER - Real Hyperliquid Data")
    print("Signals: Funding P99 + OI Divergence + Liquidation Cascade Proxy")
    print("=" * 80)

    assets = ["BTC", "ETH", "SOL"]
    horizons = [1, 4, 8, 12, 24]
    all_results = {}; all_mc = {}; all_stab = {}

    for coin in assets:
        print(f"\n{'#'*60}\n# {coin}\n{'#'*60}")
        try:
            fund = load_funding_csv(coin)
            c1h = load_candles_csv(coin, "1h")
        except FileNotFoundError as e:
            print(f"  SKIP: {e}"); continue
        print(f"  Data: {len(fund)} funding, {len(c1h)} 1h candles")

        for ms in [25, 35, 45]:
            print(f"\n  --- min_score={ms} ---")
            r = run_confluence_backtest(coin, c1h, fund, horizons_hours=horizons,
                funding_percentile=99.0, min_confluence_score=ms, min_active_signals=2)
            s = analyze_results(r)
            all_results[f"{coin}_ms{ms}"] = s
            print(f"  Trades: {s.get('total_trades',0)}")
            print(f"  Signals fired: {s.get('signals_fired', {})}")
            if s.get("total_trades", 0) > 0:
                for h in ["1h","4h","12h","24h"]:
                    hs = s.get(h, {})
                    if hs.get("trades",0) > 0:
                        print(f"  {h}: {hs['trades']}t hit={hs['hit_rate']:.1%} "
                              f"avg={hs['avg_return_pct']:+.4f}% sh={hs['sharpe']:.2f}")

        # MC on threshold 35
        r35 = run_confluence_backtest(coin, c1h, fund, horizons_hours=horizons,
            funding_percentile=99.0, min_confluence_score=35, min_active_signals=2)
        if r35["total_trades"] >= 10:
            for h in ["4h","12h","24h"]:
                mc = run_monte_carlo(r35["trades"], horizon=h)
                all_mc[f"{coin}_{h}"] = mc
                if "error" not in mc:
                    print(f"  MC {h}: med={mc['median_return_pct']}% p+={mc['prob_positive']} "
                          f"ruin={mc['prob_ruin_15pct']}")

        # Stability
        print(f"\n  --- Stability ---")
        stab = run_stability(coin, c1h, fund, horizons_hours=horizons,
            funding_percentile=99.0, min_confluence_score=35, min_active_signals=2)
        all_stab[coin] = stab
        for ph in ["in_sample", "out_of_sample"]:
            ps = stab[ph]
            h4 = ps.get("4h", {})
            print(f"  {ph}: {ps.get('total_trades',0)}t 4h_hit={h4.get('hit_rate','N/A')} "
                  f"sh={h4.get('sharpe','N/A')}")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"confluence_backtest_{ts}.json"
    path.write_text(json.dumps({"results": all_results, "mc": all_mc,
        "stability": {c: s for c, s in all_stab.items()}}, indent=2, default=str))
    print(f"\nResults saved: {path}")

    # Generate report
    _write_report(all_results, all_mc, all_stab, ts)


def _write_report(results, mc, stability, ts):
    """Write Go/No-Go markdown report."""
    lines = [f"# Confluence Go/No-Go Report", f"*{ts}*\n",
        "## Strategy: 3-Signal Confluence (Funding P99 + OI Div + Liq Cascade)",
        "4th signal (Order Book Imbalance) needs forward L2 data.\n",
        "## Results\n",
        "| Key | Trades | 4h Hit | 4h Sharpe | 12h Hit | 24h Hit | 24h Sharpe |",
        "|-----|--------|--------|-----------|---------|---------|------------|"]
    for k, s in sorted(results.items()):
        h4=s.get("4h",{}); h12=s.get("12h",{}); h24=s.get("24h",{})
        lines.append(f"| {k} | {s.get('total_trades',0)} | "
            f"{h4.get('hit_rate','N/A')} | {h4.get('sharpe','N/A')} | "
            f"{h12.get('hit_rate','N/A')} | {h24.get('hit_rate','N/A')} | "
            f"{h24.get('sharpe','N/A')} |")

    lines.extend(["\n## Monte Carlo (ms=35)\n",
        "| Key | Med Ret% | Prob+ | Ruin15% | Med Sharpe |",
        "|-----|----------|-------|---------|------------|"])
    for k, m in sorted(mc.items()):
        if "error" in m: continue
        lines.append(f"| {k} | {m.get('median_return_pct','N/A')} | "
            f"{m.get('prob_positive','N/A')} | {m.get('prob_ruin_15pct','N/A')} | "
            f"{m.get('median_sharpe','N/A')} |")

    lines.extend(["\n## Stability\n",
        "| Asset | Phase | Trades | 4h Hit | 4h Sharpe |",
        "|-------|-------|--------|--------|-----------|"])
    for c, st in sorted(stability.items()):
        for ph in ["in_sample","out_of_sample"]:
            ps = st[ph]; h4 = ps.get("4h",{})
            lines.append(f"| {c} | {ph} | {ps.get('total_trades',0)} | "
                f"{h4.get('hit_rate','N/A')} | {h4.get('sharpe','N/A')} |")

    # Verdict
    has_edge = any(s.get("4h",{}).get("hit_rate",0)>0.55 and s.get("4h",{}).get("sharpe",0)>1
                   for s in results.values())
    stable = all(
        st["out_of_sample"].get("4h",{}).get("sharpe",0) > st["in_sample"].get("4h",{}).get("sharpe",0)*0.3
        for st in stability.values()
        if isinstance(st["in_sample"].get("4h",{}).get("sharpe",0),(int,float))
        and isinstance(st["out_of_sample"].get("4h",{}).get("sharpe",0),(int,float))
        and st["in_sample"].get("4h",{}).get("trades",0) > 0
    ) if stability else False

    lines.append("\n## Verdict\n")
    if has_edge and stable:
        lines.append("### CONDITIONAL GO")
        lines.append("Proceed to paper trading. Add L2 book signal when data available.")
    elif has_edge:
        lines.append("### CONDITIONAL - Unstable")
        lines.append("Edge hints exist but degrade out-of-sample. Collect more data.")
    else:
        lines.append("### NO-GO (3-signal confluence alone)")
        lines.append("No consistent edge from historically-testable signals.")
        lines.append("\n**Next steps:**")
        lines.append("1. Start forward data collectors (liquidation WS + L2 snapshots)")
        lines.append("2. Collect 2-4 weeks of live data")
        lines.append("3. Re-test with all 4 signals")
        lines.append("4. L2 book imbalance may be the missing piece")

    lines.extend(["\n## Validated vs Forward\n",
        "### Historically Validated", "- Funding P99 Mean-Reversion",
        "- OI Divergence Proxy", "- Liquidation Cascade Proxy\n",
        "### Needs Forward Data", "- Order Book Imbalance (L2)",
        "- Real Liquidation Events (WebSocket)"])

    report = "\n".join(lines)
    rp = RESULTS_DIR / f"go_no_go_confluence_{ts}.md"
    rp.write_text(report)
    (RESULTS_DIR / "go_no_go_confluence.md").write_text(report)
    print(f"Report: {rp}")


if __name__ == "__main__":
    main()
