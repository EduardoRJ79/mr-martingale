"""
Grid Bot Configuration
All tunable parameters in one place.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load secrets
load_dotenv(Path.home() / ".openclaw" / "ws-731228" / ".secrets" / "hyperliquid.env")

# ─── Bot version ───────────────────────────────────────────────────────────
BOT_VERSION      = "1.3.2"

# ─── Hyperliquid credentials ───────────────────────────────────────────────
HL_PRIVATE_KEY   = os.environ["HL_PRIVATE_KEY"]
HL_MAIN_ADDRESS  = os.environ["HL_MAIN_ADDRESS"]
HL_TESTNET       = False   # Unused — paper mode handled by PAPER_TRADE flag below

# ─── Asset ────────────────────────────────────────────────────────────────
COIN             = "BTC"
CANDLE_INTERVAL  = "4h"
POLL_SECONDS     = 300     # 5 minutes between trigger checks
SZ_DECIMALS      = 5       # BTC size precision on Hyperliquid (szDecimals=5)

# ─── MA parameters ────────────────────────────────────────────────────────
EMA_SPAN         = 34
MA_PERIOD        = 14   # SMA14 — best Sharpe per MA optimizer (v1.1)
LONG_TRIGGER_PCT  = 0.5    # % price must be BELOW both MAs to open long grid
SHORT_TRIGGER_PCT = 2.5    # % price must be ABOVE both MAs to open short grid
TRIGGER_PCT       = LONG_TRIGGER_PCT   # alias used by legacy code

# ─── Grid parameters ──────────────────────────────────────────────────────
INITIAL_EQUITY_USD = 400.0
NUM_LEVELS       = 5
BASE_MARGIN_PCT  = 0.016   # L1 margin = 1.6% of account balance (compounds as account grows)
BASE_MARGIN_USD  = 6.4     # reference only — actual margin is dynamic via BASE_MARGIN_PCT
MULTIPLIER       = 2.0     # margin doubles each level
LEVERAGE         = 20      # long-side leverage
SHORT_LEVERAGE   = 15      # short-side leverage (tuned — less aggressive)

# Per-level gaps from previous level (%)
# L1→L2: 0.5% | L2→L3: 1.5% | L3→L4: 3.0% | L4→L5: 3.0%
LEVEL_GAPS       = [0.5, 1.5, 3.0, 3.0]

# Take profit: % from blended entry
TP_PCT           = 0.5

# ─── Paper trade mode ─────────────────────────────────────────────────────
PAPER_TRADE      = False   # LIVE MODE — real orders on Hyperliquid mainnet.

# ─── Risk ─────────────────────────────────────────────────────────────────
MAX_HOLD_HOURS   = 120     # force-close if grid stuck this long (5 days)

# ─── Fees (Brian's actual tier) ───────────────────────────────────────────
TAKER_FEE        = 0.000432   # 0.0432%
MAKER_FEE        = 0.000144   # 0.0144%

# ─── Notifications ────────────────────────────────────────────────────────
# Discord webhook URL — set in env or hardcode for the #ideas channel
DISCORD_WEBHOOK  = os.environ.get("DISCORD_WEBHOOK", "")
DISCORD_CHANNEL  = "1474189306536001659"   # #ideas

# ─── State file ───────────────────────────────────────────────────────────
STATE_FILE       = Path(__file__).parent / "grid_state.json"

# ─── Derived ──────────────────────────────────────────────────────────────
CUM_DROPS = []
_acc = 0.0
for g in LEVEL_GAPS:
    _acc += g
    CUM_DROPS.append(_acc / 100)
