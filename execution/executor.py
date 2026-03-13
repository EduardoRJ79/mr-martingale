"""
Executor — Signal → Trade

Takes a risk-approved signal and executes it (paper mode only for now).
Logs every decision to JOURNAL.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from execution.portfolio import Portfolio
from execution.risk_manager import RiskManager, PositionSize
from signals.signal_definitions import SignalResult, Direction

logger = logging.getLogger(__name__)

JOURNAL_PATH = Path(__file__).parent.parent / "JOURNAL.md"


class Executor:
    """
    Executes trades based on signals + risk approval.
    Paper mode only — no live orders.
    """

    def __init__(
        self,
        portfolio: Portfolio,
        risk_manager: RiskManager,
        mode: str = "paper",
    ):
        self.portfolio = portfolio
        self.risk = risk_manager
        self.mode = mode

    def execute_signal(
        self,
        asset: str,
        price: float,
        signal: SignalResult,
    ) -> dict[str, Any] | None:
        """
        Full trade lifecycle: signal → risk check → size → execute.
        Returns position dict if trade was opened, None otherwise.
        """
        result: dict[str, Any] = {
            "asset": asset,
            "price": price,
            "signal": signal.name,
            "direction": signal.direction.value,
            "confidence": signal.confidence,
            "action": "NO_TRADE",
            "reason": "",
        }

        # Skip neutral signals
        if signal.direction == Direction.NEUTRAL:
            result["reason"] = "Signal neutral"
            self._log_decision(result)
            return None

        # Risk check + sizing
        sizing = self.risk.size_position(
            signal=signal,
            portfolio=self.portfolio,
            asset=asset,
            price=price,
        )

        if not sizing.approved:
            result["reason"] = sizing.reasoning
            self._log_decision(result)
            return None

        # Execute in paper mode
        if self.mode != "paper":
            result["reason"] = "Live mode not implemented — refusing to trade"
            result["action"] = "BLOCKED"
            self._log_decision(result)
            logger.error("Live trading not implemented. Refusing.")
            return None

        position = self.portfolio.open_position(
            asset=asset,
            direction=signal.direction.value,
            entry_price=price,
            size_usd=sizing.size_usd,
            stop_loss_pct=sizing.stop_loss_pct,
            take_profit_pct=sizing.take_profit_pct,
            signal_name=signal.name,
            reasoning=sizing.reasoning,
        )

        result["action"] = "OPENED"
        result["reason"] = sizing.reasoning
        result["size_usd"] = sizing.size_usd
        result["stop_loss_pct"] = sizing.stop_loss_pct
        result["take_profit_pct"] = sizing.take_profit_pct

        self._log_decision(result)
        self._log_journal(result, position)

        return position

    def check_and_close(self, current_prices: dict[str, float]) -> list[dict[str, Any]]:
        """Check stops/targets on all open positions."""
        closed = self.portfolio.check_stops_and_targets(current_prices)
        for c in closed:
            self._log_journal_close(c)
        return closed

    def _log_decision(self, result: dict[str, Any]) -> None:
        """Log every trade decision."""
        logger.info(
            "DECISION: %s %s %s @ $%.2f — %s | %s",
            result["action"],
            result["direction"],
            result["asset"],
            result["price"],
            result.get("reason", ""),
            f"conf={result['confidence']:.0%}" if result.get("confidence") else "",
        )

    def _log_journal(self, result: dict[str, Any], position: dict[str, Any]) -> None:
        """Append trade entry to JOURNAL.md."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = (
            f"\n## {now} — {result['action']} {result['direction'].upper()} {result['asset']}\n"
            f"- **Mode:** {self.mode}\n"
            f"- **Signal:** {result['signal']} (confidence: {result['confidence']:.0%})\n"
            f"- **Entry:** ${result['price']:,.2f} (filled: ${position['entry_price']:,.2f})\n"
            f"- **Size:** ${result.get('size_usd', 0):,.2f}\n"
            f"- **Stop:** ${position['stop_price']:,.2f} ({result.get('stop_loss_pct', 0):.2%})\n"
            f"- **Target:** ${position['target_price']:,.2f} ({result.get('take_profit_pct', 0):.2%})\n"
            f"- **Reasoning:** {result.get('reason', 'N/A')}\n"
        )
        with open(JOURNAL_PATH, "a") as f:
            f.write(entry)

    def _log_journal_close(self, closed: dict[str, Any]) -> None:
        """Append trade exit to JOURNAL.md."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        pnl_sign = "+" if closed["pnl_usd"] >= 0 else ""
        entry = (
            f"\n## {now} — CLOSED {closed['direction'].upper()} {closed['asset']}\n"
            f"- **Exit:** ${closed['exit_price']:,.2f} [{closed['exit_reason']}]\n"
            f"- **P&L:** {pnl_sign}${closed['pnl_usd']:,.2f} ({pnl_sign}{closed['pnl_pct']:.2%})\n"
            f"- **Duration:** {closed['duration_seconds']:.0f}s\n"
            f"- **Signal:** {closed['signal_name']}\n"
        )
        with open(JOURNAL_PATH, "a") as f:
            f.write(entry)
