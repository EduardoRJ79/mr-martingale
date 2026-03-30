"""
Grid state manager — supports LONG and SHORT grids simultaneously.
Persists to JSON so bot survives restarts.
"""
import json, logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from datetime import datetime, timezone

try:
    from . import config as cfg
except ImportError:
    import config as cfg

log = logging.getLogger("grid_state")

LONG  = "long"
SHORT = "short"


@dataclass
class GridLevel:
    level:     int
    target_px: float
    filled:    bool  = False
    fill_px:   float = 0.0
    fill_qty:  float = 0.0
    margin:    float = 0.0
    notional:  float = 0.0
    oid:       Optional[int] = None


@dataclass
class GridState:
    side:           str   = ""      # "long" or "short"
    active:         bool  = False
    trigger_px:     float = 0.0
    ema34:          float = 0.0
    sma14:          float = 0.0
    opened_at:      str   = ""
    levels:         List[GridLevel] = field(default_factory=list)
    tp_oid:         Optional[int]   = None
    tp_price:       float = 0.0
    blended_entry:  float = 0.0
    total_qty:      float = 0.0
    total_margin:   float = 0.0
    # v3.0 fields
    is_favored:     bool  = True
    entry_gate:     str   = ""       # "v28", "ema20", "rescued"
    risk_pct:       float = 0.0
    max_hold_hours: int   = 2880     # 720 bars × 4h = 2880h (favored default)

    def filled_levels(self):
        return [l for l in self.levels if l.filled]

    def max_level_hit(self):
        f = self.filled_levels()
        return max(l.level for l in f) if f else 0

    def recalc(self):
        f = self.filled_levels()
        if not f: return
        self.total_qty    = sum(l.fill_qty for l in f)
        self.total_margin = sum(l.margin   for l in f)
        total_cost        = sum(l.fill_qty * l.fill_px for l in f)
        self.blended_entry = total_cost / self.total_qty
        if self.side == LONG:
            self.tp_price = self.blended_entry * (1 + cfg.TP_PCT / 100)
        else:
            self.tp_price = self.blended_entry * (1 - cfg.TP_PCT / 100)

    def hold_hours(self):
        if not self.opened_at: return 0.0
        opened = datetime.fromisoformat(self.opened_at)
        now    = datetime.now(timezone.utc)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return (now - opened).total_seconds() / 3600

    def next_unfilled(self):
        for l in self.levels:
            if not l.filled: return l
        return None

    def update(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)


@dataclass
class BotState:
    """Top-level state holding both grids."""
    long_grid:  GridState = field(default_factory=GridState)
    short_grid: GridState = field(default_factory=GridState)


# ─── Persistence ──────────────────────────────────────────────────────────

STATE_FILE = cfg.STATE_FILE

def _deserialize_grid(d: dict) -> GridState:
    levels = [GridLevel(**lv) for lv in d.pop("levels", [])]
    gs = GridState(**{k: v for k, v in d.items() if k != "levels"})
    gs.levels = levels
    return gs

def load() -> BotState:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            bs = BotState(
                long_grid  = _deserialize_grid(data.get("long_grid",  {})),
                short_grid = _deserialize_grid(data.get("short_grid", {})),
            )
            log.info(f"Loaded state: long={bs.long_grid.active} short={bs.short_grid.active}")
            return bs
        except Exception as e:
            log.error(f"Failed to load state, starting fresh: {e}")
    return BotState()

def save(bs: BotState):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({
            "long_grid":  asdict(bs.long_grid),
            "short_grid": asdict(bs.short_grid),
        }, f, indent=2)

def reset_grid(bs: BotState, side: str) -> BotState:
    if side == LONG:
        bs.long_grid = GridState()
    else:
        bs.short_grid = GridState()
    save(bs)
    return bs

def build_levels(trigger_px: float, side: str,
                 risk_pct: float = None, balance: float = None,
                 is_favored: bool = True) -> List[GridLevel]:
    """
    Build 5 grid levels using v3.0 sizing.

    L1 notional = risk_pct × balance
    L2-L5: cumulative multipliers from LEVEL_MULTS_SEQ [2.0, 2.5, 2.5, 7.0]
    Grid gaps scaled by UNFAV_SPACING_SCALE if unfavored.
    """
    leverage = cfg.LEVERAGE if side == LONG else cfg.SHORT_LEVERAGE

    # Fallback for backward compat (manual commands without risk context)
    if risk_pct is None:
        risk_pct = cfg.RISK_PCT
    if balance is None:
        balance = cfg.INITIAL_EQUITY_USD

    l1_notional = risk_pct * balance

    # Compute gap scaling
    spacing_scale = 1.0 if is_favored else cfg.UNFAV_SPACING_SCALE
    scaled_gaps = [g * spacing_scale for g in cfg.LEVEL_GAPS]
    cum_drops = []
    acc = 0.0
    for g in scaled_gaps:
        acc += g
        cum_drops.append(acc / 100.0)

    # Build cumulative multiplier sequence
    cum_mult = 1.0
    mults = [1.0]
    for m in cfg.LEVEL_MULTS_SEQ:
        cum_mult *= m
        mults.append(cum_mult)
    # mults = [1.0, 2.0, 5.0, 12.5, 87.5]

    levels = []
    for i in range(cfg.NUM_LEVELS):
        notional = l1_notional * mults[i]
        margin = notional / leverage
        if i == 0:
            target = trigger_px
        else:
            drop = cum_drops[i - 1]
            target = trigger_px * (1 - drop) if side == LONG else trigger_px * (1 + drop)
        levels.append(GridLevel(
            level=i + 1,
            target_px=round(target, 1),
            margin=margin,
            notional=notional,
        ))
    return levels
