"""
Grid Strategy - Position Sizing & Liquidation Math
Account: $200 USDC | Leverage: 20x | Asset: BTC ~$67,000
Cross-margin assumed (whole account backs all positions)
"""

def analyze_grid(
    account_usd=200,
    leverage=20,
    btc_price=67000,
    base_margin=8,          # $ margin for L1
    multiplier=2.0,         # position size multiplier per level
    level_spacing_pct=2.0,  # % drop between levels
    num_levels=4,
    maintenance_margin_rate=0.005,  # 0.5% for BTC on Hyperliquid
    tp_pct=0.5,             # take profit % above blended entry
    funding_per_8h=0.0013,  # current funding rate
):
    print(f"\n{'='*65}")
    print(f"  GRID CONFIG: {num_levels} levels | {multiplier}x multiplier | {level_spacing_pct}% spacing")
    print(f"  Base margin: ${base_margin} | Leverage: {leverage}x | BTC: ${btc_price:,}")
    print(f"{'='*65}")

    levels = []
    total_margin = 0
    total_notional = 0

    for i in range(num_levels):
        margin = base_margin * (multiplier ** i)
        notional = margin * leverage
        btc_qty = notional / btc_price
        entry = btc_price * (1 - level_spacing_pct / 100 * i)
        levels.append({
            'level': i + 1,
            'entry': entry,
            'margin': margin,
            'notional': notional,
            'btc_qty': btc_qty,
            'drop_from_l1': level_spacing_pct * i,
        })
        total_margin += margin
        total_notional += notional

    print(f"\n  LEVELS:")
    print(f"  {'L':<4} {'Entry':>10} {'Drop':>8} {'Margin':>10} {'Notional':>12} {'BTC qty':>10}")
    print(f"  {'-'*60}")
    
    running_notional = 0
    running_qty = 0
    running_cost = 0
    blended_entries = []

    for l in levels:
        running_notional += l['notional']
        running_qty += l['btc_qty']
        running_cost += l['btc_qty'] * l['entry']
        blended = running_cost / running_qty
        blended_entries.append(blended)
        print(f"  L{l['level']:<3} ${l['entry']:>9,.0f} {l['drop_from_l1']:>7.1f}% ${l['margin']:>9,.0f} ${l['notional']:>11,.0f} {l['btc_qty']:>10.5f}")

    print(f"\n  CAPITAL USAGE:")
    print(f"    Total margin deployed:  ${total_margin:,.2f} ({total_margin/account_usd*100:.0f}% of account)")
    print(f"    Total notional:         ${total_notional:,.2f}")
    print(f"    Capital reserve:        ${account_usd - total_margin:,.2f}")
    print(f"    Max leverage effective: {total_notional/account_usd:.1f}x")

    print(f"\n  BLENDED ENTRY BY LEVEL:")
    for i, (l, blended) in enumerate(zip(levels, blended_entries)):
        pct_above_last = (blended - l['entry']) / l['entry'] * 100
        print(f"    After L{i+1}: ${blended:,.0f} (need +{pct_above_last:.2f}% from L{i+1} fill to break even)")

    # Unrealized PnL when deepest level fills
    last_entry = levels[-1]['entry']
    print(f"\n  UNREALIZED PnL WHEN L{num_levels} FILLS (price = ${last_entry:,.0f}):")
    total_pnl = 0
    for l in levels[:-1]:
        pnl = l['btc_qty'] * (last_entry - l['entry'])
        total_pnl += pnl
        print(f"    L{l['level']} PnL: ${pnl:,.2f}")
    print(f"    L{num_levels} PnL: $0.00 (just opened)")
    print(f"    Total unrealized: ${total_pnl:,.2f}")
    
    equity_at_deepest = account_usd + total_pnl
    print(f"    Account equity:   ${equity_at_deepest:,.2f}")

    # Liquidation analysis from deepest level
    maint_req = total_notional * maintenance_margin_rate
    loss_buffer = equity_at_deepest - maint_req
    pct_drop_to_liq = loss_buffer / total_notional * 100
    liq_price = last_entry * (1 - pct_drop_to_liq / 100)
    total_drop_from_l1 = (btc_price - liq_price) / btc_price * 100

    print(f"\n  LIQUIDATION MATH (from L{num_levels} fill price):")
    print(f"    Maintenance margin req:  ${maint_req:,.2f}")
    print(f"    Loss buffer remaining:   ${loss_buffer:,.2f}")
    print(f"    Additional drop to liq:  {pct_drop_to_liq:.2f}%")
    print(f"    Liquidation price:       ${liq_price:,.0f}")
    print(f"    Total drop from L1:      {total_drop_from_l1:.2f}%")

    # Funding cost analysis
    hold_hours = 48  # assume 48h average hold
    funding_periods = hold_hours / 8
    funding_cost = total_notional * (funding_per_8h / 100) * funding_periods
    print(f"\n  FUNDING COST (48h hold, {funding_per_8h}%/8h):")
    print(f"    Est. funding cost:  ${funding_cost:.4f}")
    print(f"    As % of margin:     {funding_cost/total_margin*100:.4f}%")

    # Take profit scenarios
    final_blended = blended_entries[-1]
    tp_price = final_blended * (1 + tp_pct / 100)
    tp_pnl = running_qty * (tp_price - final_blended)
    tp_roi = tp_pnl / total_margin * 100

    print(f"\n  TAKE PROFIT SCENARIO (all {num_levels} levels filled, +{tp_pct}% above blended):")
    print(f"    Blended entry:  ${final_blended:,.0f}")
    print(f"    TP price:       ${tp_price:,.0f}")
    print(f"    Gross PnL:      ${tp_pnl:.4f}")
    print(f"    Net PnL:        ${tp_pnl - funding_cost:.4f} (after funding)")
    print(f"    ROI on margin:  {tp_roi:.2f}%")

    return {
        'levels': levels,
        'total_margin': total_margin,
        'blended_entries': blended_entries,
        'liq_price': liq_price,
        'total_drop_to_liq': total_drop_from_l1,
        'tp_pnl': tp_pnl,
    }


if __name__ == "__main__":
    print("BTC GRID STRATEGY — POSITION SIZING & LIQUIDATION ANALYSIS")
    print("Account: $200 | BTC: ~$67,000 | Leverage: 20x")

    # Option A: Conservative — 3 levels, 2x, 2% spacing
    analyze_grid(base_margin=10, multiplier=2.0, level_spacing_pct=2.0, num_levels=3)

    # Option B: Standard — 4 levels, 2x, 2% spacing
    analyze_grid(base_margin=8, multiplier=2.0, level_spacing_pct=2.0, num_levels=4)

    # Option C: Wider spacing — 4 levels, 2x, 3% spacing
    analyze_grid(base_margin=8, multiplier=2.0, level_spacing_pct=3.0, num_levels=4)

    # Option D: Softer multiplier — 4 levels, 1.5x, 2% spacing
    analyze_grid(base_margin=12, multiplier=1.5, level_spacing_pct=2.0, num_levels=4)
