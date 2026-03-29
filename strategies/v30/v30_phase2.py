"""
MRM v3.0 — Phase 2: Single Indicators WITHOUT dd20d
=====================================================
Same entry indicators as Phase 1, but dd20d OFF and RSI rescue OFF.
Compare with Phase 1 to determine dd20d verdict per indicator.
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(__file__))

from v30_indicators import load_data
from v30_engine import run_backtest
from v30_phase1 import PHASE1_CONFIGS, sma440_regime
import numpy as np


if __name__ == '__main__':
    data = load_data()
    results = []
    t0 = time.time()

    print(f"\nPhase 2: {len(PHASE1_CONFIGS)} configs (dd20d OFF, RSI rescue OFF, SMA440 regime)")
    print("=" * 90)

    for idx, (p1_label, entry_fn) in enumerate(PHASE1_CONFIGS):
        label = p1_label.replace('p1_', 'p2_')
        t1 = time.time()
        r = run_backtest(data, entry_fn, sma440_regime,
                         config={'use_dd20d': False, 'use_rsi_rescue': False},
                         label=label)
        results.append(r)
        elapsed = time.time() - t1
        liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
        print(f"  [{idx+1:2d}/{len(PHASE1_CONFIGS)}] {label:<30} CAGR={r['cagr']:>7.1f}%  "
              f"MaxDD={r['max_dd']:>5.1f}%  trades={r['trades']:>5}  {liq_str}  ({elapsed:.0f}s)")

    # Save Phase 2 results
    out_path = os.path.join(os.path.dirname(__file__), 'v30_phase2_results.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    # Load Phase 1 results and compare
    p1_path = os.path.join(os.path.dirname(__file__), 'v30_phase1_results.json')
    if os.path.exists(p1_path):
        with open(p1_path) as f:
            p1_results = {r['label']: r for r in json.load(f)}

        print(f"\n{'=' * 90}")
        print("dd20d Verdict (Phase 1 vs Phase 2):")
        print(f"{'Indicator':<30} {'P1 CAGR':>8} {'P1 liq':>7} {'P2 CAGR':>8} {'P2 liq':>7}  Verdict")
        print("-" * 90)

        verdicts = []
        for (p1_label, _), r2 in zip(PHASE1_CONFIGS, results):
            r1 = p1_results.get(p1_label, {})
            p1_cagr = r1.get('cagr', 0)
            p1_liq = r1.get('liq', 0)
            p2_cagr = r2.get('cagr', 0)
            p2_liq = r2.get('liq', 0)

            if p1_liq == 0 and p2_liq > 0:
                verdict = "dd20d ESSENTIAL"
            elif p1_liq == 0 and p2_liq == 0:
                if p1_cagr > p2_cagr * 1.05:
                    verdict = "dd20d HELPFUL"
                elif p2_cagr > p1_cagr * 1.05:
                    verdict = "dd20d HARMFUL"
                else:
                    verdict = "dd20d NEUTRAL"
            elif p1_liq > 0 and p2_liq > 0:
                verdict = "BOTH HAVE LIQS"
            else:
                verdict = "dd20d CAUSES LIQS"

            verdicts.append({
                'indicator': p1_label, 'verdict': verdict,
                'p1_cagr': p1_cagr, 'p1_liq': p1_liq,
                'p2_cagr': p2_cagr, 'p2_liq': p2_liq,
            })
            print(f"  {p1_label:<30} {p1_cagr:>7.1f}% {p1_liq:>6}  {p2_cagr:>7.1f}% {p2_liq:>6}  {verdict}")

        # Save verdicts
        verdict_path = os.path.join(os.path.dirname(__file__), 'v30_dd20d_verdicts.json')
        with open(verdict_path, 'w') as f:
            json.dump(verdicts, f, indent=2)

    total_time = time.time() - t0
    zero_liq = [r for r in results if r['liq'] == 0]
    zero_liq_sorted = sorted(zero_liq, key=lambda r: r['cagr'], reverse=True)

    print(f"\nPhase 2 complete: {len(results)} configs in {total_time/60:.1f} min")
    print(f"0-liq configs: {len(zero_liq)} / {len(results)}")
    print(f"\nTop 10 (0-liq, by CAGR):")
    for r in zero_liq_sorted[:10]:
        print(f"  {r['label']:<30} CAGR={r['cagr']:>7.1f}%  trades={r['trades']:>5}  MaxDD={r['max_dd']:.1f}%")
    print(f"\nResults saved to {out_path}")
