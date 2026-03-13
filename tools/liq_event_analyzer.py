"""
Liquidation Event Analyzer — Mr Martingale
==========================================
Deep-dives into the 33 worst liquidation events from the 2018-present BTC
5m backtests. Analyzes long and short separately.

Outputs a Markdown report.
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# ─── Paths ───────────────────────────────────────────────────────────────
BASE         = Path(__file__).parent.parent
RESULTS_DIR  = BASE / "signals" / "multi_asset_results"
REPORTS_DIR  = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

LIQ_JSON     = RESULTS_DIR / "btc_liquidation_events_2018plus_5m_wick_1000usd.json"
PARQUET_FILE = RESULTS_DIR / "btcusdt_spot_5m_2018_plus_cached_with_ma.parquet"

REGIME_MAP = {
    "2018-01": "2018 Bear Market (Jan selloff)",
    "2018-02": "2018 Bear Market (Feb drop)",
    "2018-03": "2018 Bear Market (March flush)",
    "2018-06": "2018 Bear Market (summer grind)",
    "2018-11": "2018 Bear Market (Nov capitulation)",
    "2019-05": "2019 Bull Run (BTC pump $5k→$14k)",
    "2019-06": "2019 Bull Run (BTC spike to $14k)",
    "2019-09": "2019 Post-peak fade",
    "2020-03": "COVID Crash (BTC -60% in 2 days)",
    "2020-04": "COVID Recovery (V-shape bounce)",
    "2020-09": "Sept 2020 Selloff",
    "2021-01": "Jan 2021 BTC ATH attempt",
    "2021-05": "May 2021 Crash (BTC -53%)",
    "2021-07": "July 2021 Recovery",
    "2021-12": "Dec 2021 post-ATH rollover",
    "2022-01": "Jan 2022 post-ATH fade",
    "2022-02": "Feb 2022 Ukraine/rates",
    "2022-05": "LUNA/UST Collapse",
    "2022-06": "3AC/Celsius contagion",
    "2022-11": "FTX Collapse",
    "2023-01": "Jan 2023 post-FTX rally",
    "2024-02": "Feb 2024 BTC new ATH push",
    "2024-08": "Aug 2024 Yen carry unwind",
}


def get_regime(ts_str):
    key = ts_str[:7]
    return REGIME_MAP.get(key, "Unknown regime")


def classify_cause(event):
    equity_before = event["equity_before"]
    side = event.get("side", "long")
    blended = event["blended"]
    bar_low = event["bar_low"]
    bar_high = event["bar_high"]
    bar_open = event["bar_open"]

    if equity_before < 5:
        return "SEQUENTIAL_LIQ: Account near-zero from prior events"
    if equity_before < 20:
        return "CHAIN_LIQ: Small equity from cascade"

    if side == "long":
        wick_pct = (bar_open - bar_low) / bar_open * 100 if bar_open > 0 else 0
        depth_below_blended = (blended - bar_low) / blended * 100
    else:
        wick_pct = (bar_high - bar_open) / bar_open * 100 if bar_open > 0 else 0
        depth_below_blended = (bar_high - blended) / blended * 100

    if wick_pct > 5:
        return f"DEEP_WICK: {wick_pct:.1f}% single-bar move from open"
    if depth_below_blended > 10:
        return f"TREND_PERSISTENCE: {depth_below_blended:.1f}% past blended (sustained move)"
    return f"MARGIN_EXHAUSTION: equity=${equity_before:.1f} couldn't sustain position"


def analyze_events(events):
    rows = []
    for e in events:
        side = e.get("side", "long")
        blended = e["blended"]
        bar_low = e["bar_low"]
        bar_high = e["bar_high"]
        bar_open = e["bar_open"]

        if side == "long":
            wick_below_blended = (blended - bar_low) / blended * 100
            wick_from_open = (bar_open - bar_low) / bar_open * 100 if bar_open > 0 else 0
            adverse_price = bar_low
        else:
            wick_below_blended = (bar_high - blended) / blended * 100
            wick_from_open = (bar_high - bar_open) / bar_open * 100 if bar_open > 0 else 0
            adverse_price = bar_high

        regime = get_regime(e["ts_mst"][:7])
        cause = classify_cause(e)

        rows.append({
            "date": e["ts_mst"][:10],
            "side": side.upper(),
            "equity_before": round(e["equity_before"], 2),
            "blended": round(blended, 0),
            "bar_open": round(bar_open, 0),
            "adverse_price": round(adverse_price, 0),
            "wick_below_blended_pct": round(wick_below_blended, 1),
            "wick_from_open_pct": round(wick_from_open, 1),
            "maint_threshold": round(e["maint_threshold"], 2),
            "equity_at_wick": round(e["equity_at_wick"], 2),
            "margin_deployed_usd": round(e["margin_used_usd"], 2),
            "notional_usd": round(e.get("notional_usd", e["margin_used_usd"] * 20), 2),
            "regime": regime,
            "cause": cause,
            "filled_levels": e["filled_levels"],
        })

    return pd.DataFrame(rows)


def generate_report(df):
    long_df = df[df["side"] == "LONG"]
    short_df = df[df["side"] == "SHORT"]

    seq_chain = df["cause"].str.startswith("SEQUENTIAL_LIQ").sum()
    chain = df["cause"].str.startswith("CHAIN_LIQ").sum()
    wick = df["cause"].str.startswith("DEEP_WICK").sum()
    trend = df["cause"].str.startswith("TREND_PERSISTENCE").sum()
    margin = df["cause"].str.startswith("MARGIN_EXHAUSTION").sum()

    worst = df.nlargest(1, "equity_before").iloc[0]
    spacing_resistant = df[df["equity_before"] > 20]

    lines = [
        "# Mr Martingale — Liquidation Event Deep Analysis",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d')}  ",
        "**Source:** BTC/USDT 5m Binance spot data (2018-01-03 → 2026-03-01)  ",
        "**Config:** $400 start | $6.4 base margin (1.6%, compounding) | 5L | 2x | 20x leverage  ",
        "**Level spacing (baseline):** [0.5%, 1.5%, 3.0%, 3.0%] gaps  ",
        "**Cumulative depths:** L2=0.5% | L3=2.0% | L4=5.0% | L5=8.0% below trigger  ",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"Total liquidation events in 8-year dataset: **{len(df)}** ({len(long_df)} LONG, {len(short_df)} SHORT)",
        "",
        "| Cause | Count | % |",
        "|-------|------:|---:|",
        f"| Sequential chain (equity <$5) | {seq_chain} | {seq_chain/len(df)*100:.0f}% |",
        f"| Chain liquidation (equity $5–$20) | {chain} | {chain/len(df)*100:.0f}% |",
        f"| Deep single-bar wick (>5% from open) | {wick} | {wick/len(df)*100:.0f}% |",
        f"| Trend persistence (>10% past blended) | {trend} | {trend/len(df)*100:.0f}% |",
        f"| Margin exhaustion | {margin} | {margin/len(df)*100:.0f}% |",
        "",
        f"**Critical insight:** {seq_chain + chain} of {len(df)} events ({(seq_chain + chain)/len(df)*100:.0f}%) occur when "
        f"the account has **already been cascaded to near-zero** by prior events in the same crash. "
        f"Only **{len(spacing_resistant)} events** have equity >$20 — these are the only ones "
        f"where wider L4/L5 spacing could realistically help.",
        "",
        "---",
        "",
        "## Long Liquidations — All 26 Events",
        "",
        "| Date | Equity $ | Blended | Adverse Low | Wick<br>Below Blended | Regime | Root Cause |",
        "|------|--------:|---------|------------|----------------------|--------|------------|",
    ]

    for _, r in long_df.iterrows():
        lines.append(
            f"| {r['date']} | ${r['equity_before']:,.1f} | ${r['blended']:,.0f} | "
            f"${r['adverse_price']:,.0f} | {r['wick_below_blended_pct']:.1f}% | "
            f"{r['regime']} | {r['cause'][:45]} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Short Liquidations — All 7 Events",
        "",
        "| Date | Equity $ | Blended | Adverse High | Wick<br>Above Blended | Regime | Root Cause |",
        "|------|--------:|---------|-------------|----------------------|--------|------------|",
    ]

    for _, r in short_df.iterrows():
        lines.append(
            f"| {r['date']} | ${r['equity_before']:,.1f} | ${r['blended']:,.0f} | "
            f"${r['adverse_price']:,.0f} | {r['wick_below_blended_pct']:.1f}% | "
            f"{r['regime']} | {r['cause'][:45]} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Events Where Spacing COULD Help (equity > $20)",
        "",
        "These are the events where meaningful capital was at risk AND spacing adjustments",
        "could potentially have prevented the liquidation.",
        "",
        "| Date | Side | Equity | Wick% Below Blended | What Happened | Spacing Verdict |",
        "|------|------|-------:|--------------------:|---------------|-----------------|",
    ]

    for _, r in spacing_resistant.sort_values("equity_before", ascending=False).iterrows():
        if "WICK" in r["cause"]:
            verdict = "Wider spacing may NOT help — wick filled all levels in one bar"
        elif "TREND" in r["cause"]:
            verdict = "Wider L5 gap COULD help — price path gradual, fills preventable"
        else:
            verdict = "Marginal benefit — equity already stressed"
        lines.append(
            f"| {r['date']} | {r['side']} | ${r['equity_before']:,.0f} | "
            f"{r['wick_below_blended_pct']:.1f}% | {r['cause'][:40]} | {verdict} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Assessment: Can L4/L5 Spacing Eliminate Liquidations?",
        "",
        "### Honest Verdict",
        "",
        "**Spacing alone CANNOT eliminate all liquidations.** Here's why:",
        "",
        "1. **Sequential cascade problem (82% of events):**",
        "   Once the account drops below ~$20 from prior events, any L5 fill will liquidate.",
        "   You can space L4/L5 as wide as 20%+, but if an aggressive bear market",
        "   (like 2018, 2022) keeps filling levels, the account will inevitably get wiped.",
        "   *No amount of spacing prevents this without also preventing meaningful profit.*",
        "",
        "2. **Deep wick problem:**",
        "   Extreme flash crashes (COVID March 2020, BTC -10% in one bar) can fill ALL levels",
        "   including a very wide L5 if they're sufficiently severe.",
        "",
        "3. **Remaining 18% of events — spacing CAN help:**",
        "   - Wider L4/L5 spacing makes those levels fill less frequently",
        "   - Fewer L4/L5 fills = fewer near-zero equity scenarios in the first place",
        "   - This BREAKS the cascade chain before it starts",
        "",
        "### What Wider Spacing Actually Does",
        "",
        "| Effect | Positive | Negative |",
        "|--------|----------|----------|",
        "| L4/L5 fill frequency | Fewer fills (price must go deeper) | Miss some profitable fills |",
        "| When L5 fills | Better entry price = lower blended | Recovery to TP is harder |",
        "| Liquidation threshold | Lower liq price (more buffer) | — |",
        "| Cascade risk | Fewer L5 fills = less equity depletion | — |",
        "| CAGR | May drop (fewer profitable deep fills) | — |",
        "| Max drawdown | Likely improves | — |",
        "",
        "**Bottom line:** Wider L4/L5 spacing is a risk-reduction lever, not a liquidation-",
        "elimination lever. It can materially REDUCE liquidation count (from 33 → potentially",
        "10–15) by preventing cascade initiators, but extreme bear markets will still generate",
        "sequences of losses that deplete equity.",
        "",
        "**See `l4l5_spacing_sweep_report_YYYY-MM-DD.md` for exact tradeoffs.**",
        "",
        "---",
        "*Generated by `tools/liq_event_analyzer.py`*",
    ]

    return "\n".join(lines)


def main():
    print("Loading liquidation events...")
    events = json.load(open(LIQ_JSON))
    print(f"  Loaded {len(events)} events")

    print("Analyzing events...")
    df = analyze_events(events)

    long_df = df[df["side"] == "LONG"]
    short_df = df[df["side"] == "SHORT"]

    print(f"\n  Long liquidations:  {len(long_df)}")
    print(f"  Short liquidations: {len(short_df)}")

    # Console summary
    print("\n=== LONG EVENTS ===")
    for _, r in long_df.iterrows():
        print(f"  {r['date']} eq=${r['equity_before']:7.1f} wick={r['wick_below_blended_pct']:5.1f}% {r['cause'][:45]}")

    print("\n=== SHORT EVENTS ===")
    for _, r in short_df.iterrows():
        print(f"  {r['date']} eq=${r['equity_before']:7.1f} wick={r['wick_below_blended_pct']:5.1f}% {r['cause'][:45]}")

    # Save report
    report_md = generate_report(df)
    report_path = REPORTS_DIR / "liq_event_analysis_2018_2026.md"
    report_path.write_text(report_md)
    print(f"\n  Report saved → reports/{report_path.name}")

    return df


if __name__ == "__main__":
    main()
