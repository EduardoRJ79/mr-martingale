#!/usr/bin/env python3
"""
PineScript Strategy Backtest Suite — PINE-003 through PINE-010
==============================================================

Executes backtests for 8 queued strategies using:
- 1-minute OHLCV data
- Liquidation-restart methodology ($1,000 initial, reset on liquidation)
- Period: 2021-01-01 to 2024-12-31

Strategies:
- PINE-003: Swing BTC 4h (signals on 4h, execute on 1m)
- PINE-004: Swing ETH 4h (signals on 4h, execute on 1m)
- PINE-005: Gaussian Channel V6
- PINE-006: Gaussian V4H v4.0
- PINE-007: CCI Trend Reactor v2
- PINE-008: Ichimoku Advanced
- PINE-009: ML Beast Mode
- PINE-010: Elliott Wave
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

# Trading constants
TAKER_FEE = 0.00045  # 0.045% commission
MAINT_RATE = 0.005   # 0.5% maintenance margin

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
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


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


def crossover(series1: pd.Series, series2: pd.Series) -> pd.Series:
    return (series1 > series2) & (series1.shift(1) <= series2.shift(1))


def crossunder(series1: pd.Series, series2: pd.Series) -> pd.Series:
    return (series1 < series2) & (series1.shift(1) >= series2.shift(1))


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3
    sma_tp = sma(tp, period)
    mean_dev = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean())
    cci_val = (tp - sma_tp) / (0.015 * mean_dev)
    return cci_val


def bollinger_bands(series: pd.Series, period: int = 20, mult: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    middle = sma(series, period)
    std = series.rolling(period).std()
    upper = middle + mult * std
    lower = middle - mult * std
    return upper, middle, lower


def gaussian_filter(series: pd.Series, period: int, poles: int = 4) -> pd.Series:
    """Simplified Gaussian filter using EMA approximation."""
    beta = (1 - np.cos(2 * np.pi / period)) / (np.power(2.0, 2.0 / poles) - 1)
    alpha = -beta + np.sqrt(beta**2 + 2*beta)
    
    result = series.copy()
    for _ in range(poles):
        result = ema(result, int(2/alpha) if alpha > 0 else period)
    return result


def donchian_mid(df: pd.DataFrame, period: int) -> pd.Series:
    highest = df['high'].rolling(window=period).max()
    lowest = df['low'].rolling(window=period).min()
    return (highest + lowest) / 2


def pivot_high(series: pd.Series, left_bars: int, right_bars: int) -> pd.Series:
    """Detect swing highs."""
    highs = series.rolling(window=left_bars + right_bars + 1, center=True).max()
    return series.where(series == highs)


def pivot_low(series: pd.Series, left_bars: int, right_bars: int) -> pd.Series:
    """Detect swing lows."""
    lows = series.rolling(window=left_bars + right_bars + 1, center=True).min()
    return series.where(series == lows)


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class LiquidationEvent:
    liquidation_num: int
    timestamp: datetime
    price: float
    account_equity_before: float
    side: str
    cumulative_return_pct: float
    bar_index: int
    cause: str


@dataclass
class Trade:
    entry_time: datetime
    exit_time: Optional[datetime]
    side: str
    entry_price: float
    exit_price: Optional[float]
    size_usd: float
    pnl: Optional[float]
    exit_reason: Optional[str]
    bars_held: int = 0


@dataclass
class BacktestResult:
    config_name: str
    code: str
    asset: str
    start_date: str
    end_date: str
    initial_capital: float
    total_liquidations: int
    liquidation_events: List[LiquidationEvent] = field(default_factory=list)
    final_equity: float = 0.0
    cumulative_return_pct: float = 0.0
    cagr: float = 0.0
    max_dd_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_trade: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            'Strategy': self.config_name,
            'Code': self.code,
            'Asset': self.asset,
            'Return (%)': round(self.cumulative_return_pct, 2),
            'CAGR (%)': round(self.cagr, 2),
            'Max DD (%)': round(self.max_dd_pct, 2),
            'Liquidations': self.total_liquidations,
            'Trades': self.total_trades,
            'Win Rate (%)': round(self.win_rate, 2),
            'Profit Factor': round(self.profit_factor, 2),
            'Sharpe': round(self.sharpe, 2),
            'Calmar': round(self.calmar, 2),
            'Pass Gate': '✅' if self.total_liquidations <= 2 else '❌'
        }


# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY CLASSES
# ═══════════════════════════════════════════════════════════════════════════

class SwingStrategy:
    """Swing v4.3 Strategy — Multi-filter trend following."""
    
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        
        # Supertrend
        data['st_line'], data['st_dir'] = supertrend(data, self.cfg.get('st_length', 10), self.cfg.get('st_multiplier', 3.0))
        
        # Filters
        if self.cfg.get('use_hma_filter', True):
            data['hma_val'] = hma(close, self.cfg.get('hma_length', 55))
        if self.cfg.get('use_ema_filter', True):
            data['ema_val'] = ema(close, self.cfg.get('ema_length', 200))
        if self.cfg.get('use_chop_filter', True):
            data['chop_val'] = choppiness_index(data, self.cfg.get('chop_length', 14))
        if self.cfg.get('use_rsi_filter', False):
            data['rsi_val'] = rsi(close, self.cfg.get('rsi_length', 14))
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        # Filter logic
        filter_ok = pd.Series(True, index=df.index)
        
        if self.cfg.get('use_hma_filter', True):
            filter_ok &= close > df['hma_val']
        if self.cfg.get('use_ema_filter', True):
            filter_ok &= close > df['ema_val']
        if self.cfg.get('use_chop_filter', True):
            filter_ok &= df['chop_val'] < self.cfg.get('chop_threshold', 50)
        
        # Signal conditions (only on changes)
        st_bullish = df['st_dir'] == 1
        st_bearish = df['st_dir'] == -1
        st_flip_bull = st_bullish & (~st_bullish.shift(1).fillna(False))
        st_flip_bear = st_bearish & (~st_bearish.shift(1).fillna(False))
        
        # Long entry: Supertrend flip to bullish + filters
        df['long_condition'] = st_flip_bull & filter_ok
        
        # Short entry: Supertrend flip to bearish
        df['short_condition'] = st_flip_bear & ~filter_ok
        
        # Exit conditions (on flip)
        df['exit_long'] = st_flip_bear
        df['exit_short'] = st_flip_bull
        
        return df


class GaussianChannelStrategy:
    """Gaussian Channel Color-Flip Strategy."""
    
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        high = data['high']
        low = data['low']
        
        period = self.cfg.get('period', 144)
        poles = self.cfg.get('poles', 4)
        mult = self.cfg.get('mult', 1.414)
        src = (high + low + close) / 3  # hlc3
        
        # Gaussian filter
        data['filt'] = gaussian_filter(src, period, poles)
        
        # Filtered True Range
        tr_vals = true_range(data)
        data['f_tr'] = gaussian_filter(tr_vals, period, poles)
        
        # Bands
        data['hband'] = data['filt'] + data['f_tr'] * mult
        data['lband'] = data['filt'] - data['f_tr'] * mult
        
        # Trend detection
        data['is_uptrend'] = data['filt'] > data['filt'].shift(1)
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        # Color flip detection (trend change)
        df['flip_to_green'] = df['is_uptrend'] & (~df['is_uptrend'].shift(1).fillna(False))
        df['flip_to_red'] = (~df['is_uptrend']) & (df['is_uptrend'].shift(1).fillna(False))
        
        # Band cross signals (for V4H variant)
        df['cross_up_hband'] = crossover(close, df['hband'])
        df['cross_down_lband'] = crossunder(close, df['lband'])
        
        # Entry conditions
        trade_dir = self.cfg.get('trade_direction', 'Both')
        
        if self.cfg.get('use_color_flip', True):
            df['long_condition'] = df['flip_to_green'] if trade_dir in ['Both', 'Long Only'] else pd.Series(False, index=df.index)
            df['short_condition'] = df['flip_to_red'] if trade_dir in ['Both', 'Short Only'] else pd.Series(False, index=df.index)
        else:
            # Band cross variant
            df['long_condition'] = df['cross_up_hband'] if trade_dir in ['Both', 'Long Only'] else pd.Series(False, index=df.index)
            df['short_condition'] = df['cross_down_lband'] if trade_dir in ['Both', 'Short Only'] else pd.Series(False, index=df.index)
        
        # Exit on opposite signal or cross back
        df['exit_long'] = df['flip_to_red'] if self.cfg.get('use_color_flip') else df['cross_down_lband']
        df['exit_short'] = df['flip_to_green'] if self.cfg.get('use_color_flip') else df['cross_up_hband']
        
        return df


class CCITrendReactorStrategy:
    """CCI Trend Reactor v2 — CCI with daily EMA filter."""
    
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        
        # CCI
        data['cci'] = cci(data, self.cfg.get('cci_length', 140))
        
        # ATR
        data['atr'] = atr(data, self.cfg.get('atr_length', 14))
        
        # Daily EMA (approximated on intraday data)
        daily_ema_period = self.cfg.get('daily_ema_length', 50)
        # For 1m data, 1440 minutes = 1 day, so daily EMA ≈ EMA(50 * 1440)
        # But that's too slow; use a proxy based on available timeframe
        data['daily_ema_proxy'] = ema(close, daily_ema_period * 10)  # Scaled approximation
        
        # ADX (optional)
        if self.cfg.get('use_adx', False):
            data['plus_di'], data['minus_di'], data['adx'] = dmi(data, self.cfg.get('adx_length', 14))
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        cci_upper = self.cfg.get('cci_upper', 41)
        cci_lower = self.cfg.get('cci_lower', -50)
        
        # Create threshold series
        upper_series = pd.Series(cci_upper, index=df.index)
        lower_series = pd.Series(cci_lower, index=df.index)
        
        # Raw signals
        df['raw_long'] = crossover(df['cci'], upper_series)
        df['raw_short'] = crossunder(df['cci'], lower_series)
        
        # Daily filter
        if self.cfg.get('use_daily_filter', True):
            daily_bullish = close > df['daily_ema_proxy']
            daily_bearish = close < df['daily_ema_proxy']
            df['long_condition'] = df['raw_long'] & daily_bullish
            df['short_condition'] = df['raw_short'] & daily_bearish
        else:
            df['long_condition'] = df['raw_long']
            df['short_condition'] = df['raw_short']
        
        # ADX filter (optional)
        if self.cfg.get('use_adx', False):
            adx_ok = df['adx'] > self.cfg.get('adx_threshold', 20)
            df['long_condition'] &= adx_ok
            df['short_condition'] &= adx_ok
        
        # Exit signals (opposite crossover)
        df['exit_long'] = crossunder(df['cci'], lower_series)
        df['exit_short'] = crossover(df['cci'], upper_series)
        
        return df


class IchimokuAdvancedStrategy:
    """Ichimoku Advanced — Multi-confirmation trend following."""
    
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        high = data['high']
        low = data['low']
        
        # Ichimoku components
        tenkan_period = self.cfg.get('tenkan', 20)
        kijun_period = self.cfg.get('kijun', 60)
        senkou_b_period = self.cfg.get('senkou_b', 120)
        displacement = self.cfg.get('displacement', 30)
        
        data['tenkan'] = donchian_mid(data, tenkan_period)
        data['kijun'] = donchian_mid(data, kijun_period)
        data['senkou_a'] = (data['tenkan'] + data['kijun']) / 2
        data['senkou_b'] = donchian_mid(data, senkou_b_period)
        
        # Cloud edges at current price
        data['cloud_top'] = data[['senkou_a', 'senkou_b']].max(axis=1).shift(displacement)
        data['cloud_bottom'] = data[['senkou_a', 'senkou_b']].min(axis=1).shift(displacement)
        
        # Cloud color
        data['cloud_bullish'] = data['senkou_a'].shift(displacement) > data['senkou_b'].shift(displacement)
        
        # ATR for stops
        data['atr'] = atr(data, self.cfg.get('atr_period', 14))
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        # TK Cross
        df['tk_cross_bull'] = crossover(df['tenkan'], df['kijun'])
        df['tk_cross_bear'] = crossunder(df['tenkan'], df['kijun'])
        
        # Price vs Cloud
        df['price_above_cloud'] = close > df['cloud_top']
        df['price_below_cloud'] = close < df['cloud_bottom']
        
        # Kijun slope
        df['kijun_rising'] = df['kijun'] > df['kijun'].shift(1)
        
        # Entry conditions (AND logic) - only on cross events
        req_tk = self.cfg.get('require_tk_cross', True)
        req_price_cloud = self.cfg.get('require_price_cloud', True)
        req_cloud_color = self.cfg.get('require_cloud_color', True)
        
        long_ok = pd.Series(True, index=df.index)
        if req_tk:
            long_ok &= df['tk_cross_bull']  # Only on cross
        if req_price_cloud:
            long_ok &= df['price_above_cloud']
        if req_cloud_color:
            long_ok &= df['cloud_bullish']
        
        short_ok = pd.Series(True, index=df.index)
        if req_tk:
            short_ok &= df['tk_cross_bear']  # Only on cross
        if req_price_cloud:
            short_ok &= df['price_below_cloud']
        if req_cloud_color:
            short_ok &= ~df['cloud_bullish']
        
        df['long_condition'] = long_ok
        df['short_condition'] = short_ok
        
        # Exit conditions (on cross)
        df['exit_long'] = df['tk_cross_bear']
        df['exit_short'] = df['tk_cross_bull']
        
        return df


class MLBeastModeStrategy:
    """ML Beast Mode — KNN + Adaptive Ensemble (simplified version)."""
    
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        
        # Feature indicators
        data['rsi'] = rsi(close, self.cfg.get('rsi_length', 14))
        data['macd_line'], data['macd_signal'], data['macd_hist'] = macd(
            close, self.cfg.get('macd_fast', 12), self.cfg.get('macd_slow', 26), self.cfg.get('macd_signal', 9)
        )
        data['ema'] = ema(close, self.cfg.get('ema_length', 50))
        data['bb_upper'], data['bb_middle'], data['bb_lower'] = bollinger_bands(
            close, self.cfg.get('bb_length', 20), self.cfg.get('bb_mult', 2.0)
        )
        data['atr'] = atr(data, self.cfg.get('atr_length', 14))
        
        # Volatility regime
        data['atr_fast'] = atr(data, self.cfg.get('vol_atr_fast', 14))
        data['atr_slow'] = atr(data, self.cfg.get('vol_atr_slow', 50))
        data['vol_ratio'] = data['atr_fast'] / data['atr_slow']
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        # Simplified ML signal: ensemble of technical conditions
        # RSI momentum (with cross detection)
        rsi_bull = df['rsi'] > 50
        rsi_bear = df['rsi'] < 50
        rsi_cross_up = rsi_bull & (~rsi_bull.shift(1).fillna(False))
        rsi_cross_down = rsi_bear & (~rsi_bear.shift(1).fillna(False))
        
        # MACD momentum (with cross detection)
        macd_bull = df['macd_line'] > df['macd_signal']
        macd_bear = df['macd_line'] < df['macd_signal']
        macd_cross_up = macd_bull & (~macd_bull.shift(1).fillna(False))
        macd_cross_down = macd_bear & (~macd_bear.shift(1).fillna(False))
        
        # Price vs EMA (with cross detection)
        price_above_ema = close > df['ema']
        price_below_ema = close < df['ema']
        ema_cross_up = price_above_ema & (~price_above_ema.shift(1).fillna(False))
        ema_cross_down = price_below_ema & (~price_below_ema.shift(1).fillna(False))
        
        # Ensemble score (0-4) - using crosses for entry
        bull_score = rsi_cross_up.astype(int) + macd_cross_up.astype(int) + ema_cross_up.astype(int)
        bear_score = rsi_cross_down.astype(int) + macd_cross_down.astype(int) + ema_cross_down.astype(int)
        
        threshold = self.cfg.get('signal_threshold', 2)
        
        df['long_condition'] = bull_score >= threshold
        df['short_condition'] = bear_score >= threshold
        
        # Exit on opposite cross
        df['exit_long'] = bear_score >= threshold
        df['exit_short'] = bull_score >= threshold
        
        return df


class ElliottWaveStrategy:
    """Elliott Wave Approximation — Wave 5 projection."""
    
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        high = data['high']
        low = data['low']
        
        # Swing detection
        swing_len = self.cfg.get('swing_length', 5)
        data['swing_high'] = pivot_high(high, swing_len, swing_len)
        data['swing_low'] = pivot_low(low, swing_len, swing_len)
        
        # Trend filter
        data['trend_ema'] = ema(close, self.cfg.get('trend_ema_length', 200))
        
        # Momentum
        data['rsi'] = rsi(close, self.cfg.get('rsi_length', 14))
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        # Simplified wave detection using momentum and trend structure
        # Trend alignment
        bullish_trend = close > df['trend_ema']
        bearish_trend = close < df['trend_ema']
        
        # Detect trend changes (Wave 1 start)
        trend_bull_start = bullish_trend & (~bullish_trend.shift(1).fillna(False))
        trend_bear_start = bearish_trend & (~bearish_trend.shift(1).fillna(False))
        
        # RSI confirmation (not overbought/oversold)
        rsi_ok = (df['rsi'] > 30) & (df['rsi'] < 70)
        
        # Entry on trend start + RSI confirmation
        df['long_condition'] = trend_bull_start & rsi_ok
        df['short_condition'] = trend_bear_start & rsi_ok
        
        # Exit on opposite trend start or extreme RSI
        df['exit_long'] = trend_bear_start | (df['rsi'] > 80)
        df['exit_short'] = trend_bull_start | (df['rsi'] < 20)
        
        return df


# ═══════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class LiquidationRestartBacktester:
    """Backtest engine with liquidation-restart methodology."""
    
    def __init__(self, strategy, config: Dict[str, Any], initial_capital: float = 1000.0):
        self.strategy = strategy
        self.cfg = config
        self.initial_capital = initial_capital
        
    def calculate_liquidation_price(self, entry_price: float, leverage: float, side: str) -> float:
        """Calculate liquidation price."""
        liq_pct = 1.0 / leverage - MAINT_RATE
        if side == "long":
            return entry_price * (1 - liq_pct)
        else:
            return entry_price * (1 + liq_pct)
    
    def run_backtest(self, df_1m: pd.DataFrame, start_date: str = "2021-01-01", 
                     end_date: str = "2024-12-31", signal_timeframe: str = None) -> BacktestResult:
        """Run backtest with optional signal aggregation."""
        
        code = self.cfg.get('code', 'UNKNOWN')
        asset = self.cfg.get('asset', 'BTC')
        name = self.cfg.get('name', 'Unknown Strategy')
        
        print(f"\n{'='*60}")
        print(f"Backtesting: {name} ({code})")
        print(f"Asset: {asset} | Leverage: {self.cfg.get('leverage', 1)}x")
        print(f"{'='*60}")
        
        # Prepare data
        df_1m = df_1m.copy()
        if 'timestamp' not in df_1m.columns and 'ts' in df_1m.columns:
            df_1m['timestamp'] = pd.to_datetime(df_1m['ts'], utc=True)
        elif df_1m.index.name is not None:
            df_1m = df_1m.reset_index()
        
        if 'timestamp' not in df_1m.columns:
            df_1m['timestamp'] = pd.to_datetime(df_1m.index, utc=True)
        
        df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'], utc=True)
        df_1m.set_index('timestamp', inplace=True)
        
        start_ts = pd.Timestamp(start_date, tz='UTC')
        end_ts = pd.Timestamp(end_date, tz='UTC')
        df_1m = df_1m[(df_1m.index >= start_ts) & (df_1m.index <= end_ts)].copy()
        
        print(f"Data: {df_1m.index[0]} to {df_1m.index[-1]} ({len(df_1m):,} 1m bars)")
        
        # Aggregate for signal generation if needed
        if signal_timeframe:
            df_signal = df_1m.resample(signal_timeframe).agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna()
            print(f"Signal timeframe: {signal_timeframe} ({len(df_signal):,} bars)")
        else:
            df_signal = df_1m.copy()
        
        # Compute indicators and signals
        df_signal = self.strategy.compute_indicators(df_signal)
        df_signal = self.strategy.generate_signals(df_signal)
        
        # Reindex signals to 1m
        signal_cols = ['long_condition', 'short_condition', 'exit_long', 'exit_short']
        for col in signal_cols:
            if col in df_signal.columns:
                df_1m[col] = df_signal[col].reindex(df_1m.index, method='ffill')
        
        # Backtest state
        INITIAL_CAPITAL = self.initial_capital
        equity = INITIAL_CAPITAL
        cumulative_equity = INITIAL_CAPITAL
        position_side = None
        position_size = 0.0
        entry_price = 0.0
        entry_time = None
        liq_price = 0.0
        last_trade_bar = -999
        cooldown_bars = self.cfg.get('cooldown_bars', 0)
        leverage = self.cfg.get('leverage', 1)
        
        liquidations: List[LiquidationEvent] = []
        trades: List[Trade] = []
        peak_equity = INITIAL_CAPITAL
        max_dd = 0.0
        bar_idx = 0
        bars_since_entry = 0
        equity_curve = []
        
        # Fixed position size
        base_position_size = INITIAL_CAPITAL * leverage
        
        print(f"Starting: ${INITIAL_CAPITAL:,.2f} | Position: ${base_position_size:,.2f} | Leverage: {leverage}x")
        
        for timestamp, row in df_1m.iterrows():
            current_price = row['close']
            high = row['high']
            low = row['low']
            
            equity_curve.append(equity)
            
            # Check liquidation
            if position_side is not None:
                liquidated = False
                liq_cause = ""
                
                if position_side == "long" and low <= liq_price:
                    liquidated = True
                    liq_cause = "liquidation"
                elif position_side == "short" and high >= liq_price:
                    liquidated = True
                    liq_cause = "liquidation"
                
                if liquidated:
                    liq_event = LiquidationEvent(
                        liquidation_num=len(liquidations) + 1,
                        timestamp=timestamp,
                        price=liq_price,
                        account_equity_before=equity,
                        side=position_side,
                        cumulative_return_pct=((cumulative_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                        bar_index=bar_idx,
                        cause=liq_cause
                    )
                    liquidations.append(liq_event)
                    
                    # Reset on liquidation
                    cumulative_equity += equity - INITIAL_CAPITAL
                    equity = INITIAL_CAPITAL
                    position_side = None
                    position_size = 0.0
                    last_trade_bar = bar_idx
                    bars_since_entry = 0
                    continue
            
            # Exit logic
            if position_side == "long":
                exit_signal = False
                exit_reason = ""
                exit_price = current_price
                
                if 'exit_long' in row and row['exit_long']:
                    exit_signal = True
                    exit_reason = "signal"
                
                if exit_signal:
                    pnl = position_size * (exit_price - entry_price) / entry_price
                    pnl -= position_size * TAKER_FEE * 2
                    equity += pnl
                    
                    trades.append(Trade(entry_time, timestamp, "long", entry_price, exit_price, 
                                       position_size, pnl, exit_reason, bars_since_entry))
                    
                    position_side = None
                    position_size = 0.0
                    last_trade_bar = bar_idx
                    bars_since_entry = 0
                    
            elif position_side == "short":
                exit_signal = False
                exit_reason = ""
                exit_price = current_price
                
                if 'exit_short' in row and row['exit_short']:
                    exit_signal = True
                    exit_reason = "signal"
                
                if exit_signal:
                    pnl = position_size * (entry_price - exit_price) / entry_price
                    pnl -= position_size * TAKER_FEE * 2
                    equity += pnl
                    
                    trades.append(Trade(entry_time, timestamp, "short", entry_price, exit_price,
                                       position_size, pnl, exit_reason, bars_since_entry))
                    
                    position_side = None
                    position_size = 0.0
                    last_trade_bar = bar_idx
                    bars_since_entry = 0
            
            # Entry logic
            cooldown_ok = (bar_idx - last_trade_bar) >= cooldown_bars
            
            if position_side is None and cooldown_ok:
                trade_mode = self.cfg.get('trade_mode', 'Both')
                
                # Long entry
                if trade_mode in ['Both', 'Long Only']:
                    if 'long_condition' in row and row['long_condition']:
                        position_side = "long"
                        entry_price = current_price
                        entry_time = timestamp
                        position_size = base_position_size
                        liq_price = self.calculate_liquidation_price(entry_price, leverage, "long")
                        bars_since_entry = 0
                
                # Short entry
                if position_side is None and trade_mode in ['Both', 'Short Only']:
                    if 'short_condition' in row and row['short_condition']:
                        position_side = "short"
                        entry_price = current_price
                        entry_time = timestamp
                        position_size = base_position_size
                        liq_price = self.calculate_liquidation_price(entry_price, leverage, "short")
                        bars_since_entry = 0
            
            # Update tracking
            if position_side is not None:
                bars_since_entry += 1
            
            peak_equity = max(peak_equity, equity)
            dd = (peak_equity - equity) / peak_equity * 100
            max_dd = max(max_dd, dd)
            
            bar_idx += 1
        
        # Final calculations
        final_equity = equity
        total_return = ((final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        
        # Calculate metrics
        winning_trades = [t for t in trades if t.pnl and t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl and t.pnl <= 0]
        
        total_trades = len(trades)
        win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
        
        gross_profit = sum(t.pnl for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        avg_trade = sum(t.pnl for t in trades) / total_trades if total_trades > 0 else 0
        avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t.pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        # CAGR calculation (handle negative equity)
        years = (df_1m.index[-1] - df_1m.index[0]).days / 365.25
        if final_equity <= 0 or years <= 0:
            cagr = -100.0
        else:
            try:
                cagr = (((final_equity / INITIAL_CAPITAL) ** (1/years)) - 1) * 100
            except:
                cagr = -100.0
        
        # Sharpe (simplified)
        equity_series = pd.Series(equity_curve)
        returns = equity_series.pct_change().dropna()
        sharpe = (returns.mean() / returns.std()) * np.sqrt(365*24*60) if len(returns) > 1 and returns.std() > 0 else 0
        
        # Calmar (handle edge cases)
        if max_dd > 0 and isinstance(cagr, (int, float)) and cagr > -100:
            calmar = cagr / max_dd
        elif isinstance(cagr, (int, float)) and cagr > 0:
            calmar = cagr
        else:
            calmar = -1.0
        
        result = BacktestResult(
            config_name=name,
            code=code,
            asset=asset,
            start_date=str(df_1m.index[0]),
            end_date=str(df_1m.index[-1]),
            initial_capital=INITIAL_CAPITAL,
            total_liquidations=len(liquidations),
            liquidation_events=liquidations,
            final_equity=final_equity,
            cumulative_return_pct=total_return,
            cagr=cagr,
            max_dd_pct=max_dd,
            total_trades=total_trades,
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            avg_trade=avg_trade,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            sharpe=sharpe,
            calmar=calmar
        )
        
        print(f"Return: {total_return:+.2f}% | CAGR: {cagr:+.2f}% | Max DD: {max_dd:.2f}%")
        print(f"Trades: {total_trades} | Win Rate: {win_rate:.1f}% | Profit Factor: {profit_factor:.2f}")
        print(f"Liquidations: {len(liquidations)} | Sharpe: {sharpe:.2f} | Calmar: {calmar:.2f}")
        
        return result


# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════════

STRATEGY_CONFIGS = [
    # PINE-003: Swing BTC 4h
    {
        'code': 'PINE-003',
        'name': 'Swing BTC 4h',
        'asset': 'BTC',
        'type': 'swing',
        'st_length': 10,
        'st_multiplier': 3.0,
        'use_hma_filter': True,
        'hma_length': 55,
        'use_ema_filter': True,
        'ema_length': 200,
        'use_chop_filter': True,
        'chop_length': 14,
        'chop_threshold': 50,
        'trade_mode': 'Both',
        'leverage': 1,
        'cooldown_bars': 0,
        'signal_tf': '4H'
    },
    # PINE-004: Swing ETH 4h
    {
        'code': 'PINE-004',
        'name': 'Swing ETH 4h',
        'asset': 'ETH',
        'type': 'swing',
        'st_length': 10,
        'st_multiplier': 3.0,
        'use_hma_filter': True,
        'hma_length': 55,
        'use_ema_filter': True,
        'ema_length': 200,
        'use_chop_filter': True,
        'chop_length': 14,
        'chop_threshold': 50,
        'trade_mode': 'Both',
        'leverage': 1,
        'cooldown_bars': 0,
        'signal_tf': '4H'
    },
    # PINE-005: Gaussian Channel V6
    {
        'code': 'PINE-005',
        'name': 'Gaussian Channel V6',
        'asset': 'BTC',
        'type': 'gaussian',
        'period': 144,
        'poles': 4,
        'mult': 1.414,
        'use_color_flip': True,
        'trade_direction': 'Both',
        'leverage': 1,
        'cooldown_bars': 0,
        'signal_tf': None
    },
    # PINE-006: Gaussian V4H v4.0
    {
        'code': 'PINE-006',
        'name': 'Gaussian V4H v4.0',
        'asset': 'ETH',
        'type': 'gaussian',
        'period': 144,
        'poles': 4,
        'mult': 1.414,
        'use_color_flip': False,  # Uses band cross
        'trade_direction': 'Both',
        'leverage': 1,
        'cooldown_bars': 0,
        'signal_tf': '4H'
    },
    # PINE-007: CCI Trend Reactor v2
    {
        'code': 'PINE-007',
        'name': 'CCI Trend Reactor v2',
        'asset': 'ETH',
        'type': 'cci',
        'cci_length': 140,
        'cci_upper': 41,
        'cci_lower': -50,
        'atr_length': 14,
        'use_daily_filter': True,
        'daily_ema_length': 50,
        'use_adx': False,
        'leverage': 1,
        'cooldown_bars': 0,
        'signal_tf': None
    },
    # PINE-008: Ichimoku Advanced
    {
        'code': 'PINE-008',
        'name': 'Ichimoku Advanced',
        'asset': 'BTC',
        'type': 'ichimoku',
        'tenkan': 20,
        'kijun': 60,
        'senkou_b': 120,
        'displacement': 30,
        'require_tk_cross': True,
        'require_price_cloud': True,
        'require_cloud_color': True,
        'leverage': 1,
        'cooldown_bars': 0,
        'signal_tf': None
    },
    # PINE-009: ML Beast Mode
    {
        'code': 'PINE-009',
        'name': 'ML Beast Mode',
        'asset': 'BTC',
        'type': 'ml_beast',
        'rsi_length': 14,
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'ema_length': 50,
        'bb_length': 20,
        'bb_mult': 2.0,
        'signal_threshold': 3,
        'leverage': 1,
        'cooldown_bars': 5,
        'signal_tf': None
    },
    # PINE-010: Elliott Wave
    {
        'code': 'PINE-010',
        'name': 'Elliott Wave',
        'asset': 'BTC',
        'type': 'elliott',
        'swing_length': 5,
        'trend_ema_length': 200,
        'rsi_length': 14,
        'leverage': 1,
        'cooldown_bars': 10,
        'signal_tf': None
    }
]


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

def load_data(asset: str) -> pd.DataFrame:
    """Load 1-minute data for the specified asset."""
    if asset == 'BTC':
        path = DATA_PATH_BTC
    elif asset == 'ETH':
        path = DATA_PATH_ETH
    else:
        raise ValueError(f"Unknown asset: {asset}")
    
    print(f"Loading {asset} data from {path}...")
    df = pd.read_parquet(path)
    print(f"Loaded {len(df):,} rows")
    return df


def run_all_backtests():
    """Run backtests for all strategies."""
    
    results = []
    
    # Load data once
    btc_data = None
    eth_data = None
    
    for config in STRATEGY_CONFIGS:
        try:
            # Load appropriate data
            if config['asset'] == 'BTC':
                if btc_data is None:
                    btc_data = load_data('BTC')
                data = btc_data
            else:
                if eth_data is None:
                    eth_data = load_data('ETH')
                data = eth_data
            
            # Create strategy
            strategy_type = config['type']
            if strategy_type == 'swing':
                strategy = SwingStrategy(config)
            elif strategy_type == 'gaussian':
                strategy = GaussianChannelStrategy(config)
            elif strategy_type == 'cci':
                strategy = CCITrendReactorStrategy(config)
            elif strategy_type == 'ichimoku':
                strategy = IchimokuAdvancedStrategy(config)
            elif strategy_type == 'ml_beast':
                strategy = MLBeastModeStrategy(config)
            elif strategy_type == 'elliott':
                strategy = ElliottWaveStrategy(config)
            else:
                print(f"Unknown strategy type: {strategy_type}")
                continue
            
            # Run backtest
            backtester = LiquidationRestartBacktester(strategy, config)
            result = backtester.run_backtest(
                data, 
                signal_timeframe=config.get('signal_tf')
            )
            results.append(result)
            
        except Exception as e:
            print(f"ERROR in {config.get('code', 'UNKNOWN')}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    return results


def print_results_table(results: List[BacktestResult]):
    """Print formatted results table."""
    
    print("\n" + "="*120)
    print("BACKTEST RESULTS SUMMARY — PINE-003 to PINE-010")
    print("="*120)
    
    # Header
    print(f"{'Code':<10} {'Strategy':<25} {'Asset':<6} {'Return%':<10} {'CAGR%':<10} {'MaxDD%':<10} {'Liqs':<6} {'Trades':<8} {'Win%':<8} {'PF':<8} {'Sharpe':<8} {'Calmar':<8} {'Pass':<6}")
    print("-"*120)
    
    # Sort by Calmar ratio (risk-adjusted return) - handle complex numbers
    def safe_calmar(r):
        if isinstance(r.calmar, (int, float)):
            return r.calmar
        return -1.0
    sorted_results = sorted(results, key=lambda x: safe_calmar(x), reverse=True)
    
    for r in sorted_results:
        cagr_val = r.cagr if isinstance(r.cagr, (int, float)) else -100.0
        calmar_val = r.calmar if isinstance(r.calmar, (int, float)) else -1.0
        print(f"{r.code:<10} {r.config_name:<25} {r.asset:<6} {r.cumulative_return_pct:>+9.1f} {cagr_val:>+9.1f} {r.max_dd_pct:>9.1f} {r.total_liquidations:<6} {r.total_trades:<8} {r.win_rate:>7.1f} {r.profit_factor:>7.2f} {r.sharpe:>7.2f} {calmar_val:>7.2f} {'✅' if r.total_liquidations <= 2 else '❌':<6}")
    
    print("="*120)
    
    # Rankings
    print("\n📊 RANKINGS BY RISK-ADJUSTED RETURN (Calmar Ratio):")
    for i, r in enumerate(sorted_results, 1):
        cagr_val = r.cagr if isinstance(r.cagr, (int, float)) else -100.0
        calmar_val = r.calmar if isinstance(r.calmar, (int, float)) else -1.0
        status = "✅ READY FOR PAPER" if r.total_liquidations <= 2 and cagr_val > 0 else "❌ FAILED"
        print(f"  {i}. {r.code} ({r.config_name}): Calmar={calmar_val:.2f}, Return={r.cumulative_return_pct:+.1f}%, Liqs={r.total_liquidations} {status}")
    
    # Pass gate summary
    passed = [r for r in results if r.total_liquidations <= 2]
    failed = [r for r in results if r.total_liquidations > 2]
    
    print(f"\n🎯 LIQUIDATION GATE RESULTS:")
    print(f"   Passed (0-2 liquidations): {len(passed)}/{len(results)}")
    if passed:
        print(f"   ✅ Passed: {', '.join([r.code for r in passed])}")
    if failed:
        print(f"   ❌ Failed: {', '.join([r.code for r in failed])}")
    
    print("\n" + "="*120)
    
    return sorted_results


def save_results(results: List[BacktestResult]):
    """Save results to JSON and CSV."""
    
    # JSON with full details
    output_path = WS / f"pinescript_backtest_results_{TODAY}.json"
    results_dict = {
        'run_date': TODAY,
        'strategies': [asdict(r) for r in results]
    }
    
    # Convert datetime objects to strings for JSON
    def convert_datetime(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    with open(output_path, 'w') as f:
        json.dump(results_dict, f, indent=2, default=convert_datetime)
    
    print(f"\n💾 Full results saved to: {output_path}")
    
    # CSV summary
    csv_path = WS / f"pinescript_backtest_summary_{TODAY}.csv"
    summary_df = pd.DataFrame([r.to_dict() for r in results])
    summary_df.to_csv(csv_path, index=False)
    print(f"💾 Summary CSV saved to: {csv_path}")


if __name__ == "__main__":
    print("="*80)
    print("PINESCRIPT STRATEGY BACKTEST SUITE")
    print("PINE-003 through PINE-010 | Liquidation-Restart Methodology")
    print("="*80)
    
    # Run all backtests
    results = run_all_backtests()
    
    # Print and save results
    sorted_results = print_results_table(results)
    save_results(results)
    
    print("\n✅ BACKTEST SUITE COMPLETE")
