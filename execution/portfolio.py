"""
Portfolio Tracker

Tracks open/closed positions, persists state to JSON, calculates
portfolio-level metrics. Supports paper trading with simulated fills.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent / "data" / "portfolio_state.json"


@dataclass
class ClosedPosition:
    asset: str
    direction: str
    entry_price: float
    exit_price: float
    size_usd: float
    pnl_usd: float
    pnl_pct: float
    signal_name: str
    entry_time: str
    exit_time: str
    exit_reason: str  # "stop_loss", "take_profit", "manual"
    duration_seconds: float


class Portfolio:
    """
    Tracks positions and portfolio state. Persists to JSON.
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        slippage: float = 0.002,
        state_file: Path | None = None,
    ):
        self.initial_capital = initial_capital
        self.slippage = slippage
        self._state_file = state_file or STATE_FILE

        # State
        self.cash: float = initial_capital
        self.open_positions: dict[str, dict[str, Any]] = {}  # asset → position dict
        self.closed_positions: list[dict[str, Any]] = []
        self.peak_value: float = initial_capital
        self.mode: str = "paper"

        self._load_state()

    # ── State persistence ──

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self.cash = data.get("cash", self.initial_capital)
                self.open_positions = data.get("open_positions", {})
                self.closed_positions = data.get("closed_positions", [])
                self.peak_value = data.get("peak_value", self.initial_capital)
                self.mode = data.get("mode", "paper")
                logger.info("Loaded portfolio state: $%.2f cash, %d open, %d closed",
                            self.cash, len(self.open_positions), len(self.closed_positions))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load state: %s — starting fresh", e)

    def save_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cash": self.cash,
            "open_positions": self.open_positions,
            "closed_positions": self.closed_positions,
            "peak_value": self.peak_value,
            "mode": self.mode,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        self._state_file.write_text(json.dumps(data, indent=2, default=str))

    # ── Portfolio metrics ──

    def total_value(self, current_prices: dict[str, float] | None = None) -> float:
        """Total portfolio value = cash + unrealized positions."""
        value = self.cash
        for asset, pos in self.open_positions.items():
            if current_prices and asset in current_prices:
                price = current_prices[asset]
            else:
                price = pos["entry_price"]  # fallback to entry
            value += self._position_value(pos, price)
        return value

    def _position_value(self, pos: dict, current_price: float) -> float:
        """Current value of a position including unrealized P&L."""
        entry = pos["entry_price"]
        size = pos["size_usd"]
        if pos["direction"] == "long":
            return size * (current_price / entry)
        else:  # short
            return size * (2 - current_price / entry)

    def unrealized_pnl(self, current_prices: dict[str, float]) -> float:
        total = 0.0
        for asset, pos in self.open_positions.items():
            price = current_prices.get(asset, pos["entry_price"])
            current_val = self._position_value(pos, price)
            total += current_val - pos["size_usd"]
        return total

    def total_exposure_pct(self) -> float:
        """Total position size as fraction of portfolio."""
        total_size = sum(p["size_usd"] for p in self.open_positions.values())
        tv = self.total_value()
        return total_size / tv if tv > 0 else 0.0

    def current_drawdown(self, current_prices: dict[str, float] | None = None) -> float:
        """Current drawdown from peak as a fraction (0.1 = 10% down)."""
        tv = self.total_value(current_prices)
        if tv >= self.peak_value:
            self.peak_value = tv
            return 0.0
        return (self.peak_value - tv) / self.peak_value

    # ── Paper trading operations ──

    def open_position(
        self,
        asset: str,
        direction: str,
        entry_price: float,
        size_usd: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        signal_name: str,
        reasoning: str,
    ) -> dict[str, Any]:
        """Open a new paper position with simulated slippage."""
        # Apply slippage
        if direction == "long":
            fill_price = entry_price * (1 + self.slippage)
            stop_price = fill_price * (1 - stop_loss_pct)
            target_price = fill_price * (1 + take_profit_pct)
        else:
            fill_price = entry_price * (1 - self.slippage)
            stop_price = fill_price * (1 + stop_loss_pct)
            target_price = fill_price * (1 - take_profit_pct)

        position = {
            "asset": asset,
            "direction": direction,
            "entry_price": round(fill_price, 6),
            "size_usd": round(size_usd, 2),
            "stop_price": round(stop_price, 2),
            "target_price": round(target_price, 2),
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "signal_name": signal_name,
            "reasoning": reasoning,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "entry_timestamp": time.time(),
        }

        self.cash -= size_usd
        self.open_positions[asset] = position
        self.save_state()

        logger.info("OPENED %s %s @ $%.2f, size $%.2f, stop $%.2f, target $%.2f",
                     direction.upper(), asset, fill_price, size_usd, stop_price, target_price)
        return position

    def close_position(
        self,
        asset: str,
        exit_price: float,
        reason: str = "manual",
    ) -> dict[str, Any] | None:
        """Close an open position. Returns closed position record."""
        if asset not in self.open_positions:
            logger.warning("No open position in %s to close", asset)
            return None

        pos = self.open_positions.pop(asset)

        # Apply slippage on exit
        if pos["direction"] == "long":
            fill_price = exit_price * (1 - self.slippage)
            pnl_usd = pos["size_usd"] * (fill_price / pos["entry_price"] - 1)
        else:
            fill_price = exit_price * (1 + self.slippage)
            pnl_usd = pos["size_usd"] * (1 - fill_price / pos["entry_price"])

        pnl_pct = pnl_usd / pos["size_usd"] if pos["size_usd"] else 0
        duration = time.time() - pos["entry_timestamp"]

        closed = {
            "asset": pos["asset"],
            "direction": pos["direction"],
            "entry_price": pos["entry_price"],
            "exit_price": round(fill_price, 6),
            "size_usd": pos["size_usd"],
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 6),
            "signal_name": pos["signal_name"],
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "exit_reason": reason,
            "duration_seconds": round(duration, 1),
        }

        self.cash += pos["size_usd"] + pnl_usd
        self.closed_positions.append(closed)

        # Update peak
        tv = self.total_value()
        if tv > self.peak_value:
            self.peak_value = tv

        self.save_state()

        logger.info("CLOSED %s %s @ $%.2f — P&L: $%.2f (%.2f%%) [%s]",
                     pos["direction"].upper(), asset, fill_price,
                     pnl_usd, pnl_pct * 100, reason)
        return closed

    def check_stops_and_targets(self, current_prices: dict[str, float]) -> list[dict[str, Any]]:
        """Check all open positions for stop/target hits. Returns list of closed positions."""
        closed = []
        # Iterate over a copy since we modify during iteration
        for asset, pos in list(self.open_positions.items()):
            price = current_prices.get(asset)
            if price is None:
                continue

            if pos["direction"] == "long":
                if price <= pos["stop_price"]:
                    c = self.close_position(asset, price, "stop_loss")
                    if c:
                        closed.append(c)
                elif price >= pos["target_price"]:
                    c = self.close_position(asset, price, "take_profit")
                    if c:
                        closed.append(c)
            else:  # short
                if price >= pos["stop_price"]:
                    c = self.close_position(asset, price, "stop_loss")
                    if c:
                        closed.append(c)
                elif price <= pos["target_price"]:
                    c = self.close_position(asset, price, "take_profit")
                    if c:
                        closed.append(c)

        return closed

    # ── Reporting ──

    def summary(self, current_prices: dict[str, float] | None = None) -> str:
        prices = current_prices or {}
        tv = self.total_value(prices)
        dd = self.current_drawdown(prices)
        upnl = self.unrealized_pnl(prices) if prices else 0
        exp = self.total_exposure_pct()

        realized = sum(c["pnl_usd"] for c in self.closed_positions)
        wins = sum(1 for c in self.closed_positions if c["pnl_usd"] > 0)
        total_trades = len(self.closed_positions)
        win_rate = wins / total_trades if total_trades > 0 else 0

        lines = [
            f"{'─'*50}",
            f"  PORTFOLIO ({self.mode.upper()} MODE)",
            f"{'─'*50}",
            f"  Total Value:     ${tv:>12,.2f}",
            f"  Cash:            ${self.cash:>12,.2f}",
            f"  Open Positions:  {len(self.open_positions):>12}",
            f"  Exposure:        {exp:>12.1%}",
            f"  Unrealized P&L:  ${upnl:>12,.2f}",
            f"  Drawdown:        {dd:>12.1%}",
            f"  Peak Value:      ${self.peak_value:>12,.2f}",
            f"{'─'*50}",
            f"  Closed Trades:   {total_trades:>12}",
            f"  Realized P&L:    ${realized:>12,.2f}",
            f"  Win Rate:        {win_rate:>12.0%}",
            f"{'─'*50}",
        ]

        for asset, pos in self.open_positions.items():
            price = prices.get(asset, pos["entry_price"])
            val = self._position_value(pos, price)
            pnl = val - pos["size_usd"]
            lines.append(
                f"  {pos['direction'].upper():>5} {asset:<6} "
                f"entry=${pos['entry_price']:>10,.2f}  "
                f"now=${price:>10,.2f}  "
                f"P&L=${pnl:>8,.2f}  "
                f"stop=${pos['stop_price']:>10,.2f}"
            )

        return "\n".join(lines)
