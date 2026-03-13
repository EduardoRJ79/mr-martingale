"""
Bot Behavior Model — Phase 4

Models how other market participants (bots, market makers, funds) behave
in response to observable conditions.  Four sub-models:

1. Liquidation Hunter Detection
2. Funding Arbitrage Bot Detection
3. Whale Behavior Patterns
4. Crowding Detector

Each has fit() / predict() and degrades gracefully with minimal data.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data"
HISTORICAL_DIR = DATA_DIR / "historical"
LIVE_DIR = DATA_DIR / "live"


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class BehaviorPrediction:
    """Output of a behavior model."""
    model_name: str
    scores: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0          # 0-1, how much data backs this
    data_points: int = 0
    interpretation: str = ""
    recommendations: dict[str, float] = field(default_factory=dict)  # signal weight adjustments


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _load_liquidation_snapshots() -> list[dict]:
    """Load all liquidation JSON snapshots from live dir, sorted by time."""
    if not LIVE_DIR.exists():
        return []
    files = sorted(LIVE_DIR.glob("liquidation_*.json"))
    snapshots = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            # extract timestamp from filename: liquidation_YYYYMMDDTHHMMSSz.json
            ts = f.stem.replace("liquidation_", "")
            snapshots.append({"timestamp": ts, "data": data})
        except (json.JSONDecodeError, KeyError):
            continue
    return snapshots


def _data_confidence(n: int, min_useful: int = 5, full_confidence: int = 50) -> float:
    """Scale confidence by data availability."""
    if n < 2:
        return 0.0
    return min(1.0, (n - 1) / (full_confidence - 1))


# ── 1. Liquidation Hunter Detection ─────────────────────────────────────────

class LiquidationHunterDetector:
    """
    Tracks how often price 'magnet-walks' toward liquidation clusters
    then reverses — a sign that other bots are hunting liquidations.
    """

    def __init__(self):
        self.hunt_events: list[dict] = []
        self.total_windows: int = 0

    def fit(self, oi_history: list[dict], liq_snapshots: list[dict]) -> None:
        """
        Analyze price movements relative to liquidation zones.
        A 'hunt' = price moved toward a cluster then reversed within a few snapshots.
        """
        if len(oi_history) < 3 or len(liq_snapshots) < 3:
            return

        # Build price series from OI history for BTC
        prices = []
        for row in oi_history:
            btc = row.get("data", {}).get("BTC", {})
            p = btc.get("mid_price")
            if p:
                prices.append(float(p))

        if len(prices) < 5:
            return

        # Simple hunt detection: look for V-shapes or inverted-V in price
        window = 3
        for i in range(window, len(prices) - window):
            pre = prices[i - window:i]
            post = prices[i:i + window]

            pre_move = prices[i] - prices[i - window]
            post_move = prices[i + window] - prices[i]

            # Hunt pattern: significant move in one direction, then reversal
            move_pct = abs(pre_move) / prices[i - window] if prices[i - window] else 0
            if move_pct < 0.001:  # Need at least 0.1% move
                continue

            # Reversal: post-move in opposite direction of pre-move
            if pre_move != 0 and post_move != 0:
                if (pre_move > 0 and post_move < 0) or (pre_move < 0 and post_move > 0):
                    reversal_pct = abs(post_move) / abs(pre_move)
                    if reversal_pct > 0.3:  # Reversal is at least 30% of initial move
                        self.hunt_events.append({
                            "index": i,
                            "pre_move_pct": pre_move / prices[i - window],
                            "reversal_pct": post_move / prices[i],
                            "reversal_ratio": reversal_pct,
                        })

            self.total_windows += 1

    def predict(self, current_data: dict[str, Any] | None = None) -> BehaviorPrediction:
        n = self.total_windows
        n_hunts = len(self.hunt_events)

        if n < 3:
            return BehaviorPrediction(
                model_name="liquidation_hunter",
                scores={"hunt_frequency": 0.0, "avg_cascade_duration": 0.0, "avg_reversal_magnitude": 0.0},
                confidence=0.0,
                data_points=n,
                interpretation="Insufficient data for liquidation hunt detection",
            )

        hunt_freq = n_hunts / n if n > 0 else 0.0
        avg_reversal = (
            statistics.mean([abs(e["reversal_pct"]) for e in self.hunt_events])
            if self.hunt_events else 0.0
        )
        avg_ratio = (
            statistics.mean([e["reversal_ratio"] for e in self.hunt_events])
            if self.hunt_events else 0.0
        )

        conf = _data_confidence(n)

        # If hunts are frequent, boost liquidation signal (front-run the hunters)
        liq_weight_adj = 0.0
        if hunt_freq > 0.3:
            liq_weight_adj = 0.15  # significant hunting → boost liq signal
            interp = f"High liquidation hunting detected ({hunt_freq:.0%} of windows). Bots are actively hunting stops."
        elif hunt_freq > 0.1:
            liq_weight_adj = 0.05
            interp = f"Moderate liquidation hunting ({hunt_freq:.0%}). Some bot activity."
        else:
            interp = f"Low liquidation hunting ({hunt_freq:.0%}). Market moves appear organic."

        return BehaviorPrediction(
            model_name="liquidation_hunter",
            scores={
                "hunt_frequency": round(hunt_freq, 4),
                "avg_reversal_magnitude": round(avg_reversal, 6),
                "avg_reversal_ratio": round(avg_ratio, 4),
                "hunt_events": n_hunts,
            },
            confidence=round(conf, 4),
            data_points=n,
            interpretation=interp,
            recommendations={"liquidation_cascade": liq_weight_adj},
        )


# ── 2. Funding Arbitrage Bot Detection ──────────────────────────────────────

class FundingArbDetector:
    """
    Tracks how quickly extreme funding rates mean-revert.
    Fast reversion = crowded arb → our edge is smaller.
    """

    def __init__(self, extreme_threshold: float = 0.0007):
        self.extreme_threshold = extreme_threshold
        self.reversion_events: list[dict] = []
        self.total_extremes: int = 0

    def fit(self, funding_history: list[dict]) -> None:
        """Analyze funding rate time series for mean-reversion speed."""
        if len(funding_history) < 5:
            return

        # Extract BTC funding series
        rates = []
        for row in funding_history:
            btc = row.get("data", {}).get("BTC", {})
            r = btc.get("current_rate")
            if r is not None:
                rates.append(float(r))

        if len(rates) < 5:
            return

        # Find extreme episodes and measure reversion time
        in_extreme = False
        extreme_start = 0
        extreme_peak = 0.0

        for i, r in enumerate(rates):
            if not in_extreme and abs(r) >= self.extreme_threshold:
                in_extreme = True
                extreme_start = i
                extreme_peak = r
                self.total_extremes += 1
            elif in_extreme:
                if abs(r) > abs(extreme_peak):
                    extreme_peak = r
                # Reversion: rate drops to less than 50% of peak
                if abs(r) < abs(extreme_peak) * 0.5:
                    duration = i - extreme_start
                    self.reversion_events.append({
                        "start_idx": extreme_start,
                        "duration": duration,
                        "peak_rate": extreme_peak,
                        "end_rate": r,
                    })
                    in_extreme = False

    def predict(self, current_data: dict[str, Any] | None = None) -> BehaviorPrediction:
        n = len(self.reversion_events)
        total = self.total_extremes

        if total == 0:
            # Check if rates never got extreme — that's also information
            return BehaviorPrediction(
                model_name="funding_arb",
                scores={"reversion_speed": 0.0, "arb_saturation": 0.0},
                confidence=0.1,
                data_points=0,
                interpretation="No extreme funding episodes observed yet. Rates have been mild.",
            )

        if n == 0:
            return BehaviorPrediction(
                model_name="funding_arb",
                scores={"reversion_speed": 0.0, "arb_saturation": 0.0},
                confidence=0.15,
                data_points=total,
                interpretation=f"{total} extreme episodes, none fully reverted yet. Slow reversion = less arb competition.",
                recommendations={"funding_extreme": 0.1},  # our edge is bigger
            )

        avg_duration = statistics.mean([e["duration"] for e in self.reversion_events])

        # Saturation score: fast reversion (1-2 snapshots) = high saturation
        # Slow reversion (10+ snapshots) = low saturation
        saturation = max(0.0, min(1.0, 1.0 - (avg_duration - 1) / 10))

        conf = _data_confidence(n, min_useful=2, full_confidence=20)

        # High saturation → reduce funding weight (arb bots already exploit it)
        funding_adj = 0.0
        if saturation > 0.7:
            funding_adj = -0.1
            interp = f"High arb saturation ({saturation:.0%}). Funding reverts in ~{avg_duration:.1f} snapshots. Many bots competing."
        elif saturation > 0.3:
            funding_adj = 0.0
            interp = f"Moderate arb activity ({saturation:.0%}). Reversion in ~{avg_duration:.1f} snapshots."
        else:
            funding_adj = 0.1
            interp = f"Low arb saturation ({saturation:.0%}). Slow reversion (~{avg_duration:.1f} snapshots). Good edge."

        return BehaviorPrediction(
            model_name="funding_arb",
            scores={
                "reversion_speed": round(avg_duration, 2),
                "arb_saturation": round(saturation, 4),
                "total_extremes": total,
                "reverted_count": n,
            },
            confidence=round(conf, 4),
            data_points=n,
            interpretation=interp,
            recommendations={"funding_extreme": funding_adj},
        )


# ── 3. Whale Behavior Patterns ──────────────────────────────────────────────

class WhaleBehaviorTracker:
    """
    Tracks OI spikes (sudden large position openings) and what happens after.
    Are whales right (price follows) or do they get hunted?
    """

    def __init__(self, spike_threshold: float = 0.005):
        """spike_threshold: minimum OI delta % to count as a 'whale' spike."""
        self.spike_threshold = spike_threshold
        self.spike_outcomes: list[dict] = []

    def fit(self, oi_history: list[dict]) -> None:
        """Analyze OI spikes and subsequent price action."""
        if len(oi_history) < 5:
            return

        # Extract BTC OI and price series
        entries = []
        for row in oi_history:
            btc = row.get("data", {}).get("BTC", {})
            oi = btc.get("open_interest_coins")
            price = btc.get("mid_price")
            delta = btc.get("oi_delta_pct")
            interp = btc.get("interpretation", "")
            if oi and price:
                entries.append({
                    "oi": float(oi), "price": float(price),
                    "delta": float(delta) if delta is not None else 0.0,
                    "interp": interp,
                })

        if len(entries) < 5:
            return

        # Find spikes and measure outcome over next few snapshots
        lookahead = min(3, len(entries) // 3)
        for i, e in enumerate(entries):
            if abs(e["delta"]) < self.spike_threshold:
                continue
            if i + lookahead >= len(entries):
                break

            future_price = entries[i + lookahead]["price"]
            price_change = (future_price - e["price"]) / e["price"]

            # Determine if whale was "right"
            if e["interp"] in ("new_longs_entering",):
                whale_direction = "long"
                was_right = price_change > 0
            elif e["interp"] in ("new_shorts_entering",):
                whale_direction = "short"
                was_right = price_change < 0
            else:
                whale_direction = "unknown"
                was_right = None

            self.spike_outcomes.append({
                "oi_delta": e["delta"],
                "direction": whale_direction,
                "price_change": price_change,
                "was_right": was_right,
            })

    def predict(self, current_data: dict[str, Any] | None = None) -> BehaviorPrediction:
        n = len(self.spike_outcomes)

        if n == 0:
            return BehaviorPrediction(
                model_name="whale_behavior",
                scores={"whale_accuracy": 0.5, "avg_impact": 0.0},
                confidence=0.0,
                data_points=0,
                interpretation="No whale-sized OI spikes detected in available data.",
            )

        known = [s for s in self.spike_outcomes if s["was_right"] is not None]
        accuracy = statistics.mean([1.0 if s["was_right"] else 0.0 for s in known]) if known else 0.5
        avg_impact = statistics.mean([abs(s["price_change"]) for s in self.spike_outcomes])

        conf = _data_confidence(n, min_useful=3, full_confidence=30)

        if accuracy > 0.6:
            interp = f"Whales are mostly right ({accuracy:.0%} accuracy). Follow their lead."
            oi_adj = 0.1
        elif accuracy < 0.4:
            interp = f"Whales are getting hunted ({accuracy:.0%} accuracy). Fade their entries."
            oi_adj = -0.05
        else:
            interp = f"Whale accuracy is coin-flip ({accuracy:.0%}). No clear signal."
            oi_adj = 0.0

        return BehaviorPrediction(
            model_name="whale_behavior",
            scores={
                "whale_accuracy": round(accuracy, 4),
                "avg_impact": round(avg_impact, 6),
                "spike_count": n,
            },
            confidence=round(conf, 4),
            data_points=n,
            interpretation=interp,
            recommendations={"oi_divergence": oi_adj},
        )


# ── 4. Crowding Detector ────────────────────────────────────────────────────

class CrowdingDetector:
    """
    Scores how 'crowded' the market is: extreme funding + high OI in one
    direction = crowded trade that will unwind violently.
    """

    def __init__(self):
        self.history: list[dict] = []

    def fit(self, funding_history: list[dict], oi_history: list[dict]) -> None:
        """Build history of crowding scores."""
        # Align funding and OI by index (they should have same count)
        n = min(len(funding_history), len(oi_history))
        for i in range(n):
            f_row = funding_history[i].get("data", {}).get("BTC", {})
            o_row = oi_history[i].get("data", {}).get("BTC", {})
            rate = f_row.get("current_rate", 0)
            oi = o_row.get("open_interest_coins", 0)
            price = o_row.get("mid_price", 0)
            if rate is not None and oi and price:
                self.history.append({
                    "rate": float(rate), "oi": float(oi), "price": float(price),
                })

    def _compute_score(self, rate: float, oi: float, oi_mean: float, oi_std: float) -> float:
        """
        Crowding = f(funding extremity, OI relative to mean).
        0 = balanced, 1 = extremely one-sided.
        """
        # Funding component: how extreme is funding? Sigmoid around 0.0005
        funding_component = 1.0 / (1.0 + math.exp(-15000 * (abs(rate) - 0.0003)))

        # OI component: how elevated is OI vs recent mean?
        if oi_std > 0:
            oi_z = (oi - oi_mean) / oi_std
            oi_component = min(1.0, max(0.0, oi_z / 3))  # z-score of 3 = max
        else:
            oi_component = 0.0

        # Combined: both need to be elevated for true crowding
        return min(1.0, (funding_component * 0.6 + oi_component * 0.4))

    def predict(self, current_data: dict[str, Any] | None = None) -> BehaviorPrediction:
        if len(self.history) < 3:
            return BehaviorPrediction(
                model_name="crowding",
                scores={"crowding_score": 0.0, "direction": "unknown"},
                confidence=0.0,
                data_points=len(self.history),
                interpretation="Insufficient data for crowding analysis.",
            )

        oi_values = [h["oi"] for h in self.history]
        oi_mean = statistics.mean(oi_values)
        oi_std = statistics.stdev(oi_values) if len(oi_values) > 1 else 0.0

        # Use latest data point (or current_data if provided)
        latest = self.history[-1]
        if current_data:
            btc = current_data.get("BTC", {})
            if "current_rate" in btc:
                latest["rate"] = float(btc["current_rate"])
            if "open_interest_coins" in btc:
                latest["oi"] = float(btc["open_interest_coins"])

        score = self._compute_score(latest["rate"], latest["oi"], oi_mean, oi_std)
        direction = "long_crowded" if latest["rate"] > 0 else "short_crowded" if latest["rate"] < 0 else "neutral"

        conf = _data_confidence(len(self.history))

        if score > 0.7:
            interp = f"HIGHLY CROWDED ({score:.0%}) — {direction}. Contrarian opportunity likely."
        elif score > 0.4:
            interp = f"Moderately crowded ({score:.0%}) — {direction}. Watch for unwind."
        else:
            interp = f"Not crowded ({score:.0%}). Balanced positioning."

        return BehaviorPrediction(
            model_name="crowding",
            scores={
                "crowding_score": round(score, 4),
                "direction": direction,
                "funding_rate": round(latest["rate"], 8),
                "oi": round(latest["oi"], 2),
            },
            confidence=round(conf, 4),
            data_points=len(self.history),
            interpretation=interp,
            recommendations={},  # Used by adaptation engine directly
        )


# ── Aggregate Model ─────────────────────────────────────────────────────────

class BotBehaviorModel:
    """Runs all sub-models and returns aggregate predictions."""

    def __init__(self):
        self.liq_hunter = LiquidationHunterDetector()
        self.funding_arb = FundingArbDetector()
        self.whale_tracker = WhaleBehaviorTracker()
        self.crowding = CrowdingDetector()

    def fit(self) -> None:
        """Load historical data and fit all models."""
        funding_history = _load_jsonl(HISTORICAL_DIR / "funding_rates.jsonl")
        oi_history = _load_jsonl(HISTORICAL_DIR / "oi_timeseries.jsonl")
        liq_snapshots = _load_liquidation_snapshots()

        logger.info("Fitting bot behavior models: %d funding, %d OI, %d liq snapshots",
                     len(funding_history), len(oi_history), len(liq_snapshots))

        self.liq_hunter.fit(oi_history, liq_snapshots)
        self.funding_arb.fit(funding_history)
        self.whale_tracker.fit(oi_history)
        self.crowding.fit(funding_history, oi_history)

    def predict(self, current_data: dict[str, Any] | None = None) -> list[BehaviorPrediction]:
        return [
            self.liq_hunter.predict(current_data),
            self.funding_arb.predict(current_data),
            self.whale_tracker.predict(current_data),
            self.crowding.predict(current_data),
        ]


# ── Standalone demo ──────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    model = BotBehaviorModel()
    model.fit()
    predictions = model.predict()

    print("\n" + "=" * 60)
    print("  BOT BEHAVIOR MODEL — PREDICTIONS")
    print("=" * 60)

    for p in predictions:
        print(f"\n  [{p.model_name}]  (confidence: {p.confidence:.0%}, data: {p.data_points} pts)")
        print(f"  {p.interpretation}")
        for k, v in p.scores.items():
            print(f"    {k}: {v}")
        if p.recommendations:
            print(f"  Recommendations: {p.recommendations}")


if __name__ == "__main__":
    main()
