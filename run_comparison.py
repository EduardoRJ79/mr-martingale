"""Compare v2.7 backtest with published results. Period: 2022-01-01 to 2026-03-25."""
import pandas as pd, numpy as np, time

# Load full data for MA warmup
df = pd.read_parquet('signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet').sort_values('ts').reset_index(drop=True)
n = len(df)

# Build MAs on full data
df['t4h'] = df['ts'].dt.floor('4h')
c4 = df.groupby('t4h').agg(c=('c','last')).sort_index()
c4['ema'] = c4['c'].ewm(span=34, adjust=False).mean()
c4['sma'] = c4['c'].rolling(14).mean()
ema_v = c4['ema'].values; sma_v = c4['sma'].values

df['t1d'] = df['ts'].dt.floor('1D')
cd = df.groupby('t1d').agg(c=('c','last')).sort_index()
cd['s440'] = cd['c'].rolling(440).mean()
s440d = {k: v for k, v in zip(cd.index.values, cd['s440'].values)}

ts = df['ts'].values; h = df['h'].values; l = df['l'].values; c = df['c'].values
t4v = df['t4h'].values

bounds = [0]
for i in range(1, n):
    if t4v[i] != t4v[i-1]: bounds.append(i)
bounds = np.array(bounds)
b2c = np.zeros(n, dtype=np.int64)
for bi in range(len(bounds)):
    s_ = bounds[bi]; e_ = bounds[bi+1] if bi+1 < len(bounds) else n
    b2c[s_:e_] = bi

# Sim window
sim_start = pd.Timestamp('2022-01-01', tz='UTC')
sim_end = pd.Timestamp('2026-03-25 23:59:59', tz='UTC')
sim_idx = np.searchsorted(ts, np.datetime64(sim_start.asm8))
sim_end_idx = np.searchsorted(ts, np.datetime64(sim_end.asm8))

# Parameters
COMM = 0.00045     # 0.045% per side
TAKER = 0.000432
MAKER = 0.000144
FUND8H = 0.000013
SLIP = 3 * 0.01
RISK = 0.30
TP = 0.005
MAINT = 0.005

# Level multipliers: [1.0, 2.0, 5.0, 12.5, 87.5]
LEVEL_MULTS_SEQ = [2.0, 2.5, 2.5, 7.0]
NOT_MULTS = [1.0]
_m = 1.0
for x in LEVEL_MULTS_SEQ:
    _m *= x; NOT_MULTS.append(_m)

LEVEL_GAPS = [0.5, 1.5, 7.0, 8.0]
def cum_drops(gaps):
    r, a = [], 0.0
    for g in gaps:
        a += g; r.append(a / 100.0)
    return r

class Lv:
    __slots__ = ['level','price','notional','qty','idx']
    def __init__(s, lv, px, nt, qt, idx):
        s.level=lv; s.price=px; s.notional=nt; s.qty=qt; s.idx=idx

bal = 1000.0
n_tp = n_to = n_liq = 0
long_n = short_n = fav_n = unfav_n = 0
liqs_out = []
act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
monthly = {}
level_dist = {}
all_pnls = []
csv_rows = []
trade_num = 0
peak_eq = 1000.0; max_dd = 0.0

t0 = time.time()
for i in range(min(n, sim_end_idx + 1)):
    ci = b2c[i]; ib = (i == bounds[ci])

    # Monthly tracking
    if i >= sim_idx:
        tp_ = pd.Timestamp(ts[i])
        k = (tp_.year, tp_.month)
        eq = bal
        if act and lvs:
            tq_ = sum(x.qty for x in lvs)
            bl_ = sum(x.qty * x.price for x in lvs) / tq_
            eq = bal + tq_ * (c[i] - bl_)
        if k not in monthly:
            monthly[k] = {'s': eq, 'e': eq, 'n': 0, 'pk': eq, 'tr': eq}
        d = monthly[k]; d['e'] = eq
        if eq > d['pk']: d['pk'] = eq
        if eq < d['tr']: d['tr'] = eq
        if eq > peak_eq: peak_eq = eq
        dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0
        if dd > max_dd: max_dd = dd

    if act:
        tq = sum(x.qty for x in lvs)
        bl = sum(x.qty * x.price for x in lvs) / tq
        tn = sum(x.notional for x in lvs)

        # LIQ at 4H close
        if ib:
            upnl = tq * (c[i] - bl)
            if bal + upnl <= tn * MAINT:
                n_liq += 1
                trade_num += 1
                liqs_out.append(
                    f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} eq=${bal:,.0f}")
                csv_rows.append([trade_num, dr, 'Y' if fav else 'N',
                    pd.Timestamp(ts[lvs[0].idx]), pd.Timestamp(ts[i]),
                    'LIQUIDATED', len(lvs), f"{lvs[0].price:.2f}", f"{bl:.2f}",
                    f"{c[i]:.2f}", f"{-bal:.2f}", '', f"{bal:.2f}", '0.00',
                    i - lvs[0].idx])
                if i >= sim_idx and k in monthly:
                    monthly[k]['n'] += 1
                bal = 1000.0; act = False; lvs = []; cd4 = ci + 1
                peak_eq = 1000.0; continue

        # Grid fills on 1m
        if len(lvs) < 5:
            for li in range(len(lvs), 5):
                p1 = lvs[0].price; df_pct = drops_[li - 1]
                if dr == 'long':
                    ft = p1 * (1 - df_pct); hit = l[i] <= ft
                else:
                    ft = p1 * (1 + df_pct); hit = h[i] >= ft
                if hit:
                    fp = (ft - SLIP) if dr == 'long' else (ft + SLIP)
                    nt_lv = lvs[0].notional * NOT_MULTS[li]
                    qt = nt_lv / fp if dr == 'long' else -nt_lv / fp
                    fee_in = nt_lv * (MAKER + COMM)
                    bal -= fee_in
                    lvs.append(Lv(li + 1, fp, nt_lv, qt, i))
                    tq = sum(x.qty for x in lvs)
                    bl = sum(x.qty * x.price for x in lvs) / tq
                    tn = sum(x.notional for x in lvs)
                    break

        # TP on 1m
        b_ = h[i] if dr == 'long' else l[i]
        tp_px = bl * (1 + TP) if dr == 'long' else bl * (1 - TP)
        tp_hit = b_ >= tp_px if dr == 'long' else b_ <= tp_px
        if tp_hit:
            ep = (tp_px - SLIP) if dr == 'long' else (tp_px + SLIP)
            fee_out = tn * (MAKER + COMM)
            hm = i - lvs[0].idx
            fund = tn * FUND8H * (hm / (8 * 60))
            gp = tq * (ep - bl)
            pnl = gp - fee_out - fund
            eq_before = bal
            bal += pnl
            n_tp += 1; all_pnls.append(pnl)
            trade_num += 1
            nl = len(lvs)
            level_dist[nl] = level_dist.get(nl, 0) + 1
            if dr == 'long': long_n += 1
            else: short_n += 1
            if fav: fav_n += 1
            else: unfav_n += 1
            csv_rows.append([trade_num, dr, 'Y' if fav else 'N',
                pd.Timestamp(ts[lvs[0].idx]), pd.Timestamp(ts[i]),
                'TP', nl, f"{lvs[0].price:.2f}", f"{bl:.2f}",
                f"{ep:.2f}", f"{pnl:.4f}", f"{fee_out+fund:.4f}",
                f"{eq_before:.2f}", f"{bal:.2f}", i - lvs[0].idx])
            if i >= sim_idx and k in monthly:
                monthly[k]['n'] += 1
            act = False; lvs = []; cd4 = ci + 1; continue

        # Timeout
        if ib and (ci - e4) >= mh:
            ep = (c[i] - SLIP) if dr == 'long' else (c[i] + SLIP)
            fee_out = tn * (TAKER + COMM)
            hm = i - lvs[0].idx
            fund = tn * FUND8H * (hm / (8 * 60))
            gp = tq * (ep - bl)
            pnl = gp - fee_out - fund
            eq_before = bal
            bal += pnl
            n_to += 1; all_pnls.append(pnl)
            trade_num += 1
            nl = len(lvs)
            level_dist[nl] = level_dist.get(nl, 0) + 1
            if dr == 'long': long_n += 1
            else: short_n += 1
            if fav: fav_n += 1
            else: unfav_n += 1
            csv_rows.append([trade_num, dr, 'Y' if fav else 'N',
                pd.Timestamp(ts[lvs[0].idx]), pd.Timestamp(ts[i]),
                'TIMEOUT', nl, f"{lvs[0].price:.2f}", f"{bl:.2f}",
                f"{ep:.2f}", f"{pnl:.4f}", f"{fee_out+fund:.4f}",
                f"{eq_before:.2f}", f"{bal:.2f}", i - lvs[0].idx])
            if i >= sim_idx and k in monthly:
                monthly[k]['n'] += 1
            act = False; lvs = []; cd4 = ci + 1; continue

    # ENTRY
    if ib and not act:
        if i < sim_idx or ci < cd4 or bal < 50 or ci < 1:
            continue
        pc = ci - 1
        if pc >= len(ema_v):
            continue
        ev, sv = ema_v[pc], sma_v[pc]
        if np.isnan(ev) or np.isnan(sv):
            continue
        px = c[i]
        dts = np.datetime64(pd.Timestamp(ts[i]).normalize().asm8)
        s4 = s440d.get(dts)
        if s4 is None or np.isnan(s4):
            s4 = s440d.get(dts - np.timedelta64(1, 'D'))
        if s4 is None or (isinstance(s4, float) and np.isnan(s4)):
            continue
        bull = px > s4
        pbe = (ev - px) / ev; pbs = (sv - px) / sv
        pae = (px - ev) / ev; pas = (px - sv) / sv
        entered = False

        # LONG
        lf = bull
        tr = 0.005 if lf else 0.005 * 3.0
        rk = RISK if lf else RISK * 0.60
        hd = 720 if lf else int(720 * 0.45)
        gp = LEVEL_GAPS if lf else [g * 1.6 for g in LEVEL_GAPS]
        if pbe >= tr and pbs >= tr:
            ep_e = px + SLIP
            nt = rk * bal  # L1 notional = risk * equity
            qt = nt / ep_e
            fee_in = nt * (TAKER + COMM)
            bal -= fee_in
            lvs = [Lv(1, ep_e, nt, qt, i)]
            dr = 'long'; fav = lf; drops_ = cum_drops(gp)
            mh = hd; e4 = ci; act = True; entered = True

        # SHORT
        if not entered:
            sf = not bull
            tr = 0.015 if sf else 0.015 * 3.0
            rk = RISK if sf else RISK * 0.60
            hd = 720 if sf else int(720 * 0.45)
            gp = LEVEL_GAPS if sf else [g * 1.6 for g in LEVEL_GAPS]
            if pae >= tr and pas >= tr:
                ep_e = px - SLIP
                nt = rk * bal
                qt = -nt / ep_e
                fee_in = nt * (TAKER + COMM)
                bal -= fee_in
                lvs = [Lv(1, ep_e, nt, qt, i)]
                dr = 'short'; fav = sf; drops_ = cum_drops(gp)
                mh = hd; e4 = ci; act = True

# Export trades to CSV
import csv
csv_path = 'v27_trades.csv'
with open(csv_path, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['trade_num','direction','favored','entry_time','exit_time',
                'exit_reason','levels_filled','l1_price','blended_entry',
                'exit_price','pnl','fees','equity_before','equity_after',
                'hold_minutes'])
    for row in csv_rows:
        w.writerow(row)
print(f"Exported {len(csv_rows)} trades to {csv_path}")

elapsed = time.time() - t0
total = n_tp + n_to + n_liq
wins = sum(1 for p in all_pnls if p > 0)
yrs = (sim_end - sim_start).days / 365.25

print("=" * 85)
print("  MRM v2.7 BACKTEST vs PUBLICADO")
print(f"  Periodo: 2022-01-01 a 2026-03-25 ({yrs:.2f} anos)")
print(f"  Capital=$1000 | Slip=3t | Comm=0.045%/lado | Taker/Maker/Funding spec")
print(f"  L1 notional = risk_pct * equity (conforme handoff)")
print(f"  Liq check: 4H close | Tempo: {elapsed:.1f}s")
print("=" * 85)

print(f"\n  {'Metrica':<25} {'Backtest':>15} {'Publicado':>15}")
print(f"  {'-'*25} {'-'*15} {'-'*15}")
print(f"  {'Total trades':<25} {total:>15,} {983:>15,}")
print(f"  {'TP exits':<25} {n_tp:>15,} {'~975':>15}")
print(f"  {'Timeouts':<25} {n_to:>15,} {'~8':>15}")
print(f"  {'Liquidacoes':<25} {n_liq:>15,} {0:>15}")
wr = f"{wins}/{len(all_pnls)} ({wins/max(len(all_pnls),1)*100:.0f}%)"
print(f"  {'Win rate':<25} {wr:>15} {'~99%':>15}")
print(f"  {'Long / Short':<25} {long_n:>7} / {short_n:<7} {'n/a':>15}")
print(f"  {'Fav / Unfav':<25} {fav_n:>7} / {unfav_n:<7} {'n/a':>15}")
eq_str = f"${bal:,.2f}"
print(f"  {'Equity final':<25} {eq_str:>15} {'$26,863':>15}")
if bal > 0:
    cagr = (bal / 1000) ** (1 / yrs) - 1
    print(f"  {'CAGR':<25} {cagr*100:>14.1f}% {'117.7%':>15}")
    print(f"  {'Retorno total':<25} {bal/1000:>14.1f}x {'26.9x':>15}")
print(f"  {'Max Drawdown':<25} {max_dd*100:>14.1f}% {'93.7%':>15}")

print(f"\n  NIVEIS:")
for lv in sorted(level_dist):
    print(f"    L{lv}: {level_dist[lv]} ({level_dist[lv]/max(total,1)*100:.1f}%)")

if liqs_out:
    print(f"\n  LIQUIDACOES:")
    for lq in liqs_out:
        print(f"    {lq}")
else:
    print(f"\n  LIQUIDACOES: ZERO!")

# Monthly table
print(f"\n  {'Mes':<10} {'Equity Final':>14} {'Retorno %':>10} {'Trades':>8} {'Max DD %':>10}")
print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*8} {'-'*10}")
for ym in sorted(monthly):
    d = monthly[ym]
    s, e = d['s'], d['e']
    r = (e / s - 1) * 100 if s > 0 else 0
    mdd = (d['tr'] / d['pk'] - 1) * 100 if d['pk'] > 0 else 0
    print(f"  {ym[0]}-{ym[1]:02d}    ${e:>13,.2f} {r:>9.2f}% {d['n']:>8} {mdd:>9.2f}%")

# Published comparison
print(f"\n  PUBLICADO (spec): $1K -> $26,863 | 983 trades | CAGR 117.7% | 0 liqs | MDD 93.7%")
