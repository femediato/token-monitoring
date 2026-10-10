"""Scoring model v1.1 (docs/SCORING.md). Pure functions of the feature dict.

Each component returns 0-100 or None (missing). A category is the weighted mean of its
available components; the total is the weighted mean of available categories, and
`coverage` records how much of the model's weight had data.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autolp.selection.params import CATEGORY_WEIGHTS, MODEL_VERSION


def N(x: float | None, a: float, b: float) -> float | None:
    """Linear normalization of x from a (0 points) to b (100 points), clamped."""
    if x is None:
        return None
    return 100 * min(1.0, max(0.0, (x - a) / (b - a)))


def tent(x: float | None, low: float, peak: float, high: float) -> float | None:
    """0 at `low`, 100 at `peak`, back to 0 at `high`; 0 outside."""
    if x is None:
        return None
    if x <= low or x >= high:
        return 0.0
    return 100 * (x - low) / (peak - low) if x <= peak else 100 * (high - x) / (high - peak)


def plateau(x: float | None, rise_to: float, flat_to: float, zero_at: float) -> float | None:
    """0 at 0 rising to 100 at `rise_to`, 100 until `flat_to`, falling to 0 at `zero_at`."""
    if x is None:
        return None
    if x <= 0:
        return 0.0
    if x < rise_to:
        return 100 * x / rise_to
    if x <= flat_to:
        return 100.0
    return max(0.0, 100 * (zero_at - x) / (zero_at - flat_to))


def bucket_share(count: int | None, observed: int | None, minimum: int = 10) -> float | None:
    """Share of the last 12 five-minute buckets; missing if fewer than `minimum` observed."""
    if count is None or observed is None or observed < minimum:
        return None
    return 100 * count / observed


def distance_from_high(d: float | None) -> float | None:
    if d is None:
        return None
    return 100.0 if d <= 10 else max(0.0, 100 * (30 - d) / 20)


ORGANIC = {"high": 100.0, "medium": 50.0, "low": 0.0}

Component = tuple[str, float, Callable[[dict[str, Any]], float | None]]

COMPONENTS: dict[str, list[Component]] = {
    "activity": [
        ("relative_volume", 0.35, lambda f: N(f.get("relvol_ratio"), 0.5, 3)),
        ("trader_growth", 0.30, lambda f: N(f.get("trader_growth_ratio"), 0.7, 1.5)),
        ("transaction_acceleration", 0.20, lambda f: N(f.get("tx_accel_ratio"), 0.7, 1.75)),
        (
            "volume_consistency",
            0.15,
            lambda f: bucket_share(f.get("active_buckets"), f.get("buckets_observed")),
        ),
    ],
    "momentum": [
        ("return_1h", 0.30, lambda f: tent(f.get("ret_1h_pct"), -5, 8, 30)),
        ("return_4h", 0.30, lambda f: tent(f.get("ret_4h_pct"), -10, 15, 60)),
        ("trend_strength", 0.25, lambda f: N(f.get("ema_ratio"), 0, 0.04)),
        ("distance_from_high", 0.15, lambda f: distance_from_high(f.get("dist_from_4h_high_pct"))),
    ],
    "liquidity": [
        (
            "dex_liquidity",
            0.35,
            lambda f: (
                N(math.log10(f["liquidity_usd"]), 5.176, 6.301)
                if f.get("liquidity_usd") and f["liquidity_usd"] > 0
                else None
            ),
        ),
        ("volume_liquidity_ratio", 0.20, lambda f: plateau(f.get("vol_liq_ratio"), 1, 5, 15)),
        (
            "sell_execution_cost",
            0.30,
            lambda f: 100 - s if (s := N(f.get("sell_cost_pct"), 0.2, 2.0)) is not None else None,
        ),
        ("liquidity_stability", 0.15, lambda f: N(f.get("depth_ratio_24h"), 0.7, 1.0)),
    ],
    "buying": [
        ("buy_volume_share", 0.55, lambda f: N(f.get("buy_share_1h"), 0.45, 0.65)),
        ("net_buying_vs_liquidity", 0.30, lambda f: N(f.get("net_buy_liq_1h"), -0.05, 0.10)),
        (
            "buying_persistence",
            0.15,
            lambda f: bucket_share(f.get("positive_buckets"), f.get("buckets_observed")),
        ),
    ],
    "risk": [
        (
            "top10_concentration",
            0.40,
            lambda f: (
                100 - s if (s := N(_frac(f.get("top10_pct")), 0.15, 0.40)) is not None else None
            ),
        ),
        (
            "largest_holder",
            0.25,
            lambda f: (
                100 - s if (s := N(_frac(f.get("top1_pct")), 0.03, 0.15)) is not None else None
            ),
        ),
        # No data source yet: a constant for every token, so it never changes the ranking.
        ("liquidity_control", 0.20, lambda f: 50.0),
        ("trading_quality", 0.15, lambda f: ORGANIC.get(f.get("organic_label") or "")),
    ],
}

REASON_CODES = {
    "liquidity_control": "not_assessed",
    "trading_quality": "proxy:jupiter_organic_score_label",
}


def _frac(pct: float | None) -> float | None:
    return None if pct is None else pct / 100


@dataclass(frozen=True)
class Score:
    model_version: str
    categories: dict[str, float | None]
    components: dict[str, dict[str, float | None]]
    total: float | None
    coverage: float


def score(f: dict[str, Any]) -> Score:
    components: dict[str, dict[str, float | None]] = {}
    categories: dict[str, float | None] = {}
    coverage = 0.0
    for cat, comps in COMPONENTS.items():
        vals = {name: fn(f) for name, _, fn in comps}
        components[cat] = vals
        avail: list[tuple[float, float]] = [
            (w, v) for name, w, _ in comps if (v := vals[name]) is not None
        ]
        weight = sum(w for w, _ in avail)
        categories[cat] = sum(w * v for w, v in avail) / weight if weight else None
        coverage += CATEGORY_WEIGHTS[cat] * weight
    present = [(CATEGORY_WEIGHTS[c], v) for c, v in categories.items() if v is not None]
    total_w = sum(w for w, _ in present)
    total = sum(w * v for w, v in present) / total_w if total_w else None
    return Score(MODEL_VERSION, categories, components, total, round(coverage, 4))


def group_of(f: dict[str, Any], activity: float | None) -> str | None:
    """Watchlist group, assigned in the order established, emerging, breakout."""
    mc = f.get("market_cap")
    if mc is None:
        return None
    if mc >= 50_000_000:
        return "established"
    if mc >= 5_000_000 and activity is not None and activity >= 50:
        return "emerging"
    ret1h, ema, dist = f.get("ret_1h_pct"), f.get("ema_ratio"), f.get("dist_from_4h_high_pct")
    if (
        mc >= 1_000_000
        and ema is not None
        and ema > 0
        and ret1h is not None
        and abs(ret1h) < 5
        and dist is not None
        and dist <= 10
    ):
        return "breakout"
    return "other"
