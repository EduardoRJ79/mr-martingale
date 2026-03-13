#!/usr/bin/env python3
import sys
import os
import time
sys.path.insert(0, os.path.dirname(__file__))

from hl_client import *
from grid_state import *
from config import LEVERAGE, SHORT_LEVERAGE
from typing import Optional

side = sys.argv[1] if len(sys.argv) > 1 else 'long'
base_margin = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

print('=== MANUAL LIVE GRID ===')
print(f'Side: {side} | L1 margin: ${base_margin}')

price = get_mid_price('BTC')
balance = get_account_balance()
print(f'BTC: ${price:,.1f} | Balance: ${balance:,.2f}')

# Approx MAs for state (bot fetches real next poll)
ema34 = price * 1.005  # placeholder
sma14 = price * 1.007

# L1 market entry
leverage = LEVERAGE if side == 'long' else SHORT_LEVERAGE
notional = base_margin * leverage
qty = round(notional / price, 5)

if side == 'long':
    result = market_buy('BTC', qty)
else:
    result = market_sell('BTC', qty)

print(f'Raw result: {result}')
statuses = result['response']['data']['statuses'][0]
if 'error' in statuses:
    print(f'ORDER ERROR: {statuses["error"]}')
    sys.exit(1)
if 'filled' not in statuses:
    print(f'ORDER NOT FILLED: {statuses}')
    sys.exit(1)
fill_px = float(statuses['filled']['avgPx'])
fill_qty = float(statuses['filled']['totalSz'])
print(f'L1 FILLED: {fill_qty:.6f} BTC @ ${fill_px:,.1f} (notional ${notional:,.1f})')

# Build full grid state
bs = load()
grid = GridState()
grid.side = side
grid.active = True
grid.trigger_px = price
grid.ema34 = ema34
grid.sma14 = sma14
grid.opened_at = time.strftime('%Y-%m-%dT%H:%M:%S+00:00')
grid.levels = build_levels(price, side, base_margin)
l1 = grid.levels[0]
l1.filled = True
l1.fill_px = fill_px
l1.fill_qty = fill_qty
grid.recalc()

print(f'Blended: ${grid.blended_entry:,.1f} | TP: ${grid.tp_price:,.1f}')

# L2-L5 limits
oids = []
for lv in grid.levels[1:]:
    lv_qty = round(lv.notional / lv.target_px, 6)
    if side == 'long':
        oid = limit_buy('BTC', lv_qty, lv.target_px)
    else:
        oid = limit_sell('BTC', lv_qty, lv.target_px)
    lv.oid = oid
    lv.fill_qty = lv_qty
    oids.append(oid)
    time.sleep(0.3)

# TP
if side == 'long':
    tp_oid = limit_sell_tp('BTC', grid.total_qty, grid.tp_price)
else:
    tp_oid = limit_buy_tp('BTC', grid.total_qty, grid.tp_price)
grid.tp_oid = tp_oid

# Save state
if side == 'long':
    bs.long_grid = grid
else:
    bs.short_grid = grid
save(bs)

print('SUCCESS — Grid LIVE')
print('L2-L5 OIDs:', oids)
print('TP OID:', tp_oid)
print('Bot PID will manage fills/TP.')
