"""
Per-Asset Grid Bot Runner (Paper Trade Only)

Runs the Mr Martingale grid strategy for a single coin in paper mode.
Each coin gets isolated: config/state/log/command queue.

Usage:
  python3 -m multi_asset.coin_runner ETH
  python3 -m multi_asset.coin_runner SOL --dry-run
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Use the existing hl_client for market data only
sys.path.insert(0, str(Path(__file__).parent.parent))
from execution.hl_client import get_mid_price, get_candles

from .asset_config import generate_coin_config, CMDS_DIR


def setup_logging(coin: str, log_file: str):
    logger = logging.getLogger(f"grid_bot_{coin}")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(f"%(asctime)s {coin:5s} %(levelname)-8s %(message)s")

    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


class PaperGridBot:
    """Paper-trade grid bot for a single asset."""

    def __init__(self, coin: str, cfg: dict, dry_run: bool = False):
        self.coin = coin
        self.cfg = cfg
        self.dry_run = dry_run
        self.log = setup_logging(coin, cfg['log_file'])

        self.state_file = Path(cfg['state_file'])
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self.cmd_dir = Path(cfg['command_dir'])
        self.cmd_dir.mkdir(parents=True, exist_ok=True)
        (self.cmd_dir / 'processed').mkdir(exist_ok=True)

        self.equity = 400.0
        self.long_grid = None
        self.short_grid = None

        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                self.equity = state.get('equity', 400.0)
                self.long_grid = state.get('long_grid')
                self.short_grid = state.get('short_grid')
                self.log.info(f"Loaded state: equity=${self.equity:.2f}")
            except Exception as e:
                self.log.error(f"Failed to load state: {e}")

    def _save_state(self):
        state = {
            'coin': self.coin,
            'equity': self.equity,
            'long_grid': self.long_grid,
            'short_grid': self.short_grid,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    def fetch_market_state(self):
        """Fetch current price and compute MAs."""
        candles = get_candles(self.coin, self.cfg['candle_interval'], n=60)
        closed = candles[:-1]
        closes = pd.Series([float(c['c']) for c in closed])

        ema = closes.ewm(span=self.cfg['ema_span'], adjust=False).mean().iloc[-1]
        if self.cfg['ma_type'] == 'sma':
            ma = closes.rolling(self.cfg['ma_period']).mean().iloc[-1]
        else:
            ma = closes.ewm(span=self.cfg['ma_period'], adjust=False).mean().iloc[-1]

        price = get_mid_price(self.coin)
        return float(price), float(ema), float(ma)

    def open_long_grid(self, price: float):
        """Simulate opening a long grid."""
        lev = self.cfg['leverage_long']
        base_margin = self.equity * self.cfg['base_margin_pct']
        levels = []

        for i in range(self.cfg['num_levels']):
            margin = base_margin * (self.cfg['multiplier'] ** i)
            notional = margin * lev
            if i == 0:
                target = price
            else:
                target = price * (1 - self.cfg['cum_drops'][i - 1])
            qty = notional / target
            levels.append({
                'level': i + 1,
                'target_px': target,
                'margin': margin,
                'qty': round(qty, self.cfg['sz_decimals']),
                'filled': i == 0,
                'fill_px': price if i == 0 else 0,
            })

        filled = [l for l in levels if l['filled']]
        total_qty = sum(l['qty'] for l in filled)
        blended = sum(l['qty'] * l['fill_px'] for l in filled) / total_qty
        tp_px = blended * (1 + self.cfg['tp_pct'] / 100)

        self.long_grid = {
            'side': 'long',
            'levels': levels,
            'blended_entry': blended,
            'tp_price': tp_px,
            'total_qty': total_qty,
            'opened_at': datetime.now(timezone.utc).isoformat(),
            'open_bar': 0,
        }
        self.log.info(f"📈 LONG opened @ ${price:,.4f} | TP: ${tp_px:,.4f} | Margin: ${base_margin:.2f}")
        self._save_state()

    def open_short_grid(self, price: float):
        """Simulate opening a short grid."""
        lev = self.cfg['leverage_short']
        base_margin = self.equity * self.cfg['base_margin_pct']
        levels = []

        for i in range(self.cfg['num_levels']):
            margin = base_margin * (self.cfg['multiplier'] ** i)
            notional = margin * lev
            if i == 0:
                target = price
            else:
                target = price * (1 + self.cfg['cum_drops'][i - 1])
            qty = notional / target
            levels.append({
                'level': i + 1,
                'target_px': target,
                'margin': margin,
                'qty': round(qty, self.cfg['sz_decimals']),
                'filled': i == 0,
                'fill_px': price if i == 0 else 0,
            })

        filled = [l for l in levels if l['filled']]
        total_qty = sum(l['qty'] for l in filled)
        blended = sum(l['qty'] * l['fill_px'] for l in filled) / total_qty
        tp_px = blended * (1 - self.cfg['tp_pct'] / 100)

        self.short_grid = {
            'side': 'short',
            'levels': levels,
            'blended_entry': blended,
            'tp_price': tp_px,
            'total_qty': total_qty,
            'opened_at': datetime.now(timezone.utc).isoformat(),
            'open_bar': 0,
        }
        self.log.info(f"📉 SHORT opened @ ${price:,.4f} | TP: ${tp_px:,.4f} | Margin: ${base_margin:.2f}")
        self._save_state()

    def check_grid(self, grid: dict, price: float) -> bool:
        """Check and update a grid. Returns True if grid should be closed."""
        if grid is None:
            return False

        # Check level fills
        for lv in grid['levels']:
            if not lv['filled']:
                if grid['side'] == 'long' and price <= lv['target_px']:
                    lv['filled'] = True
                    lv['fill_px'] = lv['target_px']
                    self.log.info(f"  L{lv['level']} filled @ ${lv['target_px']:,.4f}")
                elif grid['side'] == 'short' and price >= lv['target_px']:
                    lv['filled'] = True
                    lv['fill_px'] = lv['target_px']
                    self.log.info(f"  L{lv['level']} filled @ ${lv['target_px']:,.4f}")

        # Recalc
        filled = [l for l in grid['levels'] if l['filled']]
        if not filled:
            return False

        total_qty = sum(l['qty'] for l in filled)
        blended = sum(l['qty'] * l['fill_px'] for l in filled) / total_qty
        grid['blended_entry'] = blended
        grid['total_qty'] = total_qty

        if grid['side'] == 'long':
            grid['tp_price'] = blended * (1 + self.cfg['tp_pct'] / 100)
        else:
            grid['tp_price'] = blended * (1 - self.cfg['tp_pct'] / 100)

        # Check TP
        if grid['side'] == 'long' and price >= grid['tp_price']:
            pnl = total_qty * (grid['tp_price'] - blended)
            total_margin = sum(l['margin'] for l in filled)
            notional = total_qty * blended
            fees = notional * (self.cfg['taker_fee'] + self.cfg['maker_fee'])
            pnl -= fees
            self.equity += pnl
            self.log.info(f"✅ {grid['side'].upper()} TP HIT @ ${price:,.4f} | PnL: ${pnl:+.4f} | Equity: ${self.equity:.2f}")
            return True

        if grid['side'] == 'short' and price <= grid['tp_price']:
            pnl = total_qty * (blended - grid['tp_price'])
            total_margin = sum(l['margin'] for l in filled)
            notional = total_qty * blended
            fees = notional * (self.cfg['taker_fee'] + self.cfg['maker_fee'])
            pnl -= fees
            self.equity += pnl
            self.log.info(f"✅ {grid['side'].upper()} TP HIT @ ${price:,.4f} | PnL: ${pnl:+.4f} | Equity: ${self.equity:.2f}")
            return True

        return False

    def run(self):
        """Main loop — paper trade only."""
        self.log.info("=" * 50)
        self.log.info(f"Mr Martingale [{self.coin}] — PAPER TRADE")
        self.log.info(f"Params: EMA{self.cfg['ema_span']}/SMA{self.cfg['ma_period']} | "
                      f"Long: {self.cfg['long_trigger_pct']}%/{self.cfg['leverage_long']}x | "
                      f"Short: {self.cfg['short_trigger_pct']}%/{self.cfg['leverage_short']}x | "
                      f"TP: {self.cfg['tp_pct']}%")
        self.log.info("=" * 50)

        if self.dry_run:
            price, ema, ma = self.fetch_market_state()
            pct_below = (ema - price) / ema * 100
            pct_above = (price - ema) / ema * 100
            self.log.info(f"DRY RUN: {self.coin} ${price:,.4f} | EMA: ${ema:,.4f} | MA: ${ma:,.4f}")
            self.log.info(f"  Below EMA: {pct_below:.2f}% | Above EMA: {pct_above:.2f}%")
            self.log.info(f"  Long trigger: {self.cfg['long_trigger_pct']}% | Short trigger: {self.cfg['short_trigger_pct']}%")
            return

        while True:
            try:
                price, ema, ma = self.fetch_market_state()
                pct_below_ema = (ema - price) / ema * 100
                pct_below_ma = (ma - price) / ma * 100
                pct_above_ema = (price - ema) / ema * 100
                pct_above_ma = (price - ma) / ma * 100

                self.log.info(
                    f"[PAPER] {self.coin} ${price:,.4f} | "
                    f"↓EMA {pct_below_ema:+.2f}% ↓MA {pct_below_ma:+.2f}% | "
                    f"Long: {'OPEN' if self.long_grid else 'idle'} | "
                    f"Short: {'OPEN' if self.short_grid else 'idle'} | "
                    f"Eq: ${self.equity:.2f}"
                )

                # Check active grids
                if self.long_grid:
                    if self.check_grid(self.long_grid, price):
                        self.long_grid = None
                        self._save_state()

                if self.short_grid:
                    if self.check_grid(self.short_grid, price):
                        self.short_grid = None
                        self._save_state()

                # Open new grids
                if not self.long_grid and not self.short_grid:
                    if pct_below_ema >= self.cfg['long_trigger_pct'] and \
                       pct_below_ma >= self.cfg['long_trigger_pct']:
                        self.open_long_grid(price)
                    elif pct_above_ema >= self.cfg['short_trigger_pct'] and \
                         pct_above_ma >= self.cfg['short_trigger_pct']:
                        self.open_short_grid(price)

            except KeyboardInterrupt:
                self.log.info("Shutting down")
                self._save_state()
                break
            except Exception as e:
                self.log.exception(f"Error: {e}")

            time.sleep(self.cfg['poll_seconds'])


def main():
    parser = argparse.ArgumentParser(description="Per-asset Mr Martingale paper trader")
    parser.add_argument('coin', type=str, help='Coin symbol (ETH, SOL, XRP)')
    parser.add_argument('--dry-run', action='store_true', help='Single check, no loop')
    args = parser.parse_args()

    coin = args.coin.upper()
    cfg = generate_coin_config(coin)
    bot = PaperGridBot(coin, cfg, dry_run=args.dry_run)
    bot.run()


if __name__ == '__main__':
    main()
