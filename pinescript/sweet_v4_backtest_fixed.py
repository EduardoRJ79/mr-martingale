#!/usr/bin/env python3
"""
Sweet v4.4.4 Backtest — Fixed Position Sizing with Liquidation-Restart Methodology
==================================================================================

Fixed implementation with proper position sizing:
- Fixed dollar position size (not compounding)
- Proper leverage accounting
- Realistic margin requirements

Strategy: Sweet v4.4.4 (from PineScript)
- Entry: Supertrend stop-entry with multi-filter confirmation
- Exit: Gaussian Channel crossunder OR ZLAG crossunder
- Short: Reverse on exit signals (in "Both" mode)
- Risk: Chandelier Exit trailing stop
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# ═══════════════════════════════════════════════════════════════════════════
# PATHS & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

WS = Path("/Users/assistant/.openclaw/ws-731228")
DATA_PATH = WS / "mr-martingale-v1/signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet"
TODAY = datetime.now().strftime("%Y-%m-%d")

# Trading constants
TAKER_FEE = 0.00045  # 0.045% commission
MAINT_RATE = 0.005  # 0.5% maintenance margin

# ═══════════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS (same as before)
# ═══════════════════════════════════════════════════════════════════════════

def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """Calculate Supertrend indicator. Returns (line, direction)."""
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


def hma(series: pd.Series, period: int) -> pd.Series:
    half_period = int(period / 2)
    sqrt_period = int(np.sqrt(period))
    
    def wma(s, n):
        weights = np.arange(1, n + 1)
        return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    
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
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    
    return plus_di, minus_di, adx


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


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════════

SWEET_V444_CONFIG = {
    "name": "Sweet_v4.4.4_DOGE_15m",
    "st_length": 22,
    "st_multiplier": 5.1813,
    "trade_mode": "Both",
    "cooldown_bars": 4,
    "enable_gaussian_exit": True,
    "gauss_period": 144,
    "gauss_poles": 2,
    "gauss_tr_mult": 0.655,
    "gauss_reduced_lag": True,
    "enable_zlag_exit": False,
    "zlag_period": 541,
    "zlag_source": "low",
    "use_hma_filter": True,
    "hma_length": 68,
    "use_chop_filter": True,
    "chop_length": 7,
    "chop_threshold": 38.2,
    "use_mom_filter": False,
    "mom_length": 270,
    "use_sma_filter": False,
    "sma_length": 120,
    "use_tema_filter": True,
    "tema_length": 95,
    "use_dmi_filter": True,
    "dmi_length": 56,
    "use_chandelier": True,
    "ch_lookback": 4,
    "ch_multiplier": 1.8788,
    "position_size_pct": 100,  # Fixed at 100% for conservative testing
    "leverage": 1,
}

SWEET_V443_VIRT_CONFIG = {
    "name": "Sweet_v4.4.3_VIRT_15m",
    "st_length": 62,
    "st_multiplier": 8.3625,
    "trade_mode": "Both",
    "cooldown_bars": 4,
    "enable_gaussian_exit": False,
    "gauss_period": 176,
    "gauss_poles": 2,
    "gauss_tr_mult": 0.655,
    "gauss_reduced_lag": True,
    "enable_zlag_exit": True,
    "zlag_period": 270,
    "zlag_source": "hl2",
    "use_hma_filter": True,
    "hma_length": 56,
    "use_chop_filter": True,
    "chop_length": 1,
    "chop_threshold": 38.2,
    "use_mom_filter": False,
    "mom_length": 270,
    "use_sma_filter": True,
    "sma_length": 114,
    "use_tema_filter": True,
    "tema_length": 159,
    "use_dmi_filter": False,
    "dmi_length": 40,
    "use_chandelier": False,
    "ch_lookback": 4,
    "ch_multiplier": 1.8788,
    "position_size_pct": 100,
    "leverage": 1,
}

# Test with different leverage levels
SWEET_V444_2X_CONFIG = {**SWEET_V444_CONFIG, "name": "Sweet_v4.4.4_DOGE_15m_2x", "leverage": 2}
SWEET_V444_3X_CONFIG = {**SWEET_V444_CONFIG, "name": "Sweet_v4.4.4_DOGE_15m_3x", "leverage": 3}


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
    start_date: str
    end_date: str
    initial_capital: float
    total_liquidations: int
    liquidation_events: List[LiquidationEvent]
    final_equity: float
    cumulative_return_pct: float
    max_dd_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_trade: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    gross_profit: float
    gross_loss: float


# ═══════════════════════════════════════════════════════════════════════════
# SWEET V4 STRATEGY
# ═══════════════════════════════════════════════════════════════════════════

class SweetV4Strategy:
    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        
    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        close = data['close']
        high = data['high']
        low = data['low']
        
        # Supertrend
        data['st_line'], data['st_dir'] = supertrend(
            data, self.cfg['st_length'], self.cfg['st_multiplier']
        )
        
        # Gaussian Channel
        if self.cfg['enable_gaussian_exit']:
            lag_i = int((self.cfg['gauss_period'] - 1) / (2 * self.cfg['gauss_poles']))
            src_lag = close + close - close.shift(lag_i) if self.cfg['gauss_reduced_lag'] else close
            data['gauss_filt'] = sma(src_lag, self.cfg['gauss_period'])
            tr_vals = true_range(data)
            data['f_tr'] = sma(tr_vals, self.cfg['gauss_period'])
            data['gauss_hband'] = data['gauss_filt'] + data['f_tr'] * self.cfg['gauss_tr_mult']
        
        # ZLAG
        if self.cfg['enable_zlag_exit']:
            zlag_src = (high + low) / 2 if self.cfg['zlag_source'] == 'hl2' else low
            data['zlag_val'] = zlag(zlag_src, self.cfg['zlag_period'])
        
        # Filters
        if self.cfg['use_hma_filter']:
            data['hma_val'] = hma(close, self.cfg['hma_length'])
        if self.cfg['use_chop_filter']:
            data['chop_val'] = choppiness_index(data, self.cfg['chop_length'])
        if self.cfg['use_mom_filter']:
            data['mom_val'] = close - close.shift(self.cfg['mom_length'])
        if self.cfg['use_sma_filter']:
            data['sma_val'] = sma(close, self.cfg['sma_length'])
        if self.cfg['use_tema_filter']:
            data['tema_val'] = tema(close, self.cfg['tema_length'])
        if self.cfg['use_dmi_filter']:
            data['plus_di'], data['minus_di'], data['adx'] = dmi(data, self.cfg['dmi_length'])
        if self.cfg['use_chandelier']:
            data['ch_long_stop'], data['ch_short_stop'] = chandelier_exit(
                data, self.cfg['ch_lookback'], self.cfg['ch_multiplier']
            )
        
        return data
    
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close = df['close']
        
        filter_ok = pd.Series(True, index=df.index)
        
        if self.cfg['use_hma_filter']:
            filter_ok &= close > df['hma_val']
        if self.cfg['use_chop_filter']:
            filter_ok &= df['chop_val'] < self.cfg['chop_threshold']
        if self.cfg['use_mom_filter']:
            filter_ok &= df['mom_val'] > 0
        if self.cfg['use_sma_filter'] or self.cfg['use_tema_filter']:
            sma_ok = close > df['sma_val'] if self.cfg['use_sma_filter'] else False
            tema_ok = close > df['tema_val'] if self.cfg['use_tema_filter'] else False
            filter_ok &= (sma_ok | tema_ok)
        if self.cfg['use_dmi_filter']:
            filter_ok &= df['plus_di'] > df['minus_di']
        
        # Long: Supertrend uptrend + filters
        df['long_condition'] = (df['st_dir'] == 1) & filter_ok
        
        # Short signals
        df['gauss_exit'] = crossunder(close, df['gauss_hband']) if self.cfg['enable_gaussian_exit'] else False
        df['gauss_entry'] = crossover(close, df['gauss_hband']) if self.cfg['enable_gaussian_exit'] else False
        df['zlag_exit'] = crossunder(close, df['zlag_val']) if self.cfg['enable_zlag_exit'] else False
        df['short_signal'] = df['gauss_exit'] | df['zlag_exit']
        df['short_condition'] = df['short_signal']
        df['exit_long'] = df['short_signal']
        
        # Exit short
        st_bullish_flip = (df['st_dir'] == -1) & (df['st_dir'].shift(1) == 1)
        df['exit_short'] = df['gauss_entry'] | st_bullish_flip
        
        return df


# ═══════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE (FIXED)
# ═══════════════════════════════════════════════════════════════════════════

class SweetV4Backtester:
    def __init__(self, config: Dict[str, Any], initial_capital: float = 1000.0):
        self.cfg = config
        self.initial_capital = initial_capital
        self.strategy = SweetV4Strategy(config)
        
    def calculate_liquidation_price(self, entry_price: float, leverage: float, side: str) -> float:
        """Calculate liquidation price"""
        liq_pct = 1.0 / leverage - MAINT_RATE
        if side == "long":
            return entry_price * (1 - liq_pct)
        else:
            return entry_price * (1 + liq_pct)
    
    def run_backtest(self, df_1m: pd.DataFrame, start_date: str = "2021-01-01", end_date: str = "2024-12-31") -> BacktestResult:
        print(f"\n{'='*60}")
        print(f"Running Sweet v4 Backtest: {self.cfg['name']}")
        print(f"Leverage: {self.cfg['leverage']}x")
        print(f"{'='*60}")
        
        # Prepare data
        df_1m = df_1m.copy()
        if 'timestamp' not in df_1m.columns and 'ts' in df_1m.columns:
            df_1m['timestamp'] = pd.to_datetime(df_1m['ts'], utc=True)
        elif df_1m.index.name is not None:
            df_1m = df_1m.reset_index()
            if 'timestamp' not in df_1m.columns:
                df_1m['timestamp'] = pd.to_datetime(df_1m.iloc[:, 0], utc=True)
        
        df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'], utc=True)
        df_1m.set_index('timestamp', inplace=True)
        
        start_ts = pd.Timestamp(start_date, tz='UTC')
        end_ts = pd.Timestamp(end_date, tz='UTC')
        df_1m = df_1m[(df_1m.index >= start_ts) & (df_1m.index <= end_ts)].copy()
        
        print(f"Data: {df_1m.index[0]} to {df_1m.index[-1]} ({len(df_1m):,} 1m bars)")
        
        # Aggregate to 15m
        df_15m = df_1m.resample('15min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        print(f"15m bars: {len(df_15m):,}")
        
        # Compute signals on 15m
        df_15m = self.strategy.compute_indicators(df_15m)
        df_15m = self.strategy.generate_signals(df_15m)
        
        # Reindex to 1m
        signal_cols = ['long_condition', 'short_condition', 'exit_long', 'exit_short',
                       'ch_long_stop', 'ch_short_stop', 'gauss_hband', 'zlag_val']
        for col in signal_cols:
            if col in df_15m.columns:
                df_1m[col] = df_15m[col].reindex(df_1m.index, method='ffill')
        
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
        cooldown_bars = self.cfg['cooldown_bars'] * 15
        
        liquidations: List[LiquidationEvent] = []
        trades: List[Trade] = []
        peak_equity = INITIAL_CAPITAL
        max_dd = 0.0
        bar_idx = 0
        bars_since_entry = 0
        
        # FIXED: Use fixed position size based on initial capital, not compounding
        base_position_size = INITIAL_CAPITAL * (self.cfg['position_size_pct'] / 100) * self.cfg['leverage']
        
        print(f"Starting with ${INITIAL_CAPITAL:,.2f}, position size: ${base_position_size:,.2f} ({self.cfg['leverage']}x)")
        
        for timestamp, row in df_1m.iterrows():
            current_price = row['close']
            high = row['high']
            low = row['low']
            
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
                    continue
            
            # Exit logic
            if position_side == "long":
                exit_signal = False
                exit_reason = ""
                exit_price = current_price
                
                if 'exit_long' in row and row['exit_long']:
                    exit_signal = True
                    exit_reason = "gauss_zlag_exit"
                
                if self.cfg['use_chandelier'] and 'ch_long_stop' in row:
                    if low <= row['ch_long_stop'] and not pd.isna(row['ch_long_stop']):
                        exit_signal = True
                        exit_reason = "chandelier"
                        exit_price = min(current_price, row['ch_long_stop'])
                
                if exit_signal:
                    pnl = position_size * (exit_price - entry_price) / entry_price
                    pnl -= position_size * TAKER_FEE * 2
                    equity += pnl
                    
                    trades.append(Trade(entry_time, timestamp, "long", entry_price, exit_price, 
                                       position_size, pnl, exit_reason, bars_since_entry))
                    
                    position_side = None
                    position_size = 0.0
                    last_trade_bar = bar_idx
                    
            elif position_side == "short":
                exit_signal = False
                exit_reason = ""
                exit_price = current_price
                
                if 'exit_short' in row and row['exit_short']:
                    exit_signal = True
                    exit_reason = "gauss_cross_up" if ('gauss_entry' in row and row['gauss_entry']) else "st_bullish"
                
                if self.cfg['use_chandelier'] and 'ch_short_stop' in row:
                    if high >= row['ch_short_stop'] and not pd.isna(row['ch_short_stop']):
                        exit_signal = True
                        exit_reason = "chandelier"
                        exit_price = max(current_price, row['ch_short_stop'])
                
                if exit_signal:
                    pnl = position_size * (entry_price - exit_price) / entry_price
                    pnl -= position_size * TAKER_FEE * 2
                    equity += pnl
                    
                    trades.append(Trade(entry_time, timestamp, "short", entry_price, exit_price,
                                       position_size, pnl, exit_reason, bars_since_entry))
                    
                    position_side = None
                    position_size = 0.0
                    last_trade_bar = bar_idx
            
            # Entry logic
            cooldown_ok = (bar_idx - last_trade_bar) >= cooldown_bars
            
            if position_side is None and cooldown_ok:
                trade_mode = self.cfg['trade_mode']
                
                # Long entry
                if trade_mode in ['Both', 'Long Only']:
                    if 'long_condition' in row and row['long_condition']:
                        position_side = "long"
                        entry_price = current_price
                        entry_time = timestamp
                        # FIXED: Use fixed position size, not compounding
                        position_size = base_position_size
                        liq_price = self.calculate_liquidation_price(entry_price, self.cfg['leverage'], "long")
                        bars_since_entry = 0
                        equity -= position_size * TAKER_FEE
                
                # Short entry
                if position_side is None and trade_mode in ['Both', 'Short Only']:
                    if 'short_condition' in row and row['short_condition']:
                        position_side = "short"
                        entry_price = current_price
                        entry_time = timestamp
                        position_size = base_position_size
                        liq_price = self.calculate_liquidation_price(entry_price, self.cfg['leverage'], "short")
                        bars_since_entry = 0
                        equity -= position_size * TAKER_FEE
            
            if position_side is not None:
                bars_since_entry += 1
            
            # Track peak and drawdown
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity * 100
            if dd > max_dd:
                max_dd = dd
            
            bar_idx += 1
        
        # Close final position
        if position_side is not None:
            final_price = df_1m['close'].iloc[-1]
            if position_side == "long":
                pnl = position_size * (final_price - entry_price) / entry_price
            else:
                pnl = position_size * (entry_price - final_price) / entry_price
            pnl -= position_size * TAKER_FEE
            equity += pnl
            
            trades.append(Trade(entry_time, df_1m.index[-1], position_side, entry_price, 
                               final_price, position_size, pnl, "end_of_backtest", bars_since_entry))
        
        # Calculate metrics
        total_return = ((equity - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        cumulative_return = ((cumulative_equity + equity - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        
        winning_trades = [t for t in trades if t.pnl and t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl and t.pnl <= 0]
        
        gross_profit = sum([t.pnl for t in winning_trades if t.pnl])
        gross_loss = abs(sum([t.pnl for t in losing_trades if t.pnl]))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        avg_trade = np.mean([t.pnl for t in trades if t.pnl]) if trades else 0
        avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t.pnl for t in losing_trades]) if losing_trades else 0
        
        return BacktestResult(
            config_name=self.cfg['name'],
            start_date=start_date,
            end_date=end_date,
            initial_capital=INITIAL_CAPITAL,
            total_liquidations=len(liquidations),
            liquidation_events=liquidations,
            final_equity=equity,
            cumulative_return_pct=cumulative_return,
            max_dd_pct=max_dd,
            total_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=len(winning_trades) / len(trades) * 100 if trades else 0,
            avg_trade=avg_trade,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            gross_profit=gross_profit,
            gross_loss=gross_loss
        )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def load_data_1m(start_date: str = "2021-01-01", end_date: str = "2024-12-31") -> pd.DataFrame:
    print(f"Loading BTC 1m data...")
    df = pd.read_parquet(DATA_PATH)
    df = df.rename(columns={'ts': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    start_ts = pd.Timestamp(start_date, tz='UTC')
    end_ts = pd.Timestamp(end_date, tz='UTC')
    df = df[(df['timestamp'] >= start_ts) & (df['timestamp'] <= end_ts)].copy()
    print(f"  Loaded {len(df):,} bars from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def print_report(result: BacktestResult):
    print(f"\n{'='*70}")
    print(f"RESULTS: {result.config_name}")
    print(f"{'='*70}")
    print(f"Period: {result.start_date} to {result.end_date}")
    print(f"Initial: ${result.initial_capital:,.2f} → Final: ${result.final_equity:,.2f}")
    print(f"Return: {result.cumulative_return_pct:+.2f}% | Max DD: {result.max_dd_pct:.2f}%")
    print(f"Trades: {result.total_trades} | Win Rate: {result.win_rate:.1f}%")
    print(f"Profit Factor: {result.profit_factor:.2f} | Gross P/L: ${result.gross_profit:+.2f} / ${result.gross_loss:+.2f}")
    print(f"Avg Trade: ${result.avg_trade:+.2f} | Avg Win: ${result.avg_win:+.2f} | Avg Loss: ${result.avg_loss:+.2f}")
    print(f"Liquidations: {result.total_liquidations}")
    if result.total_liquidations > 0:
        for liq in result.liquidation_events:
            print(f"  Liq #{liq.liquidation_num}: {liq.timestamp} @ ${liq.price:,.2f}")
    print(f"{'='*70}\n")


def main():
    df = load_data_1m(start_date="2021-01-01", end_date="2024-12-31")
    
    configs = [
        SWEET_V444_CONFIG,      # 1x leverage
        SWEET_V444_2X_CONFIG,   # 2x leverage
        SWEET_V444_3X_CONFIG,   # 3x leverage
        SWEET_V443_VIRT_CONFIG, # VIRT config
    ]
    
    results = []
    for config in configs:
        backtester = SweetV4Backtester(config, initial_capital=1000.0)
        result = backtester.run_backtest(df)
        print_report(result)
        results.append(result)
        
        safe_name = config['name'].replace('.', '_').replace(' ', '_')
        output = {
            'config': config,
            'result': {
                'config_name': result.config_name,
                'start_date': result.start_date,
                'end_date': result.end_date,
                'initial_capital': result.initial_capital,
                'final_equity': result.final_equity,
                'cumulative_return_pct': result.cumulative_return_pct,
                'max_dd_pct': result.max_dd_pct,
                'total_trades': result.total_trades,
                'win_rate': result.win_rate,
                'profit_factor': result.profit_factor,
                'total_liquidations': result.total_liquidations,
                'liquidation_events': [
                    {'num': liq.liquidation_num, 'timestamp': liq.timestamp.isoformat(), 
                     'price': liq.price, 'equity_before': liq.account_equity_before}
                    for liq in result.liquidation_events
                ],
                'gross_profit': result.gross_profit,
                'gross_loss': result.gross_loss,
            }
        }
        with open(WS / f"sweet_v4_backtest_{safe_name}_{TODAY}.json", 'w') as f:
            json.dump(output, f, indent=2, default=str)
    
    # Summary comparison
    print(f"\n{'='*90}")
    print("SUMMARY COMPARISON")
    print(f"{'='*90}")
    print(f"{'Config':<35} {'Return':>10} {'Max DD':>10} {'Liqs':>5} {'Trades':>8} {'Win%':>8} {'PF':>6}")
    print(f"{'-'*90}")
    for r in results:
        print(f"{r.config_name:<35} {r.cumulative_return_pct:>+9.1f}% {r.max_dd_pct:>9.1f}% {r.total_liquidations:>5} {r.total_trades:>8} {r.win_rate:>7.1f}% {r.profit_factor:>6.2f}")
    
    # HR-216 comparison
    print(f"\n{'='*90}")
    print("ASSESSMENT vs HR-216 (C2_reset_press_20)")
    print(f"{'='*90}")
    print(f"HR-216 Benchmark: +174% return, 2.65% DD, 0 liquidations")
    print(f"")
    
    for r in results:
        status = "✓ VIABLE" if r.total_liquidations == 0 and r.max_dd_pct < 30 else "❌ REJECT"
        if r.total_liquidations > 0:
            status = "❌ CATASTROPHIC"
        elif r.max_dd_pct > 50:
            status = "❌ HIGH RISK"
        elif r.cumulative_return_pct < 0:
            status = "❌ UNPROFITABLE"
        
        print(f"{r.config_name}: {status}")
        print(f"  Return: {r.cumulative_return_pct:+.1f}% vs HR-216 +174%")
        print(f"  Max DD: {r.max_dd_pct:.1f}% vs HR-216 2.65%")
        print(f"  Liquidations: {r.total_liquidations} vs HR-216 0")
    
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
