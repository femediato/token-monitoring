"""Early opportunity detection, model early-v1 (docs/EARLY_DETECTION.md).

Separate from the ranking model. Seven binary signals, equal weight: each fired signal
adds 100/7 to the score. A signal whose input is missing does not fire (recorded as None).
An alert is a research signal, never a buy order.
"""

import math
from dataclasses import dataclass
from typing import Any

EARLY_MODEL_VERSION = "early-v1"


@dataclass(frozen=True)
class EarlyParams:
    volume_acceleration: float = 1.5  # 1h volume vs. average hour of the prior 23h
    buyer_growth: float = 1.30  # unique traders 1h vs. one hour earlier (proxy for buyers)
    buy_share: float = 0.55
    liquidity_growth: float = 1.10  # price-neutral pool depth vs. 6h earlier
    max_return_1h_pct: float = 8.0
    quality_labels: tuple[str, ...] = ("high", "medium")
    # States by score; with 7 binary signals: 6/7 = 85.7, 5/7 = 71.4, 4/7 = 57.1.
    high_priority: float = 85
    emerging: float = 70
    monitor: float = 50
    alert_min_score: float = 70
    alert_cooldown_hours: float = 6
    controls_per_alert: int = 3


EARLY = EarlyParams()

SIGNALS = (
    "volume_acceleration",
    "buyer_growth",
    "buying_pressure",
    "liquidity_growth",
    "price_restraint",
    "positive_trend",
    "participation_quality",
)


def _ge(x: float | None, threshold: float) -> bool | None:
    return None if x is None else x >= threshold


def signals(f: dict[str, Any], p: EarlyParams = EARLY) -> dict[str, bool | None]:
    v1, v24 = f.get("volume_1h"), f.get("volume_24h")
    baseline = (v24 - v1) / 23 if v1 is not None and v24 is not None and v24 > v1 else None
    label = f.get("organic_label")
    ret1h = f.get("ret_1h_pct")
    gap = f.get("ema20_gap")
    return {
        "volume_acceleration": (
            None if v1 is None or baseline is None else v1 >= p.volume_acceleration * baseline
        ),
        "buyer_growth": _ge(f.get("trader_growth_ratio"), p.buyer_growth),
        "buying_pressure": _ge(f.get("buy_share_1h"), p.buy_share),
        "liquidity_growth": _ge(f.get("depth_ratio_6h"), p.liquidity_growth),
        "price_restraint": None if ret1h is None else ret1h < p.max_return_1h_pct,
        "positive_trend": None if gap is None else gap > 0,
        "participation_quality": None if label is None else label in p.quality_labels,
    }


@dataclass(frozen=True)
class EarlyScore:
    score: float
    state: str  # high_priority | emerging | monitor | none
    signals: dict[str, bool | None]

    @property
    def fired(self) -> list[str]:
        return [k for k, v in self.signals.items() if v]


def state_of(score: float, p: EarlyParams = EARLY) -> str:
    if score >= p.high_priority:
        return "high_priority"
    if score >= p.emerging:
        return "emerging"
    if score >= p.monitor:
        return "monitor"
    return "none"


def early_score(f: dict[str, Any], p: EarlyParams = EARLY) -> EarlyScore:
    sig = signals(f, p)
    score = 100 * sum(1 for v in sig.values() if v) / len(SIGNALS)
    return EarlyScore(round(score, 2), state_of(score, p), sig)


def match_distance(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Distance between two tokens for control matching: log10 market cap + log10 liquidity."""
    try:
        return abs(math.log10(a["market_cap"]) - math.log10(b["market_cap"])) + abs(
            math.log10(a["liquidity_usd"]) - math.log10(b["liquidity_usd"])
        )
    except (KeyError, TypeError, ValueError):
        return None
