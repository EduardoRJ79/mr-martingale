"""v2.8 Phase 4: Target the remaining 2023-01-20 short L4 liq.
Building on Phase 3 best (gw-1.5-10-14 at 79.9% CAGR, 1 liq).
The liq is short L4 at $2,500 equity — need to survive L4 or prevent L4 fill."""
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
    NOT_MULTS = build_not_mults(mults_seq)
    bal = 1000.0; n_tp = n_to = n_liq = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0
    liq_details = []
    level_dist = {}

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
            if not entered:
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

    total = n_tp + n_to + n_liq
    yrs = (sim_end - sim_start).days / 365.25
    cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0
    return {
        'bal': bal, 'cagr': cagr, 'n_tp': n_tp, 'n_to': n_to, 'n_liq': n_liq,
        'total': total, 'max_dd': max_dd, 'peak_eq': peak_eq,
        'liq_details': liq_details, 'level_dist': level_dist
    }

# ── Phase 4 configs ─────────────────────────────────────────────────────────
base = {
    'risk_pct': 0.30, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 10.0, 14.0],  # Best from Phase 3
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.015,
}

configs = []
def add(name, **overrides):
    configs.append((name, {**base, **overrides}))

# The remaining liq is SHORT L4 (favored short in bear market).
# Cumulative mults at L4: [1, 2, 5, 12.5] sum=20.5x
# L3->L4 mult is 2.5. Let's reduce it.

# A: Reduce L3->L4 multiplier (L4 becomes smaller)
# [2.0, 2.5, X, 7.0] → cumulative [1, 2, 5, 5*X]
add("L4m-1.5", level_multipliers=[2.0, 2.5, 1.5, 7.0])  # cum: [1,2,5,7.5] sum=15.5
add("L4m-1.0", level_multipliers=[2.0, 2.5, 1.0, 7.0])  # cum: [1,2,5,5] sum=13
add("L4m-2.0", level_multipliers=[2.0, 2.5, 2.0, 7.0])  # cum: [1,2,5,10] sum=18

# B: Flatten the whole curve
add("flat-1.5", level_multipliers=[1.5, 1.5, 1.5, 1.5])  # cum: [1,1.5,2.25,3.375,5.06] sum=13.2
add("flat-2.0", level_multipliers=[2.0, 2.0, 2.0, 2.0])  # cum: [1,2,4,8,16] sum=31
add("mild-2-2-1.5-1.5", level_multipliers=[2.0, 2.0, 1.5, 1.5])  # cum: [1,2,4,6,9] sum=22

# C: Max 3 levels (eliminate L4/L5 entirely)
add("max3", max_levels=3)
add("max3-r0.35", max_levels=3, risk_pct=0.35)
add("max3-r0.40", max_levels=3, risk_pct=0.40)
add("max3-r0.45", max_levels=3, risk_pct=0.45)
add("max3-r0.50", max_levels=3, risk_pct=0.50)

# D: Reduce risk enough to survive L4
add("r0.25", risk_pct=0.25)
add("r0.22", risk_pct=0.22)
add("r0.20", risk_pct=0.20)
add("r0.18", risk_pct=0.18)
add("r0.15", risk_pct=0.15)

# E: Wider L3 gap (prevent L4 from filling)
# Current: [0.5, 1.5, 10.0, 14.0] → L3 at 12%, L4 at 26%
add("gap3-15", level_gaps=[0.5, 1.5, 15.0, 14.0])  # L3 at 17%, L4 at 31%
add("gap3-18", level_gaps=[0.5, 1.5, 18.0, 14.0])  # L3 at 20%, L4 at 34%
add("gap3-20", level_gaps=[0.5, 1.5, 20.0, 14.0])  # L3 at 22%, L4 at 36%

# F: Combinations targeting the L4 liq
add("r0.25-L4m1.5", risk_pct=0.25, level_multipliers=[2.0, 2.5, 1.5, 7.0])
add("r0.25-L4m1.0", risk_pct=0.25, level_multipliers=[2.0, 2.5, 1.0, 7.0])
add("r0.28-L4m1.5", risk_pct=0.28, level_multipliers=[2.0, 2.5, 1.5, 7.0])
add("r0.22-L4m1.0", risk_pct=0.22, level_multipliers=[2.0, 2.5, 1.0, 7.0])

# G: Max 4 with lower L4 multiplier
add("max4-L4m1.5", max_levels=4, level_multipliers=[2.0, 2.5, 1.5, 7.0])
add("max4-L4m1.0", max_levels=4, level_multipliers=[2.0, 2.5, 1.0, 7.0])

# H: Short-specific unfav scaling (short in bull = unfav, uses lower risk)
# The liq is favored short though, so unfav won't help directly.
# Try wider spacing for ALL shorts by using higher short_trigger
add("shtrig-0.020", short_trigger_pct=0.020)
add("shtrig-0.025", short_trigger_pct=0.025)

# I: Shorter max hold (force exit before position decays too far)
add("hold-360", max_hold_bars=360)
add("hold-240", max_hold_bars=240)

# J: Wider gaps original v27 + reduced risk  (different gap structure)
add("orig-gaps-r0.20", level_gaps=[0.5, 1.5, 7.0, 8.0], risk_pct=0.20)
add("orig-gaps-r0.15", level_gaps=[0.5, 1.5, 7.0, 8.0], risk_pct=0.15)

# K: Best combo attempts
add("KB1-max3-r0.40-tp6", max_levels=3, risk_pct=0.40, tp_pct=0.006)
add("KB2-r0.22-L4m1.0-g3-15", risk_pct=0.22, level_multipliers=[2.0, 2.5, 1.0, 7.0], level_gaps=[0.5, 1.5, 15.0, 14.0])
add("KB3-r0.25-max4-gap15", risk_pct=0.25, max_levels=4, level_gaps=[0.5, 1.5, 15.0, 14.0])
add("KB4-r0.28-flat1.5", risk_pct=0.28, level_multipliers=[1.5, 1.5, 1.5, 1.5])
add("KB5-r0.30-flat1.5-tp6", risk_pct=0.30, level_multipliers=[1.5, 1.5, 1.5, 1.5], tp_pct=0.006)

# ── Run all ─────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*115}")
print(f"  PHASE 4: {len(configs)} CONFIGS (target: 0 liqs on 1m resolution)")
print(f"{'='*115}\n")
print(f"  {'#':>3} {'Name':<30} {'CAGR%':>8} {'Liqs':>5} {'Trades':>7} {'MaxDD%':>8} {'FinalEq':>12} {'PeakEq':>12} {'LvDist':>20}")
print(f"  {'---':>3} {'-'*30} {'-'*8} {'-'*5} {'-'*7} {'-'*8} {'-'*12} {'-'*12} {'-'*20}")

for idx, (name, cfg) in enumerate(configs):
    t0 = time.time()
    r = run_backtest(cfg)
    elapsed = time.time() - t0
    ld_str = " ".join(f"L{k}:{v}" for k, v in sorted(r['level_dist'].items()))
    flag = " ***" if r['n_liq'] == 0 and r['cagr'] >= 1.0 else (" **" if r['n_liq'] == 0 else (" *" if r['n_liq'] <= 1 else ""))
    print(f"  {idx+1:>3} {name:<30} {r['cagr']*100:>7.1f}% {r['n_liq']:>5} {r['total']:>7} {r['max_dd']*100:>7.1f}% ${r['bal']:>10,.0f} ${r['peak_eq']:>10,.0f} {ld_str:>20}{flag}")
    results.append({'name': name, 'cagr': r['cagr'], 'cagr_pct': r['cagr']*100,
                    'n_liq': r['n_liq'], 'total': r['total'], 'max_dd_pct': r['max_dd']*100,
                    'bal': r['bal'], 'peak_eq': r['peak_eq'], 'liq_details': r['liq_details'],
                    'level_dist': r['level_dist']})

print(f"\n{'='*115}")
print("  WINNERS: 0 liquidations AND CAGR >= 100%")
print(f"{'='*115}")
winners = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 1.0]
if winners:
    winners.sort(key=lambda x: -x['cagr'])
    for w in winners:
        print(f"  *** {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f} Peak=${w['peak_eq']:,.0f}")
else:
    print("  None found")

print(f"\n  ALL 0-LIQ (any CAGR, sorted by CAGR):")
z = [r for r in results if r['n_liq'] == 0]
z.sort(key=lambda x: -x['cagr'])
for w in z:
    print(f"  ** {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")

print(f"\n  BEST 1-LIQ (sorted by CAGR, top 10):")
o = [r for r in results if r['n_liq'] == 1]
o.sort(key=lambda x: -x['cagr'])
for w in o[:10]:
    print(f"  * {w['name']:<30} CAGR={w['cagr_pct']:.1f}%  Liqs: {w['liq_details']}")

with open('v28_phase4_results.json', 'w') as f:
    json.dump([{k:v for k,v in r.items() if k != 'liq_details'} for r in results], f, indent=2, default=str)
print(f"\nSaved to v28_phase4_results.json")
