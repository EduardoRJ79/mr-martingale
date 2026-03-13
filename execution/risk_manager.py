"""
Risk Manager — The Paranoid Gatekeeper

Every trade must pass through here. When in doubt, the answer is NO.
Implements position sizing (fractional Kelly), drawdown halts, correlation
limits, and mandatory stop-losses.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from execution.portfolio import Portfolio

from signals.signal_definitions import Direction, SignalResult

logger = logging.getLogger(__name__)


def _load_config() -> dict[str, Any]:
    cfg = Path(__file__).parent / "config.yaml"
    if cfg.exists():
        return yaml.safe_load(cfg.read_text()) or {}
    return {}


@dataclass
class PositionSize:
    """Result of position sizing calculation."""
    size_usd: float
    size_pct: float          # fraction of portfolio
    stop_loss_pct: float     # distance from entry as fraction (e.g. 0.02 = 2%)
    take_profit_pct: float   # distance from entry as fraction
    leverage: float
    reasoning: str
    approved: bool


class RiskManager:
    """
    Paranoid risk manager. Checks everything before allowing a trade.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or _load_config()
        risk = cfg.get("risk", {})
        self.max_position_pct: float = risk.get("max_position_pct", 0.05)
        self.max_drawdown_pct: float = risk.get("max_drawdown_pct", 0.15)
        self.max_correlated: int = risk.get("max_correlated_positions", 3)
        self.always_use_stops: bool = risk.get("always_use_stops", True)
        self.slippage: float = cfg.get("execution", {}).get("slippage_tolerance", 0.002)

        # Kelly parameters — conservative defaults
        self.kelly_fraction: float = 0.25  # quarter Kelly (very conservative)
        self.min_confluence_score: float = 40.0  # below this, don't even consider
        self.min_confidence: float = 0.3  # minimum signal confidence to act

        # Default stop/target if not provided
        self.default_stop_pct: float = 0.02   # 2% stop loss
        self.default_target_pct: float = 0.04  # 4% take profit (2:1 R:R)

        self._halted = False
        self._halt_reason = ""

    def can_trade(self, portfolio: "Portfolio") -> tuple[bool, str]:
        """
        Master pre-trade check. Returns (allowed, reason).
        """
        if self._halted:
            return False, f"HALTED: {self._halt_reason}"

        # Check drawdown
        dd_ok, dd_reason = self.check_drawdown(portfolio)
        if not dd_ok:
            self._halted = True
            self._halt_reason = dd_reason
            return False, dd_reason

        # Check total exposure
        exposure = portfolio.total_exposure_pct()
        if exposure >= 0.80:
            return False, f"Total exposure {exposure:.0%} too high (max 80%)"

        return True, "OK"

    def check_drawdown(self, portfolio: "Portfolio") -> tuple[bool, str]:
        """Check if portfolio drawdown exceeds limit."""
        dd = portfolio.current_drawdown()
        if dd >= self.max_drawdown_pct:
            msg = (f"DRAWDOWN HALT: Portfolio down {dd:.1%} from peak "
                   f"(limit {self.max_drawdown_pct:.0%}). ALL TRADING STOPPED.")
            logger.critical(msg)
            return False, msg
        if dd >= self.max_drawdown_pct * 0.8:
            logger.warning("Drawdown warning: %.1f%% (limit %.0f%%)", dd * 100, self.max_drawdown_pct * 100)
        return True, "OK"

    def _check_correlation(self, direction: Direction, portfolio: "Portfolio") -> tuple[bool, str]:
        """Don't stack too many positions in the same direction."""
        same_dir = sum(1 for p in portfolio.open_positions.values()
                       if p["direction"] == direction.value)
        if same_dir >= self.max_correlated:
            return False, (f"Already {same_dir} {direction.value} positions open "
                           f"(max {self.max_correlated})")
        return True, "OK"

    def _kelly_size(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Fractional Kelly criterion.
        Returns fraction of bankroll to risk (0 if negative edge).
        """
        if avg_loss == 0 or win_rate <= 0:
            return 0.0
        b = avg_win / avg_loss  # win/loss ratio
        q = 1 - win_rate
        kelly = (win_rate * b - q) / b
        if kelly <= 0:
            return 0.0
        return kelly * self.kelly_fraction  # fractional Kelly

    def size_position(
        self,
        signal: SignalResult,
        portfolio: "Portfolio",
        asset: str,
        price: float,
        stop_pct: float | None = None,
        target_pct: float | None = None,
    ) -> PositionSize:
        """
        Determine position size for a signal. Returns PositionSize with approved=True/False.
        """
        reasons: list[str] = []

        # ── Gate 1: Can we trade at all? ──
        can, reason = self.can_trade(portfolio)
        if not can:
            return PositionSize(0, 0, 0, 0, 1, reason, False)

        # ── Gate 2: Signal quality ──
        if signal.direction == Direction.NEUTRAL:
            return PositionSize(0, 0, 0, 0, 1, "Signal is neutral — no trade", False)

        confluence_score = signal.metadata.get("score", signal.confidence * 100)
        if confluence_score < self.min_confluence_score:
            return PositionSize(0, 0, 0, 0, 1,
                                f"Confluence {confluence_score:.0f} < {self.min_confluence_score} — too weak", False)

        if signal.confidence < self.min_confidence:
            return PositionSize(0, 0, 0, 0, 1,
                                f"Confidence {signal.confidence:.0%} < {self.min_confidence:.0%} — too uncertain", False)

        # ── Gate 3: Correlation check ──
        corr_ok, corr_reason = self._check_correlation(signal.direction, portfolio)
        if not corr_ok:
            return PositionSize(0, 0, 0, 0, 1, corr_reason, False)

        # ── Gate 4: Already have position in this asset? ──
        if asset in portfolio.open_positions:
            return PositionSize(0, 0, 0, 0, 1, f"Already have open position in {asset}", False)

        # ── Sizing ──
        stop = stop_pct or self.default_stop_pct
        target = target_pct or self.default_target_pct

        if self.always_use_stops and stop <= 0:
            return PositionSize(0, 0, 0, 0, 1, "Stop loss required but none provided", False)

        # Estimate win rate from confidence (rough heuristic)
        est_win_rate = 0.4 + signal.confidence * 0.2  # 40-60% range
        kelly_frac = self._kelly_size(est_win_rate, target, stop)

        # Scale by confidence: higher confidence = closer to max
        confidence_scale = signal.confidence  # 0-1
        raw_pct = kelly_frac * confidence_scale

        # Clamp to max position size
        final_pct = min(raw_pct, self.max_position_pct)
        final_pct = max(final_pct, 0.005)  # minimum 0.5% if we're trading at all

        # Re-clamp to max
        final_pct = min(final_pct, self.max_position_pct)

        portfolio_value = portfolio.total_value()
        size_usd = final_pct * portfolio_value

        # Account for slippage
        effective_stop = stop + self.slippage

        reasons.append(f"Confluence={confluence_score:.0f}, conf={signal.confidence:.0%}")
        reasons.append(f"Kelly frac={kelly_frac:.4f}, scaled={raw_pct:.4f}")
        reasons.append(f"Size={final_pct:.2%} of ${portfolio_value:,.0f} = ${size_usd:,.0f}")
        reasons.append(f"Stop={effective_stop:.2%}, Target={target:.2%}, R:R={target/effective_stop:.1f}:1")

        return PositionSize(
            size_usd=round(size_usd, 2),
            size_pct=round(final_pct, 6),
            stop_loss_pct=round(effective_stop, 6),
            take_profit_pct=round(target, 6),
            leverage=1.0,  # no leverage for now
            reasoning=" | ".join(reasons),
            approved=True,
        )
