# v3.0 Free-Form Entry Research — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a research framework that tests ~360 indicator/regime combinations as replacements for the EMA34/SMA14 entry gate, seeking to beat v2.9's 119.1% CAGR with 0 liquidations.

**Architecture:** Reusable backtest engine (`v30_engine.py`) extracted from `backtest_v28.py` with configurable `entry_fn` and `regime_fn` callbacks. Indicator library (`v30_indicators.py`) precomputes all indicators on 4H bars. Phase scripts define config grids and call the engine.

**Tech Stack:** Python 3, pandas, numpy, multiprocessing, JSON for results.

---

### Task 1: Create `v30_indicators.py` — Indicator Library

**Files:**
- Create: `strategies/v30/v30_indicators.py`

**Step 1: Write `v30_indicators.py`**

This module loads the parquet data, builds 4H bars, and computes ALL indicators upfront.
Returns a dict-of-numpy-arrays for fast lookup in the sim loop.

```python
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
```

**Step 2: Run a quick smoke test**

```bash
cd C:\ClaudeCode\mrmartingale && python -c "from strategies.v30.v30_indicators import load_data; d = load_data(); print('OK:', len(d['ind']), 'indicators')"
```

Expected: prints indicator count, no errors.

**Step 3: Commit**

```bash
git add strategies/v30/v30_indicators.py
git commit -m "feat(v30): add indicator library with all entry/regime indicators"
```

---

### Task 2: Create `v30_engine.py` — Configurable Backtest Engine

**Files:**
- Create: `strategies/v30/v30_engine.py`

**Step 1: Write `v30_engine.py`**

This is the core simulation loop extracted from `backtest_v28.py`. The critical change:
- **v2.8/v2.9**: Entry requires `pct_below_ema >= trigger AND pct_below_sma >= trigger`, then filter_fn decides to block.
- **v3.0**: `entry_fn(ind, px, prev_candle)` IS the entire long entry condition. No EMA34/SMA14 required.
- `regime_fn(ind, px, prev_candle)` replaces `px > sma440` for favored/unfavored.
- dd20d and RSI rescue are optional layers on top.

The grid logic, exit logic, cost model, and 1-minute liq checks are **byte-for-byte identical** to `backtest_v28.py`.

```python
"""
MRM v3.0 — Configurable Backtest Engine
=========================================
Simulation loop identical to backtest_v28.py except:
  - entry_fn(ind, px, prev_candle) replaces EMA34/SMA14 entry gate
  - regime_fn(ind, px, prev_candle) replaces px > SMA440
  - dd20d and RSI rescue are optional config flags

Grid logic, exit logic, cost model, 1m liq checks: UNCHANGED.
"""
import numpy as np, pandas as pd


def cum_drops(gaps):
    result, acc = [], 0.0
    for g in gaps:
        acc += g
        result.append(acc / 100.0)
    return result


DEFAULT_CONFIG = dict(
    risk_pct=0.50,
    rescue_risk_pct=0.25,
    tp_pct=0.005,
    level_gaps=[0.5, 1.5, 10.0, 14.0],
    level_mults_seq=[2.0, 2.5, 2.5, 7.0],
    max_levels=5,
    short_trigger_pct=0.08,
    unfav_trigger_scale=3.0,
    unfav_risk_scale=0.60,
    unfav_spacing_scale=1.60,
    unfav_hold_scale=0.45,
    max_hold_bars=720,
    min_equity=50,
    # Safety filters
    use_dd20d=True,
    dd20d_threshold=-0.10,
    use_rsi_rescue=True,
    rsi_rescue_thresh=30,
    # Cost model
    comm=0.00045,
    taker=0.000432,
    maker=0.000144,
    fund_8h=0.000013,
    slip=0.03,
    maint=0.005,
)

SIM_START = pd.Timestamp('2018-10-31', tz='UTC')
SIM_END = pd.Timestamp('2026-03-28 23:59:59', tz='UTC')


def run_backtest(data, entry_fn, regime_fn, config=None, label=""):
    """
    Run full backtest with custom entry and regime functions.

    entry_fn(ind, px, prev_candle) -> bool
        Returns True if a long entry should be triggered.
        This REPLACES the EMA34/SMA14 crossunder condition.

    regime_fn(ind, px, prev_candle, sma440_val) -> bool
        Returns True if bull regime (favored for longs).
        Receives sma440_val for configs that still want to use it.

    config: dict overriding DEFAULT_CONFIG values.
    """
    p = {**DEFAULT_CONFIG}
    if config:
        p.update(config)

    # Precompute notional multipliers
    not_mults = [1.0]
    _m = 1.0
    for x in p['level_mults_seq']:
        _m *= x
        not_mults.append(_m)

    # Unpack data
    ind = data['ind']
    sma440_map = data['sma440_map']
    ts_arr, h_arr, l_arr, c_arr = data['ts_arr'], data['h_arr'], data['l_arr'], data['c_arr']
    bounds, bar_to_candle = data['bounds'], data['bar_to_candle']
    n = data['n']

    sim_idx = np.searchsorted(ts_arr, np.datetime64(SIM_START.asm8))
    sim_end_idx = np.searchsorted(ts_arr, np.datetime64(SIM_END.asm8))

    # RSI for rescue (preload)
    rsi_key = f"rsi_{14}"  # always RSI(14) for rescue
    rsi_vals = ind.get(rsi_key)

    # State
    balance = 1000.0
    n_tp = n_to = n_liq = 0
    active = False
    direction = None
    favored = None
    levels = []      # list of (level, price, notional, qty, idx)
    drops = []
    max_hold = 0
    entry_candle = 0
    cooldown_until = 0
    peak_equity = 1000.0
    max_drawdown = 0.0
    level_dist = {}
    long_count = short_count = fav_count = unfav_count = 0
    filtered_count = rescued_count = 0
    liq_events = []
    monthly = {}

    for i in range(min(n, sim_end_idx + 1)):
        ci = bar_to_candle[i]
        is_boundary = (i == bounds[ci])

        # ── Equity tracking ───────────────────────────────────────────
        if i >= sim_idx:
            equity = balance
            if active and levels:
                total_qty = sum(lv[3] for lv in levels)
                blended = sum(lv[3] * lv[1] for lv in levels) / total_qty
                equity = balance + total_qty * (c_arr[i] - blended)
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
            if dd > max_drawdown:
                max_drawdown = dd
            tp_ = pd.Timestamp(ts_arr[i])
            k = (tp_.year, tp_.month)
            if k not in monthly:
                monthly[k] = {'s': equity, 'e': equity}
            monthly[k]['e'] = equity

        # ── Active position management ────────────────────────────────
        if active:
            total_qty = sum(lv[3] for lv in levels)
            blended = sum(lv[3] * lv[1] for lv in levels) / total_qty
            total_notional = sum(lv[2] for lv in levels)

            # Liquidation check on 1m worst-case wick
            worst_px = l_arr[i] if direction == 'long' else h_arr[i]
            unrealized = total_qty * (worst_px - blended)
            if balance + unrealized <= total_notional * p['maint']:
                n_liq += 1
                liq_events.append(
                    f"{pd.Timestamp(ts_arr[i])} {direction} L{len(levels)} eq=${balance:,.0f}")
                balance = 1000.0
                active = False
                levels = []
                cooldown_until = ci + 1
                peak_equity = 1000.0
                continue

            # Grid fills on 1m bars
            if len(levels) < p['max_levels']:
                for li in range(len(levels), p['max_levels']):
                    if li - 1 >= len(drops):
                        break
                    l1_price = levels[0][1]
                    drop_pct = drops[li - 1]
                    if direction == 'long':
                        fill_trigger = l1_price * (1 - drop_pct)
                        hit = l_arr[i] <= fill_trigger
                    else:
                        fill_trigger = l1_price * (1 + drop_pct)
                        hit = h_arr[i] >= fill_trigger
                    if hit:
                        fill_px = (fill_trigger - p['slip']) if direction == 'long' else (fill_trigger + p['slip'])
                        lv_notional = levels[0][2] * not_mults[li]
                        lv_qty = lv_notional / fill_px if direction == 'long' else -lv_notional / fill_px
                        fee_in = lv_notional * (p['maker'] + p['comm'])
                        balance -= fee_in
                        levels.append((li + 1, fill_px, lv_notional, lv_qty, i))
                        break

            # Recompute after potential fill
            total_qty = sum(lv[3] for lv in levels)
            blended = sum(lv[3] * lv[1] for lv in levels) / total_qty
            total_notional = sum(lv[2] for lv in levels)

            # Take-profit on 1m bars
            best_px = h_arr[i] if direction == 'long' else l_arr[i]
            tp_price = blended * (1 + p['tp_pct']) if direction == 'long' else blended * (1 - p['tp_pct'])
            tp_hit = best_px >= tp_price if direction == 'long' else best_px <= tp_price

            if tp_hit:
                exit_px = (tp_price - p['slip']) if direction == 'long' else (tp_price + p['slip'])
                fee_out = total_notional * (p['maker'] + p['comm'])
                hold_minutes = i - levels[0][4]
                funding = total_notional * p['fund_8h'] * (hold_minutes / (8 * 60))
                gross_pnl = total_qty * (exit_px - blended)
                pnl = gross_pnl - fee_out - funding
                balance += pnl
                n_tp += 1
                nl = len(levels)
                level_dist[nl] = level_dist.get(nl, 0) + 1
                if direction == 'long': long_count += 1
                else: short_count += 1
                if favored: fav_count += 1
                else: unfav_count += 1
                active = False
                levels = []
                cooldown_until = ci + 1
                continue

            # Timeout on 4H boundary
            if is_boundary and (ci - entry_candle) >= max_hold:
                exit_px = (c_arr[i] - p['slip']) if direction == 'long' else (c_arr[i] + p['slip'])
                fee_out = total_notional * (p['taker'] + p['comm'])
                hold_minutes = i - levels[0][4]
                funding = total_notional * p['fund_8h'] * (hold_minutes / (8 * 60))
                gross_pnl = total_qty * (exit_px - blended)
                pnl = gross_pnl - fee_out - funding
                balance += pnl
                n_to += 1
                nl = len(levels)
                level_dist[nl] = level_dist.get(nl, 0) + 1
                if direction == 'long': long_count += 1
                else: short_count += 1
                if favored: fav_count += 1
                else: unfav_count += 1
                active = False
                levels = []
                cooldown_until = ci + 1
                continue

        # ── Entry logic (4H boundary only) ────────────────────────────
        if is_boundary and not active:
            if i < sim_idx or ci < cooldown_until or balance < p['min_equity'] or ci < 1:
                continue
            prev_candle = ci - 1
            if prev_candle >= len(ind['ema34']):
                continue

            px = c_arr[i]

            # Regime determination (custom)
            day_ts = np.datetime64(pd.Timestamp(ts_arr[i]).normalize().asm8)
            s440 = sma440_map.get(day_ts)
            if s440 is None or (isinstance(s440, float) and np.isnan(s440)):
                s440 = sma440_map.get(day_ts - np.timedelta64(1, 'D'))
            if s440 is None or (isinstance(s440, float) and np.isnan(s440)):
                s440 = np.nan

            is_bull = regime_fn(ind, px, prev_candle, s440)

            entered = False

            # ── LONG entry (custom entry_fn replaces EMA34/SMA14 gate) ──
            long_favored = is_bull
            risk = p['risk_pct'] if long_favored else p['risk_pct'] * p['unfav_risk_scale']
            hold = p['max_hold_bars'] if long_favored else int(p['max_hold_bars'] * p['unfav_hold_scale'])
            gaps = p['level_gaps'] if long_favored else [g * p['unfav_spacing_scale'] for g in p['level_gaps']]

            if entry_fn(ind, px, prev_candle):
                # dd20d filter (optional)
                use_rescue_risk = False
                skip = False
                if p['use_dd20d']:
                    h20 = ind['high_20d'][prev_candle] if prev_candle < len(ind['high_20d']) else np.nan
                    if not np.isnan(h20) and h20 > 0:
                        dd_from_high = (px / h20) - 1
                        if dd_from_high < p['dd20d_threshold']:
                            # dd20d would block — check RSI rescue
                            if p['use_rsi_rescue'] and rsi_vals is not None:
                                rsi_val = rsi_vals[prev_candle] if prev_candle < len(rsi_vals) else np.nan
                                if not np.isnan(rsi_val) and rsi_val <= p['rsi_rescue_thresh']:
                                    use_rescue_risk = True
                                    rescued_count += 1
                                else:
                                    skip = True
                                    filtered_count += 1
                            else:
                                skip = True
                                filtered_count += 1

                if not skip:
                    entry_risk = p['rescue_risk_pct'] if use_rescue_risk else risk
                    entry_px = px + p['slip']
                    notional = entry_risk * balance
                    qty = notional / entry_px
                    fee_in = notional * (p['taker'] + p['comm'])
                    balance -= fee_in
                    levels = [(1, entry_px, notional, qty, i)]
                    direction = 'long'
                    favored = long_favored
                    drops = cum_drops(gaps)
                    max_hold = hold
                    entry_candle = ci
                    active = True
                    entered = True

            # ── SHORT entry (unchanged — still uses EMA34/SMA14) ─────
            if not entered:
                ema_val = ind['ema34'][prev_candle]
                sma_val = ind['sma14'][prev_candle]
                if not (np.isnan(ema_val) or np.isnan(sma_val)):
                    short_favored = not is_bull
                    trigger = p['short_trigger_pct'] if short_favored else p['short_trigger_pct'] * p['unfav_trigger_scale']
                    s_risk = p['risk_pct'] if short_favored else p['risk_pct'] * p['unfav_risk_scale']
                    s_hold = p['max_hold_bars'] if short_favored else int(p['max_hold_bars'] * p['unfav_hold_scale'])
                    s_gaps = p['level_gaps'] if short_favored else [g * p['unfav_spacing_scale'] for g in p['level_gaps']]

                    pct_above_ema = (px - ema_val) / ema_val
                    pct_above_sma = (px - sma_val) / sma_val

                    if pct_above_ema >= trigger and pct_above_sma >= trigger:
                        entry_px = px - p['slip']
                        notional = s_risk * balance
                        qty = -notional / entry_px
                        fee_in = notional * (p['taker'] + p['comm'])
                        balance -= fee_in
                        levels = [(1, entry_px, notional, qty, i)]
                        direction = 'short'
                        favored = short_favored
                        drops = cum_drops(s_gaps)
                        max_hold = s_hold
                        entry_candle = ci
                        active = True

    # ── Compute stats ─────────────────────────────────────────────────
    total = n_tp + n_to + n_liq
    yrs = (SIM_END - SIM_START).days / 365.25
    cagr = ((balance / 1000) ** (1 / yrs) - 1) if balance > 0 else -1.0

    sorted_months = sorted(monthly.keys())
    n_months = len(sorted_months)
    prod = 1.0
    for ym in sorted_months:
        d = monthly[ym]
        r = d['e'] / d['s'] if d['s'] > 0 else 1
        prod *= r
    cmr = prod ** (1 / n_months) - 1 if n_months > 0 else 0

    return {
        'label': label,
        'cagr': round(cagr * 100, 1),
        'cmr': round(cmr * 100, 2),
        'max_dd': round(max_drawdown * 100, 1),
        'trades': total, 'tp': n_tp, 'to': n_to, 'liq': n_liq,
        'longs': long_count, 'shorts': short_count,
        'fav': fav_count, 'unfav': unfav_count,
        'filtered': filtered_count, 'rescued': rescued_count,
        'final_eq': round(balance, 0),
        'total_return': round(balance / 1000, 1),
        'levels': dict(sorted(level_dist.items())),
        'liq_events': liq_events,
    }
```

**Step 2: Validate engine reproduces v2.8 baseline**

Write a quick test that uses the v3.0 engine with EMA34/SMA14 entry_fn and SMA440 regime_fn (replicating v2.8 exactly). Must produce: 85.6% CAGR, 0 liqs, 889 trades.

```python
# In strategies/v30/v30_validate.py
from v30_indicators import load_data
from v30_engine import run_backtest
import numpy as np

data = load_data()
ind = data['ind']

def v28_entry(ind, px, prev_candle):
    """Replicate v2.8 EMA34+SMA14 entry gate."""
    ema = ind['ema34'][prev_candle]
    sma = ind['sma14'][prev_candle]
    if np.isnan(ema) or np.isnan(sma):
        return False
    # Need to know if favored to pick trigger — but entry_fn doesn't know that.
    # Use the MINIMUM trigger (0.5%) to replicate "any entry that v2.8 would take"
    # The regime/favored logic inside the engine handles the risk/hold scaling.
    # Actually: v2.8 requires BOTH conditions at the appropriate trigger.
    # We need to compute bull regime INSIDE entry_fn for the trigger selection.
    # PROBLEM: entry_fn doesn't have sma440. We need a different approach.
    return False  # placeholder — see Step 3

# This reveals a design issue — see Step 3 for the fix.
```

**Step 3: Fix the entry_fn signature**

The v2.8 entry condition depends on the regime (favored trigger = 0.5%, unfavored = 1.5%). The entry_fn needs access to the favored flag. Two options:

**Option A:** Pass `is_bull` to entry_fn: `entry_fn(ind, px, prev_candle, is_bull) -> bool`
**Option B:** entry_fn doesn't care about regime — it just returns True/False independently.

For v3.0, **Option A** is correct because some entry indicators may want to use different thresholds in bull vs bear (like the original MA triggers did). Update the engine's entry_fn call to:

```python
if entry_fn(ind, px, prev_candle, is_bull):
```

Then the v2.8 replication entry_fn:

```python
def v28_entry(ind, px, prev_candle, is_bull):
    ema = ind['ema34'][prev_candle]
    sma = ind['sma14'][prev_candle]
    if np.isnan(ema) or np.isnan(sma):
        return False
    trigger = 0.005 if is_bull else 0.015
    pct_below_ema = (ema - px) / ema
    pct_below_sma = (sma - px) / sma
    return pct_below_ema >= trigger and pct_below_sma >= trigger
```

**Step 4: Run validation**

```bash
cd C:\ClaudeCode\mrmartingale && python strategies/v30/v30_validate.py
```

Expected: v2.8 baseline reproduced exactly (85.6% CAGR, 0 liqs, 889 trades).

Then test v2.9 replication (same entry_fn + dd20d + RSI rescue):
Expected: 119.1% CAGR, 0 liqs, 1505 trades.

**Step 5: Commit**

```bash
git add strategies/v30/v30_engine.py strategies/v30/v30_validate.py
git commit -m "feat(v30): add configurable backtest engine, validate v2.8+v2.9 reproduction"
```

---

### Task 3: Create `v30_phase1.py` — Single Indicators WITH dd20d

**Files:**
- Create: `strategies/v30/v30_phase1.py`

**Step 1: Write Phase 1 script**

Define entry_fn factories for each indicator. Run all configs with dd20d ON, RSI rescue ON, SMA440 regime.

Entry functions to implement (each is a factory returning an entry_fn):

```python
def make_rsi_entry(period, threshold):
    """Enter when RSI <= threshold (oversold bounce)."""
    key = f'rsi_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val <= threshold
    return entry_fn

def make_stochrsi_entry(rsi_len, stoch_len, smooth_k, low=20, high=80):
    """Enter when StochRSI K <= low (oversold) or K >= high (momentum)."""
    key = f'stoch_k_{rsi_len}_{stoch_len}_{smooth_k}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and (val <= low or val >= high)
    return entry_fn

def make_spanb_entry(period):
    """Enter when price > Span B (support held)."""
    key = f'span_b_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_chandelier_entry(period, mult):
    """Enter when price > chandelier stop (uptrend)."""
    key = f'chand_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_gauss_lower_entry(period, mult):
    """Enter when price <= gaussian lower band (mean reversion)."""
    key = f'gauss_lower_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px <= val
    return entry_fn

def make_donchian_break_entry(period):
    """Enter when price > N-bar high (breakout)."""
    key = f'don_high_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_donchian_support_entry(period, pct=0.02):
    """Enter when price is within pct of N-bar low (bounce from support)."""
    key = f'don_low_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        if np.isnan(val) or val <= 0:
            return False
        return (px / val - 1) <= pct
    return entry_fn

def make_boll_lower_entry(period, mult):
    """Enter when price <= Bollinger lower band (mean reversion)."""
    key = f'boll_lower_{period}_{mult}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px <= val
    return entry_fn

def make_atr_ratio_entry(threshold=0.8):
    """Enter when ATR ratio <= threshold (low vol = calm market)."""
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind['atr_ratio_14_60'][prev_candle]
        return not np.isnan(val) and val <= threshold
    return entry_fn

def make_velocity_entry(period, threshold=0.0):
    """Enter when velocity >= threshold (price rising or stable)."""
    key = f'velocity_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and val >= threshold
    return entry_fn

def make_pivot_break_entry(lookback):
    """Enter when price > last pivot high price (structural breakout)."""
    key = f'last_pivot_price_{lookback}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

def make_ema_crossunder_entry(period, trigger_pct=0.005):
    """Enter when price is trigger_pct below EMA(N) — MA dip buy."""
    key = f'ema_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        ema = ind[key][prev_candle]
        if np.isnan(ema) or ema <= 0:
            return False
        trigger = trigger_pct if is_bull else trigger_pct * 3.0
        return (ema - px) / ema >= trigger
    return entry_fn

def make_price_above_sma_entry(period):
    """Enter when price > SMA(N) — simple trend filter."""
    key = f'sma_{period}'
    def entry_fn(ind, px, prev_candle, is_bull):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return entry_fn

# v2.8 baseline for comparison
def v28_entry(ind, px, prev_candle, is_bull):
    ema = ind['ema34'][prev_candle]
    sma = ind['sma14'][prev_candle]
    if np.isnan(ema) or np.isnan(sma):
        return False
    trigger = 0.005 if is_bull else 0.015
    return (ema - px) / ema >= trigger and (sma - px) / sma >= trigger
```

Config grid (~40 configs):

```python
PHASE1_CONFIGS = [
    # v2.8 baseline
    ('p1_v28_baseline', v28_entry),

    # RSI oversold
    ('p1_rsi7_le25', make_rsi_entry(7, 25)),
    ('p1_rsi7_le30', make_rsi_entry(7, 30)),
    ('p1_rsi14_le25', make_rsi_entry(14, 25)),
    ('p1_rsi14_le30', make_rsi_entry(14, 30)),
    ('p1_rsi14_le35', make_rsi_entry(14, 35)),
    ('p1_rsi14_le40', make_rsi_entry(14, 40)),
    ('p1_rsi21_le30', make_rsi_entry(21, 30)),

    # StochRSI extremes
    ('p1_stoch_4_7_3', make_stochrsi_entry(4, 7, 3)),
    ('p1_stoch_14_14_3', make_stochrsi_entry(14, 14, 3)),
    ('p1_stoch_11_7_20', make_stochrsi_entry(11, 7, 20)),

    # Span B support
    ('p1_spanb_120', make_spanb_entry(120)),
    ('p1_spanb_240', make_spanb_entry(240)),
    ('p1_spanb_350', make_spanb_entry(350)),

    # Chandelier uptrend
    ('p1_chand_22_3', make_chandelier_entry(22, 3.0)),
    ('p1_chand_44_3', make_chandelier_entry(44, 3.0)),
    ('p1_chand_71_3.9', make_chandelier_entry(71, 3.9)),

    # Gaussian lower band (mean reversion)
    ('p1_gauss_91_1.0', make_gauss_lower_entry(91, 1.0)),
    ('p1_gauss_144_1.5', make_gauss_lower_entry(144, 1.5)),
    ('p1_gauss_266_1.9', make_gauss_lower_entry(266, 1.9)),

    # Donchian breakout
    ('p1_don_break_120', make_donchian_break_entry(120)),
    ('p1_don_break_200', make_donchian_break_entry(200)),

    # Donchian support (bounce)
    ('p1_don_supp_120_2pct', make_donchian_support_entry(120, 0.02)),
    ('p1_don_supp_120_5pct', make_donchian_support_entry(120, 0.05)),

    # Bollinger lower band
    ('p1_boll_20_2.0', make_boll_lower_entry(20, 2.0)),
    ('p1_boll_20_2.5', make_boll_lower_entry(20, 2.5)),
    ('p1_boll_50_2.0', make_boll_lower_entry(50, 2.0)),

    # ATR ratio
    ('p1_atr_ratio_0.6', make_atr_ratio_entry(0.6)),
    ('p1_atr_ratio_0.8', make_atr_ratio_entry(0.8)),
    ('p1_atr_ratio_1.0', make_atr_ratio_entry(1.0)),

    # Velocity
    ('p1_vel_6_0pct', make_velocity_entry(6, 0.0)),
    ('p1_vel_12_0pct', make_velocity_entry(12, 0.0)),
    ('p1_vel_24_neg1pct', make_velocity_entry(24, -0.01)),

    # Pivot breakout
    ('p1_pivot_5', make_pivot_break_entry(5)),
    ('p1_pivot_7', make_pivot_break_entry(7)),
    ('p1_pivot_10', make_pivot_break_entry(10)),

    # EMA crossunder (different periods)
    ('p1_ema20_cross', make_ema_crossunder_entry(20, 0.005)),
    ('p1_ema50_cross', make_ema_crossunder_entry(50, 0.005)),
    ('p1_ema100_cross', make_ema_crossunder_entry(100, 0.005)),
    ('p1_ema200_cross', make_ema_crossunder_entry(200, 0.005)),

    # Price above SMA (trend)
    ('p1_above_sma50', make_price_above_sma_entry(50)),
    ('p1_above_sma200', make_price_above_sma_entry(200)),
]
```

Regime function for Phase 1 (SMA440 baseline):

```python
def sma440_regime(ind, px, prev_candle, sma440_val):
    if sma440_val is None or (isinstance(sma440_val, float) and np.isnan(sma440_val)):
        return True  # default to bull if no data
    return px > sma440_val
```

Run loop with progress printing and JSON output:

```python
data = load_data()
results = []
for label, entry_fn in PHASE1_CONFIGS:
    r = run_backtest(data, entry_fn, sma440_regime,
                     config={'use_dd20d': True, 'use_rsi_rescue': True},
                     label=label)
    results.append(r)
    liq_str = f"!! {r['liq']} LIQS" if r['liq'] > 0 else "0 liq"
    print(f"  {label:<30} CAGR={r['cagr']:>6.1f}%  MaxDD={r['max_dd']:>5.1f}%  "
          f"trades={r['trades']:>5}  {liq_str}")

# Save results
with open('strategies/v30/v30_phase1_results.json', 'w') as f:
    json.dump(results, f, indent=2)
```

**Step 2: Run Phase 1**

```bash
cd C:\ClaudeCode\mrmartingale && python strategies/v30/v30_phase1.py
```

Expected: ~40 results, each with CAGR/MaxDD/liqs/trades. Runtime: ~20-40 min.

**Step 3: Commit**

```bash
git add strategies/v30/v30_phase1.py strategies/v30/v30_phase1_results.json
git commit -m "feat(v30): phase 1 — single indicators with dd20d"
```

---

### Task 4: Create `v30_phase2.py` — Same Indicators WITHOUT dd20d

**Files:**
- Create: `strategies/v30/v30_phase2.py`

**Step 1: Write Phase 2 script**

Reuses the same entry_fn factories from Phase 1. Only difference: `use_dd20d=False, use_rsi_rescue=False`.

```python
# Same PHASE1_CONFIGS list, but with:
config = {'use_dd20d': False, 'use_rsi_rescue': False}
```

After running, compare Phase 1 vs Phase 2 side-by-side and produce dd20d verdict:

```python
# Load phase 1 results
with open('strategies/v30/v30_phase1_results.json') as f:
    p1 = {r['label']: r for r in json.load(f)}

# Compare
for label, entry_fn in PHASE1_CONFIGS:
    p2_label = label.replace('p1_', 'p2_')
    r1 = p1.get(label, {})
    r2 = ... # phase 2 result
    # Determine verdict
    if r1.get('liq', 0) == 0 and r2.get('liq', 0) > 0:
        verdict = "dd20d ESSENTIAL"
    elif r1.get('liq', 0) == 0 and r2.get('liq', 0) == 0:
        if r1.get('cagr', 0) > r2.get('cagr', 0) * 1.05:
            verdict = "dd20d HELPFUL"
        elif r2.get('cagr', 0) > r1.get('cagr', 0) * 1.05:
            verdict = "dd20d HARMFUL"
        else:
            verdict = "dd20d UNNECESSARY"
    else:
        verdict = "BOTH HAVE LIQS"
```

**Step 2: Run Phase 2**

```bash
cd C:\ClaudeCode\mrmartingale && python strategies/v30/v30_phase2.py
```

**Step 3: Commit**

```bash
git add strategies/v30/v30_phase2.py strategies/v30/v30_phase2_results.json
git commit -m "feat(v30): phase 2 — single indicators without dd20d, dd20d verdicts"
```

---

### Task 5: Create `v30_phase3.py` — Alternative Regime Filters

**Files:**
- Create: `strategies/v30/v30_phase3.py`

**Step 1: Write Phase 3 script**

Take top 5 entry indicators from Phase 1/2 (by CAGR among 0-liq configs). Cross with 4 alt regime filters plus SMA440 baseline.

Regime function factories:

```python
def make_spanb_regime(period):
    key = f'span_b_{period}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

def make_gauss_mid_regime(period, mult=1.0):
    key = f'gauss_mid_{period}_{mult}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

def make_donchian_mid_regime(period):
    key = f'don_mid_{period}'
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind[key][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

def make_ema200_regime():
    def regime_fn(ind, px, prev_candle, sma440_val):
        val = ind['ema_200'][prev_candle]
        return not np.isnan(val) and px > val
    return regime_fn

REGIME_FILTERS = [
    ('sma440', sma440_regime),
    ('spanb240', make_spanb_regime(240)),
    ('spanb350', make_spanb_regime(350)),
    ('gauss144', make_gauss_mid_regime(144, 1.0)),
    ('don_mid200', make_donchian_mid_regime(200)),
    ('ema200', make_ema200_regime()),
]
```

Grid: top 5 entries × 6 regimes × optimal dd20d mode = ~60-80 configs.

**Step 2: Run, commit** (same pattern as Tasks 3-4)

---

### Task 6: Create `v30_phase3b.py` — Parameter Sweep

**Files:**
- Create: `strategies/v30/v30_phase3b.py`

Take top 5-10 indicator/regime combos with 0 liqs from Phases 1-3.
Sweep 4-6 values per key parameter. For example:
- RSI: period [7, 10, 14, 21], threshold [20, 25, 28, 30, 35, 40]
- Span B: period [120, 180, 240, 300, 350, 462]
- Chandelier: atr_len [22, 33, 44, 55, 71], mult [2.0, 2.5, 3.0, 3.5, 3.9]
- etc.

~60 configs. Save to `v30_phase3b_results.json`.

---

### Task 7: Create `v30_phase4.py` — Two-Indicator Combinations

**Files:**
- Create: `strategies/v30/v30_phase4.py`

Combine top single indicators as AND or OR:

```python
def make_and_entry(entry_fn_a, entry_fn_b):
    """Both indicators must confirm."""
    def entry_fn(ind, px, prev_candle, is_bull):
        return entry_fn_a(ind, px, prev_candle, is_bull) and entry_fn_b(ind, px, prev_candle, is_bull)
    return entry_fn

def make_or_entry(entry_fn_a, entry_fn_b):
    """Either indicator triggers."""
    def entry_fn(ind, px, prev_candle, is_bull):
        return entry_fn_a(ind, px, prev_candle, is_bull) or entry_fn_b(ind, px, prev_candle, is_bull)
    return entry_fn
```

Each combo uses its optimal regime and dd20d settings from prior phases.
~60 configs.

---

### Task 8: Create `v30_phase5.py` — Three-Indicator Combinations

**Files:**
- Create: `strategies/v30/v30_phase5.py`

Add third indicator as quality gate to Phase 4 winners. ~30 configs.

---

### Task 9: Create `v30_phase6.py` — Risk Tuning

**Files:**
- Create: `strategies/v30/v30_phase6.py`

Sweep risk_pct (0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54) and rescue_risk (0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30) on top 0-liq configs from Phases 4-5.

~40 configs.

---

### Task 10: Create `v30_phase7.py` — Final Comparison

**Files:**
- Create: `strategies/v30/v30_phase7.py`
- Create: `strategies/v30/v30-research-summary.md`

Load all phase results. Rank all 0-liq configs by CAGR. Compare top 5 against v2.9 baseline.

Output:
1. Summary table: all 0-liq configs ranked by CAGR
2. v3.0 candidate (if found): full stats, entry/regime definition
3. Research narrative: what worked, what didn't, why

If v3.0 candidate beats v2.9: create `v30-spec.md` with full specification.

---

### Task 11: Commit final results

```bash
git add strategies/v30/
git commit -m "feat(v30): complete free-form entry research — X configs tested"
```

---

## Execution Notes

- **Data load is expensive** (~10-15s). Each phase script loads data ONCE and runs all configs in sequence.
- **Each backtest takes ~15-30s** on 4.5M 1-minute bars. Total runtime for ~360 configs: ~2-3 hours.
- **Phases are sequential** — later phases depend on results from earlier phases.
- **Resume-safe** — each phase saves JSON results. If interrupted, re-run only the incomplete phase.
- **The engine validation (Task 2) is the most critical step.** If it doesn't reproduce v2.8/v2.9 exactly, everything downstream is invalid.
