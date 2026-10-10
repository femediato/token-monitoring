"""Scoring model v1.1 parameters (docs/SCORING.md). Changing any value here is a model
change: bump MODEL_VERSION and document it, so stored scores stay reproducible."""

from dataclasses import dataclass, field

MODEL_VERSION = "v1.1"


@dataclass(frozen=True)
class EligibilityParams:
    min_age_days: float = 7
    min_market_cap: float = 1_000_000
    max_market_cap: float = 300_000_000
    min_liquidity_usd: float = 150_000
    min_volume_24h_usd: float = 500_000
    min_trades_24h: int = 500
    min_traders_24h: int = 150
    min_pool_age_hours: float = 48
    max_top10_pct: float = 40.0
    max_sell_cost_pct: float = 2.0
    # Freshness: older inputs count as missing, and missing safety data means ineligible.
    max_snapshot_age_minutes: float = 15
    max_security_age_hours: float = 48
    max_holders_age_hours: float = 48
    max_quote_age_minutes: float = 120
    # Any of these Token-2022 / authority flags is a critical safety failure.
    critical_flags: tuple[str, ...] = (
        "mint_authority_active",
        "freeze_authority_active",
        "permanent_delegate",
        "transfer_hook",
        "transfer_fee",
        "pausable",
        "non_transferable",
        "default_frozen",
        "mint_close_authority",
    )


@dataclass(frozen=True)
class WatchlistParams:
    size: int = 30
    group_targets: dict[str, int] = field(
        default_factory=lambda: {"established": 10, "emerging": 10, "breakout": 10}
    )
    entry_score: float = 70
    retain_score: float = 55
    outscore_margin: float = 10
    min_coverage: float = 0.8  # share of total model weight with data, required to enter
    membership_every_hours: int = 6  # non-safety changes only at 00/06/12/18 UTC
    max_replacements_per_day: int = 6


CATEGORY_WEIGHTS = {
    "activity": 0.25,
    "momentum": 0.25,
    "liquidity": 0.20,
    "buying": 0.15,
    "risk": 0.15,
}

ELIGIBILITY = EligibilityParams()
WATCHLIST = WatchlistParams()
