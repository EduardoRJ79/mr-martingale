"""
Grid Strategy Analysis
- Load pre-fetched BTC candles (4h and 1h)
- Calculate EMA 34 and MA 21
- Find trigger events (price X% below both MAs)
- Analyze snap-back size and speed
"""

import pandas as pd
import numpy as np
import gzip
import csv
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

def load_candles(coin, interval):
    path = DATA_DIR / f"candles_{coin}_{interval}.csv.gz"
    rows = []
    with gzip.open(path, 'rt') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    
    df = pd.DataFrame(rows)
    df['time'] = pd.to_datetime(df['open_time_ms'].astype(float), unit='ms')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['open'] = df['open'].astype(float)
    df = df.sort_values('time').reset_index(drop=True)
    return df

def add_indicators(df):
    df['ema34'] = df['close'].ewm(span=34, adjust=False).mean()
    df['ma21'] = df['close'].rolling(window=21).mean()
    df['pct_below_ema34'] = (df['ema34'] - df['close']) / df['ema34'] * 100
    df['pct_below_ma21'] = (df['ma21'] - df['close']) / df['ma21'] * 100
    return df

def find_trigger_events(df, threshold_pct):
    """Find distinct trigger events where price is >= threshold% below BOTH MAs"""
    triggered_mask = (
        (df['pct_below_ema34'] >= threshold_pct) &
        (df['pct_below_ma21'] >= threshold_pct)
    )
    
    # Deduplicate — find first bar of each trigger cluster
    events = []
    in_trigger = False
    for idx in df.index:
        if triggered_mask[idx] and not in_trigger:
            events.append(idx)
            in_trigger = True
        elif not triggered_mask[idx]:
            in_trigger = False
    
    return events

def analyze_snapbacks(df, event_indices, lookahead_bars=30):
    results = []
    
    for idx in event_indices:
        entry_price = df.loc[idx, 'close']
        ema34 = df.loc[idx, 'ema34']
        ma21 = df.loc[idx, 'ma21']
        mean_price = (ema34 + ma21) / 2
        
        future = df.loc[idx+1 : idx+lookahead_bars]
        if future.empty:
            continue
        
        max_high = future['high'].max()
        peak_idx = future['high'].idxmax()
        bars_to_peak = peak_idx - idx
        
        # Pre-peak drawdown
        pre_peak = future.loc[:peak_idx]
        max_dd = (entry_price - pre_peak['low'].min()) / entry_price * 100 if not pre_peak.empty else 0
        
        snapback_pct = (max_high - entry_price) / entry_price * 100
        
        results.append({
            'time': df.loc[idx, 'time'],
            'entry_price': entry_price,
            'pct_below_ema34': df.loc[idx, 'pct_below_ema34'],
            'pct_below_ma21': df.loc[idx, 'pct_below_ma21'],
            'max_snapback_pct': snapback_pct,
            'bars_to_peak': bars_to_peak,
            'reached_mean': max_high >= mean_price,
            'reached_ma21': max_high >= ma21,
            'reached_ema34': max_high >= ema34,
            'max_dd_first': max_dd,
        })
    
    return pd.DataFrame(results)

def run_analysis(interval, threshold_pct):
    label = f"{interval.upper()} | trigger >{threshold_pct}% below both MAs"
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    
    df = load_candles("BTC", interval)
    df = add_indicators(df)
    df = df.dropna(subset=['ema34', 'ma21']).reset_index(drop=True)
    
    date_range = f"{df['time'].min().date()} → {df['time'].max().date()}"
    months = (df['time'].max() - df['time'].min()).days / 30
    print(f"  Data: {len(df)} candles | {date_range} ({months:.0f} months)")
    
    events = find_trigger_events(df, threshold_pct)
    print(f"  Distinct trigger events: {len(events)}")
    print(f"  Avg per month: {len(events)/months:.1f}")
    
    snapbacks = analyze_snapbacks(df, events, lookahead_bars=30)
    
    if snapbacks.empty:
        print("  No data")
        return
    
    sb = snapbacks
    print(f"\n  Snap-back (within 30 bars after trigger):")
    print(f"    Avg:    {sb['max_snapback_pct'].mean():.2f}%")
    print(f"    Median: {sb['max_snapback_pct'].median():.2f}%")
    print(f"    Min:    {sb['max_snapback_pct'].min():.2f}%")
    print(f"    Max:    {sb['max_snapback_pct'].max():.2f}%")
    print(f"    Bars to peak (avg): {sb['bars_to_peak'].mean():.1f}")
    
    print(f"\n  Recovery rates:")
    print(f"    Reached MA21:     {sb['reached_ma21'].mean()*100:.0f}%  ({sb['reached_ma21'].sum()}/{len(sb)})")
    print(f"    Reached EMA34:    {sb['reached_ema34'].mean()*100:.0f}%  ({sb['reached_ema34'].sum()}/{len(sb)})")
    print(f"    Reached midpoint: {sb['reached_mean'].mean()*100:.0f}%  ({sb['reached_mean'].sum()}/{len(sb)})")
    
    print(f"\n  Further DD before snap (avg: {sb['max_dd_first'].mean():.2f}% | max: {sb['max_dd_first'].max():.2f}%)")
    
    print(f"\n  Snap-back size distribution:")
    bins = [(-999,0), (0,1), (1,2), (2,3), (3,5), (5,10), (10,20), (20,999)]
    labels = ['<0%','0-1%','1-2%','2-3%','3-5%','5-10%','10-20%','>20%']
    for (lo, hi), lbl in zip(bins, labels):
        count = ((sb['max_snapback_pct'] >= lo) & (sb['max_snapback_pct'] < hi)).sum()
        bar = '█' * count
        pct = count / len(sb) * 100
        print(f"    {lbl:>7}: {bar} ({count}, {pct:.0f}%)")

if __name__ == "__main__":
    print("BTC GRID STRATEGY — TRIGGER & SNAP-BACK ANALYSIS")
    print("MAs: EMA 34 + MA 21 (same as chart)")
    
    for interval in ["4h", "1h"]:
        for threshold in [0.5, 1.0, 1.5]:
            run_analysis(interval, threshold)
    
    print("\n\nSUMMARY TABLE")
    print(f"{'Timeframe':<8} {'Threshold':<10} {'Events/mo':<12} {'Avg snap%':<12} {'Med snap%':<12} {'Avg DD%':<10} {'Reach MA21'}")
    print("-" * 80)
    for interval in ["4h", "1h"]:
        df = load_candles("BTC", interval)
        df = add_indicators(df)
        df = df.dropna(subset=['ema34','ma21']).reset_index(drop=True)
        months = (df['time'].max() - df['time'].min()).days / 30
        for threshold in [0.5, 1.0, 1.5]:
            events = find_trigger_events(df, threshold)
            sb = analyze_snapbacks(df, events, lookahead_bars=30)
            if sb.empty: continue
            print(f"{interval.upper():<8} {threshold:<10.1f} {len(events)/months:<12.1f} "
                  f"{sb['max_snapback_pct'].mean():<12.2f} {sb['max_snapback_pct'].median():<12.2f} "
                  f"{sb['max_dd_first'].mean():<10.2f} {sb['reached_ma21'].mean()*100:.0f}%")
