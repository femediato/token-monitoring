from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, Query, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from autolp import __version__
from autolp.collector.schedule import expected_minutes
from autolp.config import get_settings
from autolp.db.base import utcnow
from autolp.db.migrations import current_and_head_revisions
from autolp.db.session import make_engine
from autolp.log import configure_logging, get_logger
from autolp.monitoring.health import freshness, job_health

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app.state.engine = make_engine(settings)
    log.info("api_started", env=settings.app_env.value, version=__version__)
    yield
    await app.state.engine.dispose()


app = FastAPI(title="Auto LPing", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    """Readiness: database reachable and schema at the latest migration."""
    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            current, head = await conn.run_sync(current_and_head_revisions)
    except Exception as exc:
        log.warning("readiness_failed", error=str(exc))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable"}
    ok = current == head
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "migrations_pending", "revision": current, "head": head}


@app.get("/monitoring/jobs")
async def monitoring_jobs(
    request: Request, hours: float = Query(24, gt=0, le=24 * 30)
) -> list[dict[str, Any]]:
    """Per-job cadence health: coverage, failures and gaps over the last `hours`."""
    async with AsyncSession(request.app.state.engine) as session:
        jobs = await job_health(
            session, expected_minutes(get_settings()), utcnow() - timedelta(hours=hours)
        )
    return [j.as_dict() for j in jobs]


@app.get("/monitoring/freshness")
async def monitoring_freshness(request: Request) -> dict[str, Any]:
    """Tracked-universe size and how current its market data is."""
    async with AsyncSession(request.app.state.engine) as session:
        return (await freshness(session, get_settings())).as_dict()
