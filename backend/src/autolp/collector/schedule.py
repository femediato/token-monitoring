"""Job registry: names, cadences and wall-clock alignment shared by the collector and the
monitoring endpoints (which compare actual runs against the expected cadence)."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from autolp.collector.jobs import Collector
from autolp.collector.runs import JobFn
from autolp.config import Settings
from autolp.db.models import TrackingStatus
from autolp.paper.engine import run_paper
from autolp.paper.params import PAPER
from autolp.selection.engine import run_scoring
from autolp.strategies.live import run_strategy_signals


@dataclass(frozen=True)
class JobSpec:
    name: str
    every_minutes: int
    offset_seconds: int  # stagger so jobs don't hit providers at the same instant
    make: Callable[[Collector], JobFn]
    run_on_start: bool = False


def job_specs(settings: Settings) -> list[JobSpec]:
    c = settings.collection
    return [
        JobSpec("discovery", c.discovery_minutes, 0, lambda col: col.discover, run_on_start=True),
        JobSpec(
            "snapshots_active",
            c.snapshot_minutes,
            20,
            lambda col: partial(col.snapshots, status=TrackingStatus.ACTIVE),
        ),
        JobSpec(
            "snapshots_dormant",
            c.dormant_snapshot_minutes,
            40,
            lambda col: partial(col.snapshots, status=TrackingStatus.DORMANT),
        ),
        JobSpec("pools", c.snapshot_minutes, 50, lambda col: col.pools),
        JobSpec("pool_discovery", 60, 70, lambda col: col.pool_discovery, run_on_start=True),
        JobSpec("security", 60, 100, lambda col: col.security, run_on_start=True),
        JobSpec("candles", c.candles_minutes, 130, lambda col: col.candles),
        JobSpec("quotes", c.quotes_minutes, 160, lambda col: col.quotes),
        JobSpec("holders", 60, 190, lambda col: col.holders),
        JobSpec("candle_backfill", 15, 220, lambda col: col.backfill_candles),
        JobSpec("tracking", 60, 280, lambda col: col.update_tracking),
        # Selection (Phase 3): reads stored data only, never calls providers.
        JobSpec("scoring", 15, 240, lambda col: partial(run_scoring, col.factory)),
        # Early detection (Phase 4): entry quotes right after alerts, outcomes as they mature.
        JobSpec("alert_entry_quotes", 5, 270, lambda col: col.alert_entry_quotes),
        JobSpec("alert_outcomes", 15, 290, lambda col: col.alert_outcomes),
        # Strategies (Phase 5): after the candles job has fetched the quarter's candles.
        JobSpec(
            "strategy_signals", 15, 600, lambda col: partial(run_strategy_signals, col.factory)
        ),
        # Paper trading (Phase 7): executable quotes only, nothing is signed or sent.
        JobSpec(
            "paper_trading",
            PAPER.tick_minutes,
            30,
            lambda col: partial(run_paper, col.factory, col.jup),
        ),
    ]


def expected_minutes(settings: Settings) -> dict[str, int]:
    return {s.name: s.every_minutes for s in job_specs(settings)}
