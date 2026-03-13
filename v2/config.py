"""
Mr Martingale v2 — Configuration
=================================
Paper-trade ONLY. True compounding, no stop-loss, 440 SMA soft-bias regime filter.

Key v2 parameters (from MR_MARTINGALE_V2_SPEC.md + research reports):
  - true compounding: base_notional = RISK_PCT × equity at each grid open
  - no stop-loss: spacing + regime filter are the only risk controls
  - 5 levels with late_expand spacing: [0.5, 1.5, 8.0, 7.0]
    Cumulative depths: L2=0.5%, L3=2.0%, L4=10.0%, L5=17.0%
  - 400-day SMA soft-bias regime filter: favored side full strength, unfavored side degraded not disabled
  - convex per-step multipliers: [1.5, 2.0, 3.0, 5.0]
  - risk_pct 25% (L1 notional = 25% of current equity)
  - max_hold_bars 160 (160 × 4h ≈ 26.7 days)

Spacing interpretation (late_expand):
  "late_expand" means the grid stays tight early (normal mean-reversion fills)
  and expands massively at L3→L4 and L4→L5. This was resolved from:
  - asymmetric_compounding_sweep.py: best zero-liq config = [8,7] (L3→L4=8%, L4→L5=7%)
  - L4/L5 sweep report: [8,7] = best PnL at zero liquidations (+277%)
  - Fixed early levels L1→L2=0.5%, L2→L3=1.5% from v1 baseline
  Full level_gaps = [0.5, 1.5, 9.0, 6.0]

DO NOT MODIFY execution/ — v1 live bot is untouched.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load credentials from same secrets file as v1 (read-only for market data)
_SECRETS = Path.home() / ".openclaw" / "ws-731228" / ".secrets" / "hyperliquid.env"
if _SECRETS.exists():
    load_dotenv(_SECRETS)

# ─── Version / identity ────────────────────────────────────────────────────
BOT_VERSION = "3.0.0-paper"
BOT_NAME    = "MrM-v3.0"

# ─── ALWAYS paper trade — this flag must NEVER be False in v2 ──────────────
PAPER_TRADE = True

# ─── Asset ────────────────────────────────────────────────────────────────
COIN            = "BTC"
CANDLE_INTERVAL = "4h"
SZ_DECIMALS     = 5
POLL_SECONDS    = 300        # 5 min between polls

# ─── Short-term MA / trigger ──────────────────────────────────────────────
EMA_SPAN          = 34
MA_PERIOD         = 14       # SMA14
LONG_TRIGGER_PCT  = 0.5      # % price below both MAs → open long
SHORT_TRIGGER_PCT = 1.5      # % price above both MAs -> open short

# ─── 400-day SMA regime filter ────────────────────────────────────────────
REGIME_MA_PERIOD = 440       # 440-day SMA
REGIME_INTERVAL  = "1d"      # daily candles
REGIME_FETCH_N   = 480       # fetch buffer (430 to ensure 400+ closes)

# Soft-bias regime model (v2.1)
REGIME_MODE = "soft"        # "soft" or "hard"
UNFAV_RISK_SCALE    = 0.65   # 45% of normal risk on the unfavored side
UNFAV_SPACING_SCALE = 1.50   # widen spacing 2.4x on the unfavored side
UNFAV_EDGE_K_SCALE  = 1.50   # require 2x stronger dislocation to enter
UNFAV_HOLD_SCALE    = 0.50   # shorter max hold on unfavored side

# ─── True compounding ─────────────────────────────────────────────────────
# L1 notional = RISK_PCT × equity  (NOT margin)
# L1 margin   = L1_notional / leverage
# Example: equity=$400, RISK_PCT=0.30 → L1 notional=$120 → margin=$6 at 20×
INITIAL_EQUITY = 1000.0
RISK_PCT       = 0.25        # 25% of current equity -> L1 notional

# ─── Ladder ───────────────────────────────────────────────────────────────
NUM_LEVELS = 5

# Convex per-step multipliers: L2=L1×1.5, L3=L2×2.0, L4=L3×3.0, L5=L4×5.0
LEVEL_MULTIPLIERS = [2.0, 2.5, 2.5, 7.0]   # 4 multipliers for 5 levels

# late_expand spacing: [L1→L2, L2→L3, L3→L4, L4→L5] gap percentages
# Resolved from: asymmetric_compounding_sweep [8,7] best zero-liq result
# + fixed L1→L2=0.5%, L2→L3=1.5% from v1 baseline (per l4l5_spacing_sweep)
# Cumulative: L2=0.5%, L3=2.0%, L4=10.0%, L5=17.0% from trigger
LEVEL_GAPS = [0.5, 1.5, 9.0, 6.0]   # late_expand profile

# ─── Leverage ─────────────────────────────────────────────────────────────
LEVERAGE_LONG  = 20
LEVERAGE_SHORT = 15

# ─── Take profit ──────────────────────────────────────────────────────────
TP_PCT = 0.5   # 0.5% from blended entry price

# ─── Max hold (timeout) ───────────────────────────────────────────────────
MAX_HOLD_BARS = 160   # 160 x 4h ~ 26.7 days; force-close (timeout exit)

# ─── Fees ─────────────────────────────────────────────────────────────────
TAKER_FEE = 0.000432   # 0.0432%
MAKER_FEE  = 0.000144  # 0.0144%

# ─── Notifications ────────────────────────────────────────────────────────
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

# ─── Paths ────────────────────────────────────────────────────────────────
V2_DIR     = Path(__file__).parent
STATE_DIR  = V2_DIR / "state"
LOG_DIR    = V2_DIR / "logs"
STATE_FILE = STATE_DIR / "v2_paper_state.json"
LOG_FILE   = LOG_DIR / "v2_paper_bot.log"

# ─── Derived: cumulative drop depths ──────────────────────────────────────
CUM_DROPS = []
_acc = 0.0
for g in LEVEL_GAPS:
    _acc += g
    CUM_DROPS.append(_acc / 100.0)
