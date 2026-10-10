"""Bookkeeping for scheduled jobs: every execution is recorded in collector_runs."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autolp.db.base import utcnow
from autolp.db.models import CollectorRun, RunStatus
from autolp.log import get_logger

log = get_logger(__name__)


@dataclass
class JobResult:
    rows_written: int = 0
    api_credits_used: int = 0


JobFn = Callable[[int], Awaitable[JobResult]]


async def run_recorded(
    factory: async_sessionmaker[AsyncSession], job: str, fn: JobFn
) -> CollectorRun:
    """Run `fn(run_id)`, recording start, outcome and errors. Errors are logged, not raised,
    so one failing job never stops the scheduler."""
    async with factory() as session, session.begin():
        run = CollectorRun(job=job, status=RunStatus.RUNNING)
        session.add(run)
        await session.flush()
        run_id = run.id

    status, error, result = RunStatus.SUCCESS, None, JobResult()
    try:
        result = await fn(run_id)
    except Exception as exc:
        status, error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"
        log.exception("job_failed", job=job, run_id=run_id)

    async with factory() as session, session.begin():
        await session.execute(
            update(CollectorRun)
            .where(CollectorRun.id == run_id)
            .values(
                finished_at=utcnow(),
                status=status,
                error=error,
                rows_written=result.rows_written,
                api_credits_used=result.api_credits_used,
            )
        )
        run = await session.get_one(CollectorRun, run_id, populate_existing=True)
    log.info("job_finished", job=job, run_id=run_id, status=status.value, rows=result.rows_written)
    return run


async def mark_interrupted_runs(factory: async_sessionmaker[AsyncSession]) -> int:
    """Runs left RUNNING by a crash/restart are closed as FAILED so gaps stay visible."""
    async with factory() as session, session.begin():
        res: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
            update(CollectorRun)
            .where(CollectorRun.status == RunStatus.RUNNING)
            .values(status=RunStatus.FAILED, finished_at=utcnow(), error="interrupted")
        )
    return res.rowcount
