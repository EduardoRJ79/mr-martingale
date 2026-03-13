"""
Multi-Signal Confluence Engine

Combines outputs from all available signals into a weighted confluence score.
Handles missing signals gracefully (scores based on what's available).

The key insight: individual signals may be weak (funding P99: 61% hit,
cascade proxy: unknown alone) but COMBINED confluence of multiple weak
signals can produce a stronger edge.

Confluence score: 0-100, with configurable minimum threshold.
"""
from __future__ import annotations
import logging, math
from dataclasses import dataclass, field
from typing import Any
from signals.signal_definitions import Direction, SignalResult

logger = logging.getLogger(__name__)

# Default signal weights (must sum to ~1.0 for available signals)
DEFAULT_WEIGHTS = {
    "funding_extreme": 0.30,       # Historically validated (weak but real)
    "oi_divergence": 0.25,         # Volume-based proxy
    "liquidation_cascade": 0.25,   # Price-action proxy
    "book_imbalance": 0.20,        # Forward-only (when available)
}

@dataclass
class ConfluenceResult:
    """Output of confluence scoring."""
    direction: Direction
    score: float              # 0-100
    confidence: float         # 0.0-1.0
    n_signals_active: int
    n_signals_total: int
    signal_agreement: float   # 0.0-1.0 (how much signals agree)
    signals: dict[str, SignalResult] = field(default_factory=dict)
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_tradeable(self):
        return self.direction != Direction.NEUTRAL and self.score > 0


class ConfluenceEngine:
    """
    Combines multiple signal results into a single directional score.

    Key design decisions:
    1. Normalize weights to available signals (if L2 book is missing,
       redistribute its weight proportionally to others)
    2. Agreement bonus: signals pointing same direction get a multiplier
    3. Conflict penalty: opposing signals reduce score
    4. Minimum active signals: need at least 2 active signals to fire
    5. Score decay: score degrades if only 1 signal type is active
    """

    def __init__(self, weights=None, min_score=35.0, min_active_signals=2):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self.min_score = min_score
        self.min_active_signals = min_active_signals

    def score(self, signals: dict[str, SignalResult]) -> ConfluenceResult:
        """
        Score a set of signal results.

        Args:
            signals: dict of signal_name -> SignalResult

        Returns:
            ConfluenceResult with direction, score, and metadata
        """
        if not signals:
            return ConfluenceResult(
                Direction.NEUTRAL, 0.0, 0.0, 0, 0, 0.0, {},
                "No signals provided")

        # Normalize weights to available signals
        available_weight = sum(self.weights.get(name, 0.1) for name in signals)
        if available_weight == 0:
            available_weight = 1.0

        # Accumulate directional scores
        long_score = 0.0
        short_score = 0.0
        active_signals = []
        all_directions = []

        for name, sig in signals.items():
            w = self.weights.get(name, 0.1) / available_weight  # Normalized
            if sig.is_active:
                active_signals.append(name)
                all_directions.append(sig.direction)
                if sig.direction == Direction.LONG:
                    long_score += sig.confidence * w
                elif sig.direction == Direction.SHORT:
                    short_score += sig.confidence * w

        n_active = len(active_signals)
        n_total = len(signals)

        # Not enough active signals
        if n_active < self.min_active_signals:
            return ConfluenceResult(
                Direction.NEUTRAL, 0.0, 0.0, n_active, n_total, 0.0, signals,
                f"Only {n_active} active signals (need {self.min_active_signals})",
                metadata={"long_score": round(long_score*100, 1),
                          "short_score": round(short_score*100, 1)})

        # Determine dominant direction
        if long_score > short_score:
            direction = Direction.LONG
            dominant = long_score
            opposing = short_score
        elif short_score > long_score:
            direction = Direction.SHORT
            dominant = short_score
            opposing = long_score
        else:
            return ConfluenceResult(
                Direction.NEUTRAL, 0.0, 0.0, n_active, n_total, 0.5, signals,
                "Perfectly balanced signals")

        # Agreement ratio: what fraction of active signals agree with dominant?
        agreeing = sum(1 for d in all_directions if d == direction)
        agreement = agreeing / n_active if n_active > 0 else 0

        # Base score from weighted confidences (0-1 range)
        base_score = dominant * 100

        # Agreement bonus: unanimous signals get up to 1.3x multiplier
        agreement_mult = 0.7 + 0.3 * agreement

        # Conflict penalty: opposing signals reduce score
        conflict_penalty = 1.0 - (opposing / max(dominant, 0.001)) * 0.5
        conflict_penalty = max(0.3, conflict_penalty)

        # Coverage bonus: more active signals = more confidence
        coverage = n_active / max(n_total, 1)
        coverage_mult = 0.8 + 0.2 * coverage

        final_score = base_score * agreement_mult * conflict_penalty * coverage_mult
        final_score = min(100.0, max(0.0, final_score))
        confidence = final_score / 100.0

        if final_score < self.min_score:
            return ConfluenceResult(
                Direction.NEUTRAL, round(final_score, 1), round(confidence, 4),
                n_active, n_total, round(agreement, 2), signals,
                f"Score {final_score:.1f} below threshold {self.min_score}",
                metadata={"long_pct": round(long_score*100, 1),
                          "short_pct": round(short_score*100, 1),
                          "agreement": round(agreement, 2),
                          "active": active_signals})

        # Build reasoning
        parts = [f"Confluence {final_score:.0f}/100 -> {direction.value.upper()}"]
        parts.append(f"Agreement: {agreement:.0%} ({agreeing}/{n_active} signals)")
        for name in active_signals:
            s = signals[name]
            parts.append(f"  {name}: {s.direction.value} ({s.confidence:.0%})")

        return ConfluenceResult(
            direction, round(final_score, 1), round(confidence, 4),
            n_active, n_total, round(agreement, 2), signals,
            " | ".join(parts),
            metadata={"long_pct": round(long_score*100, 1),
                      "short_pct": round(short_score*100, 1),
                      "agreement": round(agreement, 2),
                      "agreement_mult": round(agreement_mult, 3),
                      "conflict_penalty": round(conflict_penalty, 3),
                      "coverage_mult": round(coverage_mult, 3),
                      "active": active_signals})


if __name__ == "__main__":
    from signals.signal_definitions import (
        FundingRateExtremeSignal, OIDivergenceSignal,
        LiquidationCascadeProxySignal, OrderBookImbalanceSignal
    )

    # Simulate a confluence scenario
    sigs = {
        "funding_extreme": SignalResult("funding_extreme", Direction.SHORT, 0.5,
            "P99 mean-revert: extreme positive funding"),
        "oi_divergence": SignalResult("oi_divergence", Direction.SHORT, 0.4,
            "Vol spike + price drop: new shorts"),
        "liquidation_cascade": SignalResult("liquidation_cascade", Direction.LONG, 0.3,
            "V-bottom recovery"),
    }

    engine = ConfluenceEngine()
    result = engine.score(sigs)
    print(f"Direction: {result.direction.value}")
    print(f"Score: {result.score}/100")
    print(f"Confidence: {result.confidence:.2%}")
    print(f"Active: {result.n_signals_active}/{result.n_signals_total}")
    print(f"Agreement: {result.signal_agreement:.0%}")
    print(f"Reasoning: {result.reasoning}")
