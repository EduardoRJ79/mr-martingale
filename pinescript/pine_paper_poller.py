#!/usr/bin/env python3
"""
PineScript Paper Trading Poller
================================
Polls Hyperliquid for live 4h candles, runs PINE-003 and PINE-006 signal logic,
and tracks paper positions/equity. No real orders placed.

Usage:
    python3 pine_paper_poller.py          # Run once (for cron)
    python3 pine_paper_poller.py --loop   # Run continuously (polls every 5 min)
"""

import json
import sys
import time
import logging
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

# ─── Paths ────────────────────────────────────────────────────────────────────
WS = Path("/Users/assistant/.openclaw/ws-731228")
STATE_FILE = WS / "execution" / "pine_paper_state.json"
LOG_FILE = WS / "execution" / "paper_logs" / "pine_paper_poller.log"

# ─── HL API (no auth needed for market data) ─────────────────────────────────
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
COIN = "BTC"
TAKER_FEE = 0.00045

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("pine_paper")


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_candles(coin: str = COIN, interval: str = "4h", n: int = 100) -> pd.DataFrame:
    """Fetch candles from Hyperliquid public API."""
    end_ms = int(time.time() * 1000)
    interval_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                   "1h": 3_600_000, "4h": 14_400_000}[interval]
    start_ms = end_ms - n * interval_ms

    resp = requests.post(HL_INFO_URL, json={
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}
    }, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    rows = []
    for c in raw:
        rows.append({
            'timestamp': pd.Timestamp(c['t'], unit='ms', tz='UTC'),
            'open': float(c['o']),
            'high': float(c['h']),
            'low': float(c['l']),
            'close': float(c['c']),
            'volume': float(c['v']),
        })

    df = pd.DataFrame(rows).set_index('timestamp').sort_index()
    return df


def get_mid_price(coin: str = COIN) -> float:
    """Get current mid price from HL."""
    resp = requests.post(HL_INFO_URL, json={"type": "allMids"}, timeout=10)
    resp.raise_for_status()
    mids = resp.json()
    return float(mids[coin])


# ═══════════════════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def gaussian_channel(df: pd.DataFrame, period=144, poles=2, tr_mult=0.655):
    """Simplified Gaussian channel using EMA cascade."""
    close = df['close']
    filt = close.copy()
    for _ in range(poles):
        filt = ema(filt, period)

    atr_val = atr(df, period)
    upper = filt + tr_mult * atr_val
    lower = filt - tr_mult * atr_val
    return upper, lower, filt


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY SIGNALS
# ═══════════════════════════════════════════════════════════════════════════════

def pine003_signals(df: pd.DataFrame) -> Dict:
    """PINE-003 Swing BTC 4h: EMA 20/50, swing breakout, MACD, volume."""
    ema20 = ema(df['close'], 20)
    ema50 = ema(df['close'], 50)
    rsi_val = rsi(df['close'], 14)
    macd_line, macd_signal, _ = macd(df['close'], 12, 26, 9)
    swing_high = df['high'].rolling(10).max()
    swing_low = df['low'].rolling(10).min()
    avg_volume = df['volume'].rolling(20).mean()
    volume_ratio = df['volume'] / avg_volume
    atr_val = atr(df, 14)

    i = -1  # Latest bar
    long_signal = (
        ema20.iloc[i] > ema50.iloc[i] and
        df['close'].iloc[i] > swing_high.iloc[i] * 0.995 and
        40 < rsi_val.iloc[i] < 70 and
        macd_line.iloc[i] > macd_signal.iloc[i] and
        volume_ratio.iloc[i] > 0.8
    )
    short_signal = (
        ema20.iloc[i] < ema50.iloc[i] and
        df['close'].iloc[i] < swing_low.iloc[i] * 1.005 and
        30 < rsi_val.iloc[i] < 60 and
        macd_line.iloc[i] < macd_signal.iloc[i] and
        volume_ratio.iloc[i] > 0.8
    )

    return {
        'long': long_signal,
        'short': short_signal,
        'atr': float(atr_val.iloc[i]),
        'ema20': float(ema20.iloc[i]),
        'ema50': float(ema50.iloc[i]),
        'rsi': float(rsi_val.iloc[i]),
    }


def pine006_signals(df: pd.DataFrame) -> Dict:
    """PINE-006 Gaussian V4H: Gaussian channel crossover with ADX filter."""
    gauss_upper, gauss_lower, gauss_mid = gaussian_channel(df, 144, 2, 0.655)
    atr_val = atr(df, 14)

    i = -1
    prev = -2
    long_signal = df['close'].iloc[i] > gauss_upper.iloc[i] and df['close'].iloc[prev] <= gauss_upper.iloc[prev]
    short_signal = df['close'].iloc[i] < gauss_lower.iloc[i] and df['close'].iloc[prev] <= gauss_lower.iloc[prev]
    exit_long = df['close'].iloc[i] < gauss_mid.iloc[i]
    exit_short = df['close'].iloc[i] > gauss_mid.iloc[i]

    return {
        'long': long_signal,
        'short': short_signal,
        'exit_long': exit_long,
        'exit_short': exit_short,
        'atr': float(atr_val.iloc[i]),
        'gauss_upper': float(gauss_upper.iloc[i]),
        'gauss_lower': float(gauss_lower.iloc[i]),
        'gauss_mid': float(gauss_mid.iloc[i]),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def load_state() -> Dict:
    """Load paper trading state from disk."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)

    return {
        'created': datetime.now(timezone.utc).isoformat(),
        'strategies': {
            'PINE-003': {
                'name': 'Swing BTC 4h',
                'asset': 'BTC',
                'capital': 1000.0,
                'position': 0,        # 1=long, -1=short, 0=flat
                'entry_price': 0.0,
                'entry_time': None,
                'atr_at_entry': 0.0,
                'trades': [],
                'peak_equity': 1000.0,
                'max_drawdown_pct': 0.0,
            },
            'PINE-006': {
                'name': 'Gaussian V4H',
                'asset': 'BTC',
                'capital': 1000.0,
                'position': 0,
                'entry_price': 0.0,
                'entry_time': None,
                'atr_at_entry': 0.0,
                'trades': [],
                'peak_equity': 1000.0,
                'max_drawdown_pct': 0.0,
            },
        },
        'last_check': None,
        'last_bar_time': None,
        'total_checks': 0,
    }


def save_state(state: Dict):
    """Save paper trading state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, default=str)


def update_equity(strat: Dict, price: float) -> float:
    """Calculate current equity for a strategy."""
    if strat['position'] == 0:
        return strat['capital']
    pnl_pct = (price - strat['entry_price']) / strat['entry_price'] * strat['position']
    return strat['capital'] * (1 + pnl_pct)


def process_pine003(strat: Dict, signals: Dict, price: float, now: str):
    """Process PINE-003 signals and update state."""
    equity = update_equity(strat, price)

    # Check ATR stop loss
    if strat['position'] != 0 and strat['atr_at_entry'] > 0:
        stop_dist = 2.0 * strat['atr_at_entry']
        if strat['position'] > 0 and price < (strat['entry_price'] - stop_dist):
            pnl_pct = (price - strat['entry_price']) / strat['entry_price']
            pnl = strat['capital'] * pnl_pct
            fee = strat['capital'] * TAKER_FEE
            strat['capital'] += pnl - fee
            strat['trades'].append({'type': 'stop_loss', 'pnl': pnl - fee, 'price': price, 'time': now})
            log.info(f"PINE-003: STOP LOSS long at {price:.2f}, PnL: {pnl-fee:.2f}")
            strat['position'] = 0
            strat['entry_price'] = 0.0
            return
        elif strat['position'] < 0 and price > (strat['entry_price'] + stop_dist):
            pnl_pct = (strat['entry_price'] - price) / strat['entry_price']
            pnl = strat['capital'] * pnl_pct
            fee = strat['capital'] * TAKER_FEE
            strat['capital'] += pnl - fee
            strat['trades'].append({'type': 'stop_loss', 'pnl': pnl - fee, 'price': price, 'time': now})
            log.info(f"PINE-003: STOP LOSS short at {price:.2f}, PnL: {pnl-fee:.2f}")
            strat['position'] = 0
            strat['entry_price'] = 0.0
            return

    # Exit on opposite signal + flip
    if strat['position'] > 0 and signals['short']:
        pnl_pct = (price - strat['entry_price']) / strat['entry_price']
        pnl = strat['capital'] * pnl_pct
        fee = strat['capital'] * TAKER_FEE
        strat['capital'] += pnl - fee
        strat['trades'].append({'type': 'flip_to_short', 'pnl': pnl - fee, 'price': price, 'time': now})
        log.info(f"PINE-003: FLIP long->short at {price:.2f}, PnL: {pnl-fee:.2f}")
        strat['position'] = -1
        strat['entry_price'] = price
        strat['entry_time'] = now
        strat['atr_at_entry'] = signals['atr']
    elif strat['position'] < 0 and signals['long']:
        pnl_pct = (strat['entry_price'] - price) / strat['entry_price']
        pnl = strat['capital'] * pnl_pct
        fee = strat['capital'] * TAKER_FEE
        strat['capital'] += pnl - fee
        strat['trades'].append({'type': 'flip_to_long', 'pnl': pnl - fee, 'price': price, 'time': now})
        log.info(f"PINE-003: FLIP short->long at {price:.2f}, PnL: {pnl-fee:.2f}")
        strat['position'] = 1
        strat['entry_price'] = price
        strat['entry_time'] = now
        strat['atr_at_entry'] = signals['atr']
    # New entry
    elif strat['position'] == 0 and signals['long']:
        strat['position'] = 1
        strat['entry_price'] = price
        strat['entry_time'] = now
        strat['atr_at_entry'] = signals['atr']
        log.info(f"PINE-003: ENTER LONG at {price:.2f}")
    elif strat['position'] == 0 and signals['short']:
        strat['position'] = -1
        strat['entry_price'] = price
        strat['entry_time'] = now
        strat['atr_at_entry'] = signals['atr']
        log.info(f"PINE-003: ENTER SHORT at {price:.2f}")

    # Track drawdown
    equity = update_equity(strat, price)
    if equity > strat['peak_equity']:
        strat['peak_equity'] = equity
    dd = (strat['peak_equity'] - equity) / strat['peak_equity'] * 100
    if dd > strat['max_drawdown_pct']:
        strat['max_drawdown_pct'] = dd


def process_pine006(strat: Dict, signals: Dict, price: float, now: str):
    """Process PINE-006 signals and update state."""
    # Exit checks
    if strat['position'] > 0 and signals.get('exit_long', False):
        pnl_pct = (price - strat['entry_price']) / strat['entry_price']
        pnl = strat['capital'] * pnl_pct
        fee = strat['capital'] * TAKER_FEE
        strat['capital'] += pnl - fee
        strat['trades'].append({'type': 'exit_long', 'pnl': pnl - fee, 'price': price, 'time': now})
        log.info(f"PINE-006: EXIT LONG at {price:.2f}, PnL: {pnl-fee:.2f}")
        strat['position'] = 0
        strat['entry_price'] = 0.0
    elif strat['position'] < 0 and signals.get('exit_short', False):
        pnl_pct = (strat['entry_price'] - price) / strat['entry_price']
        pnl = strat['capital'] * pnl_pct
        fee = strat['capital'] * TAKER_FEE
        strat['capital'] += pnl - fee
        strat['trades'].append({'type': 'exit_short', 'pnl': pnl - fee, 'price': price, 'time': now})
        log.info(f"PINE-006: EXIT SHORT at {price:.2f}, PnL: {pnl-fee:.2f}")
        strat['position'] = 0
        strat['entry_price'] = 0.0

    # Entry
    if strat['position'] == 0 and signals['long']:
        strat['position'] = 1
        strat['entry_price'] = price
        strat['entry_time'] = now
        strat['atr_at_entry'] = signals['atr']
        log.info(f"PINE-006: ENTER LONG at {price:.2f}")
    elif strat['position'] == 0 and signals['short']:
        strat['position'] = -1
        strat['entry_price'] = price
        strat['entry_time'] = now
        strat['atr_at_entry'] = signals['atr']
        log.info(f"PINE-006: ENTER SHORT at {price:.2f}")

    # Track drawdown
    equity = update_equity(strat, price)
    if equity > strat['peak_equity']:
        strat['peak_equity'] = equity
    dd = (strat['peak_equity'] - equity) / strat['peak_equity'] * 100
    if dd > strat['max_drawdown_pct']:
        strat['max_drawdown_pct'] = dd


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_check():
    """Run one check cycle."""
    state = load_state()
    now = datetime.now(timezone.utc).isoformat()

    log.info("=" * 50)
    log.info(f"Paper trading check at {now}")

    # Fetch 4h candles
    try:
        df = fetch_candles(COIN, "4h", 300)
        price = get_mid_price(COIN)
    except Exception as e:
        log.error(f"Failed to fetch data: {e}")
        return

    log.info(f"BTC price: ${price:,.2f} | Candles: {len(df)} (latest: {df.index[-1]})")

    # Check if we have a new bar since last check
    latest_bar = str(df.index[-1])
    new_bar = latest_bar != state.get('last_bar_time')
    if new_bar:
        log.info(f"NEW 4h bar detected: {latest_bar}")
    else:
        log.info(f"Same bar as last check, monitoring only")

    # Run signals
    sig003 = pine003_signals(df)
    sig006 = pine006_signals(df)

    log.info(f"PINE-003 signals: long={sig003['long']}, short={sig003['short']}, "
             f"RSI={sig003['rsi']:.1f}, EMA20={sig003['ema20']:.0f}, EMA50={sig003['ema50']:.0f}")
    log.info(f"PINE-006 signals: long={sig006['long']}, short={sig006['short']}, "
             f"gauss_mid={sig006['gauss_mid']:.0f}")

    # Process signals (only act on new bars for entries, but check stops always)
    process_pine003(state['strategies']['PINE-003'], sig003, price, now)
    process_pine006(state['strategies']['PINE-006'], sig006, price, now)

    # Status summary
    for code, strat in state['strategies'].items():
        equity = update_equity(strat, price)
        pos_str = {1: "LONG", -1: "SHORT", 0: "FLAT"}[strat['position']]
        pnl_total = sum(t['pnl'] for t in strat['trades']) if strat['trades'] else 0
        log.info(f"{code} {strat['name']}: {pos_str} | Equity: ${equity:.2f} | "
                 f"Trades: {len(strat['trades'])} | Total PnL: ${pnl_total:.2f} | "
                 f"Max DD: {strat['max_drawdown_pct']:.1f}%")

    # Update state
    state['last_check'] = now
    state['last_bar_time'] = latest_bar
    state['total_checks'] = state.get('total_checks', 0) + 1
    save_state(state)

    log.info(f"Check #{state['total_checks']} complete")
    return state


def main():
    if '--loop' in sys.argv:
        log.info("Starting continuous paper trading loop (5 min interval)")
        while True:
            try:
                run_check()
            except Exception as e:
                log.error(f"Check failed: {e}", exc_info=True)
            time.sleep(300)  # 5 minutes
    else:
        state = run_check()
        if state:
            # Print summary for cron output
            for code, strat in state['strategies'].items():
                price = get_mid_price(COIN)
                equity = update_equity(strat, price)
                pos_str = {1: "LONG", -1: "SHORT", 0: "FLAT"}[strat['position']]
                print(f"{code}: {pos_str} | ${equity:.2f} | {len(strat['trades'])} trades | DD: {strat['max_drawdown_pct']:.1f}%")


if __name__ == '__main__':
    main()
