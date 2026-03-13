"""
Signal Engine Phase 3 (Confluence)
1. Funding Rate Extremes (P99 mean-reversion)
2. OI Divergence (volume+price proxy)
3. Liquidation Cascade Proxy (price action)
4. Order Book Imbalance (forward-only, live L2)
"""
from __future__ import annotations
import logging, math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
import numpy as np
import yaml

logger = logging.getLogger(__name__)

class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"

@dataclass
class SignalResult:
    name: str
    direction: Direction
    confidence: float
    reasoning: str
    metadata: dict[str, Any] = field(default_factory=dict)
    @property
    def is_active(self):
        return self.confidence > 0 and self.direction != Direction.NEUTRAL

def _load_config():
    cfg = Path(__file__).parent.parent / "execution" / "config.yaml"
    if cfg.exists():
        return yaml.safe_load(cfg.read_text()) or {}
    return {}

class FundingRateExtremeSignal:
    """P99 mean-reversion funding signal."""
    def __init__(self, percentile_threshold=99.0, absolute_threshold=None,
                 rolling_window=2000, mode="classic"):
        self.percentile_threshold = percentile_threshold
        self.absolute_threshold = absolute_threshold
        self.rolling_window = rolling_window
        self.mode = mode

    def evaluate_from_history(self, current_rate, recent_rates):
        if len(recent_rates) < 100:
            return SignalResult("funding_extreme", Direction.NEUTRAL, 0.0, "Insufficient history")
        threshold = self.absolute_threshold
        if threshold is None:
            ar = [abs(r) for r in recent_rates[-self.rolling_window:]]
            threshold = float(np.percentile(ar, self.percentile_threshold))
        if threshold == 0: threshold = 1e-10
        if abs(current_rate) < threshold:
            return SignalResult("funding_extreme", Direction.NEUTRAL, 0.0,
                f"Rate {current_rate:.8f} below P{self.percentile_threshold:.0f}")
        ext = abs(current_rate) / threshold
        conf = min(0.95, 0.3 + 0.4 * (1 - math.exp(-0.5 * (ext - 1))))
        if self.mode == "classic":
            d = Direction.SHORT if current_rate > 0 else Direction.LONG
            r = f"[MEAN-REVERT] {current_rate:.8f} at {ext:.1f}x P{self.percentile_threshold:.0f}"
        else:
            d = Direction.LONG if current_rate > 0 else Direction.SHORT
            r = f"[MOMENTUM] {current_rate:.8f} at {ext:.1f}x P{self.percentile_threshold:.0f}"
        return SignalResult("funding_extreme", d, round(conf, 4), r,
            metadata={"rate": current_rate, "threshold": threshold,
                      "extremity": round(ext, 2), "mode": self.mode})

    def evaluate(self, funding_data):
        rate = funding_data.get("current_rate", 0)
        if "recent_rates" in funding_data:
            return self.evaluate_from_history(rate, funding_data["recent_rates"])
        t = self.absolute_threshold or 0.00013
        if abs(rate) < t:
            return SignalResult("funding_extreme", Direction.NEUTRAL, 0.0, "Below threshold")
        return self.evaluate_from_history(rate, [rate] * 1000)

class OIDivergenceSignal:
    """Volume+price divergence as OI proxy."""
    def __init__(self, volume_spike_percentile=90.0, volume_window=24,
                 price_move_threshold=0.005):
        self.volume_spike_percentile = volume_spike_percentile
        self.volume_window = volume_window
        self.price_move_threshold = price_move_threshold

    def evaluate_from_candles(self, cc, rc):
        if len(rc) < self.volume_window:
            return SignalResult("oi_divergence", Direction.NEUTRAL, 0.0, "Insufficient history")
        rv = [c["volume"] for c in rc[-self.volume_window:]]
        vt = float(np.percentile(rv, self.volume_spike_percentile))
        mv = float(np.median(rv))
        cv = cc["volume"]
        pc = (cc["close"] - cc["open"]) / cc["open"]
        vr = cv / mv if mv > 0 else 1.0
        vs = cv >= vt
        bm = abs(pc) >= self.price_move_threshold

        if vs and bm:
            conf = min(0.8, 0.3 + 0.1 * (vr - 1))
            d = Direction.LONG if pc > 0 else Direction.SHORT
            reg = "new_longs" if pc > 0 else "new_shorts"
            return SignalResult("oi_divergence", d, round(conf, 4),
                f"Vol {vr:.1f}x + {pc*100:+.2f}%: {reg}",
                metadata={"regime": reg, "vol_ratio": round(vr, 2),
                          "price_change_pct": round(pc*100, 4)})
        elif not vs and bm:
            conf = min(0.5, 0.15 + 0.05 * abs(pc) / self.price_move_threshold)
            if pc > 0:
                return SignalResult("oi_divergence", Direction.SHORT, round(conf, 4),
                    f"Up {pc*100:+.2f}% low vol: weak",
                    metadata={"regime": "weak_rally", "vol_ratio": round(vr, 2)})
            else:
                return SignalResult("oi_divergence", Direction.LONG, round(conf, 4),
                    f"Down {pc*100:+.2f}% low vol: weak",
                    metadata={"regime": "weak_selloff", "vol_ratio": round(vr, 2)})
        return SignalResult("oi_divergence", Direction.NEUTRAL, 0.0, "No action",
                            metadata={"vol_ratio": round(vr, 2)})

    def evaluate(self, oi_data):
        interp = oi_data.get("interpretation", "no_previous_data")
        od = oi_data.get("oi_delta_pct"); pd = oi_data.get("price_delta_pct")
        if interp == "no_previous_data" or od is None or pd is None:
            return SignalResult("oi_divergence", Direction.NEUTRAL, 0.0, "No OI data")
        bc = min(1.0, (abs(od)+abs(pd))*10)
        dm = {"new_longs_entering":(Direction.LONG,0.6),"new_shorts_entering":(Direction.SHORT,0.6),
              "shorts_closing":(Direction.SHORT,0.4),"longs_closing":(Direction.LONG,0.4)}
        if interp in dm:
            d,m = dm[interp]
            return SignalResult("oi_divergence",d,round(bc*m,4),f"OI {interp}",metadata={"regime":interp})
        return SignalResult("oi_divergence", Direction.NEUTRAL, 0.0, f"Unknown: {interp}")

class LiquidationCascadeProxySignal:
    """Cascade patterns from price action: long wicks, V-shapes."""
    def __init__(self, wick_ratio_threshold=0.80, volatility_window=24,
                 volatility_spike_percentile=95.0, cascade_lookback=3):
        self.wick_ratio_threshold = wick_ratio_threshold
        self.volatility_window = volatility_window
        self.volatility_spike_percentile = volatility_spike_percentile
        self.cascade_lookback = cascade_lookback

    def evaluate_from_candles(self, cc, rc):
        if len(rc) < self.volatility_window:
            return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "Insufficient history")
        cr = cc["high"] - cc["low"]
        if cr == 0:
            return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "Zero range")
        body = abs(cc["close"] - cc["open"])
        wr = 1.0 - (body / cr)
        rrs = [c["high"] - c["low"] for c in rc[-self.volatility_window:]]
        vp = float(np.percentile(rrs, self.volatility_spike_percentile))
        mr = float(np.median(rrs))
        rr = cr / mr if mr > 0 else 1.0

        if wr >= self.wick_ratio_threshold and cr >= vp:
            lw = min(cc["open"], cc["close"]) - cc["low"]
            uw = cc["high"] - max(cc["open"], cc["close"])
            if lw > uw * 1.5:
                cf = min(0.8, 0.3 + 0.15 * (rr - 1))
                return SignalResult("liquidation_cascade", Direction.LONG, round(cf, 4),
                    f"Lower wick {wr:.0%} {rr:.1f}x vol: sell cascade done",
                    metadata={"pattern": "lower_wick", "wick_ratio": round(wr, 3),
                              "range_ratio": round(rr, 2)})
            elif uw > lw * 1.5:
                cf = min(0.8, 0.3 + 0.15 * (rr - 1))
                return SignalResult("liquidation_cascade", Direction.SHORT, round(cf, 4),
                    f"Upper wick {wr:.0%} {rr:.1f}x vol: buy cascade done",
                    metadata={"pattern": "upper_wick", "wick_ratio": round(wr, 3),
                              "range_ratio": round(rr, 2)})

        lb = min(self.cascade_lookback, len(rc))
        if lb < 2:
            return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "No pattern")
        w = rc[-lb:]
        cls = [c["close"] for c in w] + [cc["close"]]
        los = [c["low"] for c in w] + [cc["low"]]
        his = [c["high"] for c in w] + [cc["high"]]
        mi = int(np.argmin(los)); mx = int(np.argmax(his))

        if mi < len(cls)-1 and cls[0] > 0 and los[mi] > 0:
            drop = (cls[0] - los[mi]) / cls[0]
            rec = (cls[-1] - los[mi]) / los[mi]
            if drop > 0.02 and rec > drop * 0.5:
                cf = min(0.7, 0.2 + 0.1 * (drop / 0.02))
                return SignalResult("liquidation_cascade", Direction.LONG, round(cf, 4),
                    f"V-bottom: -{drop*100:.1f}% +{rec*100:.1f}%",
                    metadata={"pattern": "v_bottom", "drop_pct": round(drop*100, 2)})

        if mx < len(cls)-1 and cls[0] > 0 and his[mx] > 0:
            pump = (his[mx] - cls[0]) / cls[0]
            rej = (his[mx] - cls[-1]) / his[mx]
            if pump > 0.02 and rej > pump * 0.5:
                cf = min(0.7, 0.2 + 0.1 * (pump / 0.02))
                return SignalResult("liquidation_cascade", Direction.SHORT, round(cf, 4),
                    f"Inv-V: +{pump*100:.1f}% -{rej*100:.1f}%",
                    metadata={"pattern": "inv_v_top", "pump_pct": round(pump*100, 2)})

        return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "No cascade pattern")

    def evaluate(self, liq_data):
        nearby = liq_data.get("nearby_alerts", [])
        if not nearby:
            return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "No clusters")
        mid = liq_data["mid_price"]; oi = liq_data.get("open_interest", 0)
        lw = sum(1.0/a["leverage"] for a in nearby if a["side"]=="long")
        sw = sum(1.0/a["leverage"] for a in nearby if a["side"]=="short")
        tw = lw + sw
        if tw == 0:
            return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "Zero weight")
        of = min(1.0, math.log10(max(oi*mid, 1)) / 12)
        if lw > sw*1.2: d,dom = Direction.SHORT, lw/tw
        elif sw > lw*1.2: d,dom = Direction.LONG, sw/tw
        else:
            return SignalResult("liquidation_cascade", Direction.NEUTRAL, 0.0, "Balanced")
        cf = min(1.0, dom * of * 0.8)
        return SignalResult("liquidation_cascade", d, round(cf,4), f"Liq clusters: {d.value}",
                            metadata={"long_w":round(lw,4),"short_w":round(sw,4)})

class OrderBookImbalanceSignal:
    """L2 order book imbalance. Forward-only (no historical L2 data)."""
    def __init__(self, depth_levels=10, imbalance_threshold=0.6):
        self.depth_levels = depth_levels
        self.imbalance_threshold = imbalance_threshold

    def evaluate(self, book_data):
        bids = book_data.get("bids", [])[:self.depth_levels]
        asks = book_data.get("asks", [])[:self.depth_levels]
        if not bids or not asks:
            return SignalResult("book_imbalance", Direction.NEUTRAL, 0.0, "No book data")
        bv = sum(b["size"] for b in bids)
        av = sum(a["size"] for a in asks)
        total = bv + av
        if total == 0:
            return SignalResult("book_imbalance", Direction.NEUTRAL, 0.0, "Empty book")
        imb = (bv - av) / total
        if abs(imb) < self.imbalance_threshold:
            return SignalResult("book_imbalance", Direction.NEUTRAL, 0.0,
                f"Imbalance {imb:+.2f} below {self.imbalance_threshold}",
                metadata={"imbalance": round(imb,4), "bid_vol": round(bv,2), "ask_vol": round(av,2)})
        conf = min(0.9, 0.3 + 0.6*(abs(imb)-self.imbalance_threshold)/(1-self.imbalance_threshold))
        d = Direction.LONG if imb > 0 else Direction.SHORT
        return SignalResult("book_imbalance", d, round(conf,4),
            f"Book imbalance {imb:+.2f}: {'bid' if imb>0 else 'ask'} heavy",
            metadata={"imbalance": round(imb,4), "bid_vol": round(bv,2), "ask_vol": round(av,2)})


class ConfluenceSignal:
    """
    Aggregates individual signal results into a single confluence score (0-100).
    Weights are normalised so they sum to 1.0 across the *present* signals.
    A trade fires only when the score exceeds ``min_confluence`` (default 25).
    """

    # Map from weight-key → signal-result-key in the results dict
    _SIGNAL_KEY_MAP = {
        "liquidation_cascade": "liquidation_cascade",
        "funding_extreme": "funding_extreme",
        "oi_divergence": "oi_divergence",
        "book_imbalance": "book_imbalance",
    }

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        min_confluence: float = 25.0,
    ):
        cfg = _load_config()
        self.min_confluence = cfg.get("signals", {}).get("min_confluence", min_confluence)
        self.weights = weights or {
            "liquidation_cascade": 0.40,
            "funding_extreme": 0.25,
            "oi_divergence": 0.35,
        }

    def evaluate(self, results: dict[str, SignalResult]) -> SignalResult:
        """Score and combine the individual signals."""
        active: list[str] = []
        weighted_long = 0.0
        weighted_short = 0.0
        total_weight = 0.0

        for wkey, skey in self._SIGNAL_KEY_MAP.items():
            sig = results.get(skey)
            if sig is None:
                continue
            w = self.weights.get(wkey, 0.0)
            if w <= 0:
                continue
            total_weight += w
            if sig.is_active:
                active.append(skey)
                score_contrib = sig.confidence * 100 * w
                if sig.direction == Direction.LONG:
                    weighted_long += score_contrib
                elif sig.direction == Direction.SHORT:
                    weighted_short += score_contrib

        if total_weight == 0:
            return SignalResult("confluence", Direction.NEUTRAL, 0.0,
                                "No weighted signals present", metadata={"score": 0, "active": []})

        # Normalise by total weight so partial-signal runs are comparable
        norm_long = weighted_long / total_weight
        norm_short = weighted_short / total_weight

        if norm_long >= norm_short:
            direction = Direction.LONG
            score = norm_long
        else:
            direction = Direction.SHORT
            score = norm_short

        # Opposing signals reduce the net score
        net_score = score - min(norm_long, norm_short) * 0.5  # penalty for conflict
        net_score = max(0.0, net_score)

        is_active = net_score >= self.min_confluence and len(active) >= 2
        confidence = min(0.95, net_score / 100.0) if is_active else 0.0

        if not is_active:
            direction = Direction.NEUTRAL

        parts = ", ".join(active) if active else "none"
        reasoning = (
            f"Confluence {net_score:.1f}/100 "
            f"({'ABOVE' if is_active else 'below'} {self.min_confluence}) "
            f"| active: {parts}"
        )

        return SignalResult(
            "confluence", direction, round(confidence, 4), reasoning,
            metadata={"score": round(net_score, 2), "active": active,
                      "raw_long": round(norm_long, 2), "raw_short": round(norm_short, 2)},
        )


def evaluate_all(funding_data=None, candle_data=None, oi_data=None, liq_data=None, book_data=None,
                 funding_signal=None, oi_signal=None, liq_signal=None, book_signal=None,
                 confluence_signal=None):
    fs = funding_signal or FundingRateExtremeSignal()
    os_ = oi_signal or OIDivergenceSignal()
    ls = liq_signal or LiquidationCascadeProxySignal()
    bs = book_signal or OrderBookImbalanceSignal()
    cs = confluence_signal or ConfluenceSignal()
    results = {}
    if funding_data:
        results["funding_extreme"] = fs.evaluate(funding_data)
    if candle_data and "current" in candle_data and "recent" in candle_data:
        results["oi_divergence"] = os_.evaluate_from_candles(candle_data["current"], candle_data["recent"])
        results["liquidation_cascade"] = ls.evaluate_from_candles(candle_data["current"], candle_data["recent"])
    elif oi_data:
        results["oi_divergence"] = os_.evaluate(oi_data)
    if liq_data and "nearby_alerts" in liq_data:
        results["liquidation_cascade"] = ls.evaluate(liq_data)
    if book_data:
        results["book_imbalance"] = bs.evaluate(book_data)
    # Run confluence aggregation over whatever individual signals we collected
    results["confluence"] = cs.evaluate(results)
    return results
