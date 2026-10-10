"""Collector process entrypoint. Writes market data only; never trades."""

import asyncio
import signal
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.collector.runs import JobResult, mark_interrupted_runs, run_recorded
from autolp.config import Settings, get_settings
from autolp.db.session import make_engine, make_sessionmaker
from autolp.log import configure_logging, get_logger

log = get_logger(__name__)


async def heartbeat(run_id: int) -> JobResult:
    return JobResult()


def build_scheduler(
    settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_recorded,
        "interval",
        seconds=settings.collector_heartbeat_seconds,
        args=[factory, "heartbeat", heartbeat],
        id="heartbeat",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        next_run_time=datetime.now(UTC),  # run immediately, then on interval
    )
    return scheduler


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    engine = make_engine(settings)
    factory = make_sessionmaker(engine)

    interrupted = await mark_interrupted_runs(factory)
    if interrupted:
        log.warning("closed_interrupted_runs", count=interrupted)

    scheduler = build_scheduler(settings, factory)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    scheduler.start()
    log.info("collector_started", env=settings.app_env.value)
    await stop.wait()

    log.info("collector_stopping")
    scheduler.shutdown(wait=True)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
