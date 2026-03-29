"""
MRM v3.0 — Phase 7: Final Comparison
======================================
Load all phase results, rank all 0-liq configs, compare vs v2.9 baseline.
"""
import json, os

DIR = os.path.dirname(__file__)

def load_results(fname):
    path = os.path.join(DIR, fname)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

all_results = []
for fname in ['v30_phase1_results.json', 'v30_phase2_results.json',
              'v30_phase3_results.json', 'v30_phase3b_results.json',
              'v30_phase4_results.json', 'v30_phase5_results.json',
              'v30_phase6_results.json']:
    all_results.extend(load_results(fname))

print(f"Total configs tested: {len(all_results)}")

# Deduplicate by label
seen = {}
for r in all_results:
    label = r['label']
    if label not in seen or r['cagr'] > seen[label]['cagr']:
        seen[label] = r
all_results = list(seen.values())
print(f"Unique configs: {len(all_results)}")

zero_liq = [r for r in all_results if r['liq'] == 0]
zero_liq_sorted = sorted(zero_liq, key=lambda r: r['cagr'], reverse=True)

print(f"\n{'='*100}")
print(f"ZERO-LIQUIDATION CONFIGS: {len(zero_liq)} / {len(all_results)}")
print(f"{'='*100}")

# v2.9 baseline for reference
v29 = next((r for r in all_results if 'v29' in r['label'] or 'v28_baseline' in r['label']), None)

print(f"\n--- TOP 25 (0 liqs, by CAGR) ---")
print(f"{'Rank':<5} {'Label':<55} {'CAGR':>7} {'MaxDD':>6} {'Trades':>6} {'Beat v2.9?'}")
print("-" * 95)
for i, r in enumerate(zero_liq_sorted[:25]):
    beats = "YES" if r['cagr'] > 119.1 else "no"
    print(f"{i+1:<5} {r['label']:<55} {r['cagr']:>6.1f}% {r['max_dd']:>5.1f}% {r['trades']:>6}  {beats}")

# Count how many beat v2.9
beat_count = sum(1 for r in zero_liq if r['cagr'] > 119.1)
print(f"\nConfigs that beat v2.9 (119.1% CAGR): {beat_count}")

# Summary by entry type
print(f"\n--- BEST PER ENTRY TYPE (0 liqs) ---")
entry_types = {}
for r in zero_liq:
    # Extract entry type from label
    label = r['label']
    if 'v28orEma20' in label:
        etype = 'v28 OR ema20_t0.02'
    elif 'ema20t5_atr' in label:
        etype = 'ema20 AND atr_ratio'
    elif 'ema20spanb' in label:
        etype = 'ema20 AND spanb'
    elif 'v28_baseline' in label or 'v29' in label:
        etype = 'v28 baseline (v2.9)'
    elif 'v28_AND' in label:
        etype = 'v28 AND filter'
    elif 'ema20t02' in label:
        etype = 'ema20_t0.02 solo'
    elif 'don_break' in label:
        etype = 'donchian breakout'
    elif 'boll20' in label:
        etype = 'boll20 AND rsi'
    elif 'pivot' in label:
        etype = 'pivot AND filter'
    elif 'ema20t5_AND_spanb' in label:
        etype = 'ema20 AND spanb'
    elif 'ema50' in label:
        etype = 'ema50 AND filter'
    else:
        etype = label.split('_')[1] if '_' in label else label

    if etype not in entry_types or r['cagr'] > entry_types[etype]['cagr']:
        entry_types[etype] = r

print(f"{'Entry Type':<30} {'Best CAGR':>9} {'MaxDD':>6} {'Trades':>6} {'Label'}")
print("-" * 100)
for etype, r in sorted(entry_types.items(), key=lambda x: x[1]['cagr'], reverse=True):
    print(f"{etype:<30} {r['cagr']:>8.1f}% {r['max_dd']:>5.1f}% {r['trades']:>6}  {r['label']}")

# Save final ranking
ranking = [{'rank': i+1, **r} for i, r in enumerate(zero_liq_sorted)]
out_path = os.path.join(DIR, 'v30_final_ranking.json')
with open(out_path, 'w') as f:
    json.dump(ranking, f, indent=2)
print(f"\nFull ranking saved to {out_path}")
