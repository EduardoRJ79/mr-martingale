"""
MRM v3.0 — Indicator Library
=============================
Precomputes all entry/regime indicators on 4H bars.
Returns numpy arrays indexed by 4H candle number.
"""
import pandas as pd, numpy as np, os

DATA_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
    'signals', 'multi_asset_results', 'btcusdt_binance_1m_2017_2026.parquet')
DATA_PATH = os.path.normpath(DATA_PATH)


def compute_rsi(series, period=14):
    """Wilder RSI on any series."""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_stoch_rsi(closes, highs, lows, rsi_len=14, stoch_len=14, smooth_k=3):
    """Stochastic RSI K value on hlcc4 source."""
    hlcc4 = (highs + lows + closes + closes) / 4.0
    delta = hlcc4.diff()
    gain = delta.clip(lower=0).rolling(rsi_len).mean()
    loss = (-delta.clip(upper=0)).rolling(rsi_len).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_min = rsi.rolling(stoch_len).min()
    rsi_max = rsi.rolling(stoch_len).max()
    stoch_raw = 100 * (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    return stoch_raw.rolling(smooth_k).mean()


def compute_span_b(highs, lows, period):
    """Ichimoku-style Span B: midpoint of N-bar high/low range."""
    return (highs.rolling(period).max() + lows.rolling(period).min()) / 2.0


def compute_chandelier(highs, lows, closes, period, mult):
    """Chandelier exit: highest_high(N) - mult * ATR(N)."""
    hh = highs.rolling(period).max()
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return hh - mult * atr


def compute_atr(highs, lows, closes, period):
    """Average True Range."""
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_gaussian_channel(closes, highs, lows, period, mult):
    """Gaussian channel: SMA(period) +/- mult * SMA(TR, period). Returns (mid, upper, lower)."""
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()
    ], axis=1).max(axis=1)
    mid = closes.rolling(period).mean()
    tr_avg = tr.rolling(period).mean()
    return mid, mid + mult * tr_avg, mid - mult * tr_avg


def compute_bollinger(closes, period=20, mult=2.0):
    """Bollinger Bands. Returns (mid, upper, lower)."""
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    return mid, mid + mult * std, mid - mult * std


def compute_donchian(highs, lows, period):
    """Donchian Channel. Returns (high, low, mid)."""
    dh = highs.rolling(period).max()
    dl = lows.rolling(period).min()
    return dh, dl, (dh + dl) / 2.0


def compute_pivot_high(highs, lookback=7):
    """Detect pivot highs: a bar whose high is the highest in [i-lookback, i+lookback].
    Returns boolean array (True at pivot high bars)."""
    n = len(highs)
    result = np.zeros(n, dtype=bool)
    h = highs.values
    for i in range(lookback, n - lookback):
        if h[i] == np.max(h[i - lookback:i + lookback + 1]):
            result[i] = True
    return result


def load_data():
    """Load parquet, build 4H bars, compute all indicators. Returns everything needed."""
    print("Loading data...")
    df = pd.read_parquet(DATA_PATH).sort_values('ts').reset_index(drop=True)
    n = len(df)

    # 4H bars
    df['t4h'] = df['ts'].dt.floor('4h')
    c4 = df.groupby('t4h').agg(
        o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last')
    ).sort_index()

    c4h, c4l, c4c = c4['h'], c4['l'], c4['c']
    hlcc4 = (c4h + c4l + c4c + c4c) / 4.0

    print("Computing indicators...")
    ind = {}  # All indicators as numpy arrays

    # Base MAs (kept for reference/comparison)
    ind['ema34'] = c4c.ewm(span=34, adjust=False).mean().values
    ind['sma14'] = c4c.rolling(14).mean().values
    ind['high_20d'] = c4h.rolling(120).max().values

    # RSI variants (on hlcc4)
    for period in [7, 10, 14, 21]:
        ind[f'rsi_{period}'] = compute_rsi(hlcc4, period).values

    # Stochastic RSI variants
    for rsi_l in [4, 7, 11, 14]:
        for stoch_l in [7, 14, 18]:
            for sk in [3, 10, 20]:
                ind[f'stoch_k_{rsi_l}_{stoch_l}_{sk}'] = compute_stoch_rsi(
                    c4c, c4h, c4l, rsi_l, stoch_l, sk).values

    # Span B variants
    for p in [60, 120, 180, 240, 300, 350, 462]:
        ind[f'span_b_{p}'] = compute_span_b(c4h, c4l, p).values

    # Chandelier variants
    for p in [22, 44, 71]:
        for m in [2.0, 3.0, 3.9]:
            ind[f'chand_{p}_{m}'] = compute_chandelier(c4h, c4l, c4c, p, m).values

    # Gaussian Channel variants
    for p in [91, 144, 200, 266, 300]:
        for m in [0.75, 1.0, 1.5, 1.9]:
            mid, upper, lower = compute_gaussian_channel(c4c, c4h, c4l, p, m)
            ind[f'gauss_mid_{p}_{m}'] = mid.values
            ind[f'gauss_upper_{p}_{m}'] = upper.values
            ind[f'gauss_lower_{p}_{m}'] = lower.values

    # Donchian Channel variants
    for p in [56, 120, 168, 200, 230]:
        dh, dl, dm = compute_donchian(c4h, c4l, p)
        ind[f'don_high_{p}'] = dh.values
        ind[f'don_low_{p}'] = dl.values
        ind[f'don_mid_{p}'] = dm.values

    # Bollinger Bands variants
    for p in [20, 30, 50]:
        for m in [1.5, 2.0, 2.5]:
            mid, upper, lower = compute_bollinger(c4c, p, m)
            ind[f'boll_mid_{p}_{m}'] = mid.values
            ind[f'boll_upper_{p}_{m}'] = upper.values
            ind[f'boll_lower_{p}_{m}'] = lower.values

    # ATR variants
    for p in [14, 22, 44, 60, 120]:
        ind[f'atr_{p}'] = compute_atr(c4h, c4l, c4c, p).values

    # ATR ratio (short/long)
    ind['atr_ratio_14_60'] = (compute_atr(c4h, c4l, c4c, 14) / compute_atr(c4h, c4l, c4c, 60)).values
    ind['atr_ratio_14_120'] = (compute_atr(c4h, c4l, c4c, 14) / compute_atr(c4h, c4l, c4c, 120)).values

    # Price velocity (ROC)
    for p in [6, 12, 24, 48]:
        ind[f'velocity_{p}'] = c4c.pct_change(p).values

    # Pivot High variants
    for lb in [5, 7, 10]:
        ind[f'pivot_high_{lb}'] = compute_pivot_high(c4h, lb)
        # Also: last pivot high PRICE (rolling: most recent pivot high value)
        ph = ind[f'pivot_high_{lb}']
        last_ph_price = np.full(len(c4h), np.nan)
        h_vals = c4h.values
        last_val = np.nan
        for j in range(len(ph)):
            if ph[j]:
                last_val = h_vals[j]
            last_ph_price[j] = last_val
        ind[f'last_pivot_price_{lb}'] = last_ph_price

    # EMA variants (for crossunder entries with different periods)
    for p in [20, 50, 100, 200]:
        ind[f'ema_{p}'] = c4c.ewm(span=p, adjust=False).mean().values

    # SMA variants
    for p in [20, 50, 100, 200]:
        ind[f'sma_{p}'] = c4c.rolling(p).mean().values

    # Daily SMA440 (regime)
    df['t1d'] = df['ts'].dt.floor('1D')
    cd = df.groupby('t1d').agg(c=('c', 'last')).sort_index()
    cd['sma440'] = cd['c'].rolling(440).mean()
    sma440_map = {k: v for k, v in zip(cd.index.values, cd['sma440'].values)}

    # 1m arrays
    ts_arr = df['ts'].values
    h_arr = df['h'].values
    l_arr = df['l'].values
    c_arr = df['c'].values
    t4v = df['t4h'].values

    # 4H boundary index
    bounds = [0]
    for i in range(1, n):
        if t4v[i] != t4v[i - 1]:
            bounds.append(i)
    bounds = np.array(bounds)
    bar_to_candle = np.zeros(n, dtype=np.int64)
    for bi in range(len(bounds)):
        s_ = bounds[bi]
        e_ = bounds[bi + 1] if bi + 1 < len(bounds) else n
        bar_to_candle[s_:e_] = bi

    print(f"Data: {n:,} bars | Indicators: {len(ind)} arrays")

    return {
        'df': df, 'n': n, 'c4': c4, 'ind': ind,
        'sma440_map': sma440_map,
        'ts_arr': ts_arr, 'h_arr': h_arr, 'l_arr': l_arr, 'c_arr': c_arr,
        't4v': t4v, 'bounds': bounds, 'bar_to_candle': bar_to_candle,
    }
