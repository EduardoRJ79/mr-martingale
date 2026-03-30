"""
Grid Bot Configuration — v3.0 strategy
All tunable parameters in one place.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path.home() / ".openclaw" / "ws-731228" / ".secrets" / "hyperliquid.env")

# ─── Bot version ───────────────────────────────────────────────────────────
BOT_VERSION      = "3.0.0"

# ─── Hyperliquid credentials ───────────────────────────────────────────────
HL_PRIVATE_KEY   = os.environ["HL_PRIVATE_KEY"]
HL_MAIN_ADDRESS  = os.environ["HL_MAIN_ADDRESS"]
HL_TESTNET       = False

# ─── Asset ────────────────────────────────────────────────────────────────
COIN             = "BTC"
CANDLE_INTERVAL  = "4h"
POLL_SECONDS     = 300
SZ_DECIMALS      = 5

# ─── Indicator parameters ────────────────────────────────────────────────
EMA_SPAN         = 34       # EMA34 (4H closes)
MA_PERIOD        = 14       # SMA14 (4H closes)
EMA20_SPAN       = 20       # EMA20 (4H closes) — new gate
SMA440_SPAN      = 440      # SMA440 (daily closes) — regime filter
HIGH_20D_BARS    = 120      # rolling max of 4H highs (120 bars = 20 days)
RSI_PERIOD       = 14       # Wilder RSI on 4H closes

# ─── Entry triggers ──────────────────────────────────────────────────────
LONG_TRIGGER_PCT  = 0.5     # v28 gate: % below EMA34 AND SMA14
EMA20_TRIGGER_PCT = 2.0     # ema20 gate: % below EMA20
SHORT_TRIGGER_PCT = 8.0     # % above EMA34 AND SMA14 (was 2.5 in v1.3.2)

# ─── Regime scaling (unfavored) ──────────────────────────────────────────
UNFAV_RISK_SCALE    = 0.60
UNFAV_SPACING_SCALE = 1.60
UNFAV_TRIGGER_SCALE = 3.0
UNFAV_HOLD_SCALE    = 0.45

# ─── Filters ─────────────────────────────────────────────────────────────
DD20D_THRESHOLD      = -0.10   # drawdown from 20-day high to block entry
RSI_RESCUE_THRESHOLD = 30      # RSI(14) <= 30 rescues blocked entries

# ─── Grid parameters ──────────────────────────────────────────────────────
INITIAL_EQUITY_USD = 400.0
NUM_LEVELS       = 5
LEVERAGE         = 20
SHORT_LEVERAGE   = 15

# v3.0 position sizing (replaces BASE_MARGIN_PCT + MULTIPLIER)
RISK_PCT         = 0.50              # L1 notional = risk_pct × balance (favored)
RESCUE_RISK_PCT  = 0.28              # L1 notional when RSI-rescued
LEVEL_MULTS_SEQ  = [2.0, 2.5, 2.5, 7.0]  # L2=2x, L3=5x, L4=12.5x, L5=87.5x of L1

# Per-level gaps from previous level (%) — v3.0 values
LEVEL_GAPS       = [0.5, 1.5, 10.0, 14.0]

# Take profit: % from blended entry
TP_PCT           = 0.5

# ─── Timeout (4H bars) ───────────────────────────────────────────────────
MAX_HOLD_BARS    = 720   # 720 × 4H = 120 days (favored)
                          # × 0.45 = 324 bars = 54 days (unfavored)

# ─── Paper trade mode ─────────────────────────────────────────────────────
PAPER_TRADE      = False

# ─── Fees ─────────────────────────────────────────────────────────────────
TAKER_FEE        = 0.000432
MAKER_FEE        = 0.000144

# ─── Notifications ────────────────────────────────────────────────────────
DISCORD_WEBHOOK  = os.environ.get("DISCORD_WEBHOOK", "")
DISCORD_CHANNEL  = "1474189306536001659"

# ─── State file ───────────────────────────────────────────────────────────
STATE_FILE       = Path(__file__).parent / "grid_state.json"

# ─── Derived ──────────────────────────────────────────────────────────────
CUM_DROPS = []
_acc = 0.0
for g in LEVEL_GAPS:
    _acc += g
    CUM_DROPS.append(_acc / 100)
