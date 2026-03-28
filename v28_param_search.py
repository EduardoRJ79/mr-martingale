"""v2.8 Parameter Search: Find configs with 0 liquidations and CAGR >= 100%.
Uses the validated run_comparison.py engine logic, parameterized."""
import pandas as pd, numpy as np, time, sys, json

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
print(f"Data loaded: {n:,} bars, sim range [{sim_idx}:{sim_end_idx}]")

# ── Constants ───────────────────────────────────────────────────────────────
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

# ── Backtest function ───────────────────────────────────────────────────────
def run_backtest(cfg):
    """Run backtest with given config. Returns dict of results."""
    risk = cfg['risk_pct']
    tp = cfg['tp_pct']
    gaps = cfg['level_gaps']
    mults_seq = cfg['level_multipliers']
    max_lvl = cfg.get('max_levels', 5)
    uf_trigger = cfg.get('unfav_trigger_scale', 3.0)
    uf_risk = cfg.get('unfav_risk_scale', 0.60)
    uf_spacing = cfg.get('unfav_spacing_scale', 1.60)
    uf_hold = cfg.get('unfav_hold_scale', 0.45)
    max_hold = cfg.get('max_hold_bars', 720)
    long_trigger = cfg.get('long_trigger_pct', 0.005)
    short_trigger = cfg.get('short_trigger_pct', 0.015)
    liq_1m = cfg.get('liq_check_1m', True)  # Check liq on 1m bars too

    NOT_MULTS = build_not_mults(mults_seq)

    bal = 1000.0; n_tp = n_to = n_liq = 0
    act = False; dr = None; fav = None; lvs = []; drops_ = []; mh = 0; e4 = 0; cd4 = 0
    peak_eq = 1000.0; max_dd = 0.0

    for i in range(min(n, sim_end_idx + 1)):
        ci = b2c[i]; ib = (i == bounds[ci])

        # Track equity
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

            # LIQ check — on every 1m bar if liq_1m, else only 4H close
            do_liq_check = liq_1m or ib
            if do_liq_check:
                # Use low/high for 1m check (worst case), close for 4H
                if liq_1m and not ib:
                    worst_px = l[i] if dr == 'long' else h[i]
                else:
                    worst_px = c_[i]
                upnl = tq * (worst_px - bl)
                if bal + upnl <= tn * MAINT:
                    n_liq += 1
                    bal = 1000.0; act = False; lvs = []; cd4 = ci + 1
                    peak_eq = 1000.0; continue

            # Grid fills on 1m
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

            # TP on 1m
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

        # ENTRY
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
                ep_e = px + SLIP
                nt = rk * bal
                qt = nt / ep_e
                fee_in = nt * (TAKER + COMM)
                bal -= fee_in
                lvs = [Lv(1, ep_e, nt, qt, i)]
                dr = 'long'; fav = lf; drops_ = cum_drops(gp)
                mh = hd; e4 = ci; act = True; entered = True

            # SHORT
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
        'total': total, 'max_dd': max_dd, 'peak_eq': peak_eq
    }

# ── Define test configurations ──────────────────────────────────────────────
v27_base = {
    'risk_pct': 0.30, 'tp_pct': 0.005,
    'level_gaps': [0.5, 1.5, 7.0, 8.0],
    'level_multipliers': [2.0, 2.5, 2.5, 7.0],
    'max_levels': 5,
    'unfav_trigger_scale': 3.0, 'unfav_risk_scale': 0.60,
    'unfav_spacing_scale': 1.60, 'unfav_hold_scale': 0.45,
    'max_hold_bars': 720,
    'long_trigger_pct': 0.005, 'short_trigger_pct': 0.015,
    'liq_check_1m': True,
}

configs = []

def add(name, **overrides):
    cfg = {**v27_base, **overrides}
    configs.append((name, cfg))

# Baseline: v2.7 with 1m liq check
add("v27-baseline-1m")

# ── Phase 1: Single parameter changes ──────────────────────────────────────

# A. Risk reduction
add("risk-0.25", risk_pct=0.25)
add("risk-0.22", risk_pct=0.22)
add("risk-0.20", risk_pct=0.20)
add("risk-0.18", risk_pct=0.18)

# B. L5 multiplier reduction (the biggest lever)
add("L5mult-5.0", level_multipliers=[2.0, 2.5, 2.5, 5.0])
add("L5mult-4.0", level_multipliers=[2.0, 2.5, 2.5, 4.0])
add("L5mult-3.0", level_multipliers=[2.0, 2.5, 2.5, 3.0])
add("L5mult-2.0", level_multipliers=[2.0, 2.5, 2.5, 2.0])

# C. Max 4 levels (eliminate L5 entirely)
add("max4-levels", max_levels=4)

# D. Wider gaps (harder to reach L5)
add("gaps-wider1", level_gaps=[0.5, 1.5, 9.0, 10.0])
add("gaps-wider2", level_gaps=[0.5, 1.5, 10.0, 12.0])
add("gaps-wider3", level_gaps=[0.5, 2.0, 8.0, 10.0])

# E. Unfavored scaling
add("uf-risk-0.40", unfav_risk_scale=0.40)
add("uf-risk-0.50", unfav_risk_scale=0.50)
add("uf-spacing-2.0", unfav_spacing_scale=2.0)

# F. TP adjustments
add("tp-0.006", tp_pct=0.006)
add("tp-0.007", tp_pct=0.007)

# G. Max hold
add("hold-480", max_hold_bars=480)
add("hold-360", max_hold_bars=360)

# ── Phase 2: Combinations of promising changes ─────────────────────────────

# Reduced risk + reduced L5
add("risk0.25-L5m4", risk_pct=0.25, level_multipliers=[2.0, 2.5, 2.5, 4.0])
add("risk0.25-L5m3", risk_pct=0.25, level_multipliers=[2.0, 2.5, 2.5, 3.0])
add("risk0.22-L5m5", risk_pct=0.22, level_multipliers=[2.0, 2.5, 2.5, 5.0])
add("risk0.22-L5m4", risk_pct=0.22, level_multipliers=[2.0, 2.5, 2.5, 4.0])

# Risk + wider gaps
add("risk0.25-gaps-wider", risk_pct=0.25, level_gaps=[0.5, 1.5, 9.0, 10.0])

# Risk + L5 + wider gaps
add("r25-L5m4-gw", risk_pct=0.25, level_multipliers=[2.0, 2.5, 2.5, 4.0], level_gaps=[0.5, 1.5, 9.0, 10.0])

# Risk + TP increase (compensate for lower risk)
add("risk0.25-tp0.006", risk_pct=0.25, tp_pct=0.006)
add("risk0.22-tp0.007", risk_pct=0.22, tp_pct=0.007)

# Max 4 levels + higher risk (can afford more since no L5 risk)
add("max4-risk0.35", max_levels=4, risk_pct=0.35)
add("max4-risk0.40", max_levels=4, risk_pct=0.40)

# L5 reduction + unfav tightening
add("L5m4-uf-risk0.40", level_multipliers=[2.0, 2.5, 2.5, 4.0], unfav_risk_scale=0.40)
add("L5m3-uf-risk0.50", level_multipliers=[2.0, 2.5, 2.5, 3.0], unfav_risk_scale=0.50)

# Aggressive: small L5 + slightly higher risk + higher TP
add("r0.28-L5m3-tp6", risk_pct=0.28, level_multipliers=[2.0, 2.5, 2.5, 3.0], tp_pct=0.006)
add("r0.30-L5m3-tp6", risk_pct=0.30, level_multipliers=[2.0, 2.5, 2.5, 3.0], tp_pct=0.006)

# Flatten the whole multiplier curve
add("flat-mults", level_multipliers=[2.0, 2.0, 2.0, 2.0])
add("flat-mults-r0.28", level_multipliers=[2.0, 2.0, 2.0, 2.0], risk_pct=0.28)

# ── Run all ─────────────────────────────────────────────────────────────────
results = []
print(f"\n{'='*100}")
print(f"  RUNNING {len(configs)} CONFIGURATIONS")
print(f"{'='*100}\n")
print(f"  {'#':>3} {'Name':<30} {'CAGR%':>8} {'Liqs':>5} {'Trades':>7} {'MaxDD%':>8} {'FinalEq':>12} {'PeakEq':>12} {'Time':>6}")
print(f"  {'---':>3} {'-'*30} {'-'*8} {'-'*5} {'-'*7} {'-'*8} {'-'*12} {'-'*12} {'-'*6}")

for idx, (name, cfg) in enumerate(configs):
    t0 = time.time()
    r = run_backtest(cfg)
    elapsed = time.time() - t0
    row = {
        'name': name, **r,
        'cagr_pct': r['cagr'] * 100,
        'max_dd_pct': r['max_dd'] * 100,
    }
    results.append(row)
    flag = " *** " if r['n_liq'] == 0 and r['cagr'] >= 1.0 else ""
    print(f"  {idx+1:>3} {name:<30} {r['cagr']*100:>7.1f}% {r['n_liq']:>5} {r['total']:>7} {r['max_dd']*100:>7.1f}% ${r['bal']:>10,.0f} ${r['peak_eq']:>10,.0f} {elapsed:>5.0f}s{flag}")

# ── Summary ─────────────────────────────────────────────────────────────────
print(f"\n{'='*100}")
print("  CANDIDATES: 0 liquidations AND CAGR >= 100%")
print(f"{'='*100}")
winners = [r for r in results if r['n_liq'] == 0 and r['cagr'] >= 1.0]
if winners:
    winners.sort(key=lambda x: -x['cagr'])
    for w in winners:
        print(f"  {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f} Peak=${w['peak_eq']:,.0f}")
else:
    print("  None found — need Phase 3 refinement")

# Also show best 0-liq configs regardless of CAGR
print(f"\n  ALL 0-LIQUIDATION CONFIGS (sorted by CAGR):")
zero_liq = [r for r in results if r['n_liq'] == 0]
zero_liq.sort(key=lambda x: -x['cagr'])
for w in zero_liq:
    print(f"  {w['name']:<30} CAGR={w['cagr_pct']:.1f}% Trades={w['total']} MaxDD={w['max_dd_pct']:.1f}% Final=${w['bal']:,.0f}")

# Save results
with open('v28_search_results.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nResults saved to v28_search_results.json")
