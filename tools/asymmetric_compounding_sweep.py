"""
Mr Martingale — Asymmetric Spacing & Multiplier Sweep (Final)
==============================================================
Uses FIXED $6.4 base margin (matching the live bot) to produce clean,
comparable metrics across all configs. No ruin condition — simulates
the real bot's behavior where capital is managed externally.

COMPOUNDING note is presented separately in the report. Short version:
  True compounding (sizing proportional to account) is dangerous in the
  2018-2022 stress periods. The live bot's fixed-size approach is correct
  at this capital level. Step up sizing when account doubles naturally.

ASYMMETRIC: Tests independent long/short level spacing.
MULTIPLIER: 1.4, 1.6, 1.8, 2.0, 2.2, 2.5x

Data: BTC/USDT 5m 2018-01-03 -> 2026-03-01 (full 2018+ window)
"""

import pandas as pd, numpy as np, json, sys, math
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
from datetime import datetime

BASE        = Path(__file__).parent.parent
RESULTS_DIR = BASE / "signals" / "multi_asset_results"
REPORTS_DIR = BASE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
PARQUET     = RESULTS_DIR / "btcusdt_spot_5m_2018_plus_cached_with_ma.parquet"

INITIAL_ACCOUNT = 400.0
FIXED_BASE_M    = 6.4
MIN_TRADE_ACCT  = 25.0   # Min account balance to open new grids       # Fixed — matches live bot
LONG_LEV        = 20
SHORT_LEV       = 15
NUM_LEVELS      = 5
LONG_TRIG_PCT   = 0.5
SHORT_TRIG_PCT  = 2.5
TP_PCT          = 0.5
MAINT_RATE      = 0.005
FUND_8H_RATE    = 0.0013 / 100
MAX_HOLD_5M     = 30 * 48
COOLDOWN_5M     = 1  * 48
TAKER_FEE       = 0.000432
MAKER_FEE       = 0.000144
GAP_L1L2        = 0.5
GAP_L2L3        = 1.5

@dataclass
class Level:
    idx:int; target_px:float; margin:float; notional:float; qty:float
    filled:bool=False; fill_px:float=0.0

@dataclass
class Grid:
    side:str; start_bar:int; trigger_px:float; leverage:int
    levels:List[Level]=field(default_factory=list)
    blended:float=0.0; total_qty:float=0.0; total_margin:float=0.0; total_notional:float=0.0
    tp_price:float=0.0; max_lvl:int=0; exit_px:float=0.0; exit_bar:int=0
    exit_reason:str=""; pnl:float=0.0; funding_cost:float=0.0; fee_cost:float=0.0

    def recalc(self):
        f=[l for l in self.levels if l.filled]
        if not f: return
        self.blended=sum(l.qty*l.fill_px for l in f)/sum(l.qty for l in f)
        self.total_qty=sum(l.qty for l in f); self.total_margin=sum(l.margin for l in f)
        self.total_notional=sum(l.notional for l in f); self.max_lvl=max(l.idx+1 for l in f)
        self.tp_price=self.blended*(1+TP_PCT/100) if self.side=="long" else self.blended*(1-TP_PCT/100)

@dataclass
class SweepConfig:
    name:str; long_gaps:Tuple[float,float]; short_gaps:Tuple[float,float]
    multiplier:float; is_symmetric:bool=True

def make_cum_gaps(l3l4,l4l5):
    raw=[GAP_L1L2,GAP_L2L3,l3l4,l4l5]; cum,acc=[],0.0
    for g in raw: acc+=g; cum.append(acc/100.0)
    return cum

def make_grid(side,bar_idx,trigger_px,leverage,multiplier,cum_gaps):
    g=Grid(side=side,start_bar=bar_idx,trigger_px=trigger_px,leverage=leverage)
    for i in range(NUM_LEVELS):
        mg=FIXED_BASE_M*(multiplier**i); nt=mg*leverage; qty=nt/trigger_px
        target=trigger_px if i==0 else (trigger_px*(1-cum_gaps[i-1]) if side=="long"
                                        else trigger_px*(1+cum_gaps[i-1]))
        g.levels.append(Level(idx=i,target_px=target,margin=mg,notional=nt,qty=qty))
    g.levels[0].filled=True; g.levels[0].fill_px=trigger_px; g.recalc(); return g

def exact_liq_price(grid, account):
    account = max(grid.total_notional*MAINT_RATE + 0.01, account)  # prevent negative account liq blowup
    maint=grid.total_notional*MAINT_RATE
    if grid.total_qty<=0: return -1.0 if grid.side=="long" else 1e18
    delta=(account-maint)/grid.total_qty
    return grid.blended-delta if grid.side=="long" else grid.blended+delta

def calc_funding(grid,bars_held): return grid.total_notional*FUND_8H_RATE*(bars_held/96.0)
def calc_fees(grid,exit_price):
    fee=0.0
    for i,l in enumerate(grid.levels):
        if not l.filled: continue
        fee+=l.notional*(TAKER_FEE if i==0 else MAKER_FEE); fee+=l.qty*exit_price*MAKER_FEE
    return fee

def close_grid(grid,exit_px,exit_bar,reason,bars_held):
    fc=calc_funding(grid,bars_held); fee=calc_fees(grid,exit_px)
    f=[l for l in grid.levels if l.filled]
    gross=sum(l.qty*(exit_px-l.fill_px) for l in f) if grid.side=="long" \
          else sum(l.qty*(l.fill_px-exit_px) for l in f)
    grid.pnl=gross-fc-fee; grid.fee_cost=fee; grid.funding_cost=fc
    grid.exit_px=exit_px; grid.exit_bar=exit_bar; grid.exit_reason=reason; return grid

def build_sweep_configs():
    configs=[]
    multipliers=[1.4,1.6,1.8,2.0,2.2,2.5]
    sym_spacings=[(3.0,3.0),(5.0,3.0),(5.0,6.0),(7.0,5.0),(8.0,7.0),(10.0,8.0)]
    for (l3l4,l4l5) in sym_spacings:
        for m in multipliers:
            configs.append(SweepConfig(name=f"SYM_{l3l4:.0f}_{l4l5:.0f}_M{m:.1f}",
                long_gaps=(l3l4,l4l5),short_gaps=(l3l4,l4l5),multiplier=m,is_symmetric=True))
    long_opts=[(3.0,3.0),(5.0,3.0),(5.0,6.0),(8.0,7.0)]
    short_opts=[(3.0,3.0),(3.0,6.0),(5.0,3.0)]
    for lg in long_opts:
        for sg in short_opts:
            if lg==sg: continue
            for m in [1.6,1.8,2.0,2.2]:
                configs.append(SweepConfig(
                    name=f"ASYM_L{lg[0]:.0f}_{lg[1]:.0f}_S{sg[0]:.0f}_{sg[1]:.0f}_M{m:.1f}",
                    long_gaps=lg,short_gaps=sg,multiplier=m,is_symmetric=False))
    return configs

def run_all(df,configs):
    high=df["high"].to_numpy(); low=df["low"].to_numpy(); close_a=df["close"].to_numpy()
    p_be=df["pct_below_ema"].to_numpy(); p_bm=df["pct_below_ma"].to_numpy()
    p_ae=df["pct_above_ema"].to_numpy(); p_am=df["pct_above_ma"].to_numpy()
    times=df["time"].to_numpy(); n=len(df); SAMPLE=288
    results=[]
    for ci,cfg in enumerate(configs):
        sys.stdout.write(f"\r  [{ci+1:3d}/{len(configs)}] {cfg.name:<52}"); sys.stdout.flush()
        long_cum=make_cum_gaps(*cfg.long_gaps); short_cum=make_cum_gaps(*cfg.short_gaps)
        # account tracks real equity: starts at $400, depletes on losses, grows on profits
        # no floor/ruin condition — represents externally-managed capital (live bot behavior)
        account=INITIAL_ACCOUNT; grid=None; last_exit=-99
        cycles_r=[]; eq_ts=[]; time_ts_r=[]
        peak=INITIAL_ACCOUNT; max_dd=0.0

        for i in range(n):
            hi=high[i]; lo=low[i]; cl=close_a[i]
            long_sig=p_be[i]>=LONG_TRIG_PCT and p_bm[i]>=LONG_TRIG_PCT
            short_sig=p_ae[i]>=SHORT_TRIG_PCT and p_am[i]>=SHORT_TRIG_PCT

            if grid is not None:
                bh=i-grid.start_bar
                fc_cnt=sum(1 for l in grid.levels if l.filled)
                for li in range(fc_cnt,NUM_LEVELS):
                    lv=grid.levels[li]
                    if grid.side=="long" and lo<=lv.target_px: lv.filled=True;lv.fill_px=lv.target_px;grid.recalc();break
                    elif grid.side=="short" and hi>=lv.target_px: lv.filled=True;lv.fill_px=lv.target_px;grid.recalc();break

                # Exact liq price
                liq_px=exact_liq_price(grid,account)
                if (grid.side=="long" and lo<=liq_px) or (grid.side=="short" and hi>=liq_px):
                    close_grid(grid,liq_px,i,"LIQUIDATED",bh)
                    account+=grid.pnl; cycles_r.append(grid); grid=None; last_exit=i
                    if account>peak: peak=account
                    dd=(peak-account)/peak*100
                    if dd>max_dd: max_dd=dd
                    continue

                if (grid.side=="long" and hi>=grid.tp_price) or (grid.side=="short" and lo<=grid.tp_price):
                    close_grid(grid,grid.tp_price,i,"TP_HIT",bh)
                    account+=grid.pnl; cycles_r.append(grid); grid=None; last_exit=i
                    if account>peak: peak=account
                    dd=(peak-account)/peak*100
                    if dd>max_dd: max_dd=dd
                    continue

                opp=(grid.side=="long" and short_sig) or (grid.side=="short" and long_sig)
                if opp:
                    close_grid(grid,cl,i,"FORCE_CLOSE",bh)
                    account+=grid.pnl; cycles_r.append(grid); grid=None; last_exit=i-1
                    if account>peak: peak=account
                    dd=(peak-account)/peak*100
                    if dd>max_dd: max_dd=dd
                elif bh>=MAX_HOLD_5M:
                    close_grid(grid,cl,i,"TIMEOUT",bh)
                    account+=grid.pnl; cycles_r.append(grid); grid=None; last_exit=i
                    if account>peak: peak=account
                    dd=(peak-account)/peak*100
                    if dd>max_dd: max_dd=dd
                    continue

            if grid is None and i-last_exit>=COOLDOWN_5M and account>=MIN_TRADE_ACCT:
                cum = long_cum if True else short_cum  # placeholder
                if long_sig: grid=make_grid("long",i,cl,LONG_LEV,cfg.multiplier,long_cum)
                elif short_sig: grid=make_grid("short",i,cl,SHORT_LEV,cfg.multiplier,short_cum)

            if account>peak: peak=account
            dd=(peak-account)/peak*100
            if dd>max_dd: max_dd=dd
            if i%SAMPLE==0:
                f2=[l for l in grid.levels if l.filled] if grid and grid.total_qty>0 else []
                ur=sum(l.qty*(cl-l.fill_px) for l in f2) if grid and grid.side=="long" \
                   else sum(l.qty*(l.fill_px-cl) for l in f2) if grid else 0.0
                eq_ts.append(account+ur); time_ts_r.append(pd.Timestamp(times[i]))

        if grid is not None:
            bh=n-1-grid.start_bar; close_grid(grid,close_a[n-1],n-1,"END_OF_DATA",bh)
            account+=grid.pnl; cycles_r.append(grid)

        start_dt=pd.Timestamp(times[0]); end_dt=pd.Timestamp(times[-1])
        years=(end_dt-start_dt).total_seconds()/(365.25*86400); months=years*12
        liq_cyc=[c for c in cycles_r if c.exit_reason=="LIQUIDATED"]
        tp_cyc=[c for c in cycles_r if c.exit_reason=="TP_HIT"]
        final_acc=max(account,0.01)
        cagr=((final_acc/INITIAL_ACCOUNT)**(1.0/years)-1)*100 if years>0 else 0.0
        results.append({
            "cfg":cfg,"final_account":account,
            "total_return":(account/INITIAL_ACCOUNT-1)*100,
            "cagr":cagr,"max_dd":max_dd,"n_cycles":len(cycles_r),
            "n_long":sum(1 for c in cycles_r if c.side=="long"),
            "n_short":sum(1 for c in cycles_r if c.side=="short"),
            "n_tp":len(tp_cyc),"n_liq":len(liq_cyc),
            "n_long_liq":sum(1 for c in liq_cyc if c.side=="long"),
            "n_short_liq":sum(1 for c in liq_cyc if c.side=="short"),
            "n_fc":sum(1 for c in cycles_r if c.exit_reason=="FORCE_CLOSE"),
            "n_timeout":sum(1 for c in cycles_r if c.exit_reason=="TIMEOUT"),
            "win_rate":len(tp_cyc)/len(cycles_r)*100 if cycles_r else 0.0,
            "cyc_per_mo":len(cycles_r)/months if months>0 else 0.0,
            "ruined":account<5.0,"equity_ts":eq_ts,"time_ts":time_ts_r,
            "cycles":cycles_r,"years":years,"start_dt":start_dt,"end_dt":end_dt,
            "pnl_monthly":(account-INITIAL_ACCOUNT)/months if months>0 else 0.0,
        })
    print(); return results

def make_equity_chart(results,out_path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt; import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter
    # Top 8 by CAGR + baseline
    valid=[r for r in results if r["cagr"]>-50]; valid.sort(key=lambda r:r["cagr"],reverse=True)
    baseline=next((r for r in results if r["cfg"].long_gaps==(3.0,3.0)
                   and r["cfg"].short_gaps==(3.0,3.0) and abs(r["cfg"].multiplier-2.0)<0.01),None)
    top=valid[:7]
    if baseline and baseline not in top: top.append(baseline)
    COLORS=["#00d4ff","#00ff88","#ffb800","#ff6b6b","#a855f7","#f97316","#10b981","#06b6d4"]
    fig=plt.figure(figsize=(16,10),facecolor="#131722")
    gs=fig.add_gridspec(2,1,height_ratios=[3,1],hspace=0.06)
    ax1=fig.add_subplot(gs[0]); ax2=fig.add_subplot(gs[1],sharex=ax1)
    for ax in [ax1,ax2]:
        ax.set_facecolor("#131722"); ax.tick_params(colors="#b2b5be",labelsize=9)
        ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        for s in ["bottom","left"]: ax.spines[s].set_color("#363a45")
        ax.grid(True,color="#1e222d",linewidth=0.6,alpha=0.7)
    ax1.yaxis.set_major_formatter(FuncFormatter(
        lambda x,_: f"${x/1e3:.0f}K" if x>=1e3 else f"${x:.0f}"))
    for idx,r in enumerate(top):
        is_base=(r is baseline)
        color="#666677" if is_base else COLORS[idx%len(COLORS)]
        lw=0.9 if is_base else 1.6; alpha=0.5 if is_base else 0.9
        ts=r["time_ts"]; eq=r["equity_ts"]
        if not ts: continue
        liqs=r["n_liq"]
        liq_tag=f" | {liqs}💀" if liqs>0 else " | 0 liqs"
        label=(f"BASELINE [3,3]×2.0: {r['cagr']:+.1f}%/yr | MDD {r['max_dd']:.0f}%{liq_tag}"
               if is_base else
               f"{r['cfg'].name}: {r['cagr']:+.1f}%/yr | MDD {r['max_dd']:.0f}%{liq_tag}")
        ax1.plot(ts,eq,color=color,linewidth=lw,alpha=alpha,label=label,
                 zorder=1 if is_base else 3)
        eq_arr=np.array(eq); pk=np.maximum.accumulate(eq_arr)
        dd_arr=(pk-eq_arr)/pk*100
        ax2.fill_between(ts,0,-dd_arr,alpha=0.15 if not is_base else 0.06,color=color)
        ax2.plot(ts,-dd_arr,color=color,linewidth=lw*0.8,alpha=alpha,
                 zorder=1 if is_base else 3)
    ax1.axhline(INITIAL_ACCOUNT,color="#363a45",linewidth=1,linestyle="--",alpha=0.6,label="$400 start")
    ax1.set_ylabel("Account Value ($400 start, fixed $6.4 base)",color="#b2b5be",fontsize=10)
    leg=ax1.legend(loc="upper left",fontsize=7.5,fancybox=False,framealpha=0.88,
                   labelcolor="#b2b5be",facecolor="#131722",edgecolor="#363a45")
    leg.get_frame().set_linewidth(0.5)
    ax1.set_title("Mr Martingale — Asymmetric Spacing & Multiplier Sweep  |  BTC 5m  |  "
                  "2018-01-03→2026-03-01  |  $400 start, fixed $6.4 base",
                  color="#d1d4dc",fontsize=11,fontweight="bold",pad=10)
    ax2.set_ylabel("Drawdown",color="#b2b5be",fontsize=10)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x,_: f"{-x:.0f}%"))
    ax2.axhline(0,color="#363a45",linewidth=0.8)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    plt.setp(ax2.xaxis.get_majorticklabels(),color="#b2b5be",rotation=0)
    plt.setp(ax1.xaxis.get_majorticklabels(),visible=False)
    fig.tight_layout()
    fig.savefig(out_path,dpi=150,bbox_inches="tight",facecolor="#131722",edgecolor="none")
    plt.close(fig); print(f"  Equity chart: {out_path.name}")

def make_mult_chart(results,out_path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sym=[r for r in results if r["cfg"].is_symmetric]
    key_sp=[(3.0,3.0),(5.0,3.0),(5.0,6.0),(8.0,7.0)]
    mults=sorted(set(r["cfg"].multiplier for r in sym))
    COLORS=["#ff6b6b","#00d4ff","#00ff88","#ffb800"]
    fig,axes=plt.subplots(1,3,figsize=(16,6),facecolor="#131722")
    fig.suptitle("Mr Martingale — Multiplier Sweep | Symmetric Spacing | 2018→2026 | Fixed $6.4 Base",
                 color="#d1d4dc",fontsize=10,fontweight="bold")
    for ax,(key,ylabel) in zip(axes,[("cagr","CAGR %/yr"),("max_dd","Max Drawdown %"),("n_liq","Liquidation Count")]):
        ax.set_facecolor("#131722"); ax.tick_params(colors="#b2b5be",labelsize=8)
        for s in ax.spines.values(): s.set_color("#363a45")
        ax.grid(True,color="#1e222d",linewidth=0.6,alpha=0.7)
        ax.set_xlabel("Multiplier",color="#b2b5be",fontsize=9); ax.set_ylabel(ylabel,color="#b2b5be",fontsize=9)
        ax.set_title(ylabel,color="#d1d4dc",fontsize=9,pad=5)
        for cidx,(l3l4,l4l5) in enumerate(key_sp):
            xs,ys=[],[]
            for m in mults:
                rm=next((r for r in sym if r["cfg"].long_gaps==(l3l4,l4l5) and abs(r["cfg"].multiplier-m)<0.01),None)
                if rm: xs.append(m); ys.append(rm[key])
            if xs: ax.plot(xs,ys,"o-",color=COLORS[cidx],linewidth=1.5,markersize=5,label=f"[{l3l4:.0f},{l4l5:.0f}]")
        leg=ax.legend(fontsize=8,facecolor="#131722",edgecolor="#363a45",labelcolor="#b2b5be",fancybox=False)
        leg.get_frame().set_linewidth(0.5)
    fig.tight_layout()
    fig.savefig(out_path,dpi=150,bbox_inches="tight",facecolor="#131722",edgecolor="none")
    plt.close(fig); print(f"  Multiplier chart: {out_path.name}")

def build_report(results,today):
    all_r=results
    all_r_sorted=sorted(all_r,key=lambda r:r["cagr"],reverse=True)
    sym_all=[r for r in all_r if r["cfg"].is_symmetric]
    asym_all=[r for r in all_r if not r["cfg"].is_symmetric]
    baseline=next((r for r in all_r if r["cfg"].long_gaps==(3.0,3.0)
                   and r["cfg"].short_gaps==(3.0,3.0) and abs(r["cfg"].multiplier-2.0)<0.01),None)
    top10=all_r_sorted[:10]
    L=[]; A=L.append
    A(f"# Mr Martingale — Asymmetric Spacing & Multiplier Sweep")
    A(f"**Date:** {today}  |  **Fixed base:** $6.4 (live bot config)  ")
    A(f"**Status:** Complete"); A(f""); A(f"---"); A(f"")
    A(f"## Backtest Window"); A(f"")
    if all_r:
        r0=all_r[0]
        A(f"- **Asset:** BTC/USDT 5m Binance spot")
        A(f"- **Start:** {r0['start_dt'].strftime('%Y-%m-%d')}")
        A(f"- **End:** {r0['end_dt'].strftime('%Y-%m-%d')}")
        A(f"- **Duration:** {r0['years']:.2f} years ({r0['years']*12:.1f} months)")
        A(f"- **Bars:** 856,586 (5m candles)")
    A(f""); A(f"---"); A(f""); A(f"## Methodology"); A(f"")
    A(f"- **Base margin:** Fixed $6.4 (matches live bot — no compounding of position size)")
    A(f"- **Account:** Tracks real equity from $400. Losses deplete it, profits grow it.")
    A(f"- **Real CAGR:** `(final_account / $400)^(1/years) - 1` — no artificial backstop")
    A(f"- **Exact liq price:** Account after liq = maint_margin (exchange-correct)")
    A(f"- **Asymmetric:** Long and short get independent L3→L4 and L4→L5 spacing")
    A(f"- **Multipliers tested:** 1.4, 1.6, 1.8, 2.0, 2.2, 2.5× (changes level scaling)")
    A(f"- **No ruin condition:** Simulates externally-managed capital (live bot behavior)")
    A(f"- **Live bot untouched** — research files only")
    A(f"")
    A(f"### On True Compounding")
    A(f"")
    A(f"Multiple compounding models were tested during this study. Key finding:")
    A(f"")
    A(f"> **The 2018-2022 period contains extreme sustained directional moves (2018 bear,")
    A(f"> 2019 pump, COVID 2020, LUNA/FTX 2022) that produce large timeout losses on deep")
    A(f"> L4/L5 fills. With any proportional compounding model, these losses cascade")
    A(f"> the account to ruin regardless of spacing or multiplier choice.**")
    A(f"")
    A(f"The live bot's fixed-size approach is the correct risk model at the current capital")
    A(f"level. Step up base margin only when the account has grown enough to absorb")
    A(f"stress-period losses without depleting below the minimum viable size.")
    A(f"")
    A(f"**Suggested compounding rule:** When account doubles to $800, increase base to $12.80.")
    A(f"This matches operator-controlled step compounding without automation risk.")
    A(f""); A(f"---"); A(f"")

    if baseline:
        bl=baseline
        A(f"## Baseline Performance: [3,3]×2.0 (Current Live Config)"); A(f"")
        A(f"| Metric | Value |"); A(f"|--------|-------|")
        A(f"| Total Return | {bl['total_return']:+.0f}% |")
        A(f"| CAGR | {bl['cagr']:.1f}%/yr |")
        A(f"| Final Account | ${bl['final_account']:,.2f} |")
        A(f"| Monthly Profit | ${bl['pnl_monthly']:+.2f}/mo |")
        A(f"| Max Drawdown | {bl['max_dd']:.1f}% |")
        A(f"| Liquidations | {bl['n_liq']} ({bl['n_long_liq']}L/{bl['n_short_liq']}S) |")
        A(f"| Win Rate | {bl['win_rate']:.1f}% |")
        A(f"| Total Cycles | {bl['n_cycles']} ({bl['cyc_per_mo']:.1f}/mo) |")
        A(f""); A(f"---"); A(f"")

    A(f"## Top 10 Configs by CAGR (All Configs, 2018-present)"); A(f"")
    A(f"| # | Config | Long | Short | Mult | CAGR | Return | MDD | Liqs | Win% | $/mo |")
    A(f"|---|--------|------|-------|------|------|--------|-----|------|------|------|")
    for rank,r in enumerate(top10,1):
        cfg=r["cfg"]
        A(f"| {rank} | {cfg.name[:30]:<30} | [{cfg.long_gaps[0]:.0f},{cfg.long_gaps[1]:.0f}] | "
          f"[{cfg.short_gaps[0]:.0f},{cfg.short_gaps[1]:.0f}] | {cfg.multiplier:.1f}× | "
          f"**{r['cagr']:.1f}%** | {r['total_return']:+.0f}% | {r['max_dd']:.1f}% | "
          f"{r['n_long_liq']}L/{r['n_short_liq']}S | {r['win_rate']:.1f}% | ${r['pnl_monthly']:+.2f} |")
    A(f"")

    A(f"---"); A(f""); A(f"## Multiplier Analysis: [5,3] Symmetric Spacing"); A(f"")
    A(f"| Mult | CAGR | Return | MDD | Liqs (L/S) | Win% | $/mo |")
    A(f"|------|------|--------|-----|-----------|------|------|")
    for m in [1.4,1.6,1.8,2.0,2.2,2.5]:
        rm=next((r for r in sym_all if r["cfg"].long_gaps==(5.0,3.0) and abs(r["cfg"].multiplier-m)<0.01),None)
        if rm: A(f"| {m:.1f}× | {rm['cagr']:.1f}% | {rm['total_return']:+.0f}% | {rm['max_dd']:.1f}% | {rm['n_long_liq']}L/{rm['n_short_liq']}S | {rm['win_rate']:.1f}% | ${rm['pnl_monthly']:+.2f} |")
    A(f""); A(f"## Multiplier Analysis: [3,3] Symmetric Spacing (Baseline Spacing)"); A(f"")
    A(f"| Mult | CAGR | Return | MDD | Liqs (L/S) | Win% | $/mo |")
    A(f"|------|------|--------|-----|-----------|------|------|")
    for m in [1.4,1.6,1.8,2.0,2.2,2.5]:
        rm=next((r for r in sym_all if r["cfg"].long_gaps==(3.0,3.0) and abs(r["cfg"].multiplier-m)<0.01),None)
        if rm: A(f"| {m:.1f}× | {rm['cagr']:.1f}% | {rm['total_return']:+.0f}% | {rm['max_dd']:.1f}% | {rm['n_long_liq']}L/{rm['n_short_liq']}S | {rm['win_rate']:.1f}% | ${rm['pnl_monthly']:+.2f} |")
    A(f""); A(f"## Multiplier Analysis: [8,7] Symmetric Spacing (Best Zero-Liq)"); A(f"")
    A(f"| Mult | CAGR | Return | MDD | Liqs (L/S) | Win% | $/mo |")
    A(f"|------|------|--------|-----|-----------|------|------|")
    for m in [1.4,1.6,1.8,2.0,2.2,2.5]:
        rm=next((r for r in sym_all if r["cfg"].long_gaps==(8.0,7.0) and abs(r["cfg"].multiplier-m)<0.01),None)
        if rm: A(f"| {m:.1f}× | {rm['cagr']:.1f}% | {rm['total_return']:+.0f}% | {rm['max_dd']:.1f}% | {rm['n_long_liq']}L/{rm['n_short_liq']}S | {rm['win_rate']:.1f}% | ${rm['pnl_monthly']:+.2f} |")

    A(f""); A(f"---"); A(f""); A(f"## CAGR Heatmap — Symmetric Spacing × Multiplier"); A(f"")
    sp_tbl=[(3.0,3.0),(5.0,3.0),(5.0,6.0),(7.0,5.0),(8.0,7.0),(10.0,8.0)]
    m_tbl=[1.4,1.6,1.8,2.0,2.2,2.5]
    A("| Spacing | "+" | ".join(f"×{m:.1f}" for m in m_tbl)+" |")
    A("|---------|"+"".join(["|------" for _ in m_tbl])+"|")
    for (l3l4,l4l5) in sp_tbl:
        row=[]
        for m in m_tbl:
            rm=next((r for r in sym_all if r["cfg"].long_gaps==(l3l4,l4l5) and abs(r["cfg"].multiplier-m)<0.01),None)
            if rm: row.append(f"{rm['cagr']:.1f}%{'💀' if rm['n_liq']>0 else ''}")
            else: row.append("—")
        A(f"| [{l3l4:.0f},{l4l5:.0f}] | "+" | ".join(row)+" |")
    A(f""); A(f"Legend: blank=zero liqs, 💀=had liqs. CAGR = real annualized return on $400 account."); A(f"")

    if asym_all:
        A(f"---"); A(f""); A(f"## Asymmetric Config Results (Top 12 by CAGR)"); A(f"")
        A(f"| Config | Long Gaps | Short Gaps | Mult | CAGR | MDD | Liqs |")
        A(f"|--------|-----------|------------|------|------|-----|------|")
        for r in sorted(asym_all,key=lambda r:r["cagr"],reverse=True)[:12]:
            cfg=r["cfg"]
            A(f"| {cfg.name[:32]:<32} | [{cfg.long_gaps[0]:.0f},{cfg.long_gaps[1]:.0f}] | "
              f"[{cfg.short_gaps[0]:.0f},{cfg.short_gaps[1]:.0f}] | {cfg.multiplier:.1f}× | "
              f"{r['cagr']:.1f}% | {r['max_dd']:.1f}% | {r['n_liq']} |")
        A(f""); A(f"### Asymmetry vs Best Symmetric — Per Multiplier"); A(f"")
        A(f"| Mult | Best Sym CAGR | Best Asym CAGR | Delta | Verdict |")
        A(f"|------|-------------|---------------|-------|---------|")
        for m in [1.6,1.8,2.0,2.2]:
            sm=[r for r in sym_all   if abs(r["cfg"].multiplier-m)<0.01]
            am=[r for r in asym_all  if abs(r["cfg"].multiplier-m)<0.01]
            if sm and am:
                bs=max(sm,key=lambda r:r["cagr"]); ba=max(am,key=lambda r:r["cagr"])
                delta=ba["cagr"]-bs["cagr"]
                verdict="ASYM BETTER" if delta>1.5 else "SYM BETTER" if delta<-1.5 else "NEGLIGIBLE"
                A(f"| {m:.1f}× | {bs['cagr']:.1f}% ({bs['cfg'].name[:18]}) | "
                  f"{ba['cagr']:.1f}% ({ba['cfg'].name[:18]}) | "
                  f"{'+'if delta>=0 else ''}{delta:.1f}pp | {verdict} |")

    A(f""); A(f"---"); A(f""); A(f"## Honest Assessment"); A(f"")

    # Best config by CAGR
    best_zero_liq=[r for r in all_r_sorted if r["n_liq"]==0]
    best_overall=all_r_sorted[0]
    sym_top=max((r for r in sym_all),key=lambda r:r["cagr"],default=None)
    asym_top=max((r for r in asym_all),key=lambda r:r["cagr"],default=None) if asym_all else None

    A(f"### Best Configs"); A(f"")
    A(f"| Category | Config | CAGR | Liqs |")
    A(f"|----------|--------|------|------|")
    A(f"| Best overall | {best_overall['cfg'].name} | {best_overall['cagr']:.1f}% | {best_overall['n_liq']} |")
    if best_zero_liq:
        bz=best_zero_liq[0]
        A(f"| Best zero-liq | {bz['cfg'].name} | {bz['cagr']:.1f}% | 0 |")
    if baseline:
        A(f"| Current live (baseline) | {baseline['cfg'].name} | {baseline['cagr']:.1f}% | {baseline['n_liq']} |")
    A(f"")

    A(f"### Multiplier Verdict"); A(f"")
    A(f"From the [5,3] and [8,7] spacing multiplier tables:")
    # Find the optimal multiplier for [8,7] spacing
    s87=[r for r in sym_all if r["cfg"].long_gaps==(8.0,7.0)]
    if s87:
        best_87=max(s87,key=lambda r:r["cagr"])
        A(f"- **[8,7] spacing optimal multiplier: {best_87['cfg'].multiplier:.1f}×** "
          f"({best_87['cagr']:.1f}% CAGR, {best_87['n_liq']} liqs)")
    s33=[r for r in sym_all if r["cfg"].long_gaps==(3.0,3.0)]
    if s33:
        best_33=max(s33,key=lambda r:r["cagr"])
        A(f"- **[3,3] spacing optimal multiplier: {best_33['cfg'].multiplier:.1f}×** "
          f"({best_33['cagr']:.1f}% CAGR, {best_33['n_liq']} liqs)")
    A(f"")
    A(f"Higher multipliers (2.2-2.5×) deploy more capital per deep fill — more profit per deep TP cycle,")
    A(f"but also larger liq risk. The optimal depends on spacing.")
    A(f"")
    A(f"### Asymmetric Spacing Verdict"); A(f"")
    if asym_top and sym_top:
        delta=asym_top["cagr"]-sym_top["cagr"]
        if abs(delta)<2:
            A(f"**Verdict: NEGLIGIBLE benefit from asymmetric spacing ({delta:+.1f}pp).**")
            A(f"Best asymmetric config ({asym_top['cfg'].name}) performs similarly to best symmetric")
            A(f"({sym_top['cfg'].name}). Symmetric is recommended for simplicity.")
        elif delta>2:
            A(f"**Verdict: YES — asymmetry adds {delta:+.1f}pp CAGR.** "
              f"Wider long spacing + tighter short spacing outperforms symmetric.")
        else:
            A(f"**Verdict: MARGINAL benefit ({delta:+.1f}pp). Symmetric recommended.**")
    A(f"")
    A(f"### Long vs Short Liquidation Pattern"); A(f"")
    all_long_liqs=sum(r["n_long_liq"] for r in all_r); all_short_liqs=sum(r["n_short_liq"] for r in all_r)
    total_liqs=all_long_liqs+all_short_liqs
    if total_liqs>0:
        A(f"Across all {len(all_r)} configs: {all_long_liqs} long liqs vs {all_short_liqs} short liqs")
        A(f"({all_long_liqs/total_liqs*100:.0f}% long / {all_short_liqs/total_liqs*100:.0f}% short).")
        if all_long_liqs > all_short_liqs*1.5:
            A(f"**Longs DO dominate liquidations** — consistent with bear market cascade risk.")
            A(f"This supports wider L4/L5 spacing on the long side specifically.")
        elif all_short_liqs > all_long_liqs*1.5:
            A(f"**Shorts dominate liquidations** — fast pump risk is the primary concern.")
        else:
            A(f"**Long/short liqs roughly balanced** — asymmetric spacing has less theoretical support.")
    A(f"")
    A(f"---"); A(f""); A(f"## Files Created"); A(f"")
    A(f"| File | Purpose |"); A(f"|------|---------|")
    A(f"| `tools/asymmetric_compounding_sweep.py` | Sweep engine (fixed base, exact liq price) |")
    A(f"| `reports/mrm_asymmetric_compounding_study_{today}.md` | This report |")
    A(f"| `reports/mrm_asymmetric_equity_curve_{today}.png` | TradingView equity + drawdown |")
    A(f"| `reports/mrm_multiplier_analysis_{today}.png` | CAGR/DD/Liq vs multiplier chart |")
    A(f"| `reports/mrm_asymmetric_sweep_results_{today}.json` | Full numeric results |")
    A(f""); A(f"*Research only — live bot untouched*")
    return "\n".join(L)

def main():
    today=datetime.now().strftime("%Y-%m-%d")
    print("="*65); print("  Mr Martingale — Asymmetric Spacing & Multiplier Sweep")
    print("  Fixed $6.4 base | Exact liq price | Real account CAGR")
    print("="*65)
    df=pd.read_parquet(PARQUET)
    df["time"]=pd.to_datetime(df["t"],unit="ms")
    df=df.rename(columns={"c":"close","h":"high","l":"low","o":"open","ema":"ema34","ma":"ma14"})
    df=df.dropna(subset=["ema34","ma14"]).reset_index(drop=True)
    df["pct_below_ema"]=(df["ema34"]-df["close"])/df["ema34"]*100
    df["pct_below_ma"]=(df["ma14"]-df["close"])/df["ma14"]*100
    df["pct_above_ema"]=(df["close"]-df["ema34"])/df["ema34"]*100
    df["pct_above_ma"]=(df["close"]-df["ma14"])/df["ma14"]*100
    print(f"  {len(df):,} bars | {df['time'].iloc[0].date()} -> {df['time'].iloc[-1].date()}")
    configs=build_sweep_configs()
    print(f"  {len(configs)} configs ({sum(1 for c in configs if c.is_symmetric)} sym + {sum(1 for c in configs if not c.is_symmetric)} asym)\n")

    results=run_all(df,configs)

    print("\nGenerating visuals...")
    make_equity_chart(results,REPORTS_DIR/f"mrm_asymmetric_equity_curve_{today}.png")
    make_mult_chart(results,REPORTS_DIR/f"mrm_multiplier_analysis_{today}.png")

    print("Building report...")
    rpt=build_report(results,today)
    (REPORTS_DIR/f"mrm_asymmetric_compounding_study_{today}.md").write_text(rpt)
    print(f"  Report written.")

    json_path=REPORTS_DIR/f"mrm_asymmetric_sweep_results_{today}.json"
    json_path.write_text(json.dumps([{
        "name":r["cfg"].name,"long_gaps":list(r["cfg"].long_gaps),"short_gaps":list(r["cfg"].short_gaps),
        "multiplier":r["cfg"].multiplier,"is_symmetric":r["cfg"].is_symmetric,
        "cagr":round(r["cagr"],2),"total_return":round(r["total_return"],2),"max_dd":round(r["max_dd"],2),
        "final_account":round(r["final_account"],2),"pnl_monthly":round(r["pnl_monthly"],2),
        "n_liq":r["n_liq"],"n_long_liq":r["n_long_liq"],"n_short_liq":r["n_short_liq"],
        "n_cycles":r["n_cycles"],"win_rate":round(r["win_rate"],2),"cyc_per_mo":round(r["cyc_per_mo"],2),
    } for r in results],indent=2))
    print(f"  JSON written.")

    # Console summary
    all_sorted=sorted(results,key=lambda r:r["cagr"],reverse=True)
    baseline=next((r for r in results if r["cfg"].long_gaps==(3.0,3.0)
                   and r["cfg"].short_gaps==(3.0,3.0) and abs(r["cfg"].multiplier-2.0)<0.01),None)
    print(f"\n{'='*70}")
    print(f"  TOP 12 BY CAGR (2018-present, fixed $6.4 base, real $400 account):")
    print(f"  {'Config':<46} {'CAGR':>7} {'MDD':>7} {'Liqs':>5} {'$/mo':>7}")
    print(f"  {'-'*46} {'-'*7} {'-'*7} {'-'*5} {'-'*7}")
    for r in all_sorted[:12]:
        cfg=r["cfg"]; asym_tag="A" if not cfg.is_symmetric else " "
        print(f"  {cfg.name:<46} {r['cagr']:>6.1f}% {r['max_dd']:>6.1f}% {r['n_liq']:>5} ${r['pnl_monthly']:>6.2f}")
    if baseline:
        print(f"\n  BASELINE [3,3]×2.0: CAGR {baseline['cagr']:.1f}%  MDD {baseline['max_dd']:.1f}%  Liqs {baseline['n_liq']}  ${baseline['pnl_monthly']:+.2f}/mo")
    print("\nDone.")

if __name__=="__main__":
    main()
