"""v2.8 Phase 5: Emergency stop-loss + wider search.
Key insight from Phase 4: Jan 2023 short squeeze liquidates almost any config with
meaningful positions. New approach: add max_loss_pct — if unrealized loss exceeds
X% of equity, force-exit at market (taker) instead of getting liquidated.
This converts a 100% loss (liq) into a ~60-80% loss (recoverable)."""
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
    max_loss_pct = cfg.get('max_loss_pct', None)  # NEW: emergency stop
    NOT_MULTS = build_not_mults(mults_seq)
    bal = 1000.0; n_tp = n_to = n_liq = n_stop = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0
    liq_details = []; stop_details = []
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

            # 1m liq check
            worst_px = l[i] if dr == 'long' else h[i]
            upnl = tq * (worst_px - bl)
            if bal + upnl <= tn * MAINT:
                n_liq += 1
                liq_details.append(f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} eq=${bal:,.0f}")
                bal = 1000.0; act = False; lvs = []; cd4 = ci + 1
                peak_eq = 1000.0; continue

            # Emergency stop-loss on 1m bar (check before grid fills)
            if max_loss_pct is not None:
                upnl_worst = tq * (worst_px - bl)
                loss_ratio = -upnl_worst / bal if bal > 0 else 999
                if loss_ratio >= max_loss_pct:
                    # Force exit at worst price (conservative: assume we exit at the wick)
                    ep = (worst_px - SLIP) if dr == 'long' else (worst_px + SLIP)
                    fee_out = tn * (TAKER + COMM)
                    hm = i - lvs[0].idx
                    fund = tn * FUND8H * (hm / (8 * 60))
                    gp = tq * (ep - bl)
                    pnl = gp - fee_out - fund
                    bal += pnl
                    if bal < 50: bal = 50  # Floor
                    n_stop += 1
                    stop_details.append(f"{pd.Timestamp(ts[i])} {dr} L{len(lvs)} loss={loss_ratio*100:.0f}% eq_after=${bal:,.0f}")
                    nl = len(lvs)
                    level_dist[nl] = level_dist.get(nl, 0) + 1
                    act = False; lvs = []; cd4 = ci + 1; continue

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
                nl = len(lvs)
                level_dist[nl] = level_dist.get(nl, 0) + 1
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

    total = n_tp + n_to + n_liq + n_stop
    yrs = (sim_end - sim_start).days / 365.25
    cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0
    return {
        'bal': bal, 'cagr': cagr, 'n_tp': n_tp, 'n_to': n_to, 'n_liq': n_liq,
        'n_stop': n_stop, 'total': total, 'max_dd': max_dd, 'peak_eq': peak_eq,
        'liq_details': liq_details, 'stop_details': stop_details, 'level_dist': level_dist
    }

# ── Configs ─────────────────────────────────────────────────────────────────
base_wide = {
    'risk_pct': 0.30, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 10.0, 14.0],
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.015,
    'max_loss_pct': None,
}

configs = []
def add(name, base=base_wide, **overrides):
    configs.append((name, {**base, **overrides}))

# A: Emergency stop on the Phase 3 best config (gaps [0.5, 1.5, 10, 14])
for sl in [0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
    add(f"stop-{int(sl*100)}pct", max_loss_pct=sl)

# B: Emergency stop with original v2.7 gaps (more trades = more compounding)
base_orig = {**base_wide, 'level_gaps': [0.5, 1.5, 7.0, 8.0]}
for sl in [0.50, 0.60, 0.70, 0.80, 0.85, 0.90]:
    add(f"orig-stop-{int(sl*100)}pct", base=base_orig, max_loss_pct=sl)

# C: Emergency stop + reduced L5 mult
for sl in [0.60, 0.70, 0.80]:
    add(f"L5m3-stop-{int(sl*100)}pct", level_multipliers=[2.0, 2.5, 2.5, 3.0], max_loss_pct=sl)

# D: Emergency stop + wider gaps + reduced risk (compensate later with more CAGR)
for sl in [0.60, 0.70, 0.80]:
    add(f"r0.28-stop-{int(sl*100)}pct", risk_pct=0.28, max_loss_pct=sl)

# E: Emergency stop on original gaps with higher risk (since stop prevents total loss)
base_orig_hr = {**base_orig, 'risk_pct': 0.35}
for sl in [0.50, 0.60, 0.70]:
    add(f"orig-r35-stop-{int(sl*100)}pct", base=base_orig_hr, max_loss_pct=sl)

# F: Emergency stop + max 4 levels
for sl in [0.60, 0.70, 0.80]:
    add(f"max4-stop-{int(sl*100)}pct", max_levels=4, max_loss_pct=sl)

# G: Best combo: orig gaps + stop + slightly higher risk (stop protects)
for sl in [0.50, 0.55, 0.60, 0.65]:
    add(f"orig-r0.35-stop-{int(sl*100)}pct", base=base_orig, risk_pct=0.35, max_loss_pct=sl)

# H: Very wide gaps + stop (eliminate the Nov 2025 liq via gaps, Jan 2023 via stop)
add("gw12-stop70", level_gaps=[0.5, 1.5, 12.0, 16.0], max_loss_pct=0.70)
add("gw12-stop60", level_gaps=[0.5, 1.5, 12.0, 16.0], max_loss_pct=0.60)
add("gw10-stop70", level_gaps=[0.5, 1.5, 10.0, 14.0], max_loss_pct=0.70)
add("gw10-stop60", level_gaps=[0.5, 1.5, 10.0, 14.0], max_loss_pct=0.60)

# ── Run ─────────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*130}")
print(f"  PHASE 5: {len(configs)} CONFIGS (emergency stop-loss)")
print(f"{'='*130}\n")
hdr = f"  {'#':>3} {'Name':<30} {'CAGR%':>8} {'Liqs':>5} {'Stops':>6} {'Trades':>7} {'MaxDD%':>8} {'FinalEq':>12} {'PeakEq':>12}"
print(hdr)
print(f"  {'---':>3} {'-'*30} {'-'*8} {'-'*5} {'-'*6} {'-'*7} {'-'*8} {'-'*12} {'-'*12}")

for idx, (name, cfg) in enumerate(configs):
    t0 = time.time()
    r = run_backtest(cfg)
    elapsed = time.time() - t0
    flag = " ***" if r['n_liq'] == 0 and r['cagr'] >= 1.0 else (" **" if r['n_liq'] == 0 and r['cagr'] >= 0.5 else (" *" if r['n_liq'] == 0 else ""))
    print(f"  {idx+1:>3} {name:<30} {r['cagr']*100:>7.1f}% {r['n_liq']:>5} {r['n_stop']:>6} {r['total']:>7} {r['max_dd']*100:>7.1f}% ${r['bal']:>10,.0f} ${r['peak_eq']:>10,.0f}{flag}")
    results.append({'name': name, 'cagr': r['cagr'], 'cagr_pct': r['cagr']*100,
                    'n_liq': r['n_liq'], 'n_stop': r['n_stop'], 'total': r['total'],
                    'max_dd_pct': r['max_dd']*100, 'bal': r['bal'], 'peak_eq': r['peak_eq'],
                    'liq_details': r['liq_details'], 'stop_details': r['stop_details'],
                    'level_dist': r['level_dist'], 'config': cfg})

# ── Summary ─────────────────────────────────────────────────────────────────
print(f"\n{'='*130}")
print("  WINNERS: 0 liqs AND CAGR >= 100%")
print(f"{'='*130}")
winners = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 1.0]
if winners:
    winners.sort(key=lambda x: -x['cagr'])
    for w in winners:
        print(f"  *** {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Stops={w['n_stop']} Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f} Peak=${w['peak_eq']:,.0f}")
        for sd in w.get('stop_details', []):
            print(f"      STOP: {sd}")
else:
    print("  None found")

print(f"\n  BEST 0-LIQ (any CAGR >= 50%):")
z = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 0.5]
z.sort(key=lambda x: -x['cagr'])
for w in z:
    print(f"  ** {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Stops={w['n_stop']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")
    for sd in w.get('stop_details', []):
        print(f"     STOP: {sd}")

print(f"\n  ALL 0-LIQ (sorted by CAGR):")
all_z = [r for r in results if r['n_liq'] == 0]
all_z.sort(key=lambda x: -x['cagr'])
for w in all_z:
    print(f"  {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Stops={w['n_stop']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")

with open('v28_phase5_results.json', 'w') as f:
    json.dump([{k:v for k,v in r.items() if k not in ('liq_details','stop_details','config')} for r in results], f, indent=2, default=str)
print(f"\nSaved to v28_phase5_results.json")
