"""
Grid Backtest: $200 vs $500 Account Comparison
Runs the same strategy with two capital levels and reports side-by-side.

$200 config: BASE_MARGIN = 3.2, ACCOUNT = 200
$500 config: BASE_MARGIN = 8.0, ACCOUNT = 500

Both use: 5 levels, 2x multiplier, variable spacing [0.5, 1.5, 3.0, 3.0], 20x leverage
"""

import pandas as pd
import numpy as np
import gzip, csv
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data" / "historical"

# ─── Shared Strategy Params ────────────────────────────────────────────────
LEVERAGE           = 20
NUM_LEVELS         = 5
MULTIPLIER         = 2.0
LEVEL_GAPS         = [0.5, 1.5, 3.0, 3.0]
TRIGGER_PCT        = 0.5
TP_PCT             = 0.5
MAINT_MARGIN_RATE  = 0.005
FUNDING_PER_8H_PCT = 0.0013
TAKER_FEE          = 0.000432
MAKER_FEE          = 0.000144
MAX_HOLD_BARS      = 30
COOLDOWN_BARS      = 1

# Cumulative drops from L1 trigger
CUM_DROPS = []
acc = 0.0
for g in LEVEL_GAPS:
    acc += g
    CUM_DROPS.append(acc / 100)

# ─── Configs to compare ────────────────────────────────────────────────────
CONFIGS = [
    {"label": "$200 account / $3.2 base", "account": 200.0, "base_margin": 3.2},
    {"label": "$500 account / $8.0 base", "account": 500.0, "base_margin": 8.0},
]

# ─── Data Structures ───────────────────────────────────────────────────────
@dataclass
class Position:
    level:      int
    entry:      float
    margin:     float
    notional:   float
    btc_qty:    float
    bar_opened: int

@dataclass
class Cycle:
    start_bar:      int
    start_price:    float
    positions:      List[Position] = field(default_factory=list)
    blended_entry:  float = 0.0
    exit_price:     float = 0.0
    exit_bar:       int   = 0
    exit_reason:    str   = ""
    pnl:            float = 0.0
    funding_cost:   float = 0.0
    fee_cost:       float = 0.0
    max_levels_hit: int   = 0

# ─── Helpers ───────────────────────────────────────────────────────────────
def load_candles():
    path = DATA_DIR / "candles_BTC_4h.csv.gz"
    rows = []
    with gzip.open(path, 'rt') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    df = pd.DataFrame(rows)
    df['time']  = pd.to_datetime(df['open_time_ms'].astype(float), unit='ms')
    df['close'] = df['close'].astype(float)
    df['high']  = df['high'].astype(float)
    df['low']   = df['low'].astype(float)
    df = df.sort_values('time').reset_index(drop=True)
    df['ema34'] = df['close'].ewm(span=34, adjust=False).mean()
    df['ma21']  = df['close'].rolling(21).mean()
    df['pct_below_ema34'] = (df['ema34'] - df['close']) / df['ema34'] * 100
    df['pct_below_ma21']  = (df['ma21']  - df['close']) / df['ma21']  * 100
    return df.dropna(subset=['ema34','ma21']).reset_index(drop=True)

def blended_entry(positions):
    tc = sum(p.btc_qty * p.entry for p in positions)
    tq = sum(p.btc_qty for p in positions)
    return tc / tq if tq else 0.0

def unrealized_pnl(positions, price):
    return sum(p.btc_qty * (price - p.entry) for p in positions)

def calc_funding(positions, bars_held):
    return sum(p.notional for p in positions) * (FUNDING_PER_8H_PCT / 100) * (bars_held / 2)

def level_params(level_idx, entry_price, base_margin):
    margin   = base_margin * (MULTIPLIER ** level_idx)
    notional = margin * LEVERAGE
    btc_qty  = notional / entry_price
    return margin, notional, btc_qty

def calc_fees(positions, exit_price):
    """L1 entry = taker, L2-L5 = maker; TP exit = maker"""
    fee = 0.0
    for i, p in enumerate(positions):
        entry_rate = TAKER_FEE if i == 0 else MAKER_FEE
        fee += p.notional * entry_rate         # entry fee
        fee += p.btc_qty * exit_price * MAKER_FEE  # exit fee (maker TP)
    return fee

# ─── Backtester ────────────────────────────────────────────────────────────
def run_backtest(account_usd, base_margin):
    df      = load_candles()
    n       = len(df)
    account = account_usd
    cycles  = []
    current = None
    last_exit_bar = -99
    peak_account  = account_usd
    max_drawdown  = 0.0

    for i in range(n):
        row = df.iloc[i]
        hi, lo, cl = row['high'], row['low'], row['close']

        if current is None:
            if i - last_exit_bar < COOLDOWN_BARS:
                continue
            if (row['pct_below_ema34'] >= TRIGGER_PCT and
                    row['pct_below_ma21'] >= TRIGGER_PCT):
                m, n_, q = level_params(0, cl, base_margin)
                p = Position(level=1, entry=cl, margin=m, notional=n_,
                             btc_qty=q, bar_opened=i)
                current = Cycle(start_bar=i, start_price=cl,
                                blended_entry=cl, max_levels_hit=1)
                current.positions.append(p)
            continue

        positions  = current.positions
        levels_hit = len(positions)

        # Fill next levels
        for lvl_idx in range(levels_hit, NUM_LEVELS):
            target = current.start_price * (1 - CUM_DROPS[lvl_idx - 1])
            if lo <= target:
                m, n_, q = level_params(lvl_idx, target, base_margin)
                p = Position(level=lvl_idx+1, entry=target, margin=m,
                             notional=n_, btc_qty=q, bar_opened=i)
                positions.append(p)
                current.max_levels_hit = lvl_idx + 1
                break

        be             = blended_entry(positions)
        current.blended_entry  = be
        total_notional = sum(p.notional for p in positions)
        bars_held      = i - current.start_bar

        # Liquidation check
        equity = account + unrealized_pnl(positions, lo)
        maint  = total_notional * MAINT_MARGIN_RATE
        if equity <= maint:
            fc   = calc_funding(positions, bars_held)
            fee  = calc_fees(positions, lo)
            pnl  = -sum(p.margin for p in positions) - fc - fee
            current.exit_price = lo; current.exit_bar = i
            current.exit_reason = "LIQUIDATED"
            current.pnl = pnl; current.funding_cost = fc; current.fee_cost = fee
            account += pnl
            cycles.append(current); current = None; last_exit_bar = i
            continue

        # Take-profit check
        tp_price = be * (1 + TP_PCT / 100)
        if hi >= tp_price:
            fc    = calc_funding(positions, bars_held)
            fee   = calc_fees(positions, tp_price)
            gross = unrealized_pnl(positions, tp_price)
            pnl   = gross - fc - fee
            current.exit_price = tp_price; current.exit_bar = i
            current.exit_reason = "TP_HIT"
            current.pnl = pnl; current.funding_cost = fc; current.fee_cost = fee
            account += pnl
            cycles.append(current); current = None; last_exit_bar = i
            # track peak/drawdown
            if account > peak_account:
                peak_account = account
            dd = (peak_account - account) / peak_account * 100
            if dd > max_drawdown:
                max_drawdown = dd
            continue

        # Timeout
        if bars_held >= MAX_HOLD_BARS:
            fc    = calc_funding(positions, bars_held)
            fee   = calc_fees(positions, cl)
            gross = unrealized_pnl(positions, cl)
            pnl   = gross - fc - fee
            current.exit_price = cl; current.exit_bar = i
            current.exit_reason = "TIMEOUT"
            current.pnl = pnl; current.funding_cost = fc; current.fee_cost = fee
            account += pnl
            cycles.append(current); current = None; last_exit_bar = i

        # Track peak / drawdown continuously
        if account > peak_account:
            peak_account = account
        dd = (peak_account - account) / peak_account * 100
        if dd > max_drawdown:
            max_drawdown = dd

    # Close open cycle at end
    if current is not None:
        positions  = current.positions
        bars_held  = n - 1 - current.start_bar
        fc         = calc_funding(positions, bars_held)
        fee        = calc_fees(positions, df.iloc[-1]['close'])
        gross      = unrealized_pnl(positions, df.iloc[-1]['close'])
        pnl        = gross - fc - fee
        current.exit_price = df.iloc[-1]['close']; current.exit_bar = n-1
        current.exit_reason = "END_OF_DATA"
        current.pnl = pnl; current.funding_cost = fc; current.fee_cost = fee
        account += pnl
        cycles.append(current)

    return cycles, df, account, max_drawdown

# ─── Report ────────────────────────────────────────────────────────────────
def report(label, account_usd, base_margin, cycles, df, final_account, max_dd):
    won     = [c for c in cycles if c.exit_reason == "TP_HIT"]
    lost    = [c for c in cycles if c.exit_reason == "LIQUIDATED"]
    timeout = [c for c in cycles if c.exit_reason == "TIMEOUT"]
    months  = (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30
    total_pnl = sum(c.pnl for c in cycles)
    total_fees = sum(c.fee_cost for c in cycles)
    total_funding = sum(c.funding_cost for c in cycles)

    # Max margin at risk = L5 fill for that config
    max_margin = base_margin * (2**0 + 2**1 + 2**2 + 2**3 + 2**4)
    max_exposure_pct = max_margin / account_usd * 100

    # Level distribution
    ldist = {}
    for c in won:
        ldist[c.max_levels_hit] = ldist.get(c.max_levels_hit, 0) + 1

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Base margin: ${base_margin:.1f} | Max deployed: ${max_margin:.0f} ({max_exposure_pct:.0f}% of account)")
    print(f"  Period: {df['time'].iloc[0].date()} → {df['time'].iloc[-1].date()}")
    print(f"{'='*60}")
    print(f"  Cycles: {len(cycles)}  |  TP: {len(won)} ({len(won)/len(cycles)*100:.0f}%)  |  Liq: {len(lost)}  |  Timeout: {len(timeout)}")
    print(f"  Start: ${account_usd:.0f}  →  End: ${final_account:.2f}  ({total_pnl/account_usd*100:+.1f}%)")
    print(f"  Monthly profit: ${total_pnl/months:+.2f}/mo  |  Cycles/mo: {len(cycles)/months:.1f}")
    print(f"  Max drawdown: {max_dd:.1f}%")
    print(f"  Total fees: ${total_fees:.2f}  |  Total funding: ${total_funding:.2f}")

    if won:
        avg_profit = np.mean([c.pnl for c in won])
        avg_bars = np.mean([c.exit_bar - c.start_bar for c in won])
        print(f"  Avg profit/cycle: ${avg_profit:.2f}  |  Avg hold: {avg_bars*4:.0f}h")
        print(f"  Level fills (TP exits):")
        for lvl in sorted(ldist):
            pct = ldist[lvl]/len(won)*100
            bar = '█' * int(pct/2)
            print(f"    L{lvl}: {bar} {ldist[lvl]} ({pct:.0f}%)")

    if lost:
        print(f"  ⚠️  LIQUIDATIONS: {len(lost)}")
        for c in lost:
            drop = (c.start_price - c.exit_price) / c.start_price * 100
            date = df.iloc[c.start_bar]['time'].strftime('%Y-%m-%d')
            print(f"    {date}  {drop:.1f}% drop from ${c.start_price:,.0f} → L{c.max_levels_hit}")

    # Annualized return
    years = months / 12
    ann_return = ((final_account / account_usd) ** (1/years) - 1) * 100 if years > 0 else 0
    print(f"  Annualized return: {ann_return:.1f}%")


# ─── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔍 GRID BOT BACKTEST: $200 vs $500 ACCOUNT COMPARISON")
    print("Strategy: BTC/USDC | 5L | 2x | Variable spacing [0.5, 1.5, 3.0, 3.0] | 20x | 4H")
    print("Includes: fees (0.0432% taker / 0.0144% maker) + funding costs\n")

    all_results = []
    df_ref = None

    for cfg in CONFIGS:
        cycles, df, final, max_dd = run_backtest(cfg["account"], cfg["base_margin"])
        if df_ref is None:
            df_ref = df
        report(cfg["label"], cfg["account"], cfg["base_margin"], cycles, df, final, max_dd)
        all_results.append({
            "label": cfg["label"],
            "account": cfg["account"],
            "base_margin": cfg["base_margin"],
            "cycles": cycles,
            "final": final,
            "max_dd": max_dd,
            "months": (df['time'].iloc[-1] - df['time'].iloc[0]).days / 30,
        })

    # Summary comparison
    print(f"\n{'='*60}")
    print("  SIDE-BY-SIDE SUMMARY")
    print(f"{'='*60}")
    for r in all_results:
        won = [c for c in r["cycles"] if c.exit_reason == "TP_HIT"]
        total_pnl = sum(c.pnl for c in r["cycles"])
        months = r["months"]
        print(f"\n  {r['label']}:")
        print(f"    Return: ${r['account']:.0f} → ${r['final']:.2f} ({total_pnl/r['account']*100:+.1f}%)")
        print(f"    Monthly: ${total_pnl/months:+.2f}/mo")
        print(f"    Max DD: {r['max_dd']:.1f}%")
        print(f"    Liquidations: {sum(1 for c in r['cycles'] if c.exit_reason == 'LIQUIDATED')}")

    print(f"\n  RETURN ON CAPITAL (% basis, normalized):")
    base_pct = None
    for r in all_results:
        total_pnl = sum(c.pnl for c in r["cycles"])
        pct = total_pnl / r["account"] * 100
        if base_pct is None:
            base_pct = pct
        diff = pct - base_pct
        print(f"    {r['label']}: {pct:+.1f}% ({diff:+.1f}% vs baseline)")
