"""
Go/No-Go Report Generator

Compiles backtest results, Monte Carlo stress tests, parameter stability,
and risk analysis into a comprehensive verdict.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from signals.backtester import generate_synthetic_data, run_backtest, BacktestResult
from signals.monte_carlo import run_monte_carlo, MonteCarloSummary
from signals.stability import analyze_stability

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = Path(__file__).parent / "results"


def _load_strategy_md() -> str:
    """Load STRATEGY.md content."""
    path = PROJECT_ROOT / "STRATEGY.md"
    if path.exists():
        return path.read_text()
    return "*STRATEGY.md not found*"


def _load_latest_result(prefix: str) -> dict | None:
    """Load the most recent result file matching prefix."""
    if not RESULTS_DIR.exists():
        return None
    files = sorted(RESULTS_DIR.glob(f"{prefix}_*.json"), reverse=True)
    if files:
        return json.loads(files[0].read_text())
    return None


def _verdict_logic(
    mc: MonteCarloSummary | None,
    stability: dict[str, Any] | None,
    backtest_results: list[BacktestResult] | None,
) -> tuple[str, list[str]]:
    """
    Determine GO / NO-GO / CONDITIONAL verdict.

    Returns (verdict, list of reasons).
    """
    reasons: list[str] = []
    red_flags = 0
    yellow_flags = 0
    green_flags = 0

    # Monte Carlo checks
    if mc:
        median_return = mc.percentiles.get("total_return_pct", {}).get("p50", 0)
        if median_return > 0:
            green_flags += 1
            reasons.append(f"✅ Positive median return: {median_return:+.2f}%")
        else:
            red_flags += 1
            reasons.append(f"🚨 Negative median return: {median_return:+.2f}%")

        if mc.ruin_probability < 0.05:
            green_flags += 1
            reasons.append(f"✅ Low ruin probability: {mc.ruin_probability:.1%}")
        elif mc.ruin_probability < 0.15:
            yellow_flags += 1
            reasons.append(f"⚠️  Moderate ruin probability: {mc.ruin_probability:.1%}")
        else:
            red_flags += 1
            reasons.append(f"🚨 High ruin probability: {mc.ruin_probability:.1%}")

        median_sharpe = mc.percentiles.get("sharpe", {}).get("p50", 0)
        if median_sharpe > 0.5:
            green_flags += 1
            reasons.append(f"✅ Decent median Sharpe: {median_sharpe:.2f}")
        elif median_sharpe > 0:
            yellow_flags += 1
            reasons.append(f"⚠️  Weak median Sharpe: {median_sharpe:.2f}")
        else:
            red_flags += 1
            reasons.append(f"🚨 Negative median Sharpe: {median_sharpe:.2f}")
    else:
        yellow_flags += 1
        reasons.append("⚠️  No Monte Carlo data available")

    # Stability checks
    if stability:
        overall = stability.get("overall_stability", 0)
        fragile = stability.get("fragile_parameters", [])
        if overall >= 0.7 and not fragile:
            green_flags += 1
            reasons.append(f"✅ Parameters stable (score: {overall:.2f})")
        elif overall >= 0.4:
            yellow_flags += 1
            reasons.append(f"⚠️  Moderate parameter stability ({overall:.2f}), fragile: {fragile}")
        else:
            red_flags += 1
            reasons.append(f"🚨 Fragile parameters ({overall:.2f}): {fragile}")
    else:
        yellow_flags += 1
        reasons.append("⚠️  No stability data available")

    # Backtest checks
    if backtest_results:
        total_trades = sum(r.total_predictions for r in backtest_results)
        avg_sharpe = float(np.mean([r.sharpe for r in backtest_results if r.total_predictions > 0])) if backtest_results else 0
        if total_trades >= 100:
            green_flags += 1
            reasons.append(f"✅ Sufficient sample size: {total_trades} trades")
        elif total_trades >= 30:
            yellow_flags += 1
            reasons.append(f"⚠️  Limited sample: {total_trades} trades")
        else:
            red_flags += 1
            reasons.append(f"🚨 Insufficient data: only {total_trades} trades")
    else:
        yellow_flags += 1
        reasons.append("⚠️  No backtest results available")

    # Verdict
    if red_flags >= 2:
        return "NO-GO", reasons
    elif red_flags == 0 and yellow_flags <= 1:
        return "GO", reasons
    else:
        return "CONDITIONAL", reasons


def generate_report(
    mc_sims: int = 100,
    mc_steps: int = 300,
    stability_steps: int = 500,
    seed: int = 42,
) -> str:
    """
    Generate a comprehensive Go/No-Go report.
    Returns the Markdown content.
    """
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")

    # Run analyses
    logger.info("Running backtest...")
    data = generate_synthetic_data(n_steps=500, seed=seed)
    bt_results = run_backtest(data)

    logger.info("Running Monte Carlo (%d sims)...", mc_sims)
    mc = run_monte_carlo(n_sims=mc_sims, n_steps=mc_steps, seed=seed)

    logger.info("Running stability analysis...")
    stability = analyze_stability(n_steps_data=stability_steps, seed=seed)

    # Verdict
    verdict, reasons = _verdict_logic(mc, stability, bt_results)

    # Build report
    lines: list[str] = []
    lines.append(f"# Go/No-Go Research Report")
    lines.append(f"*Generated: {ts}*\n")

    # 1. Strategy Overview
    lines.append("## 1. Strategy Overview\n")
    strategy = _load_strategy_md()
    # Include just the core edge section
    lines.append("> Trade the Traders — position ahead of predictable algorithmic behavior")
    lines.append("> in crypto markets using liquidation cascades, funding rate extremes,")
    lines.append("> and OI divergence signals.\n")

    # 2. Signal Performance
    lines.append("## 2. Signal Performance (Backtest)\n")
    lines.append(f"| Signal | Horizon | Trades | Hit Rate | Avg Return | Sharpe |")
    lines.append(f"|--------|---------|--------|----------|------------|--------|")
    for r in sorted(bt_results, key=lambda x: (x.signal_name, x.horizon_min)):
        if r.total_predictions > 0:
            lines.append(f"| {r.signal_name} | {r.horizon_min}m | {r.total_predictions} | "
                        f"{r.hit_rate:.1%} | {r.avg_return_pct:+.4f}% | {r.sharpe:.2f} |")
    lines.append("")

    # 3. Monte Carlo
    lines.append("## 3. Monte Carlo Stress Test\n")
    lines.append(f"**{mc.n_simulations} simulations** across 5 volatility regimes\n")
    lines.append(f"| Metric | P5 | P25 | Median | P75 | P95 |")
    lines.append(f"|--------|-----|-----|--------|-----|-----|")
    for metric, pcts in mc.percentiles.items():
        label = metric.replace("_", " ").title()
        lines.append(f"| {label} | {pcts['p5']:.2f} | {pcts['p25']:.2f} | "
                     f"{pcts['p50']:.2f} | {pcts['p75']:.2f} | {pcts['p95']:.2f} |")
    lines.append(f"\n**Ruin Probability:** {mc.ruin_probability:.1%}")
    lines.append(f"**Worst Case:** {mc.worst_case.total_return_pct:+.2f}% (regime: {mc.worst_case.regime})")
    lines.append(f"**Best Case:** {mc.best_case.total_return_pct:+.2f}% (regime: {mc.best_case.regime})\n")

    # 4. Parameter Stability
    lines.append("## 4. Parameter Stability\n")
    lines.append(f"**Overall Stability Score:** {stability['overall_stability']:.2f}/1.00\n")
    lines.append(f"| Parameter | Score | Status |")
    lines.append(f"|-----------|-------|--------|")
    for pname, pdata in stability["per_parameter"].items():
        score = pdata["stability_score"]
        status = "✅ Stable" if score >= 0.7 else "⚠️ Moderate" if score >= 0.4 else "🚨 Fragile"
        lines.append(f"| {pname} | {score:.2f} | {status} |")
    if stability["fragile_parameters"]:
        lines.append(f"\n⚠️ **Fragile parameters:** {', '.join(stability['fragile_parameters'])}")
    lines.append("")

    # 5. Risk Assessment
    lines.append("## 5. Risk Assessment\n")
    dd_stats = mc.percentiles.get("max_drawdown_pct", {})
    lines.append(f"- **Median Max Drawdown:** {dd_stats.get('p50', 0):.2f}%")
    lines.append(f"- **95th Percentile Drawdown:** {dd_stats.get('p95', 0):.2f}%")
    lines.append(f"- **Worst Single Trade (P5):** {mc.percentiles.get('worst_single_trade_pct', {}).get('p5', 0):.4f}%")
    consec = mc.percentiles.get("max_consecutive_losses", {})
    lines.append(f"- **Max Consecutive Losses (P95):** {consec.get('p95', 0):.0f}")
    lines.append(f"\n**Parameter Sensitivity:**")
    for param, corr in mc.param_sensitivity.items():
        lines.append(f"- `{param}`: r={corr:+.4f}")
    lines.append("")

    # 6. Paper Trading
    lines.append("## 6. Paper Trading Track Record\n")
    lines.append("*No paper trading data yet — system in development phase.*\n")

    # 7. Verdict
    lines.append("## 7. VERDICT\n")
    lines.append(f"# **{verdict}**\n")
    lines.append("### Assessment:\n")
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")

    if verdict == "CONDITIONAL":
        lines.append("### Next Steps:\n")
        lines.append("- Collect more historical data for validation")
        lines.append("- Begin paper trading to build track record")
        lines.append("- Address any fragile parameters before live deployment")
        lines.append("- Re-run this report after paper trading period")

    report = "\n".join(lines)

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    file_ts = now.strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"go_no_go_{file_ts}.md"
    path.write_text(report)
    logger.info("Saved Go/No-Go report to %s", path)

    return report


def print_verdict(report: str) -> None:
    """Print just the verdict section."""
    in_verdict = False
    for line in report.split("\n"):
        if "## 7. VERDICT" in line:
            in_verdict = True
        if in_verdict:
            print(line)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print("Generating Go/No-Go report...\n")

    report = generate_report(mc_sims=100, mc_steps=300)

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)


if __name__ == "__main__":
    main()
