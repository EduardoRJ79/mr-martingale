"""v2.8 Phase 8: Full-period search with new filters.
New criteria: drawdown-from-high, ATR%, realized vol, stop-loss.
Period: 2018-10-31 to 2026-03-28 (7.4 years)."""
import pandas as pd, numpy as np, time, json

print("Loading data...")
df = pd.read_parquet('signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet').sort_values('ts').reset_index(drop=True)
n = len(df)
df['t4h'] = df['ts'].dt.floor('4h')
c4 = df.groupby('t4h').agg(o=('o','first'), h=('h','max'), l=('l','min'), c=('c','last')).sort_index()
c4['ema'] = c4['c'].ewm(span=34, adjust=False).mean()
c4['sma'] = c4['c'].rolling(14).mean()

# New indicators on 4H bars
c4['high_20d'] = c4['h'].rolling(120).max()   # 120 4H bars = 20 days
c4['high_30d'] = c4['h'].rolling(180).max()
c4['high_60d'] = c4['h'].rolling(360).max()
c4['atr14'] = (c4['h'] - c4['l']).rolling(14).mean()
c4['rvol20'] = c4['c'].pct_change().rolling(20).std()

ema_v = c4['ema'].values; sma_v = c4['sma'].values
c4_c = c4['c'].values
c4_h20d = c4['high_20d'].values
c4_h30d = c4['high_30d'].values
c4_h60d = c4['high_60d'].values
c4_atr14 = c4['atr14'].values
c4_rvol20 = c4['rvol20'].values

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
print(f"Data: {n:,} bars | Sim: {yrs:.2f} yrs, {sim_idx}-{sim_end_idx}")

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
    max_loss_pct = cfg.get('max_loss_pct', None)
    # New filters
    max_dd_20d = cfg.get('max_dd_20d', None)      # Max drawdown from 20d high for longs (e.g. -0.15)
    max_dd_30d = cfg.get('max_dd_30d', None)
    max_dd_60d = cfg.get('max_dd_60d', None)
    max_atr_pct = cfg.get('max_atr_pct', None)     # Max ATR14 as % of price
    max_rvol = cfg.get('max_rvol', None)            # Max 20-bar realized vol
    # Risk scaling by volatility
    vol_risk_scale = cfg.get('vol_risk_scale', False)  # If True, scale risk by vol

    NOT_MULTS = build_not_mults(mults_seq)
    bal = 1000.0; n_tp = n_to = n_liq = n_stop = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0
    liq_details = []; level_dist = {}
    long_n = short_n = n_filtered = 0
    monthly = {}

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

            # Stop-loss check (before liq)
            if max_loss_pct is not None:
                loss_ratio = -upnl / bal if bal > 0 else 999
                if loss_ratio >= max_loss_pct:
                    ep = (worst_px - SLIP) if dr == 'long' else (worst_px + SLIP)
                    fee_out = tn * (TAKER + COMM)
                    hm = i - lvs[0].idx
                    fund = tn * FUND8H * (hm / (8 * 60))
                    gp = tq * (ep - bl)
                    pnl = gp - fee_out - fund
                    bal += pnl
                    if bal < 50: bal = 50
                    n_stop += 1; nl = len(lvs)
                    level_dist[nl] = level_dist.get(nl, 0) + 1
                    if i >= sim_idx and k in monthly: monthly[k]['n'] += 1
                    act = False; lvs = []; cd4 = ci + 1; continue

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

            # LONG
            lf = bull
            tr = long_trigger if lf else long_trigger * uf_trigger
            rk = risk if lf else risk * uf_risk
            hd = max_hold if lf else int(max_hold * uf_hold)
            gp = gaps if lf else [g * uf_spacing for g in gaps]
            if pbe >= tr and pbs >= tr:
                # Apply new filters for longs
                skip = False
                if max_dd_20d is not None and pc < len(c4_h20d):
                    h20 = c4_h20d[pc]
                    if not np.isnan(h20) and h20 > 0:
                        dd = (px / h20) - 1
                        if dd < max_dd_20d: skip = True
                if not skip and max_dd_30d is not None and pc < len(c4_h30d):
                    h30 = c4_h30d[pc]
                    if not np.isnan(h30) and h30 > 0:
                        dd = (px / h30) - 1
                        if dd < max_dd_30d: skip = True
                if not skip and max_dd_60d is not None and pc < len(c4_h60d):
                    h60 = c4_h60d[pc]
                    if not np.isnan(h60) and h60 > 0:
                        dd = (px / h60) - 1
                        if dd < max_dd_60d: skip = True
                if not skip and max_atr_pct is not None and pc < len(c4_atr14):
                    atr = c4_atr14[pc]
                    if not np.isnan(atr) and px > 0:
                        if (atr / px) > max_atr_pct: skip = True
                if not skip and max_rvol is not None and pc < len(c4_rvol20):
                    rv = c4_rvol20[pc]
                    if not np.isnan(rv):
                        if rv > max_rvol: skip = True

                if skip:
                    n_filtered += 1
                else:
                    # Vol-scaled risk
                    if vol_risk_scale and pc < len(c4_rvol20):
                        rv = c4_rvol20[pc]
                        if not np.isnan(rv) and rv > 0:
                            baseline_vol = 0.01  # 1% baseline
                            scale = min(baseline_vol / rv, 1.5)
                            scale = max(scale, 0.3)
                            rk = rk * scale

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

    total = n_tp + n_to + n_liq + n_stop
    cagr = ((bal / 1000) ** (1 / yrs) - 1) if bal > 0 else -1.0
    # Monthly compound return
    sorted_months = sorted(monthly.keys())
    n_months = len(sorted_months)
    prod = 1.0
    for ym in sorted_months:
        d = monthly[ym]
        r = d['e'] / d['s'] if d['s'] > 0 else 1
        prod *= r
    cmr = prod ** (1 / n_months) - 1 if n_months > 0 else 0

    return {
        'bal': bal, 'cagr': cagr, 'cmr': cmr,
        'n_tp': n_tp, 'n_to': n_to, 'n_liq': n_liq, 'n_stop': n_stop,
        'total': total, 'max_dd': max_dd, 'peak_eq': peak_eq,
        'liq_details': liq_details, 'level_dist': level_dist,
        'long_n': long_n, 'short_n': short_n, 'n_filtered': n_filtered,
        'n_months': n_months
    }

# ── Configs ─────────────────────────────────────────────────────────────────
base = {
    'risk_pct': 0.35, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 10.0, 14.0],
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.08,
}

configs = []
def add(name, **overrides):
    configs.append((name, {**base, **overrides}))

# Baseline (v2.8 recommended from Phase 7)
add("baseline-v28")

# ── A: Drawdown from 20d high filter for longs ──
for dd in [-0.10, -0.12, -0.15, -0.18, -0.20, -0.22]:
    add(f"dd20d-{int(abs(dd)*100)}pct", max_dd_20d=dd)

# ── B: Drawdown from 30d high ──
for dd in [-0.15, -0.18, -0.20, -0.25]:
    add(f"dd30d-{int(abs(dd)*100)}pct", max_dd_30d=dd)

# ── C: ATR% filter ──
for atr in [0.015, 0.020, 0.025, 0.030]:
    add(f"atr-{atr:.3f}", max_atr_pct=atr)

# ── D: RVol filter ──
for rv in [0.010, 0.015, 0.020, 0.025]:
    add(f"rvol-{rv:.3f}", max_rvol=rv)

# ── E: DD20d + risk combos ──
add("dd20-15-r0.30", max_dd_20d=-0.15, risk_pct=0.30)
add("dd20-15-r0.35", max_dd_20d=-0.15, risk_pct=0.35)
add("dd20-15-r0.40", max_dd_20d=-0.15, risk_pct=0.40)
add("dd20-15-r0.45", max_dd_20d=-0.15, risk_pct=0.45)
add("dd20-18-r0.35", max_dd_20d=-0.18, risk_pct=0.35)
add("dd20-18-r0.40", max_dd_20d=-0.18, risk_pct=0.40)
add("dd20-18-r0.45", max_dd_20d=-0.18, risk_pct=0.45)
add("dd20-20-r0.40", max_dd_20d=-0.20, risk_pct=0.40)
add("dd20-20-r0.45", max_dd_20d=-0.20, risk_pct=0.45)
add("dd20-20-r0.50", max_dd_20d=-0.20, risk_pct=0.50)

# ── F: DD + ATR combo ──
add("dd20-15-atr020", max_dd_20d=-0.15, max_atr_pct=0.020)
add("dd20-18-atr025", max_dd_20d=-0.18, max_atr_pct=0.025)
add("dd20-20-atr025", max_dd_20d=-0.20, max_atr_pct=0.025)

# ── G: DD + stop-loss combo ──
add("dd20-15-stop70", max_dd_20d=-0.15, max_loss_pct=0.70)
add("dd20-18-stop70", max_dd_20d=-0.18, max_loss_pct=0.70)
add("dd20-20-stop80", max_dd_20d=-0.20, max_loss_pct=0.80)
add("dd20-15-stop80", max_dd_20d=-0.15, max_loss_pct=0.80)

# ── H: Vol-scaled risk ──
add("volscale", vol_risk_scale=True)
add("volscale-dd20-18", vol_risk_scale=True, max_dd_20d=-0.18)
add("volscale-r0.40", vol_risk_scale=True, risk_pct=0.40)

# ── I: DD + higher risk (aggressive recovery) ──
add("dd20-12-r0.50", max_dd_20d=-0.12, risk_pct=0.50)
add("dd20-12-r0.45", max_dd_20d=-0.12, risk_pct=0.45)
add("dd20-10-r0.50", max_dd_20d=-0.10, risk_pct=0.50)
add("dd20-10-r0.55", max_dd_20d=-0.10, risk_pct=0.55)

# ── J: DD60d (longer lookback, less sensitive) ──
add("dd60-25-r0.40", max_dd_60d=-0.25, risk_pct=0.40)
add("dd60-25-r0.45", max_dd_60d=-0.25, risk_pct=0.45)
add("dd60-30-r0.45", max_dd_60d=-0.30, risk_pct=0.45)
add("dd60-30-r0.50", max_dd_60d=-0.30, risk_pct=0.50)

# ── Run ─────────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*140}")
print(f"  PHASE 8: {len(configs)} CONFIGS — FULL PERIOD (7.4 yrs, 1m liq)")
print(f"{'='*140}\n")
print(f"  {'#':>3} {'Name':<28} {'CAGR%':>8} {'CMR%':>7} {'Liqs':>5} {'Stops':>6} {'Trades':>7} {'Filt':>6} {'MaxDD%':>8} {'FinalEq':>12} {'PeakEq':>12}")
print(f"  {'---':>3} {'-'*28} {'-'*8} {'-'*7} {'-'*5} {'-'*6} {'-'*7} {'-'*6} {'-'*8} {'-'*12} {'-'*12}")

for idx, (name, cfg) in enumerate(configs):
    t0 = time.time()
    r = run_backtest(cfg)
    el = time.time() - t0
    flag = " ***" if r['n_liq'] == 0 and r['n_stop'] == 0 and r['cagr'] >= 0.5 else (" **" if r['n_liq'] == 0 else (" *" if r['n_liq'] <= 1 else ""))
    print(f"  {idx+1:>3} {name:<28} {r['cagr']*100:>7.1f}% {r['cmr']*100:>6.2f}% {r['n_liq']:>5} {r['n_stop']:>6} {r['total']:>7} {r['n_filtered']:>6} {r['max_dd']*100:>7.1f}% ${r['bal']:>10,.0f} ${r['peak_eq']:>10,.0f}{flag}")
    results.append({'name': name, 'cagr': r['cagr'], 'cagr_pct': r['cagr']*100,
                    'cmr': r['cmr'], 'cmr_pct': r['cmr']*100,
                    'n_liq': r['n_liq'], 'n_stop': r['n_stop'], 'total': r['total'],
                    'n_filtered': r['n_filtered'],
                    'max_dd_pct': r['max_dd']*100, 'bal': r['bal'], 'peak_eq': r['peak_eq'],
                    'liq_details': r['liq_details'], 'level_dist': r['level_dist']})

print(f"\n{'='*140}")
print("  WINNERS: 0 liqs, 0 stops, CAGR >= 50%")
print(f"{'='*140}")
winners = [r for r in results if r['n_liq'] == 0 and r['n_stop'] == 0 and r['cagr'] >= 0.5]
winners.sort(key=lambda x: -x['cagr'])
for w in winners:
    print(f"  *** {w['name']:<28} CAGR={w['cagr_pct']:.1f}% CMR={w['cmr_pct']:.2f}% Trades={w['total']} Filt={w['n_filtered']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")

print(f"\n  ALL 0-LIQ (incl stops, sorted by CAGR, top 15):")
z = [r for r in results if r['n_liq'] == 0]
z.sort(key=lambda x: -x['cagr'])
for w in z[:15]:
    stp = f" stops={w['n_stop']}" if w['n_stop'] > 0 else ""
    print(f"  {w['name']:<28} CAGR={w['cagr_pct']:.1f}% CMR={w['cmr_pct']:.2f}% Trades={w['total']} Filt={w['n_filtered']} MaxDD={w['max_dd_pct']:.1f}%{stp}")

print(f"\n  BEST 1-LIQ (top 10):")
o = [r for r in results if r['n_liq'] == 1]
o.sort(key=lambda x: -x['cagr'])
for w in o[:10]:
    print(f"  {w['name']:<28} CAGR={w['cagr_pct']:.1f}% Liqs: {w['liq_details']}")

with open('v28_phase8_results.json', 'w') as f:
    json.dump([{k:v for k,v in r.items() if k not in ('liq_details','level_dist')} for r in results], f, indent=2, default=str)
