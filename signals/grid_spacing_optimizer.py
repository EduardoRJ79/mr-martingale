"""
Grid Spacing Optimizer
Analyzes the distribution of how far BTC drops after a trigger event,
then finds the optimal spacing for each grid level to maximize expected PnL.
"""

import pandas as pd
import numpy as np
import gzip, csv
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

BASE_MARGIN  = 8.0
MULTIPLIER   = 2.0
LEVERAGE     = 20
NUM_LEVELS   = 5
TRIGGER_PCT  = 0.5
TP_PCT       = 0.5
LOOKAHEAD    = 60   # bars to look ahead for snap-back (60 × 4h = 10 days)

def load_candles(coin, interval):
    path = DATA_DIR / f"candles_{coin}_{interval}.csv.gz"
    rows = []
    with gzip.open(path, 'rt') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    df = pd.DataFrame(rows)
    df['time']  = pd.to_datetime(df['open_time_ms'].astype(float), unit='ms')
    df['close'] = df['close'].astype(float)
    df['high']  = df['high'].astype(float)
    df['low']   = df['low'].astype(float)
    df = df.sort_values('time').reset_index(drop=True)
    df['ema34'] = df['close'].ewm(span=34, adjust=False).mean()
    df['ma21']  = df['close'].rolling(21).mean()
    df['pct_below_ema34'] = (df['ema34'] - df['close']) / df['ema34'] * 100
    df['pct_below_ma21']  = (df['ma21']  - df['close']) / df['ma21']  * 100
    return df.dropna(subset=['ema34','ma21']).reset_index(drop=True)

def find_trigger_events(df):
    """Find first bar of each trigger cluster."""
    events, in_trig = [], False
    for i in df.index:
        triggered = (df.loc[i,'pct_below_ema34'] >= TRIGGER_PCT and
                     df.loc[i,'pct_below_ma21']  >= TRIGGER_PCT)
        if triggered and not in_trig:
            events.append(i)
            in_trig = True
        elif not triggered:
            in_trig = False
    return events

def measure_drop_profile(df, events):
    """
    For each trigger event, measure:
    - max drop (low) from entry price within LOOKAHEAD bars
    - snap-back size (high) after that low
    Returns a DataFrame with one row per event.
    """
    records = []
    for idx in events:
        entry = df.loc[idx, 'close']
        future = df.loc[idx+1 : idx+LOOKAHEAD]
        if future.empty:
            continue

        # Running low — track drawdown bar by bar
        min_low = entry
        min_bar = 0
        for offset, (i, row) in enumerate(future.iterrows()):
            if row['low'] < min_low:
                min_low = row['low']
                min_bar = offset + 1

        max_dd_pct  = (entry - min_low) / entry * 100
        snap_from_low = future['high'].max()
        snapback_pct  = (snap_from_low - entry) / entry * 100

        records.append({
            'entry':        entry,
            'max_dd_pct':   max_dd_pct,
            'snapback_pct': snapback_pct,
            'bars_to_low':  min_bar,
        })
    return pd.DataFrame(records)

def p_fill(drop_pct, profiles):
    """Probability that price drops at least drop_pct% from L1 entry."""
    return (profiles['max_dd_pct'] >= drop_pct).mean()

def expected_pnl_for_spacing(spacing_pct, level_idx, profiles):
    """
    For a given spacing (cumulative drop from L1 to this level),
    compute expected PnL contribution of filling this level.
    Assumes TP at 0.5% above blended entry after ALL levels up to this one fill.
    """
    prob = p_fill(spacing_pct, profiles)
    if prob == 0:
        return 0, prob

    margin   = BASE_MARGIN * (MULTIPLIER ** level_idx)
    notional = margin * LEVERAGE

    # Position opened at spacing_pct% below L1 entry
    # Price needs to recover to blended entry + TP_PCT to exit
    # Simplified: each level contributes notional * TP_PCT/100 gross at TP
    # (exact blended varies, but directionally correct for optimization)
    gross_profit = notional * (TP_PCT / 100)
    expected = prob * gross_profit
    return expected, prob

def optimize_spacings(profiles):
    """
    For each level, find the cumulative drop % from L1 that maximizes
    expected PnL contribution. Search 0.5% to 8% in 0.25% steps.
    """
    print("\n" + "="*65)
    print("  SPACING OPTIMIZATION")
    print("  Finding optimal cumulative drop for each level")
    print("="*65)

    spacings_to_test = [round(x * 0.25, 2) for x in range(2, 33)]  # 0.5% to 8%
    optimal = []

    for lvl_idx in range(1, NUM_LEVELS):  # L2=1, L3=2, L4=3, L5=4
        best_spacing, best_ev, best_prob = 0, -1, 0
        results = []

        for spacing in spacings_to_test:
            ev, prob = expected_pnl_for_spacing(spacing, lvl_idx, profiles)
            results.append((spacing, ev, prob))
            if ev > best_ev:
                best_ev, best_spacing, best_prob = ev, spacing, prob

        margin = BASE_MARGIN * (MULTIPLIER ** lvl_idx)
        print(f"\n  L{lvl_idx+1} (${margin:.0f} margin, ${margin*LEVERAGE:.0f} notional):")
        print(f"  {'Drop%':>7}  {'Fill%':>7}  {'E[PnL]':>8}  {'Bar'}")
        print(f"  {'-'*38}")
        for spacing, ev, prob in results:
            marker = " ◄ OPTIMAL" if spacing == best_spacing else ""
            print(f"  {spacing:>6.2f}%  {prob*100:>6.1f}%  ${ev:>7.4f}{marker}")

        optimal.append({
            'level':    lvl_idx + 1,
            'spacing':  best_spacing,
            'prob':     best_prob,
            'ev':       best_ev,
        })

    return optimal

def backtest_with_spacings(df, events, spacings, label=""):
    """Quick simulation with given cumulative spacings to measure actual performance."""
    wins, total_pnl = 0, 0
    level_hits = {i+1: 0 for i in range(NUM_LEVELS)}

    for idx in events:
        entry = df.loc[idx, 'close']
        future = df.loc[idx+1 : idx+LOOKAHEAD]
        if future.empty:
            continue

        # Figure out which levels fill
        positions = [(1, entry, BASE_MARGIN)]  # (level, entry_price, margin)
        level_hits[1] += 1

        for lvl_idx, spacing in enumerate(spacings, start=1):
            target = entry * (1 - spacing / 100)
            if future['low'].min() <= target:
                margin = BASE_MARGIN * (MULTIPLIER ** lvl_idx)
                positions.append((lvl_idx+1, target, margin))
                level_hits[lvl_idx+1] += 1
            else:
                break  # levels fill in order

        # Blended entry
        total_qty  = sum(m * LEVERAGE / p for _, p, m in positions)
        total_cost = sum(m * LEVERAGE for _, p, m in positions)
        blended    = total_cost / total_qty

        # Check if snap-back reaches TP
        tp_price = blended * (1 + TP_PCT / 100)
        if future['high'].max() >= tp_price:
            gross = total_qty * (tp_price - blended)
            total_pnl += gross
            wins += 1

    n = len(events)
    print(f"\n  {label}")
    print(f"    Win rate:       {wins}/{n} ({wins/n*100:.0f}%)")
    print(f"    Avg PnL/cycle:  ${total_pnl/n:.4f}")
    print(f"    Level fill rates:")
    for lvl, hits in level_hits.items():
        pct = hits / n * 100
        bar = '█' * int(pct / 2)
        print(f"      L{lvl}: {bar} {pct:.1f}%")
    return total_pnl / n

if __name__ == "__main__":
    print("Loading data and finding trigger events...")
    df = load_candles("BTC", "4h")
    events = find_trigger_events(df)
    print(f"Found {len(events)} trigger events")

    print("\nMeasuring drop profiles after each trigger...")
    profiles = measure_drop_profile(df, events)

    # Drop distribution summary
    print("\n" + "="*65)
    print("  DROP DISTRIBUTION AFTER TRIGGER (cumulative %)")
    print("="*65)
    print(f"  {'Drop':>6}  {'Prob fill':>10}  {'Histogram'}")
    print(f"  {'-'*50}")
    for drop in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]:
        prob = p_fill(drop, profiles)
        bar  = '█' * int(prob * 40)
        print(f"  {drop:>5.1f}%  {prob*100:>9.1f}%  {bar}")

    # Optimize
    optimal = optimize_spacings(profiles)

    print("\n\n" + "="*65)
    print("  OPTIMAL SPACING SUMMARY")
    print("="*65)
    opt_spacings = [o['spacing'] for o in optimal]
    print(f"  Uniform 2% spacings:   [2.0, 4.0, 6.0, 8.0] (cumulative)")
    print(f"  Optimal spacings:      {opt_spacings} (cumulative from L1)")

    # Convert cumulative to per-level gaps
    gaps = [opt_spacings[0]] + [round(opt_spacings[i]-opt_spacings[i-1], 2) for i in range(1, len(opt_spacings))]
    print(f"  Per-level gaps:        {gaps}")

    print(f"\n  {'Level':<8} {'Cum drop':>10} {'Fill prob':>10} {'E[PnL]':>10}")
    print(f"  {'-'*45}")
    for o in optimal:
        print(f"  L{o['level']:<7} {o['spacing']:>9.2f}%  {o['prob']*100:>8.1f}%  ${o['ev']:>9.4f}")

    # Compare uniform vs optimal via quick simulation
    print("\n\n" + "="*65)
    print("  QUICK SIMULATION COMPARISON")
    print("="*65)
    uniform_spacings = [2.0, 4.0, 6.0, 8.0]
    backtest_with_spacings(df, events, uniform_spacings, "Uniform 2% spacing (cumulative: 2/4/6/8%)")
    backtest_with_spacings(df, events, opt_spacings, f"Optimal spacing (cumulative: {opt_spacings})")
