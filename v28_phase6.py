"""v2.8 Phase 6: Radical restructuring.
Approaches: max 2 levels (shallow grid), long-only, stop+floor,
very high short trigger, and higher risk compensating shallow grids."""
import pandas as pd, numpy as np, time, json

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
sim_start = pd.Timestamp('2022-01-01', tz='UTC')
sim_end = pd.Timestamp('2026-03-25 23:59:59', tz='UTC')
sim_idx = np.searchsorted(ts, np.datetime64(sim_start.asm8))
sim_end_idx = np.searchsorted(ts, np.datetime64(sim_end.asm8))
print(f"Data loaded: {n:,} bars")

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
    short_trigger = cfg.get('short_trigger_pct', 0.015)
    max_loss_pct = cfg.get('max_loss_pct', None)
    long_only = cfg.get('long_only', False)
    NOT_MULTS = build_not_mults(mults_seq)
    bal = 1000.0; n_tp = n_to = n_liq = n_stop = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0
    liq_details = []; stop_details = []; level_dist = {}
    long_n = short_n = 0

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

        if act:
            tq = sum(x.qty for x in lvs)
            bl = sum(x.qty * x.price for x in lvs) / tq
            tn = sum(x.notional for x in lvs)
            worst_px = l[i] if dr == 'long' else h[i]
            upnl = tq * (worst_px - bl)
            if bal + upnl <= tn * MAINT:
                n_liq += 1
                liq_details.append(f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} eq=${bal:,.0f}")
                bal = 1000.0; act = False; lvs = []; cd4 = ci + 1
                peak_eq = 1000.0; continue

            if max_loss_pct is not None:
                upnl_worst = tq * (worst_px - bl)
                loss_ratio = -upnl_worst / bal if bal > 0 else 999
                if loss_ratio >= max_loss_pct:
                    ep = (worst_px - SLIP) if dr == 'long' else (worst_px + SLIP)
                    fee_out = tn * (TAKER + COMM)
                    hm = i - lvs[0].idx
                    fund = tn * FUND8H * (hm / (8 * 60))
                    gp = tq * (ep - bl)
                    pnl = gp - fee_out - fund
                    bal += pnl
                    if bal < 50: bal = 50
                    n_stop += 1
                    stop_details.append(f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} loss={loss_ratio*100:.0f}% eq_after=${bal:,.0f}")
                    nl = len(lvs)
                    level_dist[nl] = level_dist.get(nl, 0) + 1
                    act = False; lvs = []; cd4 = ci + 1; continue

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
                bal += pnl; n_tp += 1
                nl = len(lvs)
                level_dist[nl] = level_dist.get(nl, 0) + 1
                if dr == 'long': long_n += 1
                else: short_n += 1
                act = False; lvs = []; cd4 = ci + 1; continue

            if ib and (ci - e4) >= mh:
                ep = (c_[i] - SLIP) if dr == 'long' else (c_[i] + SLIP)
                fee_out = tn * (TAKER + COMM)
                hm = i - lvs[0].idx
                fund = tn * FUND8H * (hm / (8 * 60))
                gp = tq * (ep - bl)
                pnl = gp - fee_out - fund
                bal += pnl; n_to += 1
                nl = len(lvs)
                level_dist[nl] = level_dist.get(nl, 0) + 1
                if dr == 'long': long_n += 1
                else: short_n += 1
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
                ep_e = px + SLIP
                nt = rk * bal; qt = nt / ep_e
                fee_in = nt * (TAKER + COMM)
                bal -= fee_in
                lvs = [Lv(1, ep_e, nt, qt, i)]
                dr = 'long'; fav = lf; drops_ = cum_drops(gp)
                mh = hd; e4 = ci; act = True; entered = True
            if not entered and not long_only:
                sf = not bull
                tr = short_trigger if sf else short_trigger * uf_trigger
                rk = risk if sf else risk * uf_risk
                hd = max_hold if sf else int(max_hold * uf_hold)
                gp = gaps if sf else [g * uf_spacing for g in gaps]
                if pae >= tr and pas >= tr:
                    ep_e = px - SLIP
                    nt = rk * bal; qt = -nt / ep_e
                    fee_in = nt * (TAKER + COMM)
                    bal -= fee_in
                    lvs = [Lv(1, ep_e, nt, qt, i)]
                    dr = 'short'; fav = sf; drops_ = cum_drops(gp)
                    mh = hd; e4 = ci; act = True

    total = n_tp + n_to + n_liq + n_stop
    yrs = (sim_end - sim_start).days / 365.25
    cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0
    return {
        'bal': bal, 'cagr': cagr, 'n_tp': n_tp, 'n_to': n_to, 'n_liq': n_liq,
        'n_stop': n_stop, 'total': total, 'max_dd': max_dd, 'peak_eq': peak_eq,
        'liq_details': liq_details, 'stop_details': stop_details,
        'level_dist': level_dist, 'long_n': long_n, 'short_n': short_n
    }

# ── Config groups ───────────────────────────────────────────────────────────
base_wide = {
    'risk_pct': 0.30, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 10.0, 14.0],
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.015,
    'max_loss_pct': None, 'long_only': False,
}

configs = []
def add(name, base=base_wide, **overrides):
    configs.append((name, {**base, **overrides}))

# ── A: Long-only (eliminates all short liqs) ──
add("long-only", long_only=True)
add("long-only-r0.35", long_only=True, risk_pct=0.35)
add("long-only-r0.40", long_only=True, risk_pct=0.40)
add("long-only-r0.45", long_only=True, risk_pct=0.45)
add("long-only-r0.50", long_only=True, risk_pct=0.50)
# Long-only with original gaps (more trades)
base_orig = {**base_wide, 'level_gaps': [0.5, 1.5, 7.0, 8.0]}
add("lo-orig", base=base_orig, long_only=True)
add("lo-orig-r0.35", base=base_orig, long_only=True, risk_pct=0.35)
add("lo-orig-r0.40", base=base_orig, long_only=True, risk_pct=0.40)

# ── B: Max 2 levels (very shallow grid, safe from deep fills) ──
add("max2", max_levels=2)
add("max2-r0.40", max_levels=2, risk_pct=0.40)
add("max2-r0.50", max_levels=2, risk_pct=0.50)
add("max2-r0.60", max_levels=2, risk_pct=0.60)
add("max2-r0.70", max_levels=2, risk_pct=0.70)
# Max 2 with original gaps
add("max2-orig", base=base_orig, max_levels=2)
add("max2-orig-r0.50", base=base_orig, max_levels=2, risk_pct=0.50)
add("max2-orig-r0.60", base=base_orig, max_levels=2, risk_pct=0.60)
add("max2-orig-r0.70", base=base_orig, max_levels=2, risk_pct=0.70)
add("max2-orig-r0.80", base=base_orig, max_levels=2, risk_pct=0.80)

# ── C: Very high short trigger (avoid most/all short entries) ──
add("shtrig-0.03", short_trigger_pct=0.030)
add("shtrig-0.04", short_trigger_pct=0.040)
add("shtrig-0.05", short_trigger_pct=0.050)
add("shtrig-0.08", short_trigger_pct=0.080)
add("shtrig-0.10", short_trigger_pct=0.100)

# ── D: Long-only + max 2 levels + high risk (max safety, compensate with risk) ──
add("lo-max2-r0.50", long_only=True, max_levels=2, risk_pct=0.50)
add("lo-max2-r0.60", long_only=True, max_levels=2, risk_pct=0.60)
add("lo-max2-r0.70", long_only=True, max_levels=2, risk_pct=0.70)
add("lo-max2-r0.80", long_only=True, max_levels=2, risk_pct=0.80)

# ── E: Long-only + wider gaps + higher risk ──
add("lo-r0.40-gw", long_only=True, risk_pct=0.40, level_gaps=[0.5, 1.5, 10.0, 14.0])
add("lo-r0.50-gw", long_only=True, risk_pct=0.50, level_gaps=[0.5, 1.5, 10.0, 14.0])

# ── F: Long-only + original gaps + various risk levels ──
add("lo-orig-r0.45", base=base_orig, long_only=True, risk_pct=0.45)
add("lo-orig-r0.50", base=base_orig, long_only=True, risk_pct=0.50)
add("lo-orig-r0.55", base=base_orig, long_only=True, risk_pct=0.55)

# ── G: Stop at 80% + wider gaps (best combo from P5 + gaps) ──
add("stop80-gw", max_loss_pct=0.80)
add("stop80-r0.35", max_loss_pct=0.80, risk_pct=0.35)

# ── H: Higher L2 mult with max2 (more recovery per L2 trade) ──
add("max2-L2m3-r0.50", max_levels=2, level_multipliers=[3.0, 2.5, 2.5, 7.0], risk_pct=0.50)
add("max2-L2m4-r0.50", max_levels=2, level_multipliers=[4.0, 2.5, 2.5, 7.0], risk_pct=0.50)
add("max2-L2m3-orig-r0.60", base=base_orig, max_levels=2, level_multipliers=[3.0, 2.5, 2.5, 7.0], risk_pct=0.60)

# ── Run ─────────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*130}")
print(f"  PHASE 6: {len(configs)} CONFIGS (radical restructuring)")
print(f"{'='*130}\n")
print(f"  {'#':>3} {'Name':<30} {'CAGR%':>8} {'Liqs':>5} {'Stops':>6} {'Trades':>7} {'MaxDD%':>8} {'FinalEq':>12} {'PeakEq':>12} {'L/S':>10}")
print(f"  {'---':>3} {'-'*30} {'-'*8} {'-'*5} {'-'*6} {'-'*7} {'-'*8} {'-'*12} {'-'*12} {'-'*10}")

for idx, (name, cfg) in enumerate(configs):
    t0 = time.time()
    r = run_backtest(cfg)
    ls = f"{r['long_n']}/{r['short_n']}"
    flag = " ***" if r['n_liq'] == 0 and r['cagr'] >= 1.0 else (" **" if r['n_liq'] == 0 and r['cagr'] >= 0.5 else (" *" if r['n_liq'] == 0 else ""))
    print(f"  {idx+1:>3} {name:<30} {r['cagr']*100:>7.1f}% {r['n_liq']:>5} {r['n_stop']:>6} {r['total']:>7} {r['max_dd']*100:>7.1f}% ${r['bal']:>10,.0f} ${r['peak_eq']:>10,.0f} {ls:>10}{flag}")
    results.append({'name': name, 'cagr': r['cagr'], 'cagr_pct': r['cagr']*100,
                    'n_liq': r['n_liq'], 'n_stop': r['n_stop'], 'total': r['total'],
                    'max_dd_pct': r['max_dd']*100, 'bal': r['bal'], 'peak_eq': r['peak_eq'],
                    'long_n': r['long_n'], 'short_n': r['short_n'],
                    'liq_details': r['liq_details'], 'stop_details': r['stop_details'],
                    'level_dist': r['level_dist']})

print(f"\n{'='*130}")
print("  WINNERS: 0 liqs AND CAGR >= 100%")
print(f"{'='*130}")
winners = [r for r in results if r['n_liq'] == 0 and r['n_stop'] == 0 and r['cagr'] >= 1.0]
if winners:
    winners.sort(key=lambda x: -x['cagr'])
    for w in winners:
        print(f"  *** {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f} Peak=${w['peak_eq']:,.0f}")
else:
    print("  None found (pure, no stops)")

print(f"\n  WINNERS (including stop-protected): 0 liqs AND CAGR >= 100%")
winners2 = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 1.0]
if winners2:
    winners2.sort(key=lambda x: -x['cagr'])
    for w in winners2:
        print(f"  *** {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Stops={w['n_stop']} Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")
else:
    print("  None found")

print(f"\n  ALL 0-LIQ (any CAGR, sorted by CAGR, top 20):")
z = [r for r in results if r['n_liq'] == 0]
z.sort(key=lambda x: -x['cagr'])
for w in z[:20]:
    stp = f" stops={w['n_stop']}" if w['n_stop'] > 0 else ""
    print(f"  {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}{stp}")
    for sd in w.get('stop_details', []):
        print(f"     STOP: {sd}")
    for ld in w.get('liq_details', []):
        print(f"     LIQ: {ld}")

with open('v28_phase6_results.json', 'w') as f:
    json.dump([{k:v for k,v in r.items() if k not in ('liq_details','stop_details')} for r in results], f, indent=2, default=str)
print(f"\nSaved to v28_phase6_results.json")
