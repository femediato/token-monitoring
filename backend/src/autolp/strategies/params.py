"""Trading strategy parameters, version strat-v1 (docs/STRATEGIES.md).

Changing any value here is a strategy change: bump STRATEGY_VERSION and document it, so
stored signals and replays stay reproducible. Strategies only ever produce paper decisions.
"""

from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any

STRATEGY_VERSION = "strat-v1"


@dataclass(frozen=True)
class ExitParams:
    take_profit_pct: float
    stop_loss_pct: float
    max_hold_hours: float
    # "fixed" (strat-v1): the percentages above. "atr" (research option): the stop sits
    # atr_mult x ATR below the entry (clamped to [min_stop_pct, max_stop_pct]) and the
    # target gives reward_risk x the risk, both after the round-trip cost.
    mode: str = "fixed"
    atr_mult: float = 3.0
    reward_risk: float = 2.0
    min_stop_pct: float = 2.0
    max_stop_pct: float = 15.0


@dataclass(frozen=True)
class ManageParams:
    """Stop management after the entry (research options, off by default). R is the
    initial risk: entry price minus the initial stop."""

    breakeven_at_r: float = 0  # once the high is this many R up, stop to entry + costs
    trail_atr: float = 0  # trail the stop this many ATRs below the highest high ...
    trail_after_r: float = 1.0  # ... once the high is this many R up


@dataclass(frozen=True)
class MomentumParams:
    """Strategy A: a 5-minute close crosses above the previous completed 1-hour high."""

    min_relative_volume: float = 1.5  # V1h / (V24h / 24)
    min_buying_score: float = 60  # Buying-pressure category score (model v1.1)
    min_hour_candles: int = 10  # of 12, for the reference 1-hour bar to count
    exit: ExitParams = field(default_factory=lambda: ExitParams(15, 7, 4))
    manage: ManageParams = field(default_factory=ManageParams)


@dataclass(frozen=True)
class PullbackParams:
    """Strategy B: uptrend (EMA20 > EMA50), 5-10% retrace from a recent high, recovery."""

    ema_fast: int = 20
    ema_slow: int = 50
    high_lookback_candles: int = 48  # "recent local high": the last 4 hours
    min_retrace_pct: float = 5
    max_retrace_pct: float = 10
    min_buy_share_improvement: float = 0.0  # buy share 1h now minus 1h earlier, must exceed
    exit: ExitParams = field(default_factory=lambda: ExitParams(15, 7, 8))
    manage: ManageParams = field(default_factory=ManageParams)


@dataclass(frozen=True)
class RangeParams:
    """Strategy C: two+ support and resistance tests, reversal near support, R/R >= 2."""

    window_candles: int = 72  # range is measured over the last 6 hours
    recent_candles: int = 3  # the current return to support; excluded from "prior" tests
    min_width_pct: float = 4.0
    max_width_pct: float = 30.0
    touch_tolerance: float = 0.15  # a touch is within 15% of the range width of the level
    swing_neighbours: int = 2  # a swing low/high is the extreme of +-2 candles
    min_touch_gap_candles: int = 6  # separate tests are >= 30 minutes apart
    min_support_tests: int = 2
    min_resistance_tests: int = 2
    stop_buffer_pct: float = 1.5  # stop this far below support ("invalidated support")
    min_reward_risk: float = 2.0  # after expected round-trip costs
    # Not fixed by the plan ("defined during calibration"); provisional until Phase 6.
    max_hold_hours: float = 12
    # Research option: also keep the stop at least atr_stop_mult x ATR below the entry
    # (the wider of the two stops); 0 = off.
    atr_stop_mult: float = 0
    manage: ManageParams = field(default_factory=ManageParams)


@dataclass(frozen=True)
class RandomParams:
    """Research baseline, never enabled live: enter at random (a deterministic hash of the
    token and the candle) with the shared checks, filters and the given exits. Comparing a
    strategy with random entries under the same exits measures its entry timing."""

    prob_pct: float = 2.0  # chance per token and 5-minute candle
    seed: str = "random-entry"
    exit: ExitParams = field(default_factory=lambda: ExitParams(15, 7, 4))
    manage: ManageParams = field(default_factory=ManageParams)


@dataclass(frozen=True)
class FilterParams:
    """Entry filters for every strategy (research options, off by default). A filter that
    is on and lacks its input fails: missing data never counts as a pass."""

    # Market regime: share of eligible tokens whose 4h return (ret_4h_pct of their latest
    # fresh evaluation) is positive. 0 = off.
    min_breadth_pct: float = 0
    min_breadth_tokens: int = 8  # fewer tokens with a 4h return -> breadth is missing
    # The token's own 4h return must lie in [min, max]; None = no bound.
    min_ret_4h_pct: float | None = None
    max_ret_4h_pct: float | None = None


@dataclass(frozen=True)
class RiskParams:
    """Shared portfolio rules (master plan, Phase 5)."""

    starting_equity_usd: float = 10_000
    risk_per_trade_pct: float = 0.5  # planned loss at the stop, incl. costs
    max_positions: int = 3
    max_open_risk_pct: float = 1.5
    daily_loss_stop_pct: float = 3.0  # no new entries after this loss in a UTC day
    one_position_per_token: bool = True
    # Execution-cost estimate: the latest paired sell quote.
    max_quote_age_minutes: float = 45
    max_cost_pct: float = 2.0
    # Positions are capped at the size the cost estimate was measured for, and a share of
    # the token's liquidity; anything below the minimum order is rejected.
    quoted_size_usd: float = 1000  # = collection.quote_sell_usd
    max_liquidity_share_pct: float = 1.0
    min_order_usd: float = 25
    fee_usd_per_swap: float = 0.05  # network + priority fee estimate
    # Research options, off by default:
    # - size for the stop overshoot too (loss per $ = stop distance + costs + this);
    expected_stop_slippage_pct: float = 0
    # - cancel an entry whose fill price is above entry_ref + this x (entry_ref - stop);
    max_entry_drift_r: float = 0
    # - no new entries for pause_hours after this many losses in a row, or after equity
    #   falls this far below its peak.
    max_consecutive_losses: int = 0
    drawdown_pause_pct: float = 0
    pause_hours: float = 24


@dataclass(frozen=True)
class DataParams:
    """Inputs older than this count as missing: no decision is made on stale data."""

    max_candle_age_minutes: float = 25  # end of the last completed candle
    max_evaluation_age_minutes: float = 20
    candles_needed: int = 120  # 10 hours: EMA50 warm-up and the range window
    prev_evaluation_minutes: tuple[float, float] = (45, 75)  # "an hour earlier" window


@dataclass(frozen=True)
class StrategyParams:
    momentum: MomentumParams = field(default_factory=MomentumParams)
    pullback: PullbackParams = field(default_factory=PullbackParams)
    range: RangeParams = field(default_factory=RangeParams)
    risk: RiskParams = field(default_factory=RiskParams)
    data: DataParams = field(default_factory=DataParams)
    random: RandomParams = field(default_factory=RandomParams)
    filters: FilterParams = field(default_factory=FilterParams)
    atr_candles: int = 48  # ATR: mean true range of the last 4 hours of 5-minute candles
    enabled: tuple[str, ...] = ("momentum", "pullback", "range")


PARAMS = StrategyParams()


def override(obj: Any, path: str, value: Any) -> Any:
    """Return a copy of a (nested, frozen) dataclass with `path` (a.b.c) set to value."""
    head, _, rest = path.partition(".")
    if isinstance(obj, dict):
        return {**obj, head: override(obj[head], rest, value) if rest else value}
    if not is_dataclass(obj) or head not in {f.name for f in fields(obj)}:
        raise SystemExit(f"unknown parameter: {head}")
    cur = getattr(obj, head)
    if isinstance(cur, tuple) and isinstance(value, list):
        value = tuple(value)
    return replace(obj, **{head: override(cur, rest, value) if rest else value})  # type: ignore[type-var]


def with_overrides(p: StrategyParams, overrides: dict[str, Any]) -> StrategyParams:
    """Strategy parameters with `{"momentum.exit.mode": "atr", ...}` applied."""
    for key, value in overrides.items():
        p = override(p, key, value)
    return p
