from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from autolp.collector.runs import JobResult, mark_interrupted_runs, run_recorded
from autolp.config import Settings
from autolp.db.models import CollectorRun, RawObservation, RunStatus, Token
from autolp.db.repo import raw_observation_sink
from autolp.db.session import make_engine, make_sessionmaker
from autolp.providers.http import ProviderResponse

from .conftest import alembic

Factory = async_sessionmaker[AsyncSession]


async def test_timescale_hypertable_and_utc(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        ht = await conn.scalar(
            text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'raw_observations'"
            )
        )
        tz = await conn.scalar(text("SHOW timezone"))
    assert ht == 1
    assert tz == "UTC"


async def test_raw_observation_sink_persists_payload(factory: Factory) -> None:
    resp = ProviderResponse(
        "jupiter",
        "/tokens/v2/search",
        {"query": "m1"},
        200,
        [{"id": "m1", "usdPrice": 0.01}],
        datetime.now(UTC),
    )
    await raw_observation_sink(factory)(resp)
    async with factory() as s:
        row = (await s.scalars(select(RawObservation))).one()
    assert row.payload == [{"id": "m1", "usdPrice": 0.01}]
    assert row.payload_sha256 == resp.payload_sha256
    assert row.fetched_at == resp.fetched_at
    assert row.fetched_at.tzinfo is not None


async def test_run_recorded_success_and_failure(factory: Factory) -> None:
    async def ok(run_id: int) -> JobResult:
        return JobResult(rows_written=7, api_credits_used=2)

    async def boom(run_id: int) -> JobResult:
        raise RuntimeError("provider down")

    good = await run_recorded(factory, "job_ok", ok)
    bad = await run_recorded(factory, "job_bad", boom)
    assert (good.status, good.rows_written, good.api_credits_used) == (RunStatus.SUCCESS, 7, 2)
    assert good.finished_at is not None and good.finished_at >= good.started_at
    assert bad.status is RunStatus.FAILED
    assert bad.error == "RuntimeError: provider down"


async def test_interrupted_runs_are_closed(factory: Factory) -> None:
    async with factory() as s, s.begin():
        s.add(CollectorRun(job="crashed", status=RunStatus.RUNNING))
    assert await mark_interrupted_runs(factory) == 1
    async with factory() as s:
        run = (await s.scalars(select(CollectorRun))).one()
    assert run.status is RunStatus.FAILED and run.error == "interrupted"


async def test_data_survives_new_engine(settings: Settings, factory: Factory) -> None:
    """Simulates a service restart: data written by one engine is visible to a fresh one."""
    async with factory() as s, s.begin():
        s.add(Token(mint="So1anaMint111", discovery_source="test"))
    fresh = make_engine(settings)
    try:
        async with make_sessionmaker(fresh)() as s:
            assert await s.scalar(select(func.count()).select_from(Token)) == 1
    finally:
        await fresh.dispose()


async def test_naive_timestamps_are_never_stored(factory: Factory) -> None:
    async with factory() as s, s.begin():
        s.add(CollectorRun(job="tz", started_at=datetime.now(UTC) - timedelta(hours=1)))
    async with factory() as s:
        run = (await s.scalars(select(CollectorRun))).one()
    assert run.started_at.utcoffset() == timedelta(0)


async def test_migrations_downgrade_and_reupgrade(engine: AsyncEngine) -> None:
    alembic("downgrade", "base")
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('raw_observations')")) is None
    alembic("upgrade", "head")
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT to_regclass('raw_observations')")) is not None
