"""Collector process entrypoint. Writes market data only; never trades."""

import asyncio
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.jobs import Collector
from autolp.collector.runs import JobResult, mark_interrupted_runs, run_recorded
from autolp.collector.schedule import expected_minutes, job_specs
from autolp.config import Settings, get_settings
from autolp.db.base import utcnow
from autolp.db.session import make_engine, make_sessionmaker
from autolp.log import configure_logging, get_logger
from autolp.monitoring.health import freshness, job_health

log = get_logger(__name__)


async def heartbeat(run_id: int) -> JobResult:
    return JobResult()


def make_self_check(
    settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> Callable[[], Awaitable[None]]:
    """Logs a warning for any job that is stale or failing, and for stale market data."""
    expected = expected_minutes(settings)

    async def self_check() -> None:
        async with factory() as session:
            jobs = await job_health(session, expected, utcnow() - timedelta(hours=6))
            fresh = await freshness(session, settings)
        for j in jobs:
            if j.status in ("stale", "never_ran") or j.failures:
                log.warning("collection_job_unhealthy", **j.as_dict())
        if fresh.active_stale:
            log.warning("collection_data_stale", **fresh.as_dict())

    return self_check


def aligned_start(every_minutes: int, offset_seconds: int, now: datetime) -> datetime:
    """First run time aligned to the wall clock (e.g. :00, :05, :10 for 5-minute jobs)."""
    period = every_minutes * 60
    epoch = int(now.timestamp())
    start = epoch - epoch % period + offset_seconds
    if start <= epoch:
        start += period
    return datetime.fromtimestamp(start, UTC)


def build_scheduler(
    settings: Settings, factory: async_sessionmaker[AsyncSession], collector: Collector
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    now = datetime.now(UTC)
    scheduler.add_job(
        run_recorded,
        IntervalTrigger(seconds=settings.collector_heartbeat_seconds, timezone=UTC),
        args=[factory, "heartbeat", heartbeat],
        id="heartbeat",
        max_instances=1,
        coalesce=True,
        next_run_time=now,
    )
    scheduler.add_job(
        make_self_check(settings, factory),
        IntervalTrigger(minutes=15, start_date=aligned_start(15, 290, now), timezone=UTC),
        id="self_check",
        max_instances=1,
        coalesce=True,
    )
    for i, spec in enumerate(job_specs(settings)):
        start = aligned_start(spec.every_minutes, spec.offset_seconds, now)
        scheduler.add_job(
            run_recorded,
            IntervalTrigger(minutes=spec.every_minutes, start_date=start, timezone=UTC),
            args=[factory, spec.name, spec.make(collector)],
            id=spec.name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=spec.every_minutes * 30,
            next_run_time=now + timedelta(seconds=5 * i) if spec.run_on_start else start,
        )
    return scheduler


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    engine = make_engine(settings)
    factory = make_sessionmaker(engine)
    collector = Collector.from_settings(settings, factory)

    interrupted = await mark_interrupted_runs(factory)
    if interrupted:
        log.warning("closed_interrupted_runs", count=interrupted)

    scheduler = build_scheduler(settings, factory, collector)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    scheduler.start()
    log.info("collector_started", env=settings.app_env.value, jobs=len(scheduler.get_jobs()))
    fast = (
        asyncio.create_task(collector.fast.run(stop))
        if settings.collection.fast_prices_enabled
        else None
    )
    await stop.wait()

    log.info("collector_stopping")
    scheduler.shutdown(wait=False)
    if fast is not None:
        await fast
    await collector.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
