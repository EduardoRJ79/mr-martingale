"""
MRM v2.8 — Production Backtest
===============================
Validated engine with the winning v2.8 configuration.

Changes from v2.7:
  1. risk_pct:           0.30 -> 0.50
  2. short_trigger_pct:  1.5% -> 8.0%
  3. level_gaps:         [0.5, 1.5, 7.0, 8.0] -> [0.5, 1.5, 10.0, 14.0]
  4. NEW: dd20d filter   — block long entries when price > 10% below 20-day high

Data: signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet
Period: 2018-10-31 (SMA440 warmup) to end of data
Liq check: 1-minute bar resolution (worst-case wick)
"""
import pandas as pd, numpy as np, time, csv, os

# ── Data loading ────────────────────────────────────────────────────────────
print("Loading data...")
DATA_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
    'signals', 'multi_asset_results', 'btcusdt_binance_1m_2017_2026.parquet')
DATA_PATH = os.path.normpath(DATA_PATH)
df = pd.read_parquet(DATA_PATH).sort_values('ts').reset_index(drop=True)
n = len(df)

# 4H bars + indicators
df['t4h'] = df['ts'].dt.floor('4h')
c4 = df.groupby('t4h').agg(
    o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last')
).sort_index()
c4['ema34'] = c4['c'].ewm(span=34, adjust=False).mean()
c4['sma14'] = c4['c'].rolling(14).mean()
c4['high_20d'] = c4['h'].rolling(120).max()  # 120 x 4H = 20 days

ema_v = c4['ema34'].values
sma_v = c4['sma14'].values
high_20d_v = c4['high_20d'].values

# Daily SMA440
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

# Simulation window
SIM_START = pd.Timestamp('2018-10-31', tz='UTC')
SIM_END = pd.Timestamp('2026-03-28 23:59:59', tz='UTC')
sim_idx = np.searchsorted(ts_arr, np.datetime64(SIM_START.asm8))
sim_end_idx = np.searchsorted(ts_arr, np.datetime64(SIM_END.asm8))
print(f"Data: {n:,} bars | Sim: {SIM_START.date()} to {SIM_END.date()} | idx [{sim_idx}:{sim_end_idx}]")

# ── v2.8 Parameters ────────────────────────────────────────────────────────
RISK_PCT = 0.50
TP_PCT = 0.005
LEVEL_GAPS = [0.5, 1.5, 10.0, 14.0]
LEVEL_MULTS_SEQ = [2.0, 2.5, 2.5, 7.0]
MAX_LEVELS = 5
LONG_TRIGGER_PCT = 0.005
SHORT_TRIGGER_PCT = 0.08
UNFAV_TRIGGER_SCALE = 3.0
UNFAV_RISK_SCALE = 0.60
UNFAV_SPACING_SCALE = 1.60
UNFAV_HOLD_SCALE = 0.45
MAX_HOLD_BARS = 720
MIN_EQUITY = 50
DD_20D_THRESHOLD = -0.10   # NEW: block longs when price >10% below 20d high

# Cost model
COMM = 0.00045      # 0.045% per side
TAKER = 0.000432
MAKER = 0.000144
FUND_8H = 0.000013  # 0.0013% per 8h on notional
SLIP = 0.03          # 3 ticks
MAINT = 0.005        # 0.5% maintenance margin

# Derived
NOT_MULTS = [1.0]
_m = 1.0
for x in LEVEL_MULTS_SEQ:
    _m *= x
    NOT_MULTS.append(_m)
# NOT_MULTS = [1.0, 2.0, 5.0, 12.5, 87.5]


def cum_drops(gaps):
    """Cumulative percentage drops for grid levels."""
    result, acc = [], 0.0
    for g in gaps:
        acc += g
        result.append(acc / 100.0)
    return result


class Level:
    """Represents a single grid fill."""
    __slots__ = ['level', 'price', 'notional', 'qty', 'idx']

    def __init__(self, level, price, notional, qty, idx):
        self.level = level
        self.price = price
        self.notional = notional
        self.qty = qty
        self.idx = idx


# ── Simulation ──────────────────────────────────────────────────────────────
balance = 1000.0
n_tp = n_to = n_liq = 0
active = False
direction = None
favored = None
levels = []
drops = []
max_hold = 0
entry_candle = 0
cooldown_until = 0
peak_equity = 1000.0
max_drawdown = 0.0
level_dist = {}
long_count = short_count = fav_count = unfav_count = filtered_count = 0
liq_events = []
monthly = {}
csv_rows = []
trade_num = 0

t0 = time.time()

for i in range(min(n, sim_end_idx + 1)):
    ci = bar_to_candle[i]
    is_boundary = (i == bounds[ci])

    # ── Equity tracking ─────────────────────────────────────────────────
    if i >= sim_idx:
        equity = balance
        if active and levels:
            total_qty = sum(lv.qty for lv in levels)
            blended = sum(lv.qty * lv.price for lv in levels) / total_qty
            equity = balance + total_qty * (c_arr[i] - blended)
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
        tp_ = pd.Timestamp(ts_arr[i])
        k = (tp_.year, tp_.month)
        if k not in monthly:
            monthly[k] = {'s': equity, 'e': equity, 'n': 0, 'pk': equity, 'tr': equity}
        d = monthly[k]
        d['e'] = equity
        if equity > d['pk']:
            d['pk'] = equity
        if equity < d['tr']:
            d['tr'] = equity

    # ── Active position management ──────────────────────────────────────
    if active:
        total_qty = sum(lv.qty for lv in levels)
        blended = sum(lv.qty * lv.price for lv in levels) / total_qty
        total_notional = sum(lv.notional for lv in levels)

        # Liquidation check on 1m worst-case wick
        worst_px = l_arr[i] if direction == 'long' else h_arr[i]
        unrealized = total_qty * (worst_px - blended)
        if balance + unrealized <= total_notional * MAINT:
            n_liq += 1
            trade_num += 1
            liq_events.append(
                f"{pd.Timestamp(ts_arr[i])} {direction} L{len(levels)} eq=${balance:,.0f}")
            csv_rows.append([
                trade_num, direction, 'Y' if favored else 'N',
                pd.Timestamp(ts_arr[levels[0].idx]),
                pd.Timestamp(ts_arr[i]),
                'LIQUIDATED', len(levels),
                f"{levels[0].price:.2f}", f"{blended:.2f}", f"{c_arr[i]:.2f}",
                f"{-balance:.2f}", '', f"{balance:.2f}", '0.00',
                i - levels[0].idx
            ])
            if i >= sim_idx and k in monthly:
                monthly[k]['n'] += 1
            balance = 1000.0
            active = False
            levels = []
            cooldown_until = ci + 1
            peak_equity = 1000.0
            continue

        # Grid fills on 1m bars
        if len(levels) < MAX_LEVELS:
            for li in range(len(levels), MAX_LEVELS):
                if li - 1 >= len(drops):
                    break
                l1_price = levels[0].price
                drop_pct = drops[li - 1]
                if direction == 'long':
                    fill_trigger = l1_price * (1 - drop_pct)
                    hit = l_arr[i] <= fill_trigger
                else:
                    fill_trigger = l1_price * (1 + drop_pct)
                    hit = h_arr[i] >= fill_trigger
                if hit:
                    fill_px = (fill_trigger - SLIP) if direction == 'long' else (fill_trigger + SLIP)
                    lv_notional = levels[0].notional * NOT_MULTS[li]
                    lv_qty = lv_notional / fill_px if direction == 'long' else -lv_notional / fill_px
                    fee_in = lv_notional * (MAKER + COMM)
                    balance -= fee_in
                    levels.append(Level(li + 1, fill_px, lv_notional, lv_qty, i))
                    total_qty = sum(lv.qty for lv in levels)
                    blended = sum(lv.qty * lv.price for lv in levels) / total_qty
                    total_notional = sum(lv.notional for lv in levels)
                    break

        # Take-profit on 1m bars
        best_px = h_arr[i] if direction == 'long' else l_arr[i]
        tp_price = blended * (1 + TP_PCT) if direction == 'long' else blended * (1 - TP_PCT)
        tp_hit = best_px >= tp_price if direction == 'long' else best_px <= tp_price

        if tp_hit:
            exit_px = (tp_price - SLIP) if direction == 'long' else (tp_price + SLIP)
            fee_out = total_notional * (MAKER + COMM)
            hold_minutes = i - levels[0].idx
            funding = total_notional * FUND_8H * (hold_minutes / (8 * 60))
            gross_pnl = total_qty * (exit_px - blended)
            pnl = gross_pnl - fee_out - funding
            eq_before = balance
            balance += pnl
            n_tp += 1
            trade_num += 1
            nl = len(levels)
            level_dist[nl] = level_dist.get(nl, 0) + 1
            if direction == 'long':
                long_count += 1
            else:
                short_count += 1
            if favored:
                fav_count += 1
            else:
                unfav_count += 1
            csv_rows.append([
                trade_num, direction, 'Y' if favored else 'N',
                pd.Timestamp(ts_arr[levels[0].idx]),
                pd.Timestamp(ts_arr[i]),
                'TP', nl,
                f"{levels[0].price:.2f}", f"{blended:.2f}", f"{exit_px:.2f}",
                f"{pnl:.4f}", f"{fee_out + funding:.4f}",
                f"{eq_before:.2f}", f"{balance:.2f}",
                hold_minutes
            ])
            if i >= sim_idx and k in monthly:
                monthly[k]['n'] += 1
            active = False
            levels = []
            cooldown_until = ci + 1
            continue

        # Timeout on 4H boundary
        if is_boundary and (ci - entry_candle) >= max_hold:
            exit_px = (c_arr[i] - SLIP) if direction == 'long' else (c_arr[i] + SLIP)
            fee_out = total_notional * (TAKER + COMM)
            hold_minutes = i - levels[0].idx
            funding = total_notional * FUND_8H * (hold_minutes / (8 * 60))
            gross_pnl = total_qty * (exit_px - blended)
            pnl = gross_pnl - fee_out - funding
            eq_before = balance
            balance += pnl
            n_to += 1
            trade_num += 1
            nl = len(levels)
            level_dist[nl] = level_dist.get(nl, 0) + 1
            if direction == 'long':
                long_count += 1
            else:
                short_count += 1
            if favored:
                fav_count += 1
            else:
                unfav_count += 1
            csv_rows.append([
                trade_num, direction, 'Y' if favored else 'N',
                pd.Timestamp(ts_arr[levels[0].idx]),
                pd.Timestamp(ts_arr[i]),
                'TIMEOUT', nl,
                f"{levels[0].price:.2f}", f"{blended:.2f}", f"{exit_px:.2f}",
                f"{pnl:.4f}", f"{fee_out + funding:.4f}",
                f"{eq_before:.2f}", f"{balance:.2f}",
                hold_minutes
            ])
            if i >= sim_idx and k in monthly:
                monthly[k]['n'] += 1
            active = False
            levels = []
            cooldown_until = ci + 1
            continue

    # ── Entry logic (4H boundary only) ──────────────────────────────────
    if is_boundary and not active:
        if i < sim_idx or ci < cooldown_until or balance < MIN_EQUITY or ci < 1:
            continue
        prev_candle = ci - 1
        if prev_candle >= len(ema_v):
            continue
        ema_val = ema_v[prev_candle]
        sma_val = sma_v[prev_candle]
        if np.isnan(ema_val) or np.isnan(sma_val):
            continue

        px = c_arr[i]
        day_ts = np.datetime64(pd.Timestamp(ts_arr[i]).normalize().asm8)
        s440 = sma440_map.get(day_ts)
        if s440 is None or np.isnan(s440):
            s440 = sma440_map.get(day_ts - np.timedelta64(1, 'D'))
        if s440 is None or (isinstance(s440, float) and np.isnan(s440)):
            continue

        is_bull = px > s440
        pct_below_ema = (ema_val - px) / ema_val
        pct_below_sma = (sma_val - px) / sma_val
        pct_above_ema = (px - ema_val) / ema_val
        pct_above_sma = (px - sma_val) / sma_val
        entered = False

        # ── LONG entry ──
        long_favored = is_bull
        trigger = LONG_TRIGGER_PCT if long_favored else LONG_TRIGGER_PCT * UNFAV_TRIGGER_SCALE
        risk = RISK_PCT if long_favored else RISK_PCT * UNFAV_RISK_SCALE
        hold = MAX_HOLD_BARS if long_favored else int(MAX_HOLD_BARS * UNFAV_HOLD_SCALE)
        gaps = LEVEL_GAPS if long_favored else [g * UNFAV_SPACING_SCALE for g in LEVEL_GAPS]

        if pct_below_ema >= trigger and pct_below_sma >= trigger:
            # v2.8 NEW: Drawdown-from-20d-high filter
            skip = False
            if prev_candle < len(high_20d_v):
                h20 = high_20d_v[prev_candle]
                if not np.isnan(h20) and h20 > 0:
                    dd_from_high = (px / h20) - 1
                    if dd_from_high < DD_20D_THRESHOLD:
                        skip = True
                        filtered_count += 1

            if not skip:
                entry_px = px + SLIP
                notional = risk * balance
                qty = notional / entry_px
                fee_in = notional * (TAKER + COMM)
                balance -= fee_in
                levels = [Level(1, entry_px, notional, qty, i)]
                direction = 'long'
                favored = long_favored
                drops = cum_drops(gaps)
                max_hold = hold
                entry_candle = ci
                active = True
                entered = True

        # ── SHORT entry ──
        if not entered:
            short_favored = not is_bull
            trigger = SHORT_TRIGGER_PCT if short_favored else SHORT_TRIGGER_PCT * UNFAV_TRIGGER_SCALE
            risk = RISK_PCT if short_favored else RISK_PCT * UNFAV_RISK_SCALE
            hold = MAX_HOLD_BARS if short_favored else int(MAX_HOLD_BARS * UNFAV_HOLD_SCALE)
            gaps = LEVEL_GAPS if short_favored else [g * UNFAV_SPACING_SCALE for g in LEVEL_GAPS]

            if pct_above_ema >= trigger and pct_above_sma >= trigger:
                entry_px = px - SLIP
                notional = risk * balance
                qty = -notional / entry_px
                fee_in = notional * (TAKER + COMM)
                balance -= fee_in
                levels = [Level(1, entry_px, notional, qty, i)]
                direction = 'short'
                favored = short_favored
                drops = cum_drops(gaps)
                max_hold = hold
                entry_candle = ci
                active = True

elapsed = time.time() - t0

# ── Export trades CSV ───────────────────────────────────────────────────────
csv_path = os.path.join(os.path.dirname(__file__), 'v28_trades.csv')
with open(csv_path, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        'trade_num', 'direction', 'favored', 'entry_time', 'exit_time',
        'exit_reason', 'levels_filled', 'l1_price', 'blended_entry',
        'exit_price', 'pnl', 'fees', 'equity_before', 'equity_after',
        'hold_minutes'
    ])
    for row in csv_rows:
        w.writerow(row)
print(f"Exported {len(csv_rows)} trades to {csv_path}")

# ── Results ─────────────────────────────────────────────────────────────────
total = n_tp + n_to + n_liq
yrs = (SIM_END - SIM_START).days / 365.25
cagr = ((balance / 1000) ** (1 / yrs) - 1) if balance > 0 else -1.0

sorted_months = sorted(monthly.keys())
n_months = len(sorted_months)
prod = 1.0
month_rets = []
for ym in sorted_months:
    d = monthly[ym]
    r = d['e'] / d['s'] if d['s'] > 0 else 1
    prod *= r
    month_rets.append(r - 1)
cmr = prod ** (1 / n_months) - 1 if n_months > 0 else 0

pos_months = sum(1 for r in month_rets if r > 0)
neg_months = sum(1 for r in month_rets if r < 0)
flat_months = sum(1 for r in month_rets if r == 0)
avg_pos = np.mean([r for r in month_rets if r > 0]) * 100 if pos_months > 0 else 0
avg_neg = np.mean([r for r in month_rets if r < 0]) * 100 if neg_months > 0 else 0

print()
print("=" * 90)
print("  MRM v2.8 — BACKTEST RESULTS")
print(f"  Period: {SIM_START.date()} to {SIM_END.date()} ({yrs:.2f} years, {n_months} months)")
print(f"  Capital: $1,000 | Slip: 3 ticks | Comm: 0.045%/side | 1m liq check")
print(f"  Runtime: {elapsed:.1f}s")
print("=" * 90)

print(f"\n  {'Metric':<40} {'Value':>20}")
print(f"  {'-' * 40} {'-' * 20}")
print(f"  {'Total trades':<40} {total:>20,}")
print(f"  {'  TP exits':<40} {n_tp:>20,}")
print(f"  {'  Timeouts':<40} {n_to:>20,}")
print(f"  {'  Liquidations':<40} {n_liq:>20}")
print(f"  {'Long / Short':<40} {long_count:>9} / {short_count:<9}")
print(f"  {'Favored / Unfavored':<40} {fav_count:>9} / {unfav_count:<9}")
print(f"  {'Entries filtered (dd20d)':<40} {filtered_count:>20,}")
print(f"  {'Final equity':<40} {'$' + f'{balance:,.2f}':>20}")
print(f"  {'Peak equity':<40} {'$' + f'{peak_equity:,.2f}':>20}")
print(f"  {'Total return':<40} {f'{balance / 1000:,.1f}x':>20}")
print(f"  {'CAGR':<40} {f'{cagr * 100:.1f}%':>20}")
print(f"  {'Compound monthly return (geometric)':<40} {f'{cmr * 100:.2f}%':>20}")
print(f"  {'CAGR from CMR':<40} {f'{((1 + cmr) ** 12 - 1) * 100:.1f}%':>20}")
print(f"  {'Max drawdown':<40} {f'{max_drawdown * 100:.1f}%':>20}")

print(f"\n  LEVELS:")
for lv in sorted(level_dist):
    print(f"    L{lv}: {level_dist[lv]:>5} ({level_dist[lv] / max(total, 1) * 100:.1f}%)")

if liq_events:
    print(f"\n  LIQUIDATIONS:")
    for le in liq_events:
        print(f"    {le}")
else:
    print(f"\n  LIQUIDATIONS: ZERO!")

print(f"\n  MONTHLY STATS:")
print(f"    Positive months:  {pos_months}/{n_months} ({pos_months / n_months * 100:.0f}%)")
print(f"    Negative months:  {neg_months}/{n_months} ({neg_months / n_months * 100:.0f}%)")
print(f"    Flat months:      {flat_months}/{n_months}")
print(f"    Avg positive:     +{avg_pos:.1f}%")
print(f"    Avg negative:     {avg_neg:.1f}%")
if month_rets:
    print(f"    Best month:       +{max(month_rets) * 100:.1f}%")
    print(f"    Worst month:      {min(month_rets) * 100:.1f}%")

print(f"\n  {'Month':<10} {'Equity':>14} {'Return %':>10} {'Trades':>8} {'MDD %':>10}")
print(f"  {'-' * 10} {'-' * 14} {'-' * 10} {'-' * 8} {'-' * 10}")
for ym in sorted_months:
    d = monthly[ym]
    s, e = d['s'], d['e']
    ret = (e / s - 1) * 100 if s > 0 else 0
    mdd = (d['tr'] / d['pk'] - 1) * 100 if d['pk'] > 0 else 0
    print(f"  {ym[0]}-{ym[1]:02d}    ${e:>12,.0f} {ret:>9.1f}% {d['n']:>8} {mdd:>9.1f}%")
