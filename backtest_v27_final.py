"""
MRM v2.7 Backtest — spec-faithful implementation
Uses spec's own fee structure: taker 0.0432%, maker 0.0144%, funding 0.0013%/8h
Plus user-specified: slippage 3 ticks
Commission 0.045% per side (user: "comissão de 0,0045")
Reset to $1000 after liquidation
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List
import sys, time

# ═══════ PARAMETERS ════════════════════════════════════════════════════════
INITIAL_CAPITAL = 1000.0
RESET_CAPITAL   = 1000.0

RISK_PCT        = 0.30
TP_PCT          = 0.0050
NUM_LEVELS      = 5
LEVEL_GAPS      = [0.5, 1.5, 7.0, 8.0]
LEVEL_MULTS     = [2.0, 2.5, 2.5, 7.0]

EMA_SPAN, SMA_SPAN, DMA_PERIOD = 34, 14, 440
LONG_TRIG = 0.005
SHORT_TRIG = 0.015
LEV_LONG, LEV_SHORT = 20, 15

UNFAV_RISK  = 0.60
UNFAV_SPACE = 1.60
UNFAV_TRIG  = 3.00
UNFAV_HOLD  = 0.45

MAX_HOLD    = 720
COOLDOWN    = 1

TAKER_FEE   = 0.000432
MAKER_FEE   = 0.000144
FUNDING_8H  = 0.000013
MAINT_RATE  = 0.005
SLIP_TICKS  = 3
TICK_SZ     = 0.01
COMMISSION  = 0.00045   # 0.045% additional per side
MIN_EQ      = 50.0

# Notional multipliers: cumulative products
NOT_MULTS = [1.0]
_m = 1.0
for _x in LEVEL_MULTS:
    _m *= _x
    NOT_MULTS.append(_m)
# [1.0, 2.0, 5.0, 12.5, 87.5]

def cum_drops(gaps):
    r, a = [], 0.0
    for g in gaps:
        a += g; r.append(a / 100.0)
    return r

@dataclass
class Lv:
    level: int; price: float; notional: float; qty: float; idx: int

@dataclass
class Trade:
    dir: str; fav: bool; t_entry: object; t_exit: object
    n_levels: int; l1_px: float; blended: float; exit_px: float
    reason: str; pnl: float; fees: float; eq_before: float; eq_after: float


def run(data_path, start='2017-01-01'):
    df_full = pd.read_parquet(data_path).sort_values('ts').reset_index(drop=True)
    # Use full dataset for MA warmup, but only simulate from start_date
    sim_start = pd.Timestamp(start, tz='UTC')
    df = df_full  # Keep all data for MA computation
    n = len(df)
    print(f"Full data: {n:,} bars | {df['ts'].iloc[0]} to {df['ts'].iloc[-1]}")
    print(f"Sim starts: {sim_start}")

    # 4h candles + MAs
    df['t4h'] = df['ts'].dt.floor('4h')
    c4 = df.groupby('t4h').agg(c=('c','last')).sort_index()
    c4['ema'] = c4['c'].ewm(span=EMA_SPAN, adjust=False).mean()
    c4['sma'] = c4['c'].rolling(SMA_SPAN).mean()
    ema_v = c4['ema'].values; sma_v = c4['sma'].values

    # Daily SMA440
    df['t1d'] = df['ts'].dt.floor('1D')
    cd = df.groupby('t1d').agg(c=('c','last')).sort_index()
    cd['s440'] = cd['c'].rolling(DMA_PERIOD).mean()
    s440d = {k: v for k, v in zip(cd.index.values, cd['s440'].values)}

    # Arrays
    ts = df['ts'].values; h = df['h'].values; l = df['l'].values; c = df['c'].values
    t4v = df['t4h'].values

    # 4h boundaries
    bounds = [0]
    for i in range(1, n):
        if t4v[i] != t4v[i-1]: bounds.append(i)
    bounds = np.array(bounds)

    # Map 1m -> 4h index
    b2c = np.zeros(n, dtype=np.int64)
    for bi in range(len(bounds)):
        s = bounds[bi]; e = bounds[bi+1] if bi+1 < len(bounds) else n
        b2c[s:e] = bi

    print(f"4h: {len(c4)} | SMA440 from: {cd['s440'].first_valid_index()}")

    # Find first 1m bar at or after sim_start
    sim_start_np = np.datetime64(sim_start.asm8)
    sim_start_idx = np.searchsorted(ts, sim_start_np)
    print(f"Sim starts at bar {sim_start_idx} ({pd.Timestamp(ts[sim_start_idx])})")

    # ═══ SIM ═══════════════════════════════════════════════════════════════
    t0 = time.time()
    bal = INITIAL_CAPITAL
    trades, liqs = [], []
    act = False; dr = None; fav = None; lvs = []; drops = []; mh = 0; e4 = 0; cd4 = 0; efee = 0.0
    mo = {}

    def track(i, eq):
        tp = pd.Timestamp(ts[i]); k = (tp.year, tp.month)
        if k not in mo: mo[k] = {'s': eq, 'e': eq, 'n': 0, 'pk': eq, 'tr': eq}
        d = mo[k]; d['e'] = eq
        if eq > d['pk']: d['pk'] = eq
        if eq < d['tr']: d['tr'] = eq
        return k

    def eq_at(b, lvlist, px):
        if not lvlist: return b
        tq = sum(x.qty for x in lvlist)
        bl = sum(x.qty * x.price for x in lvlist) / tq
        return b + tq * (px - bl)

    def fee_entry(not_val, is_l1):
        """Entry fee: L1=taker, L2+=maker, plus commission."""
        base = not_val * (TAKER_FEE if is_l1 else MAKER_FEE)
        return base + not_val * COMMISSION

    def fee_exit(not_val, is_tp):
        """Exit fee: TP=maker, timeout/liq=taker, plus commission."""
        base = not_val * (MAKER_FEE if is_tp else TAKER_FEE)
        return base + not_val * COMMISSION

    for i in range(n):
        if i % 500000 == 0 and i > 0:
            el = time.time() - t0
            eq = eq_at(bal, lvs if act else [], c[i])
            print(f"  {i/n*100:.1f}% {el:.0f}s eq=${eq:.2f} t={len(trades)} lq={len(liqs)}")

        ci = b2c[i]; ib = (i == bounds[ci])
        if i >= sim_start_idx:
            mk = track(i, eq_at(bal, lvs if act else [], c[i]))
        else:
            mk = None

        if act:
            tq = sum(x.qty for x in lvs)
            bl = sum(x.qty * x.price for x in lvs) / tq
            tn = sum(x.notional for x in lvs)

            w = l[i] if dr == 'long' else h[i]  # worst
            b = h[i] if dr == 'long' else l[i]  # best

            # Liquidation
            upnl_w = tq * (w - bl)
            if bal + upnl_w <= tn * MAINT_RATE:
                tp = pd.Timestamp(ts[i])
                t = Trade(dr, fav, pd.Timestamp(ts[lvs[0].idx]), tp,
                         len(lvs), lvs[0].price, bl, w, 'LIQ', -bal, efee, bal, 0)
                trades.append(t); liqs.append(t)
                if mk: mo[mk]["n"] += 1
                bal = RESET_CAPITAL; act = False; lvs = []; efee = 0; cd4 = ci + COOLDOWN
                continue

            # Grid fills
            if len(lvs) < NUM_LEVELS:
                for li in range(len(lvs), NUM_LEVELS):
                    df_pct = drops[li - 1]
                    p1 = lvs[0].price
                    if dr == 'long':
                        ft = p1 * (1 - df_pct); hit = l[i] <= ft
                    else:
                        ft = p1 * (1 + df_pct); hit = h[i] >= ft
                    if hit:
                        sl = SLIP_TICKS * TICK_SZ
                        fp = (ft - sl) if dr == 'long' else (ft + sl)
                        nt = lvs[0].notional * NOT_MULTS[li]
                        qt = nt / fp if dr == 'long' else -nt / fp
                        f = fee_entry(nt, False)
                        bal -= f; efee += f
                        lvs.append(Lv(li+1, fp, nt, qt, i))
                        tq = sum(x.qty for x in lvs)
                        bl = sum(x.qty * x.price for x in lvs) / tq
                        tn = sum(x.notional for x in lvs)
                        break

            # TP
            if dr == 'long':
                tp_px = bl * (1 + TP_PCT); tp_hit = b >= tp_px
            else:
                tp_px = bl * (1 - TP_PCT); tp_hit = b <= tp_px

            if tp_hit:
                sl = SLIP_TICKS * TICK_SZ
                ep = (tp_px - sl) if dr == 'long' else (tp_px + sl)
                xf = fee_exit(tn, True)
                hm = i - lvs[0].idx
                fund = tn * FUNDING_8H * (hm / (8*60))
                gp = tq * (ep - bl)
                np_ = gp - xf - fund
                bal += gp - xf - fund
                tf = efee + xf + fund
                tp2 = pd.Timestamp(ts[i])
                t = Trade(dr, fav, pd.Timestamp(ts[lvs[0].idx]), tp2,
                         len(lvs), lvs[0].price, bl, ep, 'TP', np_, tf, bal-np_, bal)
                trades.append(t)
                if mk: mo[mk]["n"] += 1
                act = False; lvs = []; efee = 0; cd4 = ci + COOLDOWN
                continue

            # Timeout
            if ib and (ci - e4) >= mh:
                ep = c[i]
                sl = SLIP_TICKS * TICK_SZ
                ep = (ep - sl) if dr == 'long' else (ep + sl)
                xf = fee_exit(tn, False)
                hm = i - lvs[0].idx
                fund = tn * FUNDING_8H * (hm / (8*60))
                gp = tq * (ep - bl)
                np_ = gp - xf - fund
                bal += gp - xf - fund
                tf = efee + xf + fund
                tp2 = pd.Timestamp(ts[i])
                t = Trade(dr, fav, pd.Timestamp(ts[lvs[0].idx]), tp2,
                         len(lvs), lvs[0].price, bl, ep, 'TIMEOUT', np_, tf, bal-np_, bal)
                trades.append(t)
                if mk: mo[mk]["n"] += 1
                act = False; lvs = []; efee = 0; cd4 = ci + COOLDOWN
                continue

        # ── ENTRY ──
        if ib and not act:
            if i < sim_start_idx: continue  # don't trade before sim start
            if ci < cd4 or bal < MIN_EQ or ci < 1: continue
            pc = ci - 1
            if pc >= len(ema_v): continue
            ev, sv = ema_v[pc], sma_v[pc]
            if np.isnan(ev) or np.isnan(sv): continue
            px = c[i]

            tp2 = pd.Timestamp(ts[i])
            dts = np.datetime64(tp2.normalize().asm8)
            s4 = s440d.get(dts)
            if s4 is None or np.isnan(s4):
                s4 = s440d.get(dts - np.timedelta64(1,'D'))
            if s4 is None or (isinstance(s4, float) and np.isnan(s4)): continue

            bull = px > s4
            pbe = (ev - px) / ev; pbs = (sv - px) / sv
            pae = (px - ev) / ev; pas = (px - sv) / sv

            entered = False
            # LONG
            lf = bull
            tr = LONG_TRIG if lf else LONG_TRIG * UNFAV_TRIG
            rk = RISK_PCT if lf else RISK_PCT * UNFAV_RISK
            lv = LEV_LONG
            hd = MAX_HOLD if lf else int(MAX_HOLD * UNFAV_HOLD)
            gp = LEVEL_GAPS if lf else [g * UNFAV_SPACE for g in LEVEL_GAPS]

            if pbe >= tr and pbs >= tr:
                sl = SLIP_TICKS * TICK_SZ; ep = px + sl
                nt = rk * bal * lv; qt = nt / ep
                f = fee_entry(nt, True); bal -= f
                lvs = [Lv(1, ep, nt, qt, i)]
                dr = 'long'; fav = lf; drops = cum_drops(gp); mh = hd; e4 = ci; efee = f
                act = True; entered = True

            if not entered:
                sf = not bull
                tr = SHORT_TRIG if sf else SHORT_TRIG * UNFAV_TRIG
                rk = RISK_PCT if sf else RISK_PCT * UNFAV_RISK
                lv = LEV_SHORT
                hd = MAX_HOLD if sf else int(MAX_HOLD * UNFAV_HOLD)
                gp = LEVEL_GAPS if sf else [g * UNFAV_SPACE for g in LEVEL_GAPS]

                if pae >= tr and pas >= tr:
                    sl = SLIP_TICKS * TICK_SZ; ep = px - sl
                    nt = rk * bal * lv; qt = -nt / ep
                    f = fee_entry(nt, True); bal -= f
                    lvs = [Lv(1, ep, nt, qt, i)]
                    dr = 'short'; fav = sf; drops = cum_drops(gp); mh = hd; e4 = ci; efee = f
                    act = True

    # Close open
    if act and lvs:
        tq = sum(x.qty for x in lvs)
        bl = sum(x.qty * x.price for x in lvs) / tq
        tn = sum(x.notional for x in lvs)
        ep = c[-1]
        xf = fee_exit(tn, False)
        hm = n-1-lvs[0].idx; fund = tn * FUNDING_8H * (hm/(8*60))
        gp = tq*(ep-bl); bal += gp-xf-fund
        tp2 = pd.Timestamp(ts[-1])
        t = Trade(dr, fav, pd.Timestamp(ts[lvs[0].idx]), tp2,
                 len(lvs), lvs[0].price, bl, ep, 'EOD', gp-xf-fund, efee+xf+fund, bal-(gp-xf-fund), bal)
        trades.append(t); mk2=(tp2.year,tp2.month)
        if mk2 in mo: mo[mk2]['n'] += 1

    print(f"Done {time.time()-t0:.1f}s | {len(trades)} trades | {len(liqs)} liqs")
    return bal, trades, liqs, mo


def report(eq, trades, liqs, mo):
    print("\n" + "=" * 90)
    print("  MRM v2.7 BACKTEST VALIDATION")
    print(f"  ${INITIAL_CAPITAL:.0f} start | slip={SLIP_TICKS}t | taker={TAKER_FEE*100:.4f}% maker={MAKER_FEE*100:.4f}% comm={COMMISSION*100:.3f}%")
    print("=" * 90)

    tot = len(trades)
    tp = [t for t in trades if t.reason == 'TP']
    to = [t for t in trades if t.reason == 'TIMEOUT']
    lq = [t for t in trades if t.reason == 'LIQ']

    print(f"\n  TRADES: {tot}")
    if tot:
        print(f"    TP:       {len(tp)} ({len(tp)/tot*100:.1f}%)")
        print(f"    Timeout:  {len(to)}")
        print(f"    Liqs:     {len(lq)}")
        if tp:
            w = sum(1 for t in tp if t.pnl > 0)
            print(f"    WinRate:  {w}/{len(tp)} ({w/len(tp)*100:.1f}%)")
            print(f"    AvgTP$:   ${np.mean([t.pnl for t in tp]):.2f}")

    lo = [t for t in trades if t.dir=='long']
    sh = [t for t in trades if t.dir=='short']
    fa = [t for t in trades if t.fav]
    uf = [t for t in trades if not t.fav]
    print(f"    Long={len(lo)} Short={len(sh)} Fav={len(fa)} Unfav={len(uf)}")

    ld = {}
    for t in trades: ld[t.n_levels] = ld.get(t.n_levels, 0) + 1
    for lv in sorted(ld): print(f"    L{lv}: {ld[lv]} ({ld[lv]/tot*100:.1f}%)")

    print(f"\n  LIQUIDATIONS ({len(lq)}):")
    for lt in lq:
        print(f"    {lt.t_entry} -> {lt.t_exit} | {lt.dir} {'F' if lt.fav else 'U'} L{lt.n_levels} | lost=${-lt.pnl:,.0f}")
    if not lq: print("    NONE — zero liquidations!")

    print(f"\n  EQUITY: ${INITIAL_CAPITAL:,.0f} -> ${eq:,.2f}")
    if tot and eq > 0:
        f = trades[0].t_entry; la = trades[-1].t_exit
        y = (la-f).total_seconds()/(365.25*86400)
        if y > 0:
            cagr = (eq/INITIAL_CAPITAL)**(1/y)-1
            print(f"    CAGR: {cagr*100:.1f}% ({y:.2f}y)")

    # Max DD
    eqs = [INITIAL_CAPITAL]
    for t in trades:
        if t.reason == 'LIQ': eqs.append(RESET_CAPITAL)
        else: eqs.append(t.eq_after)
    ea = np.array(eqs); pk = np.maximum.accumulate(ea)
    dd = (ea - pk) / pk
    print(f"    MaxDD: {dd.min()*100:.1f}%")
    print(f"    Fees:  ${sum(t.fees for t in trades):,.2f}")

    # Monthly table
    print(f"\n  {'Month':<10} {'EndEquity':>14} {'Ret%':>9} {'Trades':>7} {'MaxDD%':>9}")
    print(f"  {'-'*10} {'-'*14} {'-'*9} {'-'*7} {'-'*9}")
    for ym in sorted(mo):
        d = mo[ym]
        s, e = d['s'], d['e']
        r = (e/s-1)*100 if s > 0 else 0
        mdd = (d['tr']/d['pk']-1)*100 if d['pk'] > 0 else 0
        print(f"  {ym[0]}-{ym[1]:02d}    ${e:>13,.2f} {r:>8.2f}% {d['n']:>7} {mdd:>8.2f}%")

    # Published comparison (2022+ only)
    print(f"\n  PUBLISHED v2.7 (2022-2026): CAGR=117.7% | $1K->$26,863 | 983 trades | 0 liqs")


if __name__ == '__main__':
    DP = 'signals/multi_asset_results/btcusdt_binance_1m_2017_2026.parquet'
    start = sys.argv[1] if len(sys.argv) > 1 else '2017-01-01'
    eq, tr, lq, mo = run(DP, start)
    report(eq, tr, lq, mo)
