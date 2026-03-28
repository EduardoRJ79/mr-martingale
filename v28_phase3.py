"""v2.8 Phase 3: Aggressive search targeting 0 liqs on 1m resolution.
Builds on Phase 1+2 findings: wider gaps and max4 levels were the best levers."""
import pandas as pd, numpy as np, time, json

# ── Load data once ──────────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_parquet('signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet').sort_values('ts').reset_index(drop=True)
n = len(df)
df['t4h'] = df['ts'].dt.floor('4h')
c4 = df.groupby('t4h').agg(c=('c','last')).sort_index()
c4['ema'] = c4['c'].ewm(span=34, adjust=False).mean()
c4['sma'] = c4['c'].rolling(14).mean()
ema_v = c4['ema'].values; sma_v = c4['sma'].values
df['t1d'] = df['ts'].dt.floor('1D')
cd = df.groupby('t1d').agg(c=('c','last')).sort_index()
cd['s440'] = cd['c'].rolling(440).mean()
s440d = {k: v for k, v in zip(cd.index.values, cd['s440'].values)}
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
    NOT_MULTS = build_not_mults(mults_seq)
    bal = 1000.0; n_tp = n_to = n_liq = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0
    liq_details = []

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

            # 1m liq check using worst-case price
            worst_px = l[i] if dr == 'long' else h[i]
            upnl = tq * (worst_px - bl)
            if bal + upnl <= tn * MAINT:
                n_liq += 1
                liq_details.append(f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} eq=${bal:,.0f}")
                bal = 1000.0; act = False; lvs = []; cd4 = ci + 1
                peak_eq = 1000.0; continue

            # Grid fills
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

            # TP
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
                act = False; lvs = []; cd4 = ci + 1; continue

            # Timeout
            if ib and (ci - e4) >= mh:
                ep = (c_[i] - SLIP) if dr == 'long' else (c_[i] + SLIP)
                fee_out = tn * (TAKER + COMM)
                hm = i - lvs[0].idx
                fund = tn * FUND8H * (hm / (8 * 60))
                gp = tq * (ep - bl)
                pnl = gp - fee_out - fund
                bal += pnl; n_to += 1
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
                nt = rk * bal
                qt = nt / ep_e
                fee_in = nt * (TAKER + COMM)
                bal -= fee_in
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
                    ep_e = px - SLIP
                    nt = rk * bal
                    qt = -nt / ep_e
                    fee_in = nt * (TAKER + COMM)
                    bal -= fee_in
                    lvs = [Lv(1, ep_e, nt, qt, i)]
                    dr = 'short'; fav = sf; drops_ = cum_drops(gp)
                    mh = hd; e4 = ci; act = True

    total = n_tp + n_to + n_liq
    yrs = (sim_end - sim_start).days / 365.25
    cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0
    return {
        'bal': bal, 'cagr': cagr, 'n_tp': n_tp, 'n_to': n_to, 'n_liq': n_liq,
        'total': total, 'max_dd': max_dd, 'peak_eq': peak_eq, 'liq_details': liq_details
    }

# ── Phase 3 configs ─────────────────────────────────────────────────────────
v27_base = {
    'risk_pct': 0.30, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 7.0, 8.0],
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.015,
}

configs = []
def add(name, **overrides):
    cfg = {**v27_base, **overrides}
    configs.append((name, cfg))

# Strategy A: Wider gaps (the strongest single lever)
# gaps-wider2 [0.5, 1.5, 10.0, 12.0] had 79.9% CAGR, 1 liq
add("gw-1.5-10-14", level_gaps=[0.5, 1.5, 10.0, 14.0])
add("gw-1.5-10-16", level_gaps=[0.5, 1.5, 10.0, 16.0])
add("gw-1.5-12-14", level_gaps=[0.5, 1.5, 12.0, 14.0])
add("gw-1.5-12-16", level_gaps=[0.5, 1.5, 12.0, 16.0])
add("gw-2.0-10-14", level_gaps=[0.5, 2.0, 10.0, 14.0])
add("gw-2.0-12-16", level_gaps=[0.5, 2.0, 12.0, 16.0])

# Strategy B: Max 4 levels with tweaks
# max4 had 67.2% CAGR, 1 liq. Push further.
add("max4-gw1", max_levels=4, level_gaps=[0.5, 1.5, 10.0, 12.0])
add("max4-gw2", max_levels=4, level_gaps=[0.5, 1.5, 12.0, 14.0])
add("max4-r0.25", max_levels=4, risk_pct=0.25)
add("max4-r0.22", max_levels=4, risk_pct=0.22)

# Strategy C: Wider gaps + reduced L5 multiplier
add("gw10-14-L5m3", level_gaps=[0.5, 1.5, 10.0, 14.0], level_multipliers=[2.0, 2.5, 2.5, 3.0])
add("gw10-16-L5m3", level_gaps=[0.5, 1.5, 10.0, 16.0], level_multipliers=[2.0, 2.5, 2.5, 3.0])
add("gw12-16-L5m4", level_gaps=[0.5, 1.5, 12.0, 16.0], level_multipliers=[2.0, 2.5, 2.5, 4.0])
add("gw12-16-L5m3", level_gaps=[0.5, 1.5, 12.0, 16.0], level_multipliers=[2.0, 2.5, 2.5, 3.0])

# Strategy D: Wider gaps + reduced risk
add("gw10-14-r0.25", level_gaps=[0.5, 1.5, 10.0, 14.0], risk_pct=0.25)
add("gw10-16-r0.25", level_gaps=[0.5, 1.5, 10.0, 16.0], risk_pct=0.25)
add("gw12-16-r0.25", level_gaps=[0.5, 1.5, 12.0, 16.0], risk_pct=0.25)
add("gw12-16-r0.28", level_gaps=[0.5, 1.5, 12.0, 16.0], risk_pct=0.28)

# Strategy E: Wider gaps + unfav tightening
add("gw10-14-uf0.4", level_gaps=[0.5, 1.5, 10.0, 14.0], unfav_risk_scale=0.40)
add("gw12-16-uf0.4", level_gaps=[0.5, 1.5, 12.0, 16.0], unfav_risk_scale=0.40)
add("gw10-14-ufsp2", level_gaps=[0.5, 1.5, 10.0, 14.0], unfav_spacing_scale=2.0)

# Strategy F: Kitchen sink — combine multiple protections
add("KS1-gw10-14-L5m3-r0.28", level_gaps=[0.5, 1.5, 10.0, 14.0], level_multipliers=[2.0, 2.5, 2.5, 3.0], risk_pct=0.28)
add("KS2-gw12-16-L5m3-r0.28", level_gaps=[0.5, 1.5, 12.0, 16.0], level_multipliers=[2.0, 2.5, 2.5, 3.0], risk_pct=0.28)
add("KS3-gw10-14-L5m4-r0.25", level_gaps=[0.5, 1.5, 10.0, 14.0], level_multipliers=[2.0, 2.5, 2.5, 4.0], risk_pct=0.25)
add("KS4-max4-gw-r0.28", max_levels=4, level_gaps=[0.5, 1.5, 10.0, 12.0], risk_pct=0.28)
add("KS5-max4-gw-r0.30", max_levels=4, level_gaps=[0.5, 1.5, 10.0, 12.0], risk_pct=0.30)

# Strategy G: Very wide L4 gap to essentially prevent L5 fills
add("gw-1.5-10-20", level_gaps=[0.5, 1.5, 10.0, 20.0])
add("gw-1.5-10-25", level_gaps=[0.5, 1.5, 10.0, 25.0])
add("gw-1.5-12-20", level_gaps=[0.5, 1.5, 12.0, 20.0])

# Strategy H: Flatter multiplier curve + wide gaps
add("flat2-gw10-14", level_multipliers=[2.0, 2.0, 2.0, 2.0], level_gaps=[0.5, 1.5, 10.0, 14.0])
add("flat2-gw12-16", level_multipliers=[2.0, 2.0, 2.0, 2.0], level_gaps=[0.5, 1.5, 12.0, 16.0])

# Strategy I: TP adjustment with protection
add("gw10-14-tp6", level_gaps=[0.5, 1.5, 10.0, 14.0], tp_pct=0.006)
add("gw12-16-tp6", level_gaps=[0.5, 1.5, 12.0, 16.0], tp_pct=0.006)

# ── Run all ─────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*110}")
print(f"  PHASE 3: {len(configs)} CONFIGURATIONS (1m liq check)")
print(f"{'='*110}\n")
print(f"  {'#':>3} {'Name':<35} {'CAGR%':>8} {'Liqs':>5} {'Trades':>7} {'MaxDD%':>8} {'FinalEq':>12} {'PeakEq':>12} {'Time':>5}")
print(f"  {'---':>3} {'-'*35} {'-'*8} {'-'*5} {'-'*7} {'-'*8} {'-'*12} {'-'*12} {'-'*5}")

for idx, (name, cfg) in enumerate(configs):
    t0 = time.time()
    r = run_backtest(cfg)
    elapsed = time.time() - t0
    row = {'name': name, **{k:v for k,v in r.items() if k != 'liq_details'},
           'cagr_pct': r['cagr']*100, 'max_dd_pct': r['max_dd']*100, 'liq_details': r['liq_details']}
    results.append(row)
    flag = " ***" if r['n_liq'] == 0 else (" *" if r['n_liq'] <= 1 else "")
    print(f"  {idx+1:>3} {name:<35} {r['cagr']*100:>7.1f}% {r['n_liq']:>5} {r['total']:>7} {r['max_dd']*100:>7.1f}% ${r['bal']:>10,.0f} ${r['peak_eq']:>10,.0f} {elapsed:>4.0f}s{flag}")

# ── Summary ─────────────────────────────────────────────────────────────────
print(f"\n{'='*110}")
print("  WINNERS: 0 liquidations AND CAGR >= 100%")
print(f"{'='*110}")
winners = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 1.0]
if winners:
    winners.sort(key=lambda x: -x['cagr'])
    for w in winners:
        print(f"  {w['name']:<35} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")
else:
    print("  None found")

print(f"\n  ALL 0-LIQ CONFIGS (any CAGR):")
z = [r for r in results if r['n_liq'] == 0]
z.sort(key=lambda x: -x['cagr'])
for w in z:
    print(f"  {w['name']:<35} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")

print(f"\n  ALL 1-LIQ CONFIGS (sorted by CAGR):")
o = [r for r in results if r['n_liq'] == 1]
o.sort(key=lambda x: -x['cagr'])
for w in o[:15]:
    print(f"  {w['name']:<35} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")
    for ld in w.get('liq_details', []):
        print(f"    LIQ: {ld}")

with open('v28_phase3_results.json', 'w') as f:
    json.dump([{k:v for k,v in r.items() if k != 'liq_details'} for r in results], f, indent=2, default=str)
print(f"\nResults saved to v28_phase3_results.json")
