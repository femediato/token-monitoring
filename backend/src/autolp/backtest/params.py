"""Backtest parameters, version bt-v2 (docs/BACKTESTING.md).

Changing any value here changes what a backtest measures: bump BACKTEST_VERSION and record
the change in docs/BACKTESTING.md. Every stored run keeps the full parameter set.
"""

from dataclasses import dataclass, field

from autolp.strategies.execution import ExecutionParams
from autolp.strategies.params import ExitParams

BACKTEST_VERSION = "bt-v2"


@dataclass(frozen=True)
class SplitParams:
    """Chronological split of the research window (master plan: days 1-45 development,
    46-60 validation, 61-90 untouched holdout). A shorter window is split in the same
    proportions; the holdout is only simulated when explicitly unsealed."""

    development_days: float = 45
    validation_days: float = 15
    holdout_days: float = 30

    @property
    def target_days(self) -> float:
        return self.development_days + self.validation_days + self.holdout_days


@dataclass(frozen=True)
class UniverseParams:
    size: int = 30
    # Random control: each selected token is replaced by one of the `random_nearest`
    # closest non-selected tokens by |dlog10 market cap| + |dlog10 liquidity|.
    random_nearest: int = 5
    # Baseline candidates: not critical, and at least this large (a third of the
    # eligibility floors), so the controls are tokens a trader could plausibly hold.
    candidate_min_market_cap: float = 300_000
    candidate_min_liquidity_usd: float = 50_000


@dataclass(frozen=True)
class EarlyEntryParams:
    """Backtest-only variant "early on": enter on an early-detection alert (Phase 4) with
    the shared checks; exits as strategy A."""

    exit: ExitParams = field(default_factory=lambda: ExitParams(15, 7, 4))


@dataclass(frozen=True)
class BacktestParams:
    split: SplitParams = field(default_factory=SplitParams)
    universe: UniverseParams = field(default_factory=UniverseParams)
    early: EarlyEntryParams = field(default_factory=EarlyEntryParams)
    # Live candles reach the strategy job about 10 minutes after they close (GeckoTerminal
    # free tier, docs/STRATEGIES.md); signals in the backtest see them with this delay.
    candle_latency_minutes: float = 10
    forward_hours: tuple[int, ...] = (1, 4, 24)  # selection forward-return horizons
    seed: str = BACKTEST_VERSION
    scenarios: dict[str, ExecutionParams] = field(
        default_factory=lambda: {
            # Realistic: quoted cost, entry at the open after the decision, stops 1% below
            # the trigger, 2% failed swaps.
            "base": ExecutionParams(
                "base",
                entry_delay_minutes=0,
                cost_multiplier=1.0,
                stop_slippage_pct=1.0,
                swap_failure_pct=2.0,
                seed=BACKTEST_VERSION,
            ),
            # Paper-calibrated (bt-v2): what the first 43 paper fills (11-15 Oct 2026,
            # docs/PAPER_TRADING.md) measured - realised cost about the quoted cost, the fill
            # 0.7% worse than the candle price, stops 1.7% below the trigger, no failures.
            "paper": ExecutionParams(
                "paper",
                entry_delay_minutes=0,
                cost_multiplier=1.0,
                stop_slippage_pct=1.7,
                swap_failure_pct=0.0,
                price_shortfall_pct=0.7,
                seed=BACKTEST_VERSION,
            ),
            # Stress: 1.5x costs (the Phase 9 cost gate), a 5-minute later entry, stops 5%
            # below the trigger, 5% failed swaps.
            "stress": ExecutionParams(
                "stress",
                entry_delay_minutes=5,
                cost_multiplier=1.5,
                stop_slippage_pct=5.0,
                swap_failure_pct=5.0,
                seed=BACKTEST_VERSION,
            ),
        }
    )


@dataclass(frozen=True)
class Gates:
    """Phase 9 research gates, shown against every report (indicative only)."""

    min_oos_profit_factor: float = 1.3
    max_drawdown_pct: float = 15.0
    stress_profitable: bool = True


BT = BacktestParams()
GATES = Gates()

UNIVERSES = ("selection", "fixed", "watchlist", "mcap", "volume", "random", "early")
UNIVERSE_LABELS = {
    "selection": "Our selection (dynamic)",
    "fixed": "Our selection, fixed at start",
    "watchlist": "Top-30 watchlist (as recorded)",
    "mcap": "Top 30 by market cap",
    "volume": "Top 30 by 24h volume",
    "random": "Random, matched by size/liquidity",
    "early": "Our selection + early-alert entries",
    # Research sweeps only (backtest.data.load(research=True)).
    "liquid": "Any liquid token with candles (eligible or not)",
}
