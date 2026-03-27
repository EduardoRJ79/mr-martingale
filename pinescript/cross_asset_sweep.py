#!/usr/bin/env python3
"""
Cross-Asset Strategy Sweep — PineScript Winning Strategies
===========================================================

Tests all 4 winning strategies across multiple assets:
- PINE-001: Sweet v4.4.4
- PINE-003: Swing BTC 4h  
- PINE-004: Swing ETH 4h
- PINE-006: Gaussian V4H v4.0

Methodology:
- 1-minute data, liquidation-restart
- 2021-01-01 to present
- Generate performance matrix
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import warnings
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════════════════════
# PATHS & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

WS = Path("/Users/assistant/.openclaw/ws-731228")
DATA_PATH_BTC = WS / "mr-martingale-v1/signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet"
DATA_PATH_ETH = WS / "mr-martingale-v1/signals/multi_asset_results/ethusdt_binance_1m_2017_2024.parquet"
TODAY = datetime.now().strftime("%Y-%m-%d")

TAKER_FEE = 0.00045
MAINT_RATE = 0.005

# ═══════════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ═══════════════════════════════════════════════════════════════════════════

def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """Calculate Supertrend indicator."""
    high, low, close = df['high'], df['low'], df['close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    
    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr
    
    st_line = pd.Series(index=df.index, dtype=float)
    st_dir = pd.Series(index=df.index, dtype=float)
    
    for i in range(len(df)):
        if i == 0:
            st_line.iloc[i] = upper_band.iloc[i]
            st_dir.iloc[i] = 1
        else:
            prev_close = close.iloc[i-1]
            prev_st = st_line.iloc[i-1]
            
            if prev_close > prev_st:
                st_line.iloc[i] = max(lower_band.iloc[i], prev_st)
                st_dir.iloc[i] = 1 if close.iloc[i] > st_line.iloc[i] else -1
            else:
                st_line.iloc[i] = min(upper_band.iloc[i], prev_st)
                st_dir.iloc[i] = -1 if close.iloc[i] < st_line.iloc[i] else 1
    
    return st_line, st_dir


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def hma(series: pd.Series, period: int) -> pd.Series:
    half_period = int(period / 2)
    sqrt_period = int(np.sqrt(period))
    wma_half = wma(series, half_period)
    wma_full = wma(series, period)
    raw_hma = 2 * wma_half - wma_full
    return wma(raw_hma, sqrt_period)


def tema(series: pd.Series, period: int) -> pd.Series:
    ema1 = ema(series, period)
    ema2 = ema(ema1, period)
    ema3 = ema(ema2, period)
    return 3 * (ema1 - ema2) + ema3


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr_sum = true_range(df).rolling(window=period).sum()
    highest_high = df['high'].rolling(window=period).max()
    lowest_low = df['low'].rolling(window=period).min()
    price_range = highest_high - lowest_low
    chop = 100 * np.log10(tr_sum / price_range) / np.log10(period)
    return chop


def dmi(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    high, low, close = df['high'], df['low'], df['close']
    
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    plus_dm[plus_dm <= minus_dm] = 0
    minus_dm[minus_dm <= plus_dm] = 0
    
    tr = true_range(df)
    atr_val = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr_val
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr_val
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    
    return plus_di, minus_di, adx


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def super_smoother_3pole(src: pd.Series, length: int) -> pd.Series:
    arg = np.pi / length
    a1 = np.exp(-arg)
    b1 = 2 * a1 * np.cos(1.738 * arg)
    c1 = a1 ** 2
    coef4 = c1 ** 2
    coef3 = -(c1 + b1 * c1)
    coef2 = b1 + c1
    coef1 = 1 - coef2 - coef3 - coef4
    
    ssf = pd.Series(index=src.index, dtype=float)
    for i in range(len(src)):
        if i < 3:
            ssf.iloc[i] = src.iloc[i]
        else:
            ssf.iloc[i] = (coef1 * src.iloc[i] + 
                          coef2 * ssf.iloc[i-1] + 
                          coef3 * ssf.iloc[i-2] + 
                          coef4 * ssf.iloc[i-3])
    return ssf


def zlag(src: pd.Series, period: int) -> pd.Series:
    avg1 = super_smoother_3pole(src, period)
    avg2 = super_smoother_3pole(avg1, period)
    return 2.0 * avg1 - avg2


def chandelier_exit(df: pd.DataFrame, lookback: int = 4, multiplier: float = 1.8788) -> Tuple[pd.Series, pd.Series]:
    high, low = df['high'], df['low']
    highest_high = high.rolling(window=lookback).max()
    lowest_low = low.rolling(window=lookback).min()
    range_val = highest_high - lowest_low
    long_stop = highest_high - multiplier * range_val
    short_stop = lowest_low + multiplier * range_val
    return long_stop, short_stop


def gaussian_channel(df: pd.DataFrame, period: int = 144, poles: int = 4, tr_mult: float = 1.414) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Gaussian Channel with configurable poles."""
    hl2 = (df['high'] + df['low']) / 2
    tr = true_range(df)
    
    # Simplified gaussian smoothing (using EMA cascade for poles)
    smooth = hl2.copy()
    for _ in range(poles):
        smooth = ema(smooth, period)
    
    atr_val = tr.ewm(span=period).mean()
    upper = smooth + tr_mult * atr_val
    lower = smooth - tr_mult * atr_val
    
    return upper, lower, smooth


def crossover(s1: pd.Series, s2: pd.Series) -> pd.Series:
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))


def crossunder(s1: pd.Series, s2: pd.Series) -> pd.Series:
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))


# ═══════════════════════════════════════════════════════════════════════════
# SWEET V4.4.4 STRATEGY
# ═══════════════════════════════════════════════════════════════════════════

def run_sweet_v444(df: pd.DataFrame, initial_capital: float = 1000.0, leverage: float = 1.0) -> Dict:
    """Run Sweet v4.4.4 strategy on dataframe.

    Original PineScript designed for 15m bars — resample 1m data to 15m for
    signal generation, then execute trades on 1m bars for liquidation precision.
    """

    # Configuration (tuned for 15m timeframe)
    st_length = 22
    st_multiplier = 5.1813
    cooldown_bars = 4  # 4 × 15m = 1 hour cooldown
    gauss_period = 144
    gauss_poles = 2
    gauss_tr_mult = 0.655
    hma_length = 68
    chop_length = 7
    chop_threshold = 38.2
    tema_length = 95
    dmi_length = 56
    ch_lookback = 4
    ch_multiplier = 1.8788

    # Resample to 15m for signal generation (matches original PineScript)
    df_15m = df.resample('15min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    # Calculate indicators on 15m bars
    st_line, st_dir = supertrend(df_15m, st_length, st_multiplier)
    hma_val = hma(df_15m['close'], hma_length)
    chop = choppiness_index(df_15m, chop_length)
    tema_val = tema(df_15m['close'], tema_length)
    plus_di, minus_di, _ = dmi(df_15m, dmi_length)
    gauss_upper, gauss_lower, gauss_mid = gaussian_channel(df_15m, gauss_period, gauss_poles, gauss_tr_mult)
    ch_long_stop, ch_short_stop = chandelier_exit(df_15m, ch_lookback, ch_multiplier)

    # Signals on 15m
    in_uptrend = st_dir > 0
    long_cond_15m = (
        in_uptrend &
        (df_15m['close'] > hma_val) &
        (chop < chop_threshold) &
        (df_15m['close'] > tema_val) &
        (plus_di > minus_di)
    )

    exit_long_15m = crossunder(df_15m['close'], gauss_upper) | (df_15m['close'] < ch_long_stop)

    # Reindex signals to 1m for execution precision
    long_cond = long_cond_15m.reindex(df.index, method='ffill').fillna(False)
    exit_long = exit_long_15m.reindex(df.index, method='ffill').fillna(False)

    # Only fire on transitions
    long_cond = long_cond & ~long_cond.shift(1).fillna(False)
    exit_long = exit_long & ~exit_long.shift(1).fillna(False)

    # Cooldown in 1m bars (4 × 15 = 60 bars)
    cooldown_bars_1m = cooldown_bars * 15

    # Simulation on 1m bars
    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    cooldown = 0
    trades = []
    liquidations = 0
    equity_curve = [initial_capital]

    for i in range(100, len(df)):
        price = df['close'].iloc[i]

        # Check liquidation
        if position != 0:
            pnl_pct = (price - entry_price) / entry_price * (1 if position > 0 else -1)
            unrealized = position * entry_price * pnl_pct
            equity = capital + unrealized

            if equity <= 0:
                liquidations += 1
                capital = initial_capital
                position = 0
                cooldown = 0
                equity_curve.append(capital)
                continue

        if cooldown > 0:
            cooldown -= 1
            equity_curve.append(capital + (position * (price - entry_price) if position != 0 else 0))
            continue

        # Exit logic
        if position > 0 and exit_long.iloc[i]:
            pnl = position * (price - entry_price)
            fee = abs(position * price) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee, 'exit_price': price})
            position = 0
            cooldown = cooldown_bars_1m

        # Entry logic
        elif position == 0 and long_cond.iloc[i]:
            position_size = (capital * leverage) / price
            position = position_size
            entry_price = price

        equity = capital + (position * (price - entry_price) if position != 0 else 0)
        equity_curve.append(equity)
    
    # Close final position
    if position != 0:
        price = df['close'].iloc[-1]
        pnl = position * (price - entry_price)
        fee = abs(position * price) * TAKER_FEE
        capital += pnl - fee
        trades.append({'pnl': pnl - fee, 'exit_price': price})
    
    # Calculate metrics
    final_equity = equity_curve[-1]
    total_return = (final_equity - initial_capital) / initial_capital * 100
    
    peak = initial_capital
    max_dd = 0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] <= 0]
    
    gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
    gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 1
    
    return {
        'strategy': 'Sweet_v4.4.4',
        'final_equity': final_equity,
        'total_return_pct': total_return,
        'max_drawdown_pct': max_dd,
        'total_trades': len(trades),
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': len(winning_trades) / len(trades) * 100 if trades else 0,
        'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 0,
        'liquidations': liquidations,
        'sharpe': 0,  # Simplified
        'calmar': total_return / max_dd if max_dd > 0 else total_return
    }


# ═══════════════════════════════════════════════════════════════════════════
# SWING BTC 4H STRATEGY (PINE-003)
# Matches runner: EMA 20/50, swing high/low breakout, MACD, volume filter
# ═══════════════════════════════════════════════════════════════════════════

def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Calculate Stochastic oscillator."""
    lowest_low = df['low'].rolling(k_period).min()
    highest_high = df['high'].rolling(k_period).max()
    k = 100 * (df['close'] - lowest_low) / (highest_high - lowest_low)
    d = k.rolling(d_period).mean()
    return k, d


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands."""
    middle = sma(series, period)
    std = series.rolling(period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def run_swing_btc_4h(df: pd.DataFrame, initial_capital: float = 1000.0, leverage: float = 1.0) -> Dict:
    """Run Swing BTC 4H strategy — EMA 20/50, swing breakout, MACD, volume."""

    # Resample to 4H for signals
    df_4h = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    # 4H indicators matching runner
    ema20 = ema(df_4h['close'], 20)
    ema50 = ema(df_4h['close'], 50)
    rsi_val = rsi(df_4h['close'], 14)
    atr_4h = atr(df_4h, 14)
    macd_line, macd_signal, _ = macd(df_4h['close'], 12, 26, 9)

    # Swing high/low (10-bar lookback)
    swing_high = df_4h['high'].rolling(10).max()
    swing_low = df_4h['low'].rolling(10).min()

    # Volume filter
    avg_volume = df_4h['volume'].rolling(20).mean()
    volume_ratio = df_4h['volume'] / avg_volume

    # Signals on 4H matching runner logic
    long_signal_4h = (
        (ema20 > ema50) &                            # EMA bullish
        (df_4h['close'] > swing_high * 0.995) &      # Price near/above swing high
        (rsi_val > 40) & (rsi_val < 70) &             # RSI not overbought
        (macd_line > macd_signal) &                    # MACD bullish
        (volume_ratio > 0.8)                           # Volume OK
    )
    short_signal_4h = (
        (ema20 < ema50) &                             # EMA bearish
        (df_4h['close'] < swing_low * 1.005) &        # Price near/below swing low
        (rsi_val > 30) & (rsi_val < 60) &              # RSI not oversold
        (macd_line < macd_signal) &                    # MACD bearish
        (volume_ratio > 0.8)                           # Volume OK
    )

    # Only fire on transitions (not continuously true)
    long_signal_4h = long_signal_4h & ~long_signal_4h.shift(1).fillna(False)
    short_signal_4h = short_signal_4h & ~short_signal_4h.shift(1).fillna(False)

    # Reindex to 1m
    long_signal = long_signal_4h.reindex(df.index, method='ffill').fillna(False)
    short_signal = short_signal_4h.reindex(df.index, method='ffill').fillna(False)
    
    # Simulation
    capital = initial_capital
    position = 0  # 1 = long, -1 = short, 0 = flat
    entry_price = 0.0
    trades = []
    liquidations = 0
    atr_at_entry = 0
    equity_curve = [initial_capital]
    peak = initial_capital
    max_dd = 0.0

    for i in range(100, len(df)):
        price = df['close'].iloc[i]

        # Check liquidation
        if position != 0:
            pnl_pct = (price - entry_price) / entry_price * position
            equity = capital * (1 + pnl_pct * leverage)
            if equity <= 0:
                liquidations += 1
                capital = initial_capital
                position = 0
                equity_curve.append(capital)
                peak = max(peak, capital)
                continue

        # ATR stop
        if position != 0 and atr_at_entry > 0:
            stop_dist = 2.0 * atr_at_entry
            if position > 0 and price < (entry_price - stop_dist):
                pnl = (price - entry_price) / entry_price * capital * leverage * position
                fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
                capital += pnl - fee
                trades.append({'pnl': pnl - fee})
                position = 0
            elif position < 0 and price > (entry_price + stop_dist):
                pnl = (price - entry_price) / entry_price * capital * leverage * position
                fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
                capital += pnl - fee
                trades.append({'pnl': pnl - fee})
                position = 0

        # Exit on opposite signal (check before entry to handle flips)
        if position > 0 and short_signal.iloc[i]:
            pnl = (price - entry_price) / entry_price * capital * leverage
            fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee})
            position = -1
            entry_price = price
            atr_at_entry = atr(df.iloc[:i+1], 14).iloc[-1]
        elif position < 0 and long_signal.iloc[i]:
            pnl = (price - entry_price) / entry_price * capital * leverage * -1
            fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee})
            position = 1
            entry_price = price
            atr_at_entry = atr(df.iloc[:i+1], 14).iloc[-1]
        # Entry
        elif position == 0 and long_signal.iloc[i]:
            position = 1
            entry_price = price
            atr_at_entry = atr(df.iloc[:i+1], 14).iloc[-1]
        elif position == 0 and short_signal.iloc[i]:
            position = -1
            entry_price = price
            atr_at_entry = atr(df.iloc[:i+1], 14).iloc[-1]

        # Track equity and drawdown
        equity = capital + (position * (price - entry_price) / entry_price * capital * leverage if position != 0 else 0)
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Close final position
    if position != 0:
        price = df['close'].iloc[-1]
        pnl = (price - entry_price) / entry_price * capital * leverage * position
        fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
        capital += pnl - fee
        trades.append({'pnl': pnl - fee})

    final_equity = capital
    total_return = (final_equity - initial_capital) / initial_capital * 100

    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] <= 0]

    gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
    gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 1

    years = len(df) / (365.25 * 24 * 60)  # 1m bars to years
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 and final_equity > 0 else 0

    return {
        'strategy': 'Swing_BTC_4h',
        'final_equity': final_equity,
        'total_return_pct': total_return,
        'max_drawdown_pct': max_dd,
        'total_trades': len(trades),
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': len(winning_trades) / len(trades) * 100 if trades else 0,
        'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 0,
        'liquidations': liquidations,
        'sharpe': 0,
        'calmar': cagr / max_dd if max_dd > 0 else cagr
    }


# ═══════════════════════════════════════════════════════════════════════════
# SWING ETH 4H STRATEGY (PINE-004)
# Matches runner: EMA 12/26/50 triple alignment, Bollinger Bands, Stochastic, pivot points
# ═══════════════════════════════════════════════════════════════════════════

def run_swing_eth_4h(df: pd.DataFrame, initial_capital: float = 1000.0, leverage: float = 1.0) -> Dict:
    """Run Swing ETH 4H strategy — EMA 12/26/50, Bollinger, Stochastic, pivots."""

    # Resample to 4H
    df_4h = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    # 4H indicators matching ETH runner
    ema12 = ema(df_4h['close'], 12)
    ema26 = ema(df_4h['close'], 26)
    ema50 = ema(df_4h['close'], 50)
    rsi_val = rsi(df_4h['close'], 14)
    macd_line, macd_signal, _ = macd(df_4h['close'], 12, 26, 9)
    stoch_k, stoch_d = stochastic(df_4h)
    bb_upper, bb_middle, bb_lower = bollinger_bands(df_4h['close'], 20, 2.0)
    atr_4h = atr(df_4h, 14)

    # Pivot points
    pivot = (df_4h['high'] + df_4h['low'] + df_4h['close']) / 3
    r1 = 2 * pivot - df_4h['low']
    s1 = 2 * pivot - df_4h['high']

    # Signals matching runner logic
    long_signal_4h = (
        (ema12 > ema26) & (ema26 > ema50) &          # Triple EMA alignment
        (df_4h['close'] > r1 * 0.995) &               # Above R1 pivot
        (rsi_val > 45) & (rsi_val < 75) &              # RSI range
        (macd_line > macd_signal) &                    # MACD bullish
        (stoch_k > 20) &                               # Stochastic not oversold
        (df_4h['close'] < bb_upper)                    # Not above upper BB
    )
    short_signal_4h = (
        (ema12 < ema26) & (ema26 < ema50) &           # Triple EMA bearish
        (df_4h['close'] < s1 * 1.005) &                # Below S1 pivot
        (rsi_val > 25) & (rsi_val < 55) &              # RSI range
        (macd_line < macd_signal) &                    # MACD bearish
        (stoch_k < 80) &                               # Stochastic not overbought
        (df_4h['close'] > bb_lower)                    # Not below lower BB
    )

    # Only fire on transitions
    long_signal_4h = long_signal_4h & ~long_signal_4h.shift(1).fillna(False)
    short_signal_4h = short_signal_4h & ~short_signal_4h.shift(1).fillna(False)

    # Reindex to 1m
    long_signal = long_signal_4h.reindex(df.index, method='ffill').fillna(False)
    short_signal = short_signal_4h.reindex(df.index, method='ffill').fillna(False)

    # Simulation (same engine as BTC swing)
    capital = initial_capital
    position = 0
    entry_price = 0.0
    trades = []
    liquidations = 0
    equity_curve = [initial_capital]
    peak = initial_capital
    max_dd = 0.0

    for i in range(100, len(df)):
        price = df['close'].iloc[i]

        if position != 0:
            pnl_pct = (price - entry_price) / entry_price * position
            equity = capital * (1 + pnl_pct * leverage)
            if equity <= 0:
                liquidations += 1
                capital = initial_capital
                position = 0
                equity_curve.append(capital)
                peak = max(peak, capital)
                continue

        # Exit on opposite signal
        if position > 0 and short_signal.iloc[i]:
            pnl = (price - entry_price) / entry_price * capital * leverage
            fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee})
            position = -1
            entry_price = price
        elif position < 0 and long_signal.iloc[i]:
            pnl = (price - entry_price) / entry_price * capital * leverage * -1
            fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee})
            position = 1
            entry_price = price
        elif position == 0 and long_signal.iloc[i]:
            position = 1
            entry_price = price
        elif position == 0 and short_signal.iloc[i]:
            position = -1
            entry_price = price

        equity = capital + (position * (price - entry_price) / entry_price * capital * leverage if position != 0 else 0)
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    if position != 0:
        price = df['close'].iloc[-1]
        pnl = (price - entry_price) / entry_price * capital * leverage * position
        fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
        capital += pnl - fee
        trades.append({'pnl': pnl - fee})

    final_equity = capital
    total_return = (final_equity - initial_capital) / initial_capital * 100

    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] <= 0]

    gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
    gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 1

    years = len(df) / (365.25 * 24 * 60)
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 and final_equity > 0 else 0

    return {
        'strategy': 'Swing_ETH_4h',
        'final_equity': final_equity,
        'total_return_pct': total_return,
        'max_drawdown_pct': max_dd,
        'total_trades': len(trades),
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': len(winning_trades) / len(trades) * 100 if trades else 0,
        'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 0,
        'liquidations': liquidations,
        'sharpe': 0,
        'calmar': cagr / max_dd if max_dd > 0 else cagr
    }


# ═══════════════════════════════════════════════════════════════════════════
# GAUSSIAN V4H STRATEGY
# ═══════════════════════════════════════════════════════════════════════════

def run_gaussian_v4h(df: pd.DataFrame, initial_capital: float = 1000.0, leverage: float = 1.0) -> Dict:
    """Run Gaussian V4H strategy — Color flip system."""
    
    # Resample to 4H
    df_4h = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    # Gaussian channel on 4H
    period = 144
    poles = 4
    tr_mult = 1.414
    
    hl2_4h = (df_4h['high'] + df_4h['low']) / 2
    tr_4h = true_range(df_4h)
    
    smooth = hl2_4h.copy()
    for _ in range(poles):
        smooth = ema(smooth, period)
    
    atr_4h = tr_4h.ewm(span=period).mean()
    upper = smooth + tr_mult * atr_4h
    lower = smooth - tr_mult * atr_4h
    
    # Color determination
    color_4h = pd.Series(index=df_4h.index, dtype=int)
    for i in range(len(df_4h)):
        if df_4h['close'].iloc[i] > upper.iloc[i]:
            color_4h.iloc[i] = 1  # Green (bullish)
        elif df_4h['close'].iloc[i] < lower.iloc[i]:
            color_4h.iloc[i] = -1  # Red (bearish)
        else:
            color_4h.iloc[i] = 0 if i == 0 else color_4h.iloc[i-1]  # Hold previous
    
    # Reindex to 1m
    color = color_4h.reindex(df.index, method='ffill').fillna(0)
    
    # Signals
    long_signal = (color == 1) & (color.shift(1) != 1)
    short_signal = (color == -1) & (color.shift(1) != -1)
    
    # Simulation
    capital = initial_capital
    position = 0
    entry_price = 0.0
    trades = []
    liquidations = 0
    equity_curve = [initial_capital]
    peak = initial_capital
    max_dd = 0.0

    for i in range(100, len(df)):
        price = df['close'].iloc[i]

        # Check liquidation
        if position != 0:
            pnl_pct = (price - entry_price) / entry_price * position
            equity = capital * (1 + pnl_pct * leverage)
            if equity <= 0:
                liquidations += 1
                capital = initial_capital
                position = 0
                equity_curve.append(capital)
                peak = max(peak, capital)
                continue

        # Exit/flip first, then entry
        if position > 0 and (short_signal.iloc[i] or color.iloc[i] != 1):
            pnl = (price - entry_price) / entry_price * capital * leverage
            fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee})
            position = -1 if short_signal.iloc[i] else 0
            entry_price = price if short_signal.iloc[i] else 0
        elif position < 0 and (long_signal.iloc[i] or color.iloc[i] != -1):
            pnl = (price - entry_price) / entry_price * capital * leverage * -1
            fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
            capital += pnl - fee
            trades.append({'pnl': pnl - fee})
            position = 1 if long_signal.iloc[i] else 0
            entry_price = price if long_signal.iloc[i] else 0
        elif position == 0 and long_signal.iloc[i]:
            position = 1
            entry_price = price
        elif position == 0 and short_signal.iloc[i]:
            position = -1
            entry_price = price

        # Track equity and drawdown
        equity = capital + (position * (price - entry_price) / entry_price * capital * leverage if position != 0 else 0)
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Close final
    if position != 0:
        price = df['close'].iloc[-1]
        pnl = (price - entry_price) / entry_price * capital * leverage * position
        fee = abs(capital * leverage * (price / entry_price)) * TAKER_FEE
        capital += pnl - fee
        trades.append({'pnl': pnl - fee})

    final_equity = capital
    total_return = (final_equity - initial_capital) / initial_capital * 100

    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] <= 0]

    gross_profit = sum(t['pnl'] for t in winning_trades) if winning_trades else 0
    gross_loss = abs(sum(t['pnl'] for t in losing_trades)) if losing_trades else 1

    years = len(df) / (365.25 * 24 * 60)
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 and final_equity > 0 else 0

    return {
        'strategy': 'Gaussian_V4H',
        'final_equity': final_equity,
        'total_return_pct': total_return,
        'max_drawdown_pct': max_dd,
        'total_trades': len(trades),
        'winning_trades': len(winning_trades),
        'losing_trades': len(losing_trades),
        'win_rate': len(winning_trades) / len(trades) * 100 if trades else 0,
        'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 0,
        'liquidations': liquidations,
        'sharpe': 0,
        'calmar': cagr / max_dd if max_dd > 0 else cagr
    }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

DATA_DIR = WS / "mr-martingale-v1/intelligence/data/historical"

ASSET_FILE_MAP = {
    'BTC': DATA_PATH_BTC,
    'ETH': DATA_PATH_ETH,
    'DOGE': DATA_DIR / 'dogeusdt_1m_2021_2024.parquet',
    'SOL': DATA_DIR / 'solusdt_1m_2021_2024.parquet',
    'XRP': DATA_DIR / 'xrpusdt_1m_2021_2024.parquet',
    'BNB': DATA_DIR / 'bnbusdt_1m_2021_2024.parquet',
    'LTC': DATA_DIR / 'ltcusdt_1m_2021_2024.parquet',
}


def load_data(asset: str) -> Optional[pd.DataFrame]:
    """Load data for asset."""
    path = ASSET_FILE_MAP.get(asset)
    if path is None or not path.exists():
        return None

    df = pd.read_parquet(path)

    # Handle timestamp column
    if 'ts' in df.columns:
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        df = df.set_index('ts')
    elif 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp')
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Filter to 2021+
    df = df[(df.index >= '2021-01-01') & (df.index <= '2024-12-31')]
    return df


def run_sweep() -> Dict:
    """Run cross-asset sweep."""
    
    assets = ['BTC', 'ETH', 'DOGE', 'SOL', 'XRP', 'BNB', 'LTC']
    strategies = {
        'PINE-001': run_sweet_v444,
        'PINE-003': run_swing_btc_4h,
        'PINE-004': run_swing_eth_4h,
        'PINE-006': run_gaussian_v4h
    }
    
    results = {
        'run_date': TODAY,
        'methodology': 'liquidation_restart_1k',
        'period': '2021-01-01 to present',
        'matrix': {}
    }
    
    for asset in assets:
        print(f"\n{'='*50}")
        print(f"Testing {asset}...")
        print('='*50)
        
        df = load_data(asset)
        if df is None:
            print(f"  ⚠️ No data for {asset}")
            continue
        
        print(f"  Data: {len(df):,} rows ({df.index[0]} to {df.index[-1]})")
        
        results['matrix'][asset] = {}
        
        for code, strategy_fn in strategies.items():
            print(f"\n  Running {code}...", end=' ')
            
            try:
                result = strategy_fn(df.copy())
                results['matrix'][asset][code] = result
                
                ret = result['total_return_pct']
                dd = result['max_drawdown_pct']
                liqs = result['liquidations']
                wr = result['win_rate']
                
                status = "✅ PASS" if ret > 100 and liqs == 0 else "⚠️ MARGINAL" if ret > 0 else "❌ FAIL"
                print(f"{status} | Return: {ret:+.1f}% | DD: {dd:.1f}% | Liqs: {liqs} | WR: {wr:.1f}%")
                
            except Exception as e:
                print(f"❌ ERROR: {e}")
                results['matrix'][asset][code] = {'error': str(e)}
    
    return results


if __name__ == '__main__':
    print("="*60)
    print("CROSS-ASSET STRATEGY SWEEP")
    print(f"Date: {TODAY}")
    print("="*60)
    
    results = run_sweep()
    
    # Save results
    output_file = WS / f'cross_asset_matrix_{TODAY}.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n{'='*60}")
    print(f"Results saved to: {output_file}")
    print("="*60)
    
    # Summary table
    print("\n📊 PERFORMANCE MATRIX:")
    print(f"{'Asset':<8} {'Strategy':<12} {'Return':>10} {'Max DD':>10} {'Liqs':>6} {'Win%':>8} {'Status':<12}")
    print("-" * 70)
    
    for asset in results['matrix']:
        for code in results['matrix'][asset]:
            r = results['matrix'][asset][code]
            if 'error' in r:
                continue
            
            ret = r.get('total_return_pct', 0)
            dd = r.get('max_drawdown_pct', 0)
            liqs = r.get('liquidations', 0)
            wr = r.get('win_rate', 0)
            
            if ret > 500 and liqs == 0:
                status = "🟢 LIVE READY"
            elif ret > 200 and liqs == 0:
                status = "🟡 PAPER"
            elif ret > 50:
                status = "🟡 MARGINAL"
            else:
                status = "🔴 FAIL"
            
            print(f"{asset:<8} {code:<12} {ret:>+9.1f}% {dd:>9.1f}% {liqs:>6} {wr:>7.1f}% {status:<12}")
