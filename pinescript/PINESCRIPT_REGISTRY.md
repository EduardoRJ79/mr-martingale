# PineScript Strategy Registry — Corrected Cross-Asset Results

**Document Version:** 2026-03-27-v1 (Bugs Fixed, Full Re-run)
**Classification:** VW Family Office — Portfolio High Risk — Quantitative Strategies

---

## Executive Summary

Full 7-asset re-run with 4 bugs fixed: zero-drawdown hardcode, PINE-003/004 identical logic, missing fees, Sweet v4.4.4 wrong timeframe. All previous results were unreliable.

### Data Status: COMPLETE
| Asset | 1m Data | Rows | Period | Status |
|:------|:-------:|:----:|:-------|:------:|
| BTC | Yes | 2.1M | 2021-2024 | Complete |
| ETH | Yes | 2.1M | 2021-2024 | Complete |
| DOGE | Yes | 2M | 2021-2024 | Complete |
| SOL | Yes | 2M | 2021-2024 | Complete |
| XRP | Yes | 2M | 2021-2024 | Complete |
| BNB | Yes | 2M | 2021-2024 | Complete |
| LTC | Yes | 2M | 2021-2024 | Complete |

---

## Candidates Passing <35% DD Gate (0 Liquidations)

| Strategy | Asset | Return | Max DD | Win% | Status |
|:---------|:-----:|-------:|-------:|:----:|:-------|
| PINE-003 Swing BTC 4h | BTC | +985% | 29.6% | 6.5% | PAPER CANDIDATE |
| PINE-006 Gaussian V4H | BTC | +875% | 31.4% | 54.5% | PAPER CANDIDATE |

### Borderline (35-37% DD)
| Strategy | Asset | Return | Max DD | Win% | Status |
|:---------|:-----:|-------:|-------:|:----:|:-------|
| PINE-003 Swing BTC 4h | SOL | +1,689% | 36.9% | 6.9% | WATCH |
| PINE-003 Swing BTC 4h | XRP | +132% | 36.7% | 4.5% | WATCH |

---

## Complete Performance Matrix

### PINE-001: Sweet v4.4.4 — REJECTED

Strategy fails on ALL assets after 15m resampling fix. Previous +1440% BTC claim was from buggy code.

| Asset | Return | Max DD | Liqs | Win% | Status |
|:------|-------:|-------:|:----:|:----:|:-------|
| BTC | -99.9% | 99.9% | 0 | 21.1% | FAIL |
| ETH | -99.5% | 99.8% | 0 | 22.7% | FAIL |
| DOGE | -99.9% | 100.0% | 0 | 23.3% | FAIL |
| SOL | -83.7% | 99.8% | 0 | 27.0% | FAIL |
| XRP | -100.0% | 100.0% | 0 | 23.6% | FAIL |
| BNB | -86.4% | 97.8% | 0 | 24.7% | FAIL |
| LTC | -100.0% | 100.0% | 0 | 23.8% | FAIL |

### PINE-003: Swing BTC 4h (EMA 20/50, Swing Breakout, MACD, Volume)

Most consistent strategy — positive on all 7 assets.

| Asset | Return | Max DD | Liqs | Win% | Status |
|:------|-------:|-------:|:----:|:----:|:-------|
| **BTC** | **+985%** | **29.6%** | **0** | **6.5%** | **PAPER CANDIDATE** |
| ETH | +352% | 45.7% | 0 | 5.1% | Over threshold |
| DOGE | +1,446% | 46.7% | 0 | 8.2% | Over threshold |
| **SOL** | **+1,689%** | **36.9%** | **0** | **6.9%** | **Borderline** |
| **XRP** | **+132%** | **36.7%** | **0** | **4.5%** | **Borderline** |
| BNB | +595% | 61.4% | 0 | 6.1% | Over threshold |
| LTC | +831% | 44.9% | 0 | 8.7% | Over threshold |

### PINE-004: Swing ETH 4h (EMA 12/26/50, Bollinger, Stochastic, Pivots)

Mixed results — high returns on some assets but massive drawdowns.

| Asset | Return | Max DD | Liqs | Win% | Status |
|:------|-------:|-------:|:----:|:----:|:-------|
| BTC | +202% | 57.4% | 0 | 36.4% | Over threshold |
| ETH | -81% | 99.1% | 0 | 35.9% | FAIL |
| DOGE | +1,269% | 76.0% | 0 | 39.1% | Over threshold |
| SOL | +29% | 99.0% | 1 | 42.4% | FAIL (liquidation) |
| XRP | +1,040% | 91.8% | 0 | 40.7% | Over threshold |
| BNB | +2,319% | 55.5% | 0 | 37.3% | Over threshold |
| LTC | +65% | 89.0% | 0 | 31.3% | Over threshold |

### PINE-006: Gaussian V4H v4.0

Strong returns, selective (few trades), but high DD on altcoins.

| Asset | Return | Max DD | Liqs | Win% | Status |
|:------|-------:|-------:|:----:|:----:|:-------|
| **BTC** | **+875%** | **31.4%** | **0** | **54.5%** | **PAPER CANDIDATE** |
| ETH | +585% | 66.8% | 0 | 45.7% | Over threshold |
| DOGE | +4,992% | 74.2% | 0 | 51.4% | Over threshold |
| SOL | +7,972% | 70.5% | 0 | 33.3% | Over threshold |
| XRP | +1,381% | 67.1% | 0 | 60.0% | Over threshold |
| BNB | +638% | 59.2% | 0 | 55.2% | Over threshold |
| LTC | +338% | 63.6% | 0 | 34.0% | Over threshold |

---

## Bugs Fixed (2026-03-26/27)

1. **Max drawdown hardcoded to 0** in run_swing_btc_4h and run_gaussian_v4h — neither tracked equity curves
2. **PINE-003 and PINE-004 used identical EMA 9/21 + RSI logic** — now match their actual runner code
3. **Missing fee accounting** in Swing and Gaussian strategies
4. **Sweet v4.4.4 ran on raw 1m bars** instead of resampling to 15m (original PineScript was 15m)

---

## Next Actions

1. Paper trade PINE-003 on BTC and PINE-006 on BTC (the only 2 passing <35% DD)
2. Drop PINE-001 Sweet v4.4.4 from pipeline entirely
3. Investigate if PINE-003 SOL/XRP borderline candidates can be tuned below 35%
4. Consider expanding PINE-003 to additional assets with parameter optimization

---

**Last Updated:** 2026-03-27
**Updated By:** Claude (new session, picking up from Winnie)
**Status:** CORRECTED — Previous results were invalid due to 4 bugs
