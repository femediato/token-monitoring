"""Integration tests run against a real TimescaleDB test database (`make up`, then
`make test-integration`). They refuse to run against anything but a *_test database."""

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from autolp.config import Settings
from autolp.db.models import Base
from autolp.db.session import make_engine, make_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], cwd=BACKEND_DIR, check=True, env=os.environ.copy())


@pytest.fixture(scope="session")
def settings() -> Settings:
    url = os.environ.get("DATABASE_URL", "")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.exit("DATABASE_URL must point at a *_test database for integration tests", 2)
    return Settings(_env_file=None)


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    eng = make_engine(settings)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        tables = ", ".join(Base.metadata.tables)
        await conn.execute(text(f"TRUNCATE {tables}"))
    yield make_sessionmaker(engine)
