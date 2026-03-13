"""
Adaptation Engine — Phase 4

The brain that ties meta-insights together:
1. Dynamic Signal Weighting — adjust based on bot behavior + regime
2. Strategy Evolution — track signal performance, dampen losers, boost winners
3. Edge Decay Detection — alert when hit rate is declining
4. Anti-Fragility — position sizing adjustments based on streaks
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from meta.bot_behavior_model import BehaviorPrediction
from meta.regime_detector import RegimeClassification, Regime


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class AdaptedWeights:
    """Final adapted signal weights and risk parameters."""
    signal_weights: dict[str, float]
    risk_scale: float
    position_size_multiplier: float  # anti-fragility adjustment
    regime: str
    confidence: float
    adjustments_log: list[str] = field(default_factory=list)
    edge_decay_alert: bool = False
    edge_decay_detail: str = ""


@dataclass
class SignalPerformanceRecord:
    """Track a signal's recent predictions vs outcomes."""
    signal_name: str
    predictions: list[dict] = field(default_factory=list)  # {direction, confidence, timestamp}
    outcomes: list[dict] = field(default_factory=list)      # {correct: bool, pnl: float}

    @property
    def recent_accuracy(self) -> float | None:
        if not self.outcomes:
            return None
        # Exponential decay: recent matters more
        weights = [math.exp(-0.1 * i) for i in range(len(self.outcomes))]
        weighted_correct = sum(w * (1.0 if o["correct"] else 0.0)
                               for w, o in zip(weights, reversed(self.outcomes)))
        return weighted_correct / sum(weights)


# ── Adaptation Engine ────────────────────────────────────────────────────────

DEFAULT_WEIGHTS = {
    "liquidation_cascade": 0.40,
    "funding_extreme": 0.25,
    "oi_divergence": 0.35,
}


class AdaptationEngine:
    """Combines regime, bot behavior, and signal performance into adapted weights."""

    def __init__(self):
        self.base_weights = dict(DEFAULT_WEIGHTS)
        self.signal_records: dict[str, SignalPerformanceRecord] = {}
        self.trade_history: list[dict] = []  # for streak detection
        self._consecutive_losses = 0
        self._consecutive_wins = 0

    # ── Dynamic Signal Weighting ─────────────────────────────────────────

    def adapt(
        self,
        regime: RegimeClassification,
        behavior_predictions: list[BehaviorPrediction],
        signal_performance: dict[str, SignalPerformanceRecord] | None = None,
    ) -> AdaptedWeights:
        """Produce adapted weights from all meta-layer inputs."""
        log: list[str] = []

        # Start from regime-recommended weights
        weights = dict(regime.signal_weights)
        risk_scale = regime.risk_scale
        log.append(f"Base weights from regime ({regime.regime.value}): {weights}")

        # Apply bot behavior adjustments
        for pred in behavior_predictions:
            for signal_name, adjustment in pred.recommendations.items():
                if signal_name in weights and abs(adjustment) > 0.001 and pred.confidence > 0.1:
                    old = weights[signal_name]
                    # Scale adjustment by model confidence
                    scaled_adj = adjustment * pred.confidence
                    weights[signal_name] = max(0.05, min(0.7, old + scaled_adj))
                    log.append(f"{pred.model_name}: {signal_name} {old:.2f} → {weights[signal_name]:.2f} "
                              f"(adj={scaled_adj:+.3f}, conf={pred.confidence:.0%})")

        # Apply crowding-based risk adjustment
        for pred in behavior_predictions:
            if pred.model_name == "crowding":
                crowding_score = pred.scores.get("crowding_score", 0)
                if crowding_score > 0.7:
                    risk_scale *= 0.7
                    log.append(f"Crowding high ({crowding_score:.0%}) → risk scale reduced to {risk_scale:.2f}")
                elif crowding_score > 0.4:
                    risk_scale *= 0.85
                    log.append(f"Crowding moderate ({crowding_score:.0%}) → risk scale reduced to {risk_scale:.2f}")

        # ── Strategy Evolution: performance-based dampening ──────────────
        if signal_performance:
            for name, record in signal_performance.items():
                acc = record.recent_accuracy
                if acc is None or name not in weights:
                    continue
                # Accuracy > 60% → boost; < 40% → dampen
                if acc > 0.6:
                    boost = (acc - 0.6) * 0.5  # max +0.2 at 100%
                    old = weights[name]
                    weights[name] = min(0.7, weights[name] + boost)
                    log.append(f"Performance boost: {name} {old:.2f} → {weights[name]:.2f} (acc={acc:.0%})")
                elif acc < 0.4:
                    dampen = (0.4 - acc) * 0.5
                    old = weights[name]
                    weights[name] = max(0.05, weights[name] - dampen)
                    log.append(f"Performance dampen: {name} {old:.2f} → {weights[name]:.2f} (acc={acc:.0%})")

        # Normalize weights to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {k: round(v / total, 4) for k, v in weights.items()}

        # ── Anti-Fragility: position sizing ──────────────────────────────
        pos_mult = 1.0
        if self._consecutive_losses >= 3:
            pos_mult = max(0.3, 1.0 - self._consecutive_losses * 0.15)
            log.append(f"Anti-fragility: {self._consecutive_losses} consecutive losses → size mult {pos_mult:.2f}")
        elif self._consecutive_wins >= 5:
            # Don't increase on winning streaks — avoid overconfidence
            pos_mult = 0.9
            log.append(f"Anti-fragility: {self._consecutive_wins} consecutive wins → capping size to avoid overconfidence")

        # ── Edge Decay Detection ─────────────────────────────────────────
        edge_alert = False
        edge_detail = ""
        if signal_performance:
            accuracies = [r.recent_accuracy for r in signal_performance.values() if r.recent_accuracy is not None]
            if accuracies:
                avg_acc = statistics.mean(accuracies)
                if avg_acc < 0.45:
                    edge_alert = True
                    edge_detail = f"Overall hit rate {avg_acc:.0%} is below 45%. Edge may be decaying. Review strategy."
                    log.append(f"⚠️ EDGE DECAY: {edge_detail}")

        return AdaptedWeights(
            signal_weights=weights,
            risk_scale=round(risk_scale, 4),
            position_size_multiplier=round(pos_mult, 4),
            regime=regime.regime.value,
            confidence=regime.confidence,
            adjustments_log=log,
            edge_decay_alert=edge_alert,
            edge_decay_detail=edge_detail,
        )

    def record_trade(self, won: bool, pnl: float = 0.0) -> None:
        """Update streak tracking for anti-fragility."""
        self.trade_history.append({"won": won, "pnl": pnl})
        if won:
            self._consecutive_wins += 1
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            self._consecutive_wins = 0


# ── Standalone demo ──────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from meta.bot_behavior_model import BotBehaviorModel
    from meta.regime_detector import RegimeDetector, _load_jsonl

    hist_dir = Path(__file__).parent.parent / "intelligence" / "data" / "historical"
    oi_history = _load_jsonl(hist_dir / "oi_timeseries.jsonl")
    funding_history = _load_jsonl(hist_dir / "funding_rates.jsonl")

    # Regime
    detector = RegimeDetector(window=15)
    regime = detector.classify(oi_history, funding_history)

    # Bot behavior
    model = BotBehaviorModel()
    model.fit()
    predictions = model.predict()

    # Adapt
    engine = AdaptationEngine()
    adapted = engine.adapt(regime, predictions)

    print("\n" + "=" * 60)
    print("  ADAPTATION ENGINE — ADAPTED WEIGHTS")
    print("=" * 60)
    print(f"\n  Regime:          {adapted.regime} (confidence: {adapted.confidence:.0%})")
    print(f"  Risk Scale:      {adapted.risk_scale}x")
    print(f"  Position Mult:   {adapted.position_size_multiplier}x")
    print(f"  Edge Decay:      {'⚠️ YES' if adapted.edge_decay_alert else '✅ No'}")
    print(f"\n  Adapted Signal Weights:")
    for k, v in adapted.signal_weights.items():
        base = DEFAULT_WEIGHTS.get(k, 0)
        diff = v - base
        print(f"    {k}: {v:.2%} (base: {base:.0%}, Δ{diff:+.2%})")
    print(f"\n  Adjustment Log:")
    for line in adapted.adjustments_log:
        print(f"    • {line}")


if __name__ == "__main__":
    main()
