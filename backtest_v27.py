"""
MRM v2.7 Independent Backtest Validator
- 1m bar sim, 4h-aligned entries
- True compounding (risk_pct * equity)
- Regime filter (440d SMA), Long + Short
- Exact liquidation on 1m bars
- Slippage: 3 ticks per fill/exit
- Commission: 0.0045 per side (on notional) — sole fee, replaces exchange taker/maker
- Funding: 0.0013% per 8h on notional
- Reset to $1000 after liquidation
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List
import sys, time

# ═══════ PARAMETERS (v2.7 spec) ════════════════════════════════════════════
INITIAL_CAPITAL    = 1000.0
RESET_CAPITAL      = 1000.0

NUM_LEVELS         = 5
LEVEL_GAPS         = [0.5, 1.5, 7.0, 8.0]
LEVEL_MULTIPLIERS  = [2.0, 2.5, 2.5, 7.0]
RISK_PCT           = 0.30
TP_PCT             = 0.0050    # 0.50%

EMA_SPAN           = 34
SMA_SPAN           = 14
DMA_PERIOD         = 440

LONG_TRIGGER_PCT   = 0.005
SHORT_TRIGGER_PCT  = 0.015

LEV_LONG           = 20
LEV_SHORT          = 15

UNFAV_RISK_SCALE     = 0.60
UNFAV_SPACING_SCALE  = 1.60
UNFAV_TRIGGER_SCALE  = 3.00
UNFAV_HOLD_SCALE     = 0.45

MAX_HOLD_BARS      = 720
COOLDOWN_BARS      = 1

# Costs
COMMISSION         = 0.00045  # 0.045% per side on notional (user: 0,0045 interpreted as 0.045%)
FUNDING_PER_8H     = 0.000013
MAINT_MARGIN_RATE  = 0.005
SLIPPAGE_TICKS     = 3
TICK_SIZE          = 0.01
MIN_EQUITY         = 50.0

# Notional multipliers: L1=1x, L2=2x, L3=5x, L4=12.5x, L5=87.5x
NOTIONAL_MULTS = [1.0]
_m = 1.0
for _mult in LEVEL_MULTIPLIERS:
    _m *= _mult
    NOTIONAL_MULTS.append(_m)

def cum_drops(gaps):
    r, a = [], 0.0
    for g in gaps:
        a += g
        r.append(a / 100.0)
    return r

@dataclass
class GridLevel:
    level: int
    entry_price: float
    notional: float     # always positive
    qty: float          # signed: + long, - short
    fill_1m_idx: int    # index into 1m array

@dataclass
class Trade:
    direction: str
    is_favored: bool
    entry_time: object
    exit_time: object
    levels_filled: int
    entry_price_l1: float
    blended_entry: float
    exit_price: float
    exit_reason: str
    pnl: float
    fees: float
    equity_before: float
    equity_after: float


def run_backtest(data_path, start_date='2017-01-01'):
    print(f"Loading data...")
    df = pd.read_parquet(data_path)
    df = df.sort_values('ts').reset_index(drop=True)
    start_ts = pd.Timestamp(start_date, tz='UTC')
    df = df[df['ts'] >= start_ts].reset_index(drop=True)
    print(f"Data: {len(df):,} bars, {df['ts'].iloc[0]} to {df['ts'].iloc[-1]}")

    # 4h candles
    df['ts_4h'] = df['ts'].dt.floor('4h')
    c4h = df.groupby('ts_4h').agg(
        o=('o','first'), h=('h','max'), l=('l','min'), c=('c','last')
    ).sort_index()
    c4h['ema34'] = c4h['c'].ewm(span=EMA_SPAN, adjust=False).mean()
    c4h['sma14'] = c4h['c'].rolling(SMA_SPAN).mean()

    # Daily for SMA440
    df['ts_1d'] = df['ts'].dt.floor('1D')
    c1d = df.groupby('ts_1d').agg(c=('c','last')).sort_index()
    c1d['sma440'] = c1d['c'].rolling(DMA_PERIOD).mean()

    # Numpy arrays
    ts_arr = df['ts'].values
    h_arr = df['h'].values.astype(np.float64)
    l_arr = df['l'].values.astype(np.float64)
    c_arr = df['c'].values.astype(np.float64)
    n = len(df)

    # 4h boundaries
    ts_4h_vals = df['ts_4h'].values
    boundaries = [0]
    for i in range(1, n):
        if ts_4h_vals[i] != ts_4h_vals[i-1]:
            boundaries.append(i)
    boundaries = np.array(boundaries)

    # 4h MA arrays
    c4h_r = c4h.reset_index()
    c4h_ema = c4h_r['ema34'].values
    c4h_sma = c4h_r['sma14'].values

    # Daily SMA440 dict
    c1d_r = c1d.reset_index()
    sma440_dict = {}
    for j in range(len(c1d_r)):
        sma440_dict[c1d_r['ts_1d'].values[j]] = c1d_r['sma440'].values[j]

    # Map 1m bar -> 4h candle index
    bar_to_4h = np.zeros(n, dtype=np.int64)
    for bi in range(len(boundaries)):
        s = boundaries[bi]
        e = boundaries[bi+1] if bi+1 < len(boundaries) else n
        bar_to_4h[s:e] = bi

    print(f"4h candles: {len(c4h_r)}, boundaries: {len(boundaries)}")
    print(f"SMA440 first valid: {c1d['sma440'].first_valid_index()}")

    # ═══════ SIMULATION ═════════════════════════════════════════════════
    print("\nSimulating...")
    t0 = time.time()

    balance = INITIAL_CAPITAL    # cash balance (fees deducted here)
    trades_list = []
    liquidations = []

    active = False
    direction = None
    is_favored = None
    levels: List[GridLevel] = []
    drops = []
    max_hold = 0
    entry_4h_idx = 0
    cooldown_until = 0
    entry_fees_paid = 0.0   # fees paid on entry for this trade

    monthly = {}

    def track_month(i, eq):
        ts_pd = pd.Timestamp(ts_arr[i])
        k = (ts_pd.year, ts_pd.month)
        if k not in monthly:
            monthly[k] = {'start_eq': eq, 'end_eq': eq, 'trades': 0,
                         'peak_eq': eq, 'trough_eq': eq}
        d = monthly[k]
        d['end_eq'] = eq
        if eq > d['peak_eq']: d['peak_eq'] = eq
        if eq < d['trough_eq']: d['trough_eq'] = eq
        return k

    def get_equity(bal, lvls, price):
        """Cross-margin equity = balance + unrealized PnL."""
        if not lvls:
            return bal
        total_qty = sum(lv.qty for lv in lvls)
        blended = sum(lv.qty * lv.entry_price for lv in lvls) / total_qty
        upnl = total_qty * (price - blended)
        return bal + upnl

    for i in range(n):
        if i % 500000 == 0 and i > 0:
            el = time.time() - t0
            eq = get_equity(balance, levels if active else [], c_arr[i])
            print(f"  {i/n*100:.1f}% {el:.0f}s eq=${eq:.2f} trades={len(trades_list)} liqs={len(liquidations)}")

        ci = bar_to_4h[i]
        is_boundary = (i == boundaries[ci])
        mk = track_month(i, get_equity(balance, levels if active else [], c_arr[i]))

        # ── ACTIVE POSITION ──────────────────────────────────────────
        if active:
            total_qty = sum(lv.qty for lv in levels)
            blended = sum(lv.qty * lv.entry_price for lv in levels) / total_qty
            total_notional = sum(lv.notional for lv in levels)

            # Worst price for liquidation check
            if direction == 'long':
                worst = l_arr[i]
                best = h_arr[i]
            else:
                worst = h_arr[i]
                best = l_arr[i]

            # Equity at worst = balance + upnl at worst
            upnl_worst = total_qty * (worst - blended)
            eq_worst = balance + upnl_worst
            maint = total_notional * MAINT_MARGIN_RATE

            if eq_worst <= maint:
                # LIQUIDATED - lose all remaining equity
                liq_pnl = -(balance - 0)  # lose everything
                eq_after = 0.0

                ts_pd = pd.Timestamp(ts_arr[i])
                t = Trade(direction=direction, is_favored=is_favored,
                         entry_time=pd.Timestamp(ts_arr[levels[0].fill_1m_idx]),
                         exit_time=ts_pd, levels_filled=len(levels),
                         entry_price_l1=levels[0].entry_price,
                         blended_entry=blended, exit_price=worst,
                         exit_reason='LIQUIDATED', pnl=-balance,
                         fees=entry_fees_paid, equity_before=balance,
                         equity_after=0.0)
                trades_list.append(t)
                liquidations.append(t)
                monthly[mk]['trades'] += 1

                balance = RESET_CAPITAL
                active = False
                levels = []
                entry_fees_paid = 0.0
                cooldown_until = ci + COOLDOWN_BARS
                continue

            # Grid fills (one per 1m bar)
            if len(levels) < NUM_LEVELS:
                for lvl_idx in range(len(levels), NUM_LEVELS):
                    drop_frac = drops[lvl_idx - 1]
                    l1_price = levels[0].entry_price

                    if direction == 'long':
                        fill_target = l1_price * (1 - drop_frac)
                        triggered = l_arr[i] <= fill_target
                    else:
                        fill_target = l1_price * (1 + drop_frac)
                        triggered = h_arr[i] >= fill_target

                    if triggered:
                        slip = SLIPPAGE_TICKS * TICK_SIZE
                        fp = (fill_target - slip) if direction == 'long' else (fill_target + slip)

                        l1_not = levels[0].notional
                        notional = l1_not * NOTIONAL_MULTS[lvl_idx]
                        qty = notional / fp if direction == 'long' else -notional / fp

                        # Commission deducted from balance
                        fee = notional * COMMISSION
                        balance -= fee
                        entry_fees_paid += fee

                        levels.append(GridLevel(level=lvl_idx+1, entry_price=fp,
                                               notional=notional, qty=qty, fill_1m_idx=i))

                        # Recalc
                        total_qty = sum(lv.qty for lv in levels)
                        blended = sum(lv.qty * lv.entry_price for lv in levels) / total_qty
                        total_notional = sum(lv.notional for lv in levels)
                        break

            # Take-profit check
            if direction == 'long':
                tp = blended * (1 + TP_PCT)
                tp_hit = best >= tp
            else:
                tp = blended * (1 - TP_PCT)
                tp_hit = best <= tp

            if tp_hit:
                slip = SLIPPAGE_TICKS * TICK_SIZE
                ep = (tp - slip) if direction == 'long' else (tp + slip)

                # Exit fee
                exit_fee = total_notional * COMMISSION
                # Funding
                hold_1m = i - levels[0].fill_1m_idx
                fund_periods = hold_1m / (8 * 60)
                funding = total_notional * FUNDING_PER_8H * fund_periods

                gross_pnl = total_qty * (ep - blended)
                net_pnl = gross_pnl - exit_fee - funding
                balance += gross_pnl - exit_fee - funding
                total_fee = entry_fees_paid + exit_fee + funding

                ts_pd = pd.Timestamp(ts_arr[i])
                t = Trade(direction=direction, is_favored=is_favored,
                         entry_time=pd.Timestamp(ts_arr[levels[0].fill_1m_idx]),
                         exit_time=ts_pd, levels_filled=len(levels),
                         entry_price_l1=levels[0].entry_price,
                         blended_entry=blended, exit_price=ep,
                         exit_reason='TP', pnl=net_pnl, fees=total_fee,
                         equity_before=balance - net_pnl, equity_after=balance)
                trades_list.append(t)
                monthly[mk]['trades'] += 1

                active = False
                levels = []
                entry_fees_paid = 0.0
                cooldown_until = ci + COOLDOWN_BARS
                continue

            # Timeout
            if is_boundary and (ci - entry_4h_idx) >= max_hold:
                ep = c_arr[i]
                slip = SLIPPAGE_TICKS * TICK_SIZE
                ep = (ep - slip) if direction == 'long' else (ep + slip)

                exit_fee = total_notional * COMMISSION
                hold_1m = i - levels[0].fill_1m_idx
                fund_periods = hold_1m / (8 * 60)
                funding = total_notional * FUNDING_PER_8H * fund_periods

                gross_pnl = total_qty * (ep - blended)
                net_pnl = gross_pnl - exit_fee - funding
                balance += gross_pnl - exit_fee - funding
                total_fee = entry_fees_paid + exit_fee + funding

                ts_pd = pd.Timestamp(ts_arr[i])
                t = Trade(direction=direction, is_favored=is_favored,
                         entry_time=pd.Timestamp(ts_arr[levels[0].fill_1m_idx]),
                         exit_time=ts_pd, levels_filled=len(levels),
                         entry_price_l1=levels[0].entry_price,
                         blended_entry=blended, exit_price=ep,
                         exit_reason='TIMEOUT', pnl=net_pnl, fees=total_fee,
                         equity_before=balance - net_pnl, equity_after=balance)
                trades_list.append(t)
                monthly[mk]['trades'] += 1

                active = False
                levels = []
                entry_fees_paid = 0.0
                cooldown_until = ci + COOLDOWN_BARS
                continue

        # ── ENTRY (4h boundary, idle) ─────────────────────────────────
        if is_boundary and not active:
            if ci < cooldown_until:
                continue
            if balance < MIN_EQUITY:
                continue
            if ci < 1:
                continue

            prev_ci = ci - 1
            if prev_ci >= len(c4h_ema):
                continue
            ema34 = c4h_ema[prev_ci]
            sma14 = c4h_sma[prev_ci]
            if np.isnan(ema34) or np.isnan(sma14):
                continue

            price = c_arr[i]

            # SMA440
            ts_pd = pd.Timestamp(ts_arr[i])
            day_ts = np.datetime64(ts_pd.normalize().asm8)
            sma440 = sma440_dict.get(day_ts, None)
            if sma440 is None or np.isnan(sma440):
                prev_day = day_ts - np.timedelta64(1, 'D')
                sma440 = sma440_dict.get(prev_day, None)
            if sma440 is None or np.isnan(sma440):
                continue

            is_bull = price > sma440
            pct_below_ema = (ema34 - price) / ema34
            pct_below_sma = (sma14 - price) / sma14
            pct_above_ema = (price - ema34) / ema34
            pct_above_sma = (price - sma14) / sma14

            entered = False

            # ── LONG ──
            long_fav = is_bull
            if long_fav:
                trig = LONG_TRIGGER_PCT
                risk = RISK_PCT
                lev = LEV_LONG
                hold = MAX_HOLD_BARS
                gaps = LEVEL_GAPS
            else:
                trig = LONG_TRIGGER_PCT * UNFAV_TRIGGER_SCALE
                risk = RISK_PCT * UNFAV_RISK_SCALE
                lev = LEV_LONG
                hold = int(MAX_HOLD_BARS * UNFAV_HOLD_SCALE)
                gaps = [g * UNFAV_SPACING_SCALE for g in LEVEL_GAPS]

            if pct_below_ema >= trig and pct_below_sma >= trig:
                slip = SLIPPAGE_TICKS * TICK_SIZE
                ep = price + slip  # worse for buyer
                margin = risk * balance
                notional = margin * lev
                qty = notional / ep

                fee = notional * COMMISSION
                balance -= fee  # pay entry commission

                levels = [GridLevel(level=1, entry_price=ep, notional=notional,
                                   qty=qty, fill_1m_idx=i)]
                direction = 'long'
                is_favored = long_fav
                drops = cum_drops(gaps)
                max_hold = hold
                entry_4h_idx = ci
                entry_fees_paid = fee
                active = True
                entered = True

            # ── SHORT ──
            if not entered:
                short_fav = not is_bull
                if short_fav:
                    trig = SHORT_TRIGGER_PCT
                    risk = RISK_PCT
                    lev = LEV_SHORT
                    hold = MAX_HOLD_BARS
                    gaps = LEVEL_GAPS
                else:
                    trig = SHORT_TRIGGER_PCT * UNFAV_TRIGGER_SCALE
                    risk = RISK_PCT * UNFAV_RISK_SCALE
                    lev = LEV_SHORT
                    hold = int(MAX_HOLD_BARS * UNFAV_HOLD_SCALE)
                    gaps = [g * UNFAV_SPACING_SCALE for g in LEVEL_GAPS]

                if pct_above_ema >= trig and pct_above_sma >= trig:
                    slip = SLIPPAGE_TICKS * TICK_SIZE
                    ep = price - slip  # worse for seller
                    margin = risk * balance
                    notional = margin * lev
                    qty = -notional / ep

                    fee = notional * COMMISSION
                    balance -= fee

                    levels = [GridLevel(level=1, entry_price=ep, notional=notional,
                                       qty=qty, fill_1m_idx=i)]
                    direction = 'short'
                    is_favored = short_fav
                    drops = cum_drops(gaps)
                    max_hold = hold
                    entry_4h_idx = ci
                    entry_fees_paid = fee
                    active = True

    # Close open position at end
    if active and levels:
        total_qty = sum(lv.qty for lv in levels)
        blended = sum(lv.qty * lv.entry_price for lv in levels) / total_qty
        total_notional = sum(lv.notional for lv in levels)
        ep = c_arr[-1]
        exit_fee = total_notional * COMMISSION
        hold_1m = n - 1 - levels[0].fill_1m_idx
        funding = total_notional * FUNDING_PER_8H * (hold_1m / (8*60))
        gross_pnl = total_qty * (ep - blended)
        balance += gross_pnl - exit_fee - funding
        ts_pd = pd.Timestamp(ts_arr[-1])
        t = Trade(direction=direction, is_favored=is_favored,
                 entry_time=pd.Timestamp(ts_arr[levels[0].fill_1m_idx]),
                 exit_time=ts_pd, levels_filled=len(levels),
                 entry_price_l1=levels[0].entry_price,
                 blended_entry=blended, exit_price=ep,
                 exit_reason='END_OF_DATA', pnl=gross_pnl - exit_fee - funding,
                 fees=entry_fees_paid + exit_fee + funding,
                 equity_before=balance - (gross_pnl - exit_fee - funding),
                 equity_after=balance)
        trades_list.append(t)
        mk2 = (ts_pd.year, ts_pd.month)
        if mk2 in monthly: monthly[mk2]['trades'] += 1

    print(f"\nDone in {time.time()-t0:.1f}s, {len(trades_list)} trades, {len(liquidations)} liqs")
    return balance, trades_list, liquidations, monthly


def print_report(equity, trades, liquidations, monthly):
    print("\n" + "=" * 85)
    print("  MRM v2.7 INDEPENDENT BACKTEST VALIDATION")
    print(f"  Capital=${INITIAL_CAPITAL:.0f} | Slippage={SLIPPAGE_TICKS} ticks | Commission={COMMISSION*100:.2f}%/side")
    print("=" * 85)

    total = len(trades)
    tp = [t for t in trades if t.exit_reason == 'TP']
    to = [t for t in trades if t.exit_reason == 'TIMEOUT']
    lq = [t for t in trades if t.exit_reason == 'LIQUIDATED']
    eod = [t for t in trades if t.exit_reason == 'END_OF_DATA']

    print(f"\n  TRADES: {total}")
    if total:
        print(f"    TP:          {len(tp)} ({len(tp)/total*100:.1f}%)")
        print(f"    Timeout:     {len(to)}")
        print(f"    Liquidated:  {len(lq)}")
        print(f"    End-of-data: {len(eod)}")

    if tp:
        wins = [t for t in tp if t.pnl > 0]
        print(f"    TP win rate: {len(wins)}/{len(tp)} ({len(wins)/len(tp)*100:.1f}%)")
        print(f"    Avg TP PnL:  ${np.mean([t.pnl for t in tp]):.2f}")

    long_t = [t for t in trades if t.direction == 'long']
    short_t = [t for t in trades if t.direction == 'short']
    fav_t = [t for t in trades if t.is_favored]
    unfav_t = [t for t in trades if not t.is_favored]
    print(f"\n  DIRECTION: Long={len(long_t)} Short={len(short_t)} | Favored={len(fav_t)} Unfav={len(unfav_t)}")

    ld = {}
    for t in trades:
        ld[t.levels_filled] = ld.get(t.levels_filled, 0) + 1
    print(f"\n  LEVELS:")
    for lv in sorted(ld):
        print(f"    L{lv}: {ld[lv]} ({ld[lv]/total*100:.1f}%)")

    print(f"\n  LIQUIDATIONS: {len(lq)}")
    for lt in lq:
        print(f"    {lt.entry_time} -> {lt.exit_time} | {lt.direction} {'FAV' if lt.is_favored else 'UNFAV'}")
        print(f"      L1=${lt.entry_price_l1:,.2f} liq=${lt.exit_price:,.2f} L{lt.levels_filled} lost=${-lt.pnl:,.2f}")

    print(f"\n  EQUITY:")
    print(f"    Initial:  ${INITIAL_CAPITAL:,.2f}")
    print(f"    Final:    ${equity:,.2f}")
    if INITIAL_CAPITAL > 0:
        ret = (equity / INITIAL_CAPITAL - 1) * 100
        print(f"    Return:   {ret:,.1f}%")

    if trades and len(trades) > 1:
        first = trades[0].entry_time
        last = trades[-1].exit_time
        yrs = (last - first).total_seconds() / (365.25 * 86400)
        if yrs > 0 and equity > 0:
            cagr = (equity / INITIAL_CAPITAL) ** (1 / yrs) - 1
            print(f"    CAGR:     {cagr*100:.1f}% over {yrs:.2f}y")

    # Max DD from trade-level equity curve
    eq_curve = []
    running_eq = INITIAL_CAPITAL
    for t in trades:
        if t.exit_reason == 'LIQUIDATED':
            eq_curve.append(0.0)
            running_eq = RESET_CAPITAL
        else:
            eq_curve.append(t.equity_after)
            running_eq = t.equity_after
    if eq_curve:
        eq_arr = np.array([INITIAL_CAPITAL] + eq_curve)
        peak = np.maximum.accumulate(eq_arr)
        dd = (eq_arr - peak) / peak
        print(f"    Max DD:   {dd.min()*100:.1f}%")

    total_fees = sum(t.fees for t in trades)
    print(f"    Fees:     ${total_fees:,.2f}")

    # ── MONTHLY TABLE ──
    print(f"\n  {'Month':<10} {'End Equity':>14} {'Return %':>10} {'Trades':>8} {'Max DD %':>10}")
    print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*8} {'-'*10}")
    for ym in sorted(monthly):
        d = monthly[ym]
        s, e = d['start_eq'], d['end_eq']
        r = (e / s - 1) * 100 if s > 0 else 0
        p, tr = d['peak_eq'], d['trough_eq']
        mdd = (tr / p - 1) * 100 if p > 0 else 0
        print(f"  {ym[0]}-{ym[1]:02d}    ${e:>13,.2f} {r:>9.2f}% {d['trades']:>8} {mdd:>9.2f}%")


if __name__ == '__main__':
    DATA_PATH = 'signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet'
    start = '2017-01-01'
    if len(sys.argv) > 1:
        start = sys.argv[1]
    equity, trades, liquidations, monthly = run_backtest(DATA_PATH, start)
    print_report(equity, trades, liquidations, monthly)
