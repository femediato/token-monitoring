"""Collection health: job cadence gaps, failures, and data staleness.

Shared by the API (/monitoring/*), the collector's periodic self-check and the
acceptance report (`python -m autolp.monitoring.report`).
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autolp.config import Settings
from autolp.db.base import utcnow
from autolp.db.models import CollectorRun, MarketSnapshot, RunStatus, Token, TrackingStatus


@dataclass
class JobHealth:
    job: str
    expected_every_minutes: int
    runs: int
    successes: int
    failures: int
    last_success_at: datetime | None
    minutes_since_success: float | None
    max_gap_minutes: float | None  # longest interval between successful runs in the window
    gaps: int  # intervals longer than 2x the expected cadence
    coverage_pct: float  # successful runs / expected runs in the window
    status: str  # ok | degraded | stale | never_ran

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def job_health(
    session: AsyncSession, expected: dict[str, int], since: datetime, now: datetime | None = None
) -> list[JobHealth]:
    now = now or utcnow()
    rows = (
        await session.execute(
            select(CollectorRun.job, CollectorRun.started_at, CollectorRun.status)
            .where(CollectorRun.started_at >= since)
            .order_by(CollectorRun.job, CollectorRun.started_at)
        )
    ).all()
    by_job: dict[str, list[tuple[datetime, RunStatus]]] = {}
    for job, started, run_status in rows:
        by_job.setdefault(job, []).append((started, run_status))

    out = []
    window_minutes = (now - since).total_seconds() / 60
    for job, every in expected.items():
        runs = by_job.get(job, [])
        ok_times = [t for t, s in runs if s == RunStatus.SUCCESS]
        # Gaps are measured from the window start (or first run) through `now`.
        points = [since, *ok_times, now] if ok_times else []
        intervals = [(b - a).total_seconds() / 60 for a, b in zip(points, points[1:], strict=False)]
        if ok_times and runs and runs[0][0] - since > timedelta(minutes=every * 2):
            intervals = intervals[1:]  # collector started after the window began
        last = ok_times[-1] if ok_times else None
        since_last = (now - last).total_seconds() / 60 if last else None
        expected_runs = max(1.0, window_minutes / every)
        status: str
        if last is None:
            status = "never_ran"
        elif since_last is not None and since_last > every * 3:
            status = "stale"
        elif any(i > every * 2 for i in intervals) or len(ok_times) < len(runs):
            status = "degraded"
        else:
            status = "ok"
        out.append(
            JobHealth(
                job=job,
                expected_every_minutes=every,
                runs=len(runs),
                successes=len(ok_times),
                failures=sum(1 for _, s in runs if s == RunStatus.FAILED),
                last_success_at=last,
                minutes_since_success=None if since_last is None else round(since_last, 1),
                max_gap_minutes=round(max(intervals), 1) if intervals else None,
                gaps=sum(1 for i in intervals if i > every * 2),
                coverage_pct=round(min(100.0, len(ok_times) / expected_runs * 100), 1),
                status=status,
            )
        )
    return out


@dataclass
class Freshness:
    tracked_active: int
    tracked_dormant: int
    excluded: int
    active_with_fresh_snapshot: int
    active_stale: int  # active tokens without a snapshot within stale_after_minutes
    source_lag_p50_minutes: float | None  # provider-reported staleness (observed - updated)
    source_lag_max_minutes: float | None
    stale_source_values: int  # snapshots whose provider data is older than the threshold

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def freshness(session: AsyncSession, settings: Settings) -> Freshness:
    now = utcnow()
    stale_after = timedelta(minutes=settings.collection.stale_after_minutes)
    counts = dict(
        (
            await session.execute(
                select(Token.tracking_status, func.count()).group_by(Token.tracking_status)
            )
        ).all()
    )
    fresh = await session.scalar(
        select(func.count(func.distinct(MarketSnapshot.token_mint)))
        .join(Token, Token.mint == MarketSnapshot.token_mint)
        .where(
            Token.tracking_status == TrackingStatus.ACTIVE,
            MarketSnapshot.observed_at >= now - stale_after,
        )
    )
    lag = func.extract("epoch", MarketSnapshot.observed_at - MarketSnapshot.source_updated_at) / 60
    p50, mx, n_stale = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(lag),
                func.max(lag),
                func.count().filter(lag > stale_after.total_seconds() / 60),
            ).where(MarketSnapshot.observed_at >= now - timedelta(hours=1))
        )
    ).one()
    active = counts.get(TrackingStatus.ACTIVE, 0)
    return Freshness(
        tracked_active=active,
        tracked_dormant=counts.get(TrackingStatus.DORMANT, 0),
        excluded=counts.get(TrackingStatus.EXCLUDED, 0),
        active_with_fresh_snapshot=fresh or 0,
        active_stale=active - (fresh or 0),
        source_lag_p50_minutes=None if p50 is None else round(float(p50), 1),
        source_lag_max_minutes=None if mx is None else round(float(mx), 1),
        stale_source_values=n_stale or 0,
    )
