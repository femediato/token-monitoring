"""Job registry: names, cadences and wall-clock alignment shared by the collector and the
monitoring endpoints (which compare actual runs against the expected cadence)."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from autolp.collector.jobs import Collector
from autolp.collector.runs import JobFn
from autolp.config import Settings
from autolp.db.models import TrackingStatus


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
        JobSpec("security", 60, 100, lambda col: col.security, run_on_start=True),
        JobSpec("candles", c.candles_minutes, 130, lambda col: col.candles),
        JobSpec("quotes", c.quotes_minutes, 160, lambda col: col.quotes),
        JobSpec("holders", 60, 190, lambda col: col.holders),
        JobSpec("candle_backfill", 15, 220, lambda col: col.backfill_candles),
        JobSpec("tracking", 60, 280, lambda col: col.update_tracking),
    ]


def expected_minutes(settings: Settings) -> dict[str, int]:
    return {s.name: s.every_minutes for s in job_specs(settings)}
