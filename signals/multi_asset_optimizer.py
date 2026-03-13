"""
Multi-Asset Grid Strategy Optimizer with Walk-Forward Validation
Performs parameter optimization per coin with anti-overfit controls.

For each eligible coin (ETH, SOL, XRP), this:
1. Loads 4h candle data
2. Runs walk-forward optimization (train/test splits)
3. Searches over: MA pairs, trigger thresholds, TP%, max hold, leverage
4. Ranks by risk-adjusted metrics
5. Outputs JSON + CSV results
"""

import sys
import pandas as pd
import numpy as np
import gzip, csv, json, time, itertools
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple, Dict
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"
OUT_DIR  = Path(__file__).parent / "multi_asset_results"
OUT_DIR.mkdir(exist_ok=True)

# ─── Fee constants (Brian's actual tier) ─────────────────────────────────
TAKER_FEE = 0.000432
MAKER_FEE = 0.000144
FUNDING_PER_8H = 0.0013 / 100  # as fraction
MAINT_MARGIN_RATE = 0.005

# ─── Asset-specific configs ──────────────────────────────────────────────
ASSET_CONFIGS = {
    'BTC': {'max_leverage': 40, 'sz_decimals': 5, 'price_rounding': 1},
    'ETH': {'max_leverage': 25, 'sz_decimals': 4, 'price_rounding': 2},
    'SOL': {'max_leverage': 20, 'sz_decimals': 2, 'price_rounding': 2},
    'XRP': {'max_leverage': 20, 'sz_decimals': 0, 'price_rounding': 4},
}

# ─── Parameter search space ─────────────────────────────────────────────
PARAM_GRID = {
    'ema_span':        [21, 34, 55],
    'ma_period':       [10, 14, 21],
    'ma_type':         ['sma'],
    'long_trigger_pct': [0.3, 0.5, 1.0],
    'short_trigger_pct': [1.5, 2.5, 3.5],
    'tp_pct':          [0.3, 0.5, 1.0],
    'max_hold_bars':   [20, 30],
    'leverage_long':   [20],
    'leverage_short':  [15],
}


@dataclass
class TradeResult:
    side: str       # 'long' or 'short'
    entry_bar: int
    exit_bar: int
    entry_px: float
    exit_px: float
    blended_entry: float
    max_level: int
    pnl_usd: float
    pnl_pct: float
    hold_bars: int
    exit_reason: str   # 'tp' or 'timeout'
    margin_used: float


@dataclass
class BacktestResult:
    coin: str
    params: dict
    total_trades: int
    long_trades: int
    short_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    avg_hold_bars: float
    avg_pnl_per_trade: float
    max_level_hit_avg: float
    liquidation_events: int
    trades_per_month: float
    calmar_ratio: float
    sortino_ratio: float
    window_label: str = ""


def load_candles(coin: str, interval: str = '4h') -> pd.DataFrame:
    path = DATA_DIR / f"candles_{coin}_{interval}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"No data: {path}")
    with gzip.open(path, 'rt') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    df = pd.DataFrame(rows)
    # Handle both column formats
    if 'open' in df.columns:
        df = df.rename(columns={'open':'o','high':'h','low':'l','close':'c','volume':'v','open_time_ms':'t'})
    for col in ['o', 'h', 'l', 'c']:
        df[col] = df[col].astype(float)
    df['v'] = pd.to_numeric(df['v'], errors='coerce').fillna(0)
    df['t'] = pd.to_numeric(df['t'])
    df = df.sort_values('t').reset_index(drop=True)
    return df


def compute_mas(closes: pd.Series, ema_span: int, ma_period: int, ma_type: str):
    ema = closes.ewm(span=ema_span, adjust=False).mean()
    if ma_type == 'sma':
        ma = closes.rolling(ma_period).mean()
    else:
        ma = closes.ewm(span=ma_period, adjust=False).mean()
    return ema, ma


def run_backtest(df: pd.DataFrame, coin: str, params: dict,
                 account_usd: float = 400.0) -> BacktestResult:
    """
    Event-driven dual-sided grid backtest.
    Mirrors the live grid_bot logic as closely as possible.
    """
    closes = df['c'].values
    highs = df['h'].values
    lows = df['l'].values
    n = len(closes)

    ema_span = params['ema_span']
    ma_period = params['ma_period']
    ma_type = params['ma_type']
    long_trig = params['long_trigger_pct']
    short_trig = params['short_trigger_pct']
    tp_pct = params['tp_pct']
    max_hold = params['max_hold_bars']
    lev_long = params['leverage_long']
    lev_short = params['leverage_short']

    # Compute MAs
    close_series = pd.Series(closes)
    ema_arr = close_series.ewm(span=ema_span, adjust=False).mean().values
    if ma_type == 'sma':
        ma_arr = close_series.rolling(ma_period).mean().values
    else:
        ma_arr = close_series.ewm(span=ma_period, adjust=False).mean().values

    # Grid params
    num_levels = 5
    base_margin_pct = 0.016
    multiplier = 2.0
    level_gaps = [0.5, 1.5, 3.0, 3.0]
    cum_drops = []
    acc = 0.0
    for g in level_gaps:
        acc += g
        cum_drops.append(acc / 100)

    trades: List[TradeResult] = []
    equity = account_usd
    peak_equity = equity
    max_dd = 0.0
    liquidations = 0

    # Grid state
    long_active = False
    short_active = False
    long_levels = []  # list of (level, entry_px, margin, qty, filled)
    short_levels = []
    long_tp_px = 0.0
    short_tp_px = 0.0
    long_open_bar = 0
    short_open_bar = 0
    cooldown_until = 0

    warmup = max(ema_span, ma_period) + 5

    for i in range(warmup, n):
        px = closes[i]
        hi = highs[i]
        lo = lows[i]
        ema_val = ema_arr[i]
        ma_val = ma_arr[i]

        if np.isnan(ema_val) or np.isnan(ma_val):
            continue

        pct_below_ema = (ema_val - px) / ema_val * 100
        pct_below_ma = (ma_val - px) / ma_val * 100
        pct_above_ema = (px - ema_val) / ema_val * 100
        pct_above_ma = (px - ma_val) / ma_val * 100

        # ── Check long grid ──
        if long_active:
            # Check level fills (low of bar touches target)
            for lv in long_levels:
                if not lv['filled'] and lo <= lv['target_px']:
                    lv['filled'] = True
                    lv['fill_px'] = lv['target_px']

            # Recalc blended
            filled = [l for l in long_levels if l['filled']]
            if filled:
                total_qty = sum(l['qty'] for l in filled)
                blended = sum(l['qty'] * l['fill_px'] for l in filled) / total_qty
                long_tp_px = blended * (1 + tp_pct / 100)
                total_margin = sum(l['margin'] for l in filled)

                # Check TP
                if hi >= long_tp_px:
                    # Fees
                    notional = total_qty * blended
                    open_fee = notional * TAKER_FEE  # L1 market
                    close_fee = notional * MAKER_FEE  # TP limit
                    funding_cost = notional * FUNDING_PER_8H * (i - long_open_bar) * 4 / 6  # 4h bars, ~6 per 8h period
                    pnl = total_qty * (long_tp_px - blended) - open_fee - close_fee - funding_cost
                    pnl_pct = pnl / total_margin * 100

                    trades.append(TradeResult(
                        'long', long_open_bar, i, closes[long_open_bar], long_tp_px,
                        blended, max(l['level'] for l in filled),
                        pnl, pnl_pct, i - long_open_bar, 'tp', total_margin
                    ))
                    equity += pnl
                    long_active = False
                    long_levels = []
                    cooldown_until = i + 1

                # Check timeout
                elif (i - long_open_bar) >= max_hold:
                    notional = total_qty * blended
                    open_fee = notional * TAKER_FEE
                    close_fee = notional * TAKER_FEE  # timeout = market close
                    funding_cost = notional * FUNDING_PER_8H * (i - long_open_bar) * 4 / 6
                    pnl = total_qty * (px - blended) - open_fee - close_fee - funding_cost
                    pnl_pct = pnl / total_margin * 100

                    trades.append(TradeResult(
                        'long', long_open_bar, i, closes[long_open_bar], px,
                        blended, max(l['level'] for l in filled),
                        pnl, pnl_pct, i - long_open_bar, 'timeout', total_margin
                    ))
                    equity += pnl
                    long_active = False
                    long_levels = []
                    cooldown_until = i + 1

                # Check liquidation proxy
                unrealized = total_qty * (px - blended)
                if equity + unrealized < total_margin * MAINT_MARGIN_RATE:
                    liquidations += 1
                    equity = max(equity * 0.1, 1.0)  # devastating loss
                    long_active = False
                    long_levels = []
                    cooldown_until = i + 5

        # ── Check short grid ──
        if short_active:
            for lv in short_levels:
                if not lv['filled'] and hi >= lv['target_px']:
                    lv['filled'] = True
                    lv['fill_px'] = lv['target_px']

            filled = [l for l in short_levels if l['filled']]
            if filled:
                total_qty = sum(l['qty'] for l in filled)
                blended = sum(l['qty'] * l['fill_px'] for l in filled) / total_qty
                short_tp_px = blended * (1 - tp_pct / 100)
                total_margin = sum(l['margin'] for l in filled)

                if lo <= short_tp_px:
                    notional = total_qty * blended
                    open_fee = notional * TAKER_FEE
                    close_fee = notional * MAKER_FEE
                    funding_cost = notional * FUNDING_PER_8H * (i - short_open_bar) * 4 / 6
                    pnl = total_qty * (blended - short_tp_px) - open_fee - close_fee - funding_cost
                    pnl_pct = pnl / total_margin * 100

                    trades.append(TradeResult(
                        'short', short_open_bar, i, closes[short_open_bar], short_tp_px,
                        blended, max(l['level'] for l in filled),
                        pnl, pnl_pct, i - short_open_bar, 'tp', total_margin
                    ))
                    equity += pnl
                    short_active = False
                    short_levels = []
                    cooldown_until = i + 1

                elif (i - short_open_bar) >= max_hold:
                    notional = total_qty * blended
                    open_fee = notional * TAKER_FEE
                    close_fee = notional * TAKER_FEE
                    funding_cost = notional * FUNDING_PER_8H * (i - short_open_bar) * 4 / 6
                    pnl = total_qty * (blended - px) - open_fee - close_fee - funding_cost
                    pnl_pct = pnl / total_margin * 100

                    trades.append(TradeResult(
                        'short', short_open_bar, i, closes[short_open_bar], px,
                        blended, max(l['level'] for l in filled),
                        pnl, pnl_pct, i - short_open_bar, 'timeout', total_margin
                    ))
                    equity += pnl
                    short_active = False
                    short_levels = []
                    cooldown_until = i + 1

                unrealized = total_qty * (blended - px)
                if equity + unrealized < total_margin * MAINT_MARGIN_RATE:
                    liquidations += 1
                    equity = max(equity * 0.1, 1.0)
                    short_active = False
                    short_levels = []
                    cooldown_until = i + 5

        # ── Open new grids ──
        if i < cooldown_until:
            continue

        if not long_active and not short_active:
            base_margin = equity * base_margin_pct

            if pct_below_ema >= long_trig and pct_below_ma >= long_trig:
                long_active = True
                long_open_bar = i
                long_levels = []
                for lvl in range(num_levels):
                    margin = base_margin * (multiplier ** lvl)
                    notional = margin * lev_long
                    if lvl == 0:
                        target = px
                    else:
                        target = px * (1 - cum_drops[lvl - 1])
                    qty = notional / target
                    long_levels.append({
                        'level': lvl + 1,
                        'target_px': target,
                        'margin': margin,
                        'qty': qty,
                        'filled': lvl == 0,
                        'fill_px': px if lvl == 0 else 0,
                    })

            elif pct_above_ema >= short_trig and pct_above_ma >= short_trig:
                short_active = True
                short_open_bar = i
                short_levels = []
                for lvl in range(num_levels):
                    margin = base_margin * (multiplier ** lvl)
                    notional = margin * lev_short
                    if lvl == 0:
                        target = px
                    else:
                        target = px * (1 + cum_drops[lvl - 1])
                    qty = notional / target
                    short_levels.append({
                        'level': lvl + 1,
                        'target_px': target,
                        'margin': margin,
                        'qty': qty,
                        'filled': lvl == 0,
                        'fill_px': px if lvl == 0 else 0,
                    })

        # Track drawdown
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        max_dd = max(max_dd, dd)

    # Compute summary stats
    if not trades:
        return BacktestResult(
            coin=coin, params=params, total_trades=0, long_trades=0, short_trades=0,
            win_rate=0, total_pnl=0, total_pnl_pct=0, max_drawdown_pct=0,
            sharpe_ratio=0, profit_factor=0, avg_hold_bars=0, avg_pnl_per_trade=0,
            max_level_hit_avg=0, liquidation_events=liquidations, trades_per_month=0,
            calmar_ratio=0, sortino_ratio=0
        )

    pnls = [t.pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100
    avg_pnl = np.mean(pnls)
    std_pnl = np.std(pnls) if len(pnls) > 1 else 1e-9
    sharpe = avg_pnl / std_pnl * np.sqrt(len(pnls)) if std_pnl > 0 else 0
    profit_factor = sum(wins) / abs(sum(losses)) if losses else float('inf')
    avg_hold = np.mean([t.hold_bars for t in trades])
    max_level_avg = np.mean([t.max_level for t in trades])

    # Time span in months
    dt_range = (df['t'].iloc[-1] - df['t'].iloc[warmup]) / (30.44 * 24 * 3600 * 1000)
    trades_per_month = len(trades) / max(dt_range, 1)

    calmar = (total_pnl / account_usd * 100) / max_dd if max_dd > 0 else float('inf')

    # Sortino
    downside = [p for p in pnls if p < 0]
    downside_std = np.std(downside) if downside else 1e-9
    sortino = avg_pnl / downside_std * np.sqrt(len(pnls)) if downside_std > 0 else 0

    return BacktestResult(
        coin=coin,
        params=params,
        total_trades=len(trades),
        long_trades=sum(1 for t in trades if t.side == 'long'),
        short_trades=sum(1 for t in trades if t.side == 'short'),
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl / account_usd * 100,
        max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe,
        profit_factor=profit_factor,
        avg_hold_bars=avg_hold,
        avg_pnl_per_trade=avg_pnl,
        max_level_hit_avg=max_level_avg,
        liquidation_events=liquidations,
        trades_per_month=trades_per_month,
        calmar_ratio=calmar,
        sortino_ratio=sortino,
    )


def walk_forward_optimize(coin: str, n_splits: int = 3) -> Dict:
    """
    Walk-forward optimization:
    - Split data into n_splits windows
    - For each window: train on first 70%, test on last 30%
    - Find best params on train, validate on test
    - Return params that are stable across windows
    """
    print(f"\n{'='*60}")
    print(f"  Walk-Forward Optimization: {coin}")
    print(f"{'='*60}")

    df = load_candles(coin, '4h')
    n = len(df)
    print(f"  Loaded {n} candles")

    # Create walk-forward windows
    window_size = n // n_splits
    windows = []
    for w in range(n_splits):
        start = w * window_size
        end = min(start + window_size, n)
        train_end = start + int((end - start) * 0.7)
        windows.append({
            'train': (start, train_end),
            'test': (train_end, end),
            'label': f"W{w+1}"
        })

    # Also add full-sample as reference
    train_end_full = int(n * 0.7)
    windows.append({
        'train': (0, train_end_full),
        'test': (train_end_full, n),
        'label': 'FULL'
    })

    # Build param combinations (reduced for speed)
    keys = list(PARAM_GRID.keys())
    combos = list(itertools.product(*[PARAM_GRID[k] for k in keys]))
    print(f"  Parameter combinations: {len(combos)}")

    all_window_results = []

    for w_info in windows:
        w_label = w_info['label']
        train_start, train_end = w_info['train']
        test_start, test_end = w_info['test']

        df_train = df.iloc[train_start:train_end].reset_index(drop=True)
        df_test = df.iloc[test_start:test_end].reset_index(drop=True)

        print(f"\n  Window {w_label}: train={len(df_train)} bars, test={len(df_test)} bars")

        # Train: find top 10 params
        train_results = []
        for ci, combo in enumerate(combos):
            if ci % 100 == 0:
                print(f"    ...testing combo {ci}/{len(combos)}", flush=True)
            params = dict(zip(keys, combo))
            # Clamp leverage to asset max
            max_lev = ASSET_CONFIGS[coin]['max_leverage']
            params['leverage_long'] = min(params['leverage_long'], max_lev)
            params['leverage_short'] = min(params['leverage_short'], max_lev)

            try:
                result = run_backtest(df_train, coin, params)
                if result.total_trades >= 5:  # minimum trade threshold
                    train_results.append(result)
            except Exception as e:
                continue

        if not train_results:
            print(f"    No valid results in train window {w_label}")
            continue

        # Rank by composite score: Sharpe * sqrt(trades) * profit_factor, penalize liquidations
        for r in train_results:
            r._composite = (
                r.sharpe_ratio *
                np.sqrt(max(r.total_trades, 1)) *
                min(r.profit_factor, 5.0) *
                (0.5 ** r.liquidation_events)  # halve score per liquidation
            )

        train_results.sort(key=lambda r: r._composite, reverse=True)
        top_train = train_results[:10]

        print(f"    Top train params: Sharpe={top_train[0].sharpe_ratio:.2f}, "
              f"PnL=${top_train[0].total_pnl:.2f}, Win={top_train[0].win_rate:.1f}%, "
              f"Trades={top_train[0].total_trades}")

        # Test: validate top params on out-of-sample data
        for r_train in top_train:
            try:
                r_test = run_backtest(df_test, coin, r_train.params)
                r_test.window_label = w_label
                all_window_results.append({
                    'window': w_label,
                    'params': r_train.params,
                    'train': asdict(r_train),
                    'test': asdict(r_test),
                    'train_composite': r_train._composite,
                    'test_composite': (
                        r_test.sharpe_ratio *
                        np.sqrt(max(r_test.total_trades, 1)) *
                        min(r_test.profit_factor, 5.0) *
                        (0.5 ** r_test.liquidation_events)
                    ),
                    'degradation': (
                        (r_test.sharpe_ratio - r_train.sharpe_ratio) / max(abs(r_train.sharpe_ratio), 0.01) * 100
                        if r_train.sharpe_ratio != 0 else 0
                    ),
                })
            except Exception as e:
                continue

    if not all_window_results:
        return {'coin': coin, 'error': 'No valid results', 'best_params': None}

    # Find most robust params: best average test performance with low degradation
    param_scores = {}
    for r in all_window_results:
        key = json.dumps(r['params'], sort_keys=True)
        if key not in param_scores:
            param_scores[key] = {'params': r['params'], 'test_composites': [], 'degradations': [], 'details': []}
        param_scores[key]['test_composites'].append(r['test_composite'])
        param_scores[key]['degradations'].append(r['degradation'])
        param_scores[key]['details'].append(r)

    # Robustness score: mean test composite * (1 - abs(mean degradation)/100) * consistency
    ranked = []
    for key, info in param_scores.items():
        mean_test = np.mean(info['test_composites'])
        mean_deg = np.mean([abs(d) for d in info['degradations']])
        consistency = 1 - np.std(info['test_composites']) / (abs(mean_test) + 1e-9)
        robustness = mean_test * max(1 - mean_deg / 200, 0.1) * max(consistency, 0.1)
        ranked.append({
            'params': info['params'],
            'robustness': robustness,
            'mean_test_composite': mean_test,
            'mean_degradation': np.mean(info['degradations']),
            'n_windows': len(info['test_composites']),
            'details': info['details'],
        })

    ranked.sort(key=lambda x: x['robustness'], reverse=True)
    best = ranked[0]

    # Run full-sample backtest with best params for final stats
    full_result = run_backtest(df, coin, best['params'])

    print(f"\n  BEST PARAMS for {coin}:")
    for k, v in best['params'].items():
        print(f"    {k}: {v}")
    print(f"  Full-sample: PnL=${full_result.total_pnl:.2f} ({full_result.total_pnl_pct:.1f}%), "
          f"Sharpe={full_result.sharpe_ratio:.2f}, MaxDD={full_result.max_drawdown_pct:.1f}%, "
          f"Trades={full_result.total_trades}, WinRate={full_result.win_rate:.1f}%, "
          f"Liquidations={full_result.liquidation_events}")

    return {
        'coin': coin,
        'best_params': best['params'],
        'robustness_score': best['robustness'],
        'mean_oos_degradation': best['mean_degradation'],
        'full_sample': asdict(full_result),
        'top_5': [{
            'params': r['params'],
            'robustness': r['robustness'],
            'mean_test_composite': r['mean_test_composite'],
        } for r in ranked[:5]],
        'all_window_details': best['details'],
    }


def run_all():
    """Run optimization for all eligible coins."""
    coins = ['ETH', 'SOL', 'XRP', 'BTC']  # ETH first per request, BTC for reference
    results = {}

    for coin in coins:
        try:
            r = walk_forward_optimize(coin)
            results[coin] = r

            # Save per-coin JSON
            with open(OUT_DIR / f"optimization_{coin}.json", 'w') as f:
                json.dump(r, f, indent=2, default=str)
            print(f"  Saved {coin} results to multi_asset_results/")

        except Exception as e:
            print(f"  ERROR on {coin}: {e}")
            import traceback
            traceback.print_exc()
            results[coin] = {'coin': coin, 'error': str(e)}

    # Save combined results
    with open(OUT_DIR / "all_coins_optimization.json", 'w') as f:
        json.dump(results, f, indent=2, default=str)

    # Create rankings CSV
    rankings = []
    for coin, r in results.items():
        if 'error' in r and r.get('best_params') is None:
            continue
        fs = r.get('full_sample', {})
        rankings.append({
            'coin': coin,
            'robustness_score': r.get('robustness_score', 0),
            'total_pnl': fs.get('total_pnl', 0),
            'total_pnl_pct': fs.get('total_pnl_pct', 0),
            'sharpe': fs.get('sharpe_ratio', 0),
            'max_dd_pct': fs.get('max_drawdown_pct', 0),
            'win_rate': fs.get('win_rate', 0),
            'total_trades': fs.get('total_trades', 0),
            'trades_per_month': fs.get('trades_per_month', 0),
            'liquidation_events': fs.get('liquidation_events', 0),
            'calmar': fs.get('calmar_ratio', 0),
            'sortino': fs.get('sortino_ratio', 0),
            'profit_factor': fs.get('profit_factor', 0),
            'oos_degradation': r.get('mean_oos_degradation', 0),
        })

    rankings.sort(key=lambda x: x['robustness_score'], reverse=True)

    with open(OUT_DIR / "rankings.csv", 'w', newline='') as f:
        if rankings:
            writer = csv.DictWriter(f, fieldnames=rankings[0].keys())
            writer.writeheader()
            writer.writerows(rankings)

    with open(OUT_DIR / "rankings.json", 'w') as f:
        json.dump(rankings, f, indent=2)

    print(f"\n{'='*60}")
    print("  FINAL RANKINGS")
    print(f"{'='*60}")
    for i, r in enumerate(rankings, 1):
        print(f"  #{i} {r['coin']:5s} | Robustness: {r['robustness_score']:8.2f} | "
              f"PnL: ${r['total_pnl']:>8.2f} ({r['total_pnl_pct']:>6.1f}%) | "
              f"Sharpe: {r['sharpe']:5.2f} | MaxDD: {r['max_dd_pct']:5.1f}% | "
              f"Win: {r['win_rate']:5.1f}% | Trades: {r['total_trades']:3d} | "
              f"Liqs: {r['liquidation_events']}")

    return results, rankings


if __name__ == '__main__':
    run_all()
