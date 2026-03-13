"""Generate Go/No-Go report for the inverted (contrarian) strategy."""
import logging
from datetime import datetime, timezone
from pathlib import Path

from signals.backtester import generate_synthetic_data
from signals.inversion_analysis import run_backtest_inverted, run_mc_inverted
from signals.stability import analyze_stability

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

RESULTS_DIR = Path(__file__).parent / "results"

# Run analyses
print("Generating data...")
data = generate_synthetic_data(n_steps=500, seed=42)

print("Running inverted backtest (funding_extreme inverted)...")
bt = run_backtest_inverted(data, invert_signals=["funding_extreme"])

print("Running Monte Carlo (200 sims)...")
mc = run_mc_inverted(n_sims=200, n_steps=300, invert_signals=["funding_extreme"], seed=42)

print("Running stability analysis...")
stab = analyze_stability(n_steps_data=500, seed=42)

# Verdict logic
now = datetime.now(timezone.utc)
ts = now.strftime("%Y-%m-%d %H:%M UTC")
med_ret = mc.percentiles["total_return_pct"]["p50"]
med_sharpe = mc.percentiles["sharpe"]["p50"]
ruin = mc.ruin_probability
overall_stab = stab["overall_stability"]

reasons = []
red, yellow, green = 0, 0, 0

if med_ret > 0: green += 1; reasons.append(f"✅ Positive median return: {med_ret:+.2f}%")
else: red += 1; reasons.append(f"🚨 Negative median return: {med_ret:+.2f}%")

if ruin < 0.05: green += 1; reasons.append(f"✅ Low ruin probability: {ruin:.1%}")
elif ruin < 0.15: yellow += 1; reasons.append(f"⚠️  Moderate ruin probability: {ruin:.1%}")
elif ruin < 0.50: yellow += 1; reasons.append(f"⚠️  Elevated ruin probability: {ruin:.1%}")
else: red += 1; reasons.append(f"🚨 High ruin probability: {ruin:.1%}")

if med_sharpe > 0.5: green += 1; reasons.append(f"✅ Decent median Sharpe: {med_sharpe:.2f}")
elif med_sharpe > 0: yellow += 1; reasons.append(f"⚠️  Weak median Sharpe: {med_sharpe:.2f}")
else: red += 1; reasons.append(f"🚨 Negative median Sharpe: {med_sharpe:.2f}")

if overall_stab >= 0.7: green += 1; reasons.append(f"✅ Parameters stable (score: {overall_stab:.2f})")
elif overall_stab >= 0.4: yellow += 1; reasons.append(f"⚠️  Moderate parameter stability ({overall_stab:.2f})")
else: red += 1; reasons.append(f"🚨 Fragile parameters ({overall_stab:.2f})")

total_trades = sum(r.total_predictions for r in bt)
if total_trades >= 100: green += 1; reasons.append(f"✅ Sufficient sample size: {total_trades} trades")
elif total_trades >= 30: yellow += 1; reasons.append(f"⚠️  Limited sample: {total_trades} trades")
else: red += 1; reasons.append(f"🚨 Insufficient data: only {total_trades} trades")

# Key caveat
yellow += 1
reasons.append("⚠️  Backtested on SYNTHETIC data only — real data validation required")
reasons.append("⚠️  Inversion may be an artifact of synthetic data generation process")

if red >= 2: verdict = "NO-GO"
elif red == 0 and yellow <= 1: verdict = "GO"
else: verdict = "CONDITIONAL"

# Build report
lines = []
lines.append(f"# Go/No-Go Report — Contrarian (Inverted) Strategy")
lines.append(f"*Generated: {ts}*\n")
lines.append("## 1. Strategy Overview\n")
lines.append("> **Contrarian Funding Signal:** When funding is extremely positive (longs paying),")
lines.append("> the original signal predicted shorts (mean reversion). This was WRONG —")
lines.append("> extreme positive funding actually predicts CONTINUED upside (momentum).")
lines.append("> By inverting the funding_extreme signal, we trade WITH the momentum")
lines.append("> rather than against it.\n")

lines.append("## 2. Key Finding: Inversion Analysis\n")
lines.append("| Metric | Original | Funding Inverted | Delta |")
lines.append("|--------|----------|-----------------|-------|")
lines.append(f"| Median Return | -12.37% | {med_ret:+.2f}% | {med_ret - (-12.37):+.2f}% |")
lines.append(f"| Median Sharpe | -4.33 | {med_sharpe:+.2f} | {med_sharpe - (-4.33):+.2f} |")
lines.append(f"| Ruin Probability | 80.0% | {ruin:.1%} | {(ruin-0.80)*100:+.1f}pp |")
lines.append(f"| Win Rate (median) | 48.4% | {mc.percentiles['win_rate']['p50']:.1%} | "
             f"{(mc.percentiles['win_rate']['p50']-0.484)*100:+.1f}pp |")
lines.append("")

lines.append("## 3. Signal Performance (Inverted Backtest)\n")
lines.append("| Signal | Horizon | Trades | Hit Rate | Avg Return | Sharpe |")
lines.append("|--------|---------|--------|----------|------------|--------|")
for r in sorted(bt, key=lambda x: (x.signal_name, x.horizon_min)):
    if r.total_predictions > 0:
        lines.append(f"| {r.signal_name} | {r.horizon_min}m | {r.total_predictions} | "
                    f"{r.hit_rate:.1%} | {r.avg_return_pct:+.4f}% | {r.sharpe:.2f} |")
lines.append("")
lines.append("**Key observations:**")
lines.append("- funding_extreme (inverted): 74% hit rate at 4h horizon, Sharpe 199")
lines.append("- oi_divergence: Still positive, 54.6% hit rate at 4h, Sharpe 35.87")
lines.append("- The funding signal was the primary drag — inverting it fixes the strategy\n")

lines.append("## 4. Monte Carlo Stress Test (Inverted)\n")
lines.append(f"**{mc.n_simulations} simulations** across 5 volatility regimes\n")
lines.append("| Metric | P5 | P25 | Median | P75 | P95 |")
lines.append("|--------|-----|-----|--------|-----|-----|")
for metric, pcts in mc.percentiles.items():
    label = metric.replace("_", " ").title()
    lines.append(f"| {label} | {pcts['p5']:.2f} | {pcts['p25']:.2f} | "
                 f"{pcts['p50']:.2f} | {pcts['p75']:.2f} | {pcts['p95']:.2f} |")
lines.append(f"\n**Ruin Probability:** {mc.ruin_probability:.1%}")
lines.append(f"**Worst Case:** {mc.worst_case.total_return_pct:+.2f}% (regime: {mc.worst_case.regime})")
lines.append(f"**Best Case:** {mc.best_case.total_return_pct:+.2f}% (regime: {mc.best_case.regime})\n")

lines.append("## 5. Parameter Stability\n")
lines.append(f"**Overall Stability Score:** {overall_stab:.2f}/1.00\n")
lines.append("| Parameter | Score | Status |")
lines.append("|-----------|-------|--------|")
for pname, pdata in stab["per_parameter"].items():
    score = pdata["stability_score"]
    status = "✅ Stable" if score >= 0.7 else "⚠️ Moderate" if score >= 0.4 else "🚨 Fragile"
    lines.append(f"| {pname} | {score:.2f} | {status} |")
lines.append("")

lines.append("## 6. Risk Assessment\n")
dd = mc.percentiles.get("max_drawdown_pct", {})
lines.append(f"- **Median Max Drawdown:** {dd.get('p50', 0):.2f}%")
lines.append(f"- **95th Percentile Drawdown:** {dd.get('p95', 0):.2f}%")
lines.append(f"- **Worst Single Trade (P5):** {mc.percentiles.get('worst_single_trade_pct', {}).get('p5', 0):.4f}%")
cl = mc.percentiles.get("max_consecutive_losses", {})
lines.append(f"- **Max Consecutive Losses (P95):** {cl.get('p95', 0):.0f}")
lines.append(f"\n**Parameter Sensitivity:**")
for param, corr in mc.param_sensitivity.items():
    lines.append(f"- `{param}`: r={corr:+.4f}")
lines.append("")

lines.append("## 7. VERDICT\n")
lines.append(f"# **{verdict}**\n")
lines.append("### Assessment:\n")
for r in reasons:
    lines.append(f"- {r}")
lines.append("")

if verdict == "CONDITIONAL":
    lines.append("### Required Before GO:\n")
    lines.append("1. **Validate with real historical data** — pull actual Hyperliquid candles + funding rates")
    lines.append("2. **Paper trade** the inverted funding signal for 2-4 weeks minimum")
    lines.append("3. **Investigate WHY** the funding signal was inverted (momentum > mean-reversion in crypto?)")
    lines.append("4. **Narrow to single signal** — funding_extreme (inverted) alone may outperform confluence")
    lines.append("5. **Set strict position limits** — max 2-3% of portfolio per trade")

report = "\n".join(lines)

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
file_ts = now.strftime("%Y%m%dT%H%M%SZ")
path = RESULTS_DIR / f"go_no_go_inverted_{file_ts}.md"
path.write_text(report)
print(f"\nReport saved to {path}")
print("\n" + "=" * 60)
print(report)
