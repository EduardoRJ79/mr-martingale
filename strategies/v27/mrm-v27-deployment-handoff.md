# MRM v2.7 — Deployment Handoff for Mac Mini / Openclaw

**Date:** 2026-03-26
**Target:** Mac Mini running Openclaw with Hyperliquid API connectivity
**Status:** Ready for testnet paper trading

---

## Quick Start

```bash
pip install hyperliquid-python-sdk eth-account pandas numpy
```

Create `config.json`:
```json
{
    "secret_key": "0xYOUR_HYPERLIQUID_API_PRIVATE_KEY",
    "account_address": "0xYOUR_MAIN_WALLET_PUBLIC_ADDRESS"
}
```

```bash
python mrm_v27_bot.py --testnet   # testnet first
python mrm_v27_bot.py             # mainnet
python mrm_v27_bot.py --dry-run   # signals only
```

---

## v2.7 Parameters — 4 Changes from v2.6

| Parameter | v2.6 | **v2.7** | Why |
|-----------|------|----------|-----|
| risk_pct | 0.22 | **0.30** | +20 CAGR pts. Cliff at 0.35. |
| unfav_trigger_scale | 2.0 | **3.0** | +30 CAGR pts. Filters bad shorts. |
| max_hold_bars | 360 (60d) | **720 (120d)** | Deep trades recover. |
| level_gaps | [0.5,1.5,9.0,15.0] | **[0.5,1.5,7.0,8.0]** | Tighter L4. +9 CAGR pts. |

### Complete Parameter Table

| Parameter | Value |
|-----------|-------|
| **risk_pct** | **0.30** |
| tp_pct | 0.50% |
| t2_pct | 0% |
| num_levels | 5 |
| **level_gaps** | **[0.5%, 1.5%, 7.0%, 8.0%]** |
| level_multipliers | [2.0, 2.5, 2.5, 7.0] |
| dma_period | 440 |
| ema_span | 34 (4h) |
| sma_span | 14 (4h) |
| **max_hold_bars** | **720 (120 days)** |
| long_trigger_pct | 0.5% |
| short_trigger_pct | 1.5% |
| leverage_long | 20x |
| leverage_short | 15x |
| unfav_risk_scale | 0.60 |
| unfav_spacing_scale | 1.60 |
| **unfav_trigger_scale** | **3.00** |
| unfav_hold_scale | 0.45 |
| cooldown | 1 bar (4h) |
| min_equity | $50 |

### Grid Structure (v2.7)

| Level | Gap | Cumulative | Notional |
|-------|-----|------------|----------|
| L1 | 0% | 0% | 1.0x |
| L2 | 0.5% | 0.5% | 2.0x |
| L3 | 1.5% | 2.0% | 5.0x |
| L4 | **7.0%** | **9.0%** | 12.5x |
| L5 | **8.0%** | **17.0%** | 87.5x |

### CRITICAL: Do Not Cross

| Parameter | Safe | Cliff | Consequence |
|-----------|------|-------|-------------|
| risk_pct | **0.30** | 0.35 | Liqs 2025-11-21, 2026-02-05 |
| unfav_trigger_scale | **>= 2.0** | < 2.0 | Liq 2024-02-28 |
| level_gaps[2] (L4) | **7.0-9.0%** | <= 6% or >= 10% | Liqs in 2023 or 2025 |
| tp_pct | **0.50%** | >= 0.52% | CAGR drops to 23% |

---

## Migration from v2.6

Update these 4 constants in the bot code:

```python
RISK_PCT = 0.30            # was 0.22
UNFAV_TRIGGER_SCALE = 3.0  # was 2.0
MAX_HOLD_BARS = 720        # was 360
LEVEL_GAPS = [0.5, 1.5, 7.0, 8.0]  # was [0.5, 1.5, 9.0, 15.0]
```

Delete old state file, rename to `mrm_v27_bot.py`, test on testnet.

---

## Expected Performance (1m backtest, 2022-2026)

| Metric | Value |
|--------|-------|
| **CAGR** | **117.7%** |
| Win Rate | ~99% |
| Max Drawdown | 93.7% |
| Liquidations | **0** |
| Trades/year | ~231 |
| Final equity | $26,863 (from $1K, 4.25 years) |

### Events Survived

- 2022-06 Luna/FTX ($30K -> $17K)
- 2023-01 Rally squeeze ($19K -> $23K)
- 2024-02-28 Flash crash
- 2024-08 Japan carry trade unwind
- 2025-11-21 Crash (kills risk=0.35, survives risk=0.30)
- 2026-02-05 Crash (kills risk=0.35, survives risk=0.30)

---

## Architecture (unchanged)

1. Main loop wakes at every 4h candle close (UTC 00/04/08/12/16/20)
2. Indicators: EMA34, SMA14, SMA440 regime
3. Entry: departure from MAs vs trigger. **Unfavored shorts need 4.5% departure** (1.5% x 3.0)
4. Grid: L1-L5 with tighter spacing (17% cumulative vs 26%)
5. TP at 0.50% from blended entry
6. Timeout at **120 days** (unfavored: 54 days)
7. State persistence to JSON after every event

---

## Testnet Checklist

- [ ] Bot connects and fetches candles/equity
- [ ] Indicators match TradingView EMA34/SMA14
- [ ] Entry signal fires when expected
- [ ] **Unfavored short requires 4.5% departure** (not 3.0% as in v2.6)
- [ ] L4 placed at **9.0% cumulative** from entry (not 11%)
- [ ] L5 placed at **17.0% cumulative** from entry (not 26%)
- [ ] TP at 0.50%
- [ ] **risk_pct = 0.30** (L1 notional = 30% of equity)
- [ ] **Timeout at 720 bars** (120 days)
- [ ] State persistence and recovery works

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v2.5 | 2026-03-24 | TP=0.84%, risk=0.22 |
| v2.6 | 2026-03-25 | TP=0.50%, UTS=2.0, hold=360, gaps=[0.5,1.5,9.0,15.0] |
| **v2.7** | **2026-03-26** | **risk=0.30, UTS=3.0, hold=720, gaps=[0.5,1.5,7.0,8.0]. 117.7% CAGR, 0 liqs** |
