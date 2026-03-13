# Mr. Martingale — Live Performance Report
**Generated:** 2026-02-25 17:26 MST  
**Bot live since:** 2026-02-23 (first trigger: 2026-02-22 00:44 MST)  
**Period:** 2.4 days

---

## 📊 Account Summary

| Item | Value |
|---|---|
| Initial Equity | $400.00 |
| Current Realized Balance | $418.52 |
| Open Short Margin In Use | $20.10 |
| Unrealized PnL (SHORT L1+L2) | −$0.77 |
| **Estimated Total Equity** | **$417.75** |

---

## 📈 Return Metrics

| Metric | Value |
|---|---|
| Period | 2.4 days (Feb 23 – Feb 25, 2026) |
| Total Realized PnL | **+$20.40** |
| **TWRR (realized)** | **+4.630%** |
| TWRR Annualized ⚠️ | ~90,700%/yr *(2.4d extrapolation — not meaningful)* |
| Daily Avg Return | +1.909%/day |

> ⚠️ Annualized TWRR is mathematically valid but statistically meaningless over 2.4 days. It's included for completeness only.

---

## 🏆 Closed Trade Log (10 trades)

| # | Date | Side | Level | PnL | Hold |
|---|---|---|---|---|---|
| 1 | 2026-02-23 | LONG | L1 | +$1.99 | 17.2h |
| 2 | 2026-02-23 | LONG | L2 | +$1.92 | 2.8h |
| 3 | 2026-02-24 | LONG | L3 | **+$4.50** | 18.2h |
| 4 | 2026-02-24 | LONG | L2 | +$1.95 | 0.9h |
| 5 | 2026-02-24 | LONG | L2 | +$1.96 | 1.6h |
| 6 | 2026-02-24 | LONG | L1 | +$0.65 | 0.3h |
| 7 | 2026-02-24 | LONG | L2 | +$1.97 | 6.0h |
| 8 | 2026-02-25 | SHORT | L3 | **+$3.46** | 6.6h |
| 9 | 2026-02-25 | SHORT | L2 | +$1.50 | 0.6h |
| 10 | 2026-02-25 | SHORT | L1 | +$0.50 | 0.1h |
| **TOTAL** | | | | **+$20.40** | **54.3h** |

---

## 📐 Trade Statistics

| Metric | Value |
|---|---|
| Total Trades | 10 |
| Win / Loss | **10 / 0** (100% win rate ✅) |
| Avg PnL / Trade | +$2.04 |
| Avg Hold Time | 5.4h (0.2d) |
| Best Trade | +$4.50 (T3, L3 long, 18.2h) |
| Smallest Win | +$0.50 (T10, L1 short, 0.1h) |
| Long trades | 7 → +$14.94 |
| Short trades | 3 → +$5.46 |

---

## 🔄 TWRR Sub-Period Breakdown

| Trade | Start Bal | End Bal | Sub-Return | Cumulative TWRR |
|---|---|---|---|---|
| T1 | $400.00 | $401.76 | +0.440% | +0.440% |
| T2 | $401.76 | $403.40 | +0.408% | +0.850% |
| T3 | $403.40 | $407.47 | +1.009% | +1.867% |
| T4 | $407.47 | $409.26 | +0.439% | +2.315% |
| T5 | $409.26 | $411.06 | +0.440% | +2.765% |
| T6 | $411.06 | $411.64 | +0.141% | +2.910% |
| T7 | $411.64 | $413.49 | +0.449% | +3.372% |
| T8 | $413.49 | $416.70 | +0.776% | +4.175% |
| T9 | $416.70 | $418.08 | +0.331% | +4.520% |
| T10 | $418.08 | $418.52 | +0.105% | **+4.630%** |

> TWRR = HPR in this case — no external cash flows during the period.

---

## ₿ BTC Buy-Hold Comparison

| | Mr. Martingale | BTC Buy-Hold |
|---|---|---|
| Entry | $400.00 equity | $67,426.5/BTC |
| Current | $418.52 (realized) | $68,281.5/BTC |
| Return | **+4.63%** | +1.27% |
| Absolute PnL | **+$20.40** | +$5.07 |
| **Alpha** | **+3.36 pp** | — |

⚡ **Martingale outperformed BTC buy-hold by +$15.33 absolute in 2.4 days**

---

## ⚠️ Open Position

| Detail | Value |
|---|---|
| Strategy | SHORT grid (L1 + L2 filled) |
| Blended entry | $68,107.34 |
| Total qty | 0.00443 BTC |
| Margin in use | $20.10 (15x leverage) |
| Current BTC | $68,281.5 |
| Unrealized PnL | −$0.77 |
| TP target | $67,766.8 |
| Pending levels | L3 @ $69,242 / L4 @ $71,279 / L5 @ $73,315 |

---

## 📝 Methodology Notes
- **TWRR** = Time-Weighted Rate of Return. Equal to HPR here because there were no external deposits/withdrawals during the period.
- **Annualized TWRR**: Computed as `(1 + TWRR)^(365/days) - 1`. Mathematically correct but not meaningful over < 30 days.
- **PnL figures** are net of all fees (maker 0.0144% / taker 0.0432%) as reflected in realized balance changes.
- **L-level notation**: L1 = smallest (1.6% of account), L2 = 2×, L3 = 4×, etc. Higher level = more margin deployed = deeper grid.
- **Equity curve chart**: `mr_martingale_equity_curve.png` (same directory)
