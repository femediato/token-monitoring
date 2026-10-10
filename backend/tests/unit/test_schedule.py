from datetime import UTC, datetime

from autolp.collector.main import aligned_start
from autolp.collector.schedule import job_specs
from autolp.config import Settings


def test_aligned_start_is_on_wall_clock_boundary_plus_offset() -> None:
    now = datetime(2026, 10, 10, 12, 7, 30, tzinfo=UTC)
    assert aligned_start(5, 20, now) == datetime(2026, 10, 10, 12, 10, 20, tzinfo=UTC)
    assert aligned_start(60, 100, now) == datetime(2026, 10, 10, 13, 1, 40, tzinfo=UTC)
    early = datetime(2026, 10, 10, 12, 5, 10, tzinfo=UTC)
    assert aligned_start(5, 20, early) == datetime(2026, 10, 10, 12, 5, 20, tzinfo=UTC)


def test_job_names_unique_and_cadences_positive() -> None:
    specs = job_specs(Settings(_env_file=None))
    assert len({s.name for s in specs}) == len(specs)
    assert all(s.every_minutes > 0 for s in specs)
