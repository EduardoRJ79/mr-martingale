"""v2.8 recommended config — full period backtest from SMA440 warmup to end of data."""
import pandas as pd, numpy as np, time

print("Loading data...")
df = pd.read_parquet('signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet').sort_values('ts').reset_index(drop=True)
n = len(df)

df['t4h'] = df['ts'].dt.floor('4h')
c4 = df.groupby('t4h').agg(c=('c','last')).sort_index()
c4['ema'] = c4['c'].ewm(span=34, adjust=False).mean()
c4['sma'] = c4['c'].rolling(14).mean()
ema_v = c4['ema'].values; sma_v = c4['sma'].values

df['t1d'] = df['ts'].dt.floor('1D')
cdd = df.groupby('t1d').agg(c=('c','last')).sort_index()
cdd['s440'] = cdd['c'].rolling(440).mean()
s440d = {k: v for k, v in zip(cdd.index.values, cdd['s440'].values)}

ts = df['ts'].values; h = df['h'].values; l = df['l'].values; c_ = df['c'].values
t4v = df['t4h'].values
bounds = [0]
for i in range(1, n):
    if t4v[i] != t4v[i-1]: bounds.append(i)
bounds = np.array(bounds)
b2c = np.zeros(n, dtype=np.int64)
for bi in range(len(bounds)):
    s_ = bounds[bi]; e_ = bounds[bi+1] if bi+1 < len(bounds) else n
    b2c[s_:e_] = bi

# Sim from first valid SMA440 date
sim_start = pd.Timestamp('2018-10-31', tz='UTC')
sim_end   = pd.Timestamp('2026-03-28 23:59:59', tz='UTC')
sim_idx = np.searchsorted(ts, np.datetime64(sim_start.asm8))
sim_end_idx = np.searchsorted(ts, np.datetime64(sim_end.asm8))
print(f"Data: {n:,} bars | Sim: {sim_start.date()} to {sim_end.date()} | idx [{sim_idx}:{sim_end_idx}]")

# ── v2.8 recommended config ────────────────────────────────────────────────
RISK = 0.35; TP = 0.005
LEVEL_GAPS = [0.5, 1.5, 10.0, 14.0]
LEVEL_MULTS_SEQ = [2.0, 2.5, 2.5, 7.0]
MAX_LVL = 5
UF_TRIGGER = 3.0; UF_RISK = 0.60; UF_SPACING = 1.60; UF_HOLD = 0.45
MAX_HOLD = 720
LONG_TRIGGER = 0.005; SHORT_TRIGGER = 0.08
COMM = 0.00045; TAKER = 0.000432; MAKER = 0.000144; FUND8H = 0.000013; SLIP = 0.03; MAINT = 0.005

NOT_MULTS = [1.0]; _m = 1.0
for x in LEVEL_MULTS_SEQ:
    _m *= x; NOT_MULTS.append(_m)

class Lv:
    __slots__ = ['level','price','notional','qty','idx']
    def __init__(s, lv, px, nt, qt, idx):
        s.level=lv; s.price=px; s.notional=nt; s.qty=qt; s.idx=idx

def cum_drops(gaps):
    r, a = [], 0.0
    for g in gaps:
        a += g; r.append(a / 100.0)
    return r

bal = 1000.0; n_tp = n_to = n_liq = 0
act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
peak_eq = 1000.0; max_dd = 0.0
level_dist = {}; long_n = short_n = fav_n = unfav_n = 0
liq_details = []; monthly = {}

t0 = time.time()
for i in range(min(n, sim_end_idx + 1)):
    ci = b2c[i]; ib = (i == bounds[ci])
    if i >= sim_idx:
        eq = bal
        if act and lvs:
            tq_ = sum(x.qty for x in lvs)
            bl_ = sum(x.qty * x.price for x in lvs) / tq_
            eq = bal + tq_ * (c_[i] - bl_)
        if eq > peak_eq: peak_eq = eq
        dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0
        if dd > max_dd: max_dd = dd
        tp_ = pd.Timestamp(ts[i])
        k = (tp_.year, tp_.month)
        if k not in monthly:
            monthly[k] = {'s': eq, 'e': eq, 'n': 0, 'pk': eq, 'tr': eq}
        d = monthly[k]; d['e'] = eq
        if eq > d['pk']: d['pk'] = eq
        if eq < d['tr']: d['tr'] = eq

    if act:
        tq = sum(x.qty for x in lvs)
        bl = sum(x.qty * x.price for x in lvs) / tq
        tn = sum(x.notional for x in lvs)
        worst_px = l[i] if dr == 'long' else h[i]
        upnl = tq * (worst_px - bl)
        if bal + upnl <= tn * MAINT:
            n_liq += 1
            liq_details.append(f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} eq=${bal:,.0f}")
            if i >= sim_idx and k in monthly: monthly[k]['n'] += 1
            bal = 1000.0; act = False; lvs = []; cd4 = ci + 1
            peak_eq = 1000.0; continue

        if len(lvs) < MAX_LVL:
            for li in range(len(lvs), MAX_LVL):
                if li - 1 >= len(drops_): break
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
            bal += pnl; n_tp += 1
            nl = len(lvs); level_dist[nl] = level_dist.get(nl, 0) + 1
            if dr == 'long': long_n += 1
            else: short_n += 1
            if fav: fav_n += 1
            else: unfav_n += 1
            if i >= sim_idx and k in monthly: monthly[k]['n'] += 1
            act = False; lvs = []; cd4 = ci + 1; continue

        if ib and (ci - e4) >= mh:
            ep = (c_[i] - SLIP) if dr == 'long' else (c_[i] + SLIP)
            fee_out = tn * (TAKER + COMM)
            hm = i - lvs[0].idx
            fund = tn * FUND8H * (hm / (8 * 60))
            gp = tq * (ep - bl)
            pnl = gp - fee_out - fund
            bal += pnl; n_to += 1
            nl = len(lvs); level_dist[nl] = level_dist.get(nl, 0) + 1
            if dr == 'long': long_n += 1
            else: short_n += 1
            if fav: fav_n += 1
            else: unfav_n += 1
            if i >= sim_idx and k in monthly: monthly[k]['n'] += 1
            act = False; lvs = []; cd4 = ci + 1; continue

    if ib and not act:
        if i < sim_idx or ci < cd4 or bal < 50 or ci < 1: continue
        pc = ci - 1
        if pc >= len(ema_v): continue
        ev, sv = ema_v[pc], sma_v[pc]
        if np.isnan(ev) or np.isnan(sv): continue
        px = c_[i]
        dts = np.datetime64(pd.Timestamp(ts[i]).normalize().asm8)
        s4 = s440d.get(dts)
        if s4 is None or np.isnan(s4):
            s4 = s440d.get(dts - np.timedelta64(1, 'D'))
        if s4 is None or (isinstance(s4, float) and np.isnan(s4)): continue
        bull = px > s4
        pbe = (ev - px) / ev; pbs = (sv - px) / sv
        pae = (px - ev) / ev; pas = (px - sv) / sv
        entered = False
        lf = bull
        tr = LONG_TRIGGER if lf else LONG_TRIGGER * UF_TRIGGER
        rk = RISK if lf else RISK * UF_RISK
        hd = MAX_HOLD if lf else int(MAX_HOLD * UF_HOLD)
        gp = LEVEL_GAPS if lf else [g * UF_SPACING for g in LEVEL_GAPS]
        if pbe >= tr and pbs >= tr:
            ep_e = px + SLIP; nt = rk * bal; qt = nt / ep_e
            fee_in = nt * (TAKER + COMM); bal -= fee_in
            lvs = [Lv(1, ep_e, nt, qt, i)]
            dr = 'long'; fav = lf; drops_ = cum_drops(gp)
            mh = hd; e4 = ci; act = True; entered = True
        if not entered:
            sf = not bull
            tr = SHORT_TRIGGER if sf else SHORT_TRIGGER * UF_TRIGGER
            rk = RISK if sf else RISK * UF_RISK
            hd = MAX_HOLD if sf else int(MAX_HOLD * UF_HOLD)
            gp = LEVEL_GAPS if sf else [g * UF_SPACING for g in LEVEL_GAPS]
            if pae >= tr and pas >= tr:
                ep_e = px - SLIP; nt = rk * bal; qt = -nt / ep_e
                fee_in = nt * (TAKER + COMM); bal -= fee_in
                lvs = [Lv(1, ep_e, nt, qt, i)]
                dr = 'short'; fav = sf; drops_ = cum_drops(gp)
                mh = hd; e4 = ci; act = True

elapsed = time.time() - t0
total = n_tp + n_to + n_liq
yrs = (sim_end - sim_start).days / 365.25
cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0

# Compute monthly returns
sorted_months = sorted(monthly.keys())
n_months = len(sorted_months)
monthly_rets = []
for ym in sorted_months:
    d = monthly[ym]
    r = d['e'] / d['s'] - 1 if d['s'] > 0 else 0
    monthly_rets.append(r)

# Compound monthly return = geometric mean of (1+r) per month
import math
prod = 1.0
for r in monthly_rets:
    prod *= (1 + r)
cmr = prod ** (1 / n_months) - 1 if n_months > 0 else 0

# Also compute annualized from monthly
cagr_from_monthly = (1 + cmr) ** 12 - 1

print("=" * 90)
print("  MRM v2.8 RECOMMENDED — FULL PERIOD BACKTEST")
print(f"  Periodo: {sim_start.date()} a {sim_end.date()} ({yrs:.2f} anos, {n_months} meses)")
print(f"  Config: risk=0.35 | shtrig=8% | gaps=[0.5,1.5,10,14] | 1m liq check")
print(f"  Capital=$1,000 | Slip=3t | Comm=0.045%/lado | Tempo: {elapsed:.1f}s")
print("=" * 90)

print(f"\n  {'Metrica':<35} {'Valor':>20}")
print(f"  {'-'*35} {'-'*20}")
print(f"  {'Total trades':<35} {total:>20,}")
print(f"  {'  TP exits':<35} {n_tp:>20,}")
print(f"  {'  Timeouts':<35} {n_to:>20,}")
print(f"  {'  Liquidacoes':<35} {n_liq:>20}")
print(f"  {'Long / Short':<35} {long_n:>9} / {short_n:<9}")
print(f"  {'Favored / Unfavored':<35} {fav_n:>9} / {unfav_n:<9}")
print(f"  {'Equity final':<35} {'$'+f'{bal:,.2f}':>20}")
print(f"  {'Equity pico':<35} {'$'+f'{peak_eq:,.2f}':>20}")
print(f"  {'Retorno total':<35} {f'{bal/1000:,.1f}x':>20}")
print(f"  {'CAGR':<35} {f'{cagr*100:.1f}%':>20}")
print(f"  {'Retorno mensal medio composto':<35} {f'{cmr*100:.2f}%':>20}")
print(f"  {'CAGR (de mensal composto)':<35} {f'{cagr_from_monthly*100:.1f}%':>20}")
print(f"  {'Max Drawdown':<35} {f'{max_dd*100:.1f}%':>20}")
print(f"  {'Meses no periodo':<35} {n_months:>20}")

print(f"\n  NIVEIS:")
for lv in sorted(level_dist):
    print(f"    L{lv}: {level_dist[lv]:>5} ({level_dist[lv]/max(total,1)*100:.1f}%)")

if liq_details:
    print(f"\n  LIQUIDACOES:")
    for ld in liq_details:
        print(f"    {ld}")
else:
    print(f"\n  LIQUIDACOES: ZERO!")

# Monthly table
print(f"\n  {'Mes':<10} {'Equity':>14} {'Ret%':>10} {'Trades':>8} {'MDD%':>10}")
print(f"  {'-'*10} {'-'*14} {'-'*10} {'-'*8} {'-'*10}")
for ym in sorted_months:
    d = monthly[ym]
    s, e = d['s'], d['e']
    r = (e / s - 1) * 100 if s > 0 else 0
    mdd = (d['tr'] / d['pk'] - 1) * 100 if d['pk'] > 0 else 0
    print(f"  {ym[0]}-{ym[1]:02d}    ${e:>12,.0f} {r:>9.1f}% {d['n']:>8} {mdd:>9.1f}%")

# Summary stats
pos_months = sum(1 for r in monthly_rets if r > 0)
neg_months = sum(1 for r in monthly_rets if r < 0)
flat_months = sum(1 for r in monthly_rets if r == 0)
avg_pos = np.mean([r for r in monthly_rets if r > 0]) * 100 if pos_months > 0 else 0
avg_neg = np.mean([r for r in monthly_rets if r < 0]) * 100 if neg_months > 0 else 0
best_m = max(monthly_rets) * 100; worst_m = min(monthly_rets) * 100

print(f"\n  ESTATISTICAS MENSAIS:")
print(f"    Meses positivos:  {pos_months}/{n_months} ({pos_months/n_months*100:.0f}%)")
print(f"    Meses negativos:  {neg_months}/{n_months} ({neg_months/n_months*100:.0f}%)")
print(f"    Meses flat:       {flat_months}/{n_months}")
print(f"    Media meses pos:  +{avg_pos:.1f}%")
print(f"    Media meses neg:  {avg_neg:.1f}%")
print(f"    Melhor mes:       +{best_m:.1f}%")
print(f"    Pior mes:         {worst_m:.1f}%")
print(f"    Retorno mensal composto (geom): {cmr*100:.2f}%")
print(f"    CAGR equivalente:               {cagr_from_monthly*100:.1f}%")
