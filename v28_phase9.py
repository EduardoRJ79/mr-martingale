"""v2.8 Phase 9: Fine-tune dd20d-10% filter + risk. Full period."""
import pandas as pd, numpy as np, time, json

print("Loading data...")
df = pd.read_parquet('signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet').sort_values('ts').reset_index(drop=True)
n = len(df)
df['t4h'] = df['ts'].dt.floor('4h')
c4 = df.groupby('t4h').agg(o=('o','first'), h=('h','max'), l=('l','min'), c=('c','last')).sort_index()
c4['ema'] = c4['c'].ewm(span=34, adjust=False).mean()
c4['sma'] = c4['c'].rolling(14).mean()
c4['high_20d'] = c4['h'].rolling(120).max()
ema_v = c4['ema'].values; sma_v = c4['sma'].values
c4_c = c4['c'].values; c4_h20d = c4['high_20d'].values
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
sim_start = pd.Timestamp('2018-10-31', tz='UTC')
sim_end = pd.Timestamp('2026-03-28 23:59:59', tz='UTC')
sim_idx = np.searchsorted(ts, np.datetime64(sim_start.asm8))
sim_end_idx = np.searchsorted(ts, np.datetime64(sim_end.asm8))
yrs = (sim_end - sim_start).days / 365.25
print(f"Sim: {yrs:.2f} yrs")

COMM = 0.00045; TAKER = 0.000432; MAKER = 0.000144; FUND8H = 0.000013; SLIP = 0.03; MAINT = 0.005

class Lv:
    __slots__ = ['level','price','notional','qty','idx']
    def __init__(s, lv, px, nt, qt, idx):
        s.level=lv; s.price=px; s.notional=nt; s.qty=qt; s.idx=idx

def cum_drops(gaps):
    r, a = [], 0.0
    for g in gaps:
        a += g; r.append(a / 100.0)
    return r

def build_not_mults(level_mults_seq):
    mults = [1.0]; m = 1.0
    for x in level_mults_seq:
        m *= x; mults.append(m)
    return mults

def run_backtest(cfg):
    risk = cfg['risk_pct']; tp = cfg['tp_pct']
    gaps = cfg['level_gaps']; mults_seq = cfg['level_multipliers']
    max_lvl = cfg.get('max_levels', 5)
    uf_trigger = cfg.get('unfav_trigger_scale', 3.0)
    uf_risk = cfg.get('unfav_risk_scale', 0.60)
    uf_spacing = cfg.get('unfav_spacing_scale', 1.60)
    uf_hold = cfg.get('unfav_hold_scale', 0.45)
    max_hold = cfg.get('max_hold_bars', 720)
    long_trigger = cfg.get('long_trigger_pct', 0.005)
    short_trigger = cfg.get('short_trigger_pct', 0.08)
    max_dd_20d = cfg.get('max_dd_20d', None)
    NOT_MULTS = build_not_mults(mults_seq)
    bal = 1000.0; n_tp = n_to = n_liq = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0; level_dist = {}
    long_n = short_n = fav_n = unfav_n = n_filtered = 0
    liq_details = []; monthly = {}

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
                monthly[k] = {'s': eq, 'e': eq, 'n': 0}
            monthly[k]['e'] = eq

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
            if len(lvs) < max_lvl:
                for li in range(len(lvs), max_lvl):
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
            tp_px = bl * (1 + tp) if dr == 'long' else bl * (1 - tp)
            tp_hit = b_ >= tp_px if dr == 'long' else b_ <= tp_px
            if tp_hit:
                ep = (tp_px - SLIP) if dr == 'long' else (tp_px + SLIP)
                fee_out = tn * (MAKER + COMM)
                hm = i - lvs[0].idx
                fund = tn * FUND8H * (hm / (8 * 60))
                gp = tq * (ep - bl)
                pnl = gp - fee_out - fund
                bal += pnl; n_tp += 1; nl = len(lvs)
                level_dist[nl] = level_dist.get(nl, 0) + 1
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
                bal += pnl; n_to += 1; nl = len(lvs)
                level_dist[nl] = level_dist.get(nl, 0) + 1
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
            tr = long_trigger if lf else long_trigger * uf_trigger
            rk = risk if lf else risk * uf_risk
            hd = max_hold if lf else int(max_hold * uf_hold)
            gp = gaps if lf else [g * uf_spacing for g in gaps]
            if pbe >= tr and pbs >= tr:
                skip = False
                if max_dd_20d is not None and pc < len(c4_h20d):
                    h20 = c4_h20d[pc]
                    if not np.isnan(h20) and h20 > 0:
                        if (px / h20) - 1 < max_dd_20d: skip = True
                if skip:
                    n_filtered += 1
                else:
                    ep_e = px + SLIP; nt = rk * bal; qt = nt / ep_e
                    fee_in = nt * (TAKER + COMM); bal -= fee_in
                    lvs = [Lv(1, ep_e, nt, qt, i)]
                    dr = 'long'; fav = lf; drops_ = cum_drops(gp)
                    mh = hd; e4 = ci; act = True; entered = True
            if not entered:
                sf = not bull
                tr = short_trigger if sf else short_trigger * uf_trigger
                rk = risk if sf else risk * uf_risk
                hd = max_hold if sf else int(max_hold * uf_hold)
                gp = gaps if sf else [g * uf_spacing for g in gaps]
                if pae >= tr and pas >= tr:
                    ep_e = px - SLIP; nt = rk * bal; qt = -nt / ep_e
                    fee_in = nt * (TAKER + COMM); bal -= fee_in
                    lvs = [Lv(1, ep_e, nt, qt, i)]
                    dr = 'short'; fav = sf; drops_ = cum_drops(gp)
                    mh = hd; e4 = ci; act = True

    total = n_tp + n_to + n_liq
    cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0
    sorted_months = sorted(monthly.keys())
    n_months = len(sorted_months)
    prod = 1.0
    month_rets = []
    for ym in sorted_months:
        d = monthly[ym]
        r = d['e'] / d['s'] if d['s'] > 0 else 1
        prod *= r; month_rets.append(r - 1)
    cmr = prod ** (1 / n_months) - 1 if n_months > 0 else 0
    return {
        'bal': bal, 'cagr': cagr, 'cmr': cmr,
        'n_tp': n_tp, 'n_to': n_to, 'n_liq': n_liq, 'total': total,
        'max_dd': max_dd, 'peak_eq': peak_eq,
        'liq_details': liq_details, 'level_dist': level_dist,
        'long_n': long_n, 'short_n': short_n,
        'fav_n': fav_n, 'unfav_n': unfav_n,
        'n_filtered': n_filtered, 'n_months': n_months,
        'monthly': monthly, 'month_rets': month_rets
    }

base = {
    'risk_pct': 0.35, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 10.0, 14.0],
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.08,
    'max_dd_20d': -0.10,
}

configs = []
def add(name, **overrides):
    configs.append((name, {**base, **overrides}))

# Fine-tune risk around 0.50 with dd20d=-0.10
for r in [0.40, 0.42, 0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.55]:
    add(f"r{r:.2f}", risk_pct=r)

# Fine-tune dd20d threshold around -10%
for dd in [-0.08, -0.09, -0.10, -0.11, -0.12, -0.13]:
    add(f"dd{int(abs(dd)*100)}-r0.50", max_dd_20d=dd, risk_pct=0.50)

# Different gap structures with dd20-10
add("dd10-r50-g7-8", risk_pct=0.50, level_gaps=[0.5, 1.5, 7.0, 8.0])
add("dd10-r50-g8-10", risk_pct=0.50, level_gaps=[0.5, 1.5, 8.0, 10.0])
add("dd10-r50-g10-12", risk_pct=0.50, level_gaps=[0.5, 1.5, 10.0, 12.0])
add("dd10-r50-g12-16", risk_pct=0.50, level_gaps=[0.5, 1.5, 12.0, 16.0])

# Different dd lookback periods (4H bars)
# 15 days = 90 bars, 25 days = 150 bars
for bars, label in [(90, '15d'), (120, '20d'), (150, '25d')]:
    # Need to add these as new high_Nd columns — skip, use dd20d for now
    pass

# TP variations
add("dd10-r50-tp4", risk_pct=0.50, tp_pct=0.004)
add("dd10-r50-tp6", risk_pct=0.50, tp_pct=0.006)

# Unfav variations
add("dd10-r50-uf0.5", risk_pct=0.50, unfav_risk_scale=0.50)
add("dd10-r50-uf0.4", risk_pct=0.50, unfav_risk_scale=0.40)

# Long-only variant
add("dd10-r50-lo", risk_pct=0.50, short_trigger_pct=999.0)  # effectively long-only

# Max 4 levels
add("dd10-r50-max4", risk_pct=0.50, max_levels=4)
add("dd10-r55-max4", risk_pct=0.55, max_levels=4)

# ── Run ─────────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*140}")
print(f"  PHASE 9: {len(configs)} CONFIGS — FINE-TUNING (full period, 1m liq)")
print(f"{'='*140}\n")
print(f"  {'#':>3} {'Name':<28} {'CAGR%':>8} {'CMR%':>7} {'Liqs':>5} {'Trades':>7} {'Filt':>6} {'MaxDD%':>8} {'FinalEq':>14} {'PeakEq':>14} {'L/S':>10}")
print(f"  {'---':>3} {'-'*28} {'-'*8} {'-'*7} {'-'*5} {'-'*7} {'-'*6} {'-'*8} {'-'*14} {'-'*14} {'-'*10}")

for idx, (name, cfg) in enumerate(configs):
    r = run_backtest(cfg)
    ls = f"{r['long_n']}/{r['short_n']}"
    flag = " ***" if r['n_liq'] == 0 and r['cagr'] >= 0.5 else (" **" if r['n_liq'] == 0 else (" *" if r['n_liq'] <= 1 else ""))
    print(f"  {idx+1:>3} {name:<28} {r['cagr']*100:>7.1f}% {r['cmr']*100:>6.2f}% {r['n_liq']:>5} {r['total']:>7} {r['n_filtered']:>6} {r['max_dd']*100:>7.1f}% ${r['bal']:>12,.0f} ${r['peak_eq']:>12,.0f} {ls:>10}{flag}")
    results.append({'name': name, 'cagr': r['cagr'], 'cagr_pct': r['cagr']*100,
                    'cmr': r['cmr'], 'cmr_pct': r['cmr']*100,
                    'n_liq': r['n_liq'], 'total': r['total'], 'n_filtered': r['n_filtered'],
                    'max_dd_pct': r['max_dd']*100, 'bal': r['bal'], 'peak_eq': r['peak_eq'],
                    'long_n': r['long_n'], 'short_n': r['short_n'],
                    'fav_n': r['fav_n'], 'unfav_n': r['unfav_n'],
                    'liq_details': r['liq_details'], 'level_dist': r['level_dist'],
                    'monthly': {f"{k[0]}-{k[1]:02d}": v for k,v in r['monthly'].items()} if r.get('monthly') else {},
                    'month_rets': r.get('month_rets', [])})

print(f"\n{'='*100}")
print("  WINNERS: 0 liqs, CAGR >= 50% (sorted by CAGR)")
print(f"{'='*100}")
w = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 0.5]
w.sort(key=lambda x: -x['cagr'])
for r in w:
    ld = " ".join(f"L{k}:{v}" for k,v in sorted(r['level_dist'].items()))
    print(f"  {r['name']:<28} CAGR={r['cagr_pct']:.1f}% CMR={r['cmr_pct']:.2f}% MaxDD={r['max_dd_pct']:.1f}% Trades={r['total']} ${r['bal']:,.0f}  [{ld}]")

# Print detailed monthly for the best winner
if w:
    best_name = w[0]['name']
    for name, cfg in configs:
        if name == best_name:
            r = run_backtest(cfg)
            print(f"\n{'='*80}")
            print(f"  BEST: {best_name}")
            print(f"{'='*80}")
            print(f"  CAGR:  {r['cagr']*100:.1f}%")
            print(f"  CMR:   {r['cmr']*100:.2f}% (monthly composto geometrico)")
            print(f"  CAGR via CMR: {((1+r['cmr'])**12 - 1)*100:.1f}%")
            print(f"  Liqs:  {r['n_liq']}")
            print(f"  Total: {r['total']} (TP:{r['n_tp']} TO:{r['n_to']})")
            print(f"  L/S:   {r['long_n']}/{r['short_n']}")
            print(f"  F/U:   {r['fav_n']}/{r['unfav_n']}")
            print(f"  MaxDD: {r['max_dd']*100:.1f}%")
            print(f"  Final: ${r['bal']:,.2f}")
            print(f"  Peak:  ${r['peak_eq']:,.2f}")
            print(f"  Filtered: {r['n_filtered']} entries blocked by dd20d filter")
            print(f"  Levels: {r['level_dist']}")
            pos = sum(1 for x in r['month_rets'] if x > 0)
            neg = sum(1 for x in r['month_rets'] if x < 0)
            flat = sum(1 for x in r['month_rets'] if x == 0)
            print(f"\n  Meses: {pos} pos, {neg} neg, {flat} flat ({pos/(pos+neg)*100:.0f}% positivos)")
            if r['month_rets']:
                pos_avg = np.mean([x for x in r['month_rets'] if x > 0])*100
                neg_avg = np.mean([x for x in r['month_rets'] if x < 0])*100 if neg > 0 else 0
                print(f"  Media pos: +{pos_avg:.1f}%  Media neg: {neg_avg:.1f}%")
                print(f"  Melhor: +{max(r['month_rets'])*100:.1f}%  Pior: {min(r['month_rets'])*100:.1f}%")
            print(f"\n  {'Mes':<10} {'Equity':>14} {'Ret%':>10} {'Trades':>8}")
            for ym in sorted(r['monthly'].keys()):
                d = r['monthly'][ym]
                s, e = d['s'], d['e']
                ret = (e/s - 1)*100 if s > 0 else 0
                print(f"  {ym[0]}-{ym[1]:02d}    ${e:>12,.0f} {ret:>9.1f}% {d['n']:>8}")
            break
