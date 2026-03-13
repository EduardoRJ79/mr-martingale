"""
Regime Detector — Phase 4

Classifies the current market regime using rolling windows of price, OI,
funding, and volatility.  Rules-based (not ML) — we don't have enough data yet.

Regimes:
  TRENDING_UP, TRENDING_DOWN, RANGE_BOUND, CHOPPY, CASCADE, COMPRESSION

Each regime carries recommended signal weight adjustments and risk scaling.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "intelligence" / "data"
HISTORICAL_DIR = DATA_DIR / "historical"


class Regime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGE_BOUND = "range_bound"
    CHOPPY = "choppy"
    CASCADE = "cascade"
    COMPRESSION = "compression"
    UNKNOWN = "unknown"


# Per-regime recommendations: signal weight multipliers and risk scale
REGIME_PROFILES: dict[Regime, dict[str, Any]] = {
    Regime.TRENDING_UP: {
        "signal_weights": {"liquidation_cascade": 0.3, "funding_extreme": 0.2, "oi_divergence": 0.5},
        "risk_scale": 1.2,  # ride the trend
        "description": "Strong uptrend. Favor OI momentum, reduce mean-reversion signals.",
    },
    Regime.TRENDING_DOWN: {
        "signal_weights": {"liquidation_cascade": 0.3, "funding_extreme": 0.2, "oi_divergence": 0.5},
        "risk_scale": 1.0,
        "description": "Strong downtrend. Favor OI momentum for shorts.",
    },
    Regime.RANGE_BOUND: {
        "signal_weights": {"liquidation_cascade": 0.25, "funding_extreme": 0.45, "oi_divergence": 0.3},
        "risk_scale": 0.9,
        "description": "Range-bound. Favor funding mean-reversion at extremes.",
    },
    Regime.CHOPPY: {
        "signal_weights": {"liquidation_cascade": 0.35, "funding_extreme": 0.35, "oi_divergence": 0.3},
        "risk_scale": 0.6,  # reduce size in choppy markets
        "description": "High volatility / choppy. Reduce size, be selective.",
    },
    Regime.CASCADE: {
        "signal_weights": {"liquidation_cascade": 0.55, "funding_extreme": 0.15, "oi_divergence": 0.3},
        "risk_scale": 0.5,  # dangerous environment
        "description": "Liquidation cascade in progress. Liq signal dominant, reduce risk.",
    },
    Regime.COMPRESSION: {
        "signal_weights": {"liquidation_cascade": 0.35, "funding_extreme": 0.25, "oi_divergence": 0.4},
        "risk_scale": 1.1,  # breakout imminent, be ready
        "description": "Volatility compression, OI building. Breakout imminent.",
    },
    Regime.UNKNOWN: {
        "signal_weights": {"liquidation_cascade": 0.40, "funding_extreme": 0.25, "oi_divergence": 0.35},
        "risk_scale": 0.8,
        "description": "Insufficient data to classify regime.",
    },
}


@dataclass
class RegimeClassification:
    regime: Regime
    confidence: float  # 0-1
    risk_scale: float
    signal_weights: dict[str, float]
    description: str
    metrics: dict[str, float] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)  # recent regime history


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


class RegimeDetector:
    """Rules-based regime classifier."""

    def __init__(self, window: int = 10):
        """window: number of recent data points to analyze."""
        self.window = window
        self.regime_history: list[Regime] = []

    def _extract_series(self, oi_history: list[dict], funding_history: list[dict], asset: str = "BTC"):
        """Extract aligned price, OI, funding series for an asset."""
        prices, ois, fundings = [], [], []

        for row in oi_history[-self.window * 2:]:
            d = row.get("data", {}).get(asset, {})
            p = d.get("mid_price")
            o = d.get("open_interest_coins")
            if p and o:
                prices.append(float(p))
                ois.append(float(o))

        for row in funding_history[-self.window * 2:]:
            d = row.get("data", {}).get(asset, {})
            r = d.get("current_rate")
            if r is not None:
                fundings.append(float(r))

        return prices, ois, fundings

    def classify(self, oi_history: list[dict], funding_history: list[dict],
                 asset: str = "BTC") -> RegimeClassification:
        """Classify current regime from historical data."""
        prices, ois, fundings = self._extract_series(oi_history, funding_history, asset)

        if len(prices) < 5:
            profile = REGIME_PROFILES[Regime.UNKNOWN]
            return RegimeClassification(
                regime=Regime.UNKNOWN, confidence=0.0,
                risk_scale=profile["risk_scale"],
                signal_weights=profile["signal_weights"],
                description="Insufficient data (<5 price points).",
                metrics={"data_points": len(prices)},
            )

        # Compute metrics
        n = min(self.window, len(prices))
        recent_prices = prices[-n:]
        recent_ois = ois[-n:] if len(ois) >= n else ois
        recent_fundings = fundings[-n:] if len(fundings) >= n else fundings

        # Price trend: linear regression slope normalized by mean
        x = np.arange(len(recent_prices), dtype=float)
        y = np.array(recent_prices, dtype=float)
        if len(x) > 1:
            slope = np.polyfit(x, y, 1)[0]
            price_trend = slope / np.mean(y)  # normalized slope
        else:
            price_trend = 0.0

        # Volatility: std of returns
        returns = [(recent_prices[i] - recent_prices[i - 1]) / recent_prices[i - 1]
                    for i in range(1, len(recent_prices))]
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0.0

        # Volatility trend: is vol increasing or decreasing?
        if len(returns) >= 6:
            first_half_vol = statistics.stdev(returns[:len(returns) // 2])
            second_half_vol = statistics.stdev(returns[len(returns) // 2:])
            vol_trend = (second_half_vol - first_half_vol) / (first_half_vol + 1e-10)
        else:
            vol_trend = 0.0

        # OI trend
        if len(recent_ois) > 1:
            oi_x = np.arange(len(recent_ois), dtype=float)
            oi_y = np.array(recent_ois, dtype=float)
            oi_slope = np.polyfit(oi_x, oi_y, 1)[0]
            oi_trend = oi_slope / np.mean(oi_y)
        else:
            oi_trend = 0.0

        # OI volatility (for cascade detection — sharp OI drops)
        if len(recent_ois) > 2:
            oi_changes = [(recent_ois[i] - recent_ois[i-1]) / recent_ois[i-1]
                          for i in range(1, len(recent_ois))]
            oi_vol = statistics.stdev(oi_changes)
            min_oi_change = min(oi_changes)
        else:
            oi_vol = 0.0
            min_oi_change = 0.0

        # Funding magnitude
        avg_funding = statistics.mean(recent_fundings) if recent_fundings else 0.0

        metrics = {
            "price_trend": round(price_trend, 6),
            "volatility": round(volatility, 6),
            "vol_trend": round(vol_trend, 4),
            "oi_trend": round(oi_trend, 6),
            "oi_vol": round(oi_vol, 6),
            "avg_funding": round(avg_funding, 8),
            "data_points": len(recent_prices),
        }

        # ── Classification rules ──
        regime = Regime.UNKNOWN
        confidence = 0.5

        # CASCADE: sharp OI drop + large price move
        if min_oi_change < -0.01 and volatility > 0.005:
            regime = Regime.CASCADE
            confidence = min(1.0, abs(min_oi_change) * 20 + volatility * 50)

        # COMPRESSION: decreasing volatility + building OI
        elif vol_trend < -0.3 and oi_trend > 0.0001:
            regime = Regime.COMPRESSION
            confidence = min(1.0, abs(vol_trend) * 0.8 + oi_trend * 1000)

        # TRENDING UP: positive price slope + OI rising or flat
        elif price_trend > 0.0005 and oi_trend >= -0.0001:
            regime = Regime.TRENDING_UP
            confidence = min(1.0, price_trend * 500 + max(0, oi_trend * 500))

        # TRENDING DOWN: negative price slope + OI rising or flat
        elif price_trend < -0.0005 and oi_trend >= -0.0001:
            regime = Regime.TRENDING_DOWN
            confidence = min(1.0, abs(price_trend) * 500 + max(0, oi_trend * 500))

        # CHOPPY: high volatility, no clear direction
        elif volatility > 0.003:
            regime = Regime.CHOPPY
            confidence = min(1.0, volatility * 100)

        # RANGE_BOUND: low trend, low-moderate vol
        elif abs(price_trend) < 0.0003:
            regime = Regime.RANGE_BOUND
            confidence = min(1.0, (0.0003 - abs(price_trend)) * 2000)

        # Fallback: moderate trend
        elif price_trend > 0:
            regime = Regime.TRENDING_UP
            confidence = 0.3
        else:
            regime = Regime.TRENDING_DOWN
            confidence = 0.3

        confidence = round(min(1.0, confidence), 4)
        self.regime_history.append(regime)

        profile = REGIME_PROFILES[regime]
        return RegimeClassification(
            regime=regime,
            confidence=confidence,
            risk_scale=profile["risk_scale"],
            signal_weights=profile["signal_weights"],
            description=profile["description"],
            metrics=metrics,
            history=[r.value for r in self.regime_history[-10:]],
        )


# ── Standalone demo ──────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    oi_history = _load_jsonl(HISTORICAL_DIR / "oi_timeseries.jsonl")
    funding_history = _load_jsonl(HISTORICAL_DIR / "funding_rates.jsonl")

    detector = RegimeDetector(window=15)
    result = detector.classify(oi_history, funding_history)

    print("\n" + "=" * 60)
    print("  REGIME DETECTOR")
    print("=" * 60)
    print(f"\n  Regime:     {result.regime.value}")
    print(f"  Confidence: {result.confidence:.0%}")
    print(f"  Risk Scale: {result.risk_scale}x")
    print(f"  {result.description}")
    print(f"\n  Signal Weights: {result.signal_weights}")
    print(f"\n  Metrics:")
    for k, v in result.metrics.items():
        print(f"    {k}: {v}")
    if result.history:
        print(f"\n  Regime History: {' → '.join(result.history)}")


if __name__ == "__main__":
    main()
