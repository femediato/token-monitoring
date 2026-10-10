"""ORM models. Every timestamp column is timezone-aware and stored in UTC."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Enum, Identity, Index, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autolp.db.base import Base, utcnow


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class CollectorRun(Base):
    """One execution of a scheduled job; the basis for gap and staleness monitoring."""

    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e]),
        default=RunStatus.RUNNING,
    )
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    api_credits_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_collector_runs_job_started", "job", "started_at"),)


class RawObservation(Base):
    """Verbatim provider response, persisted before any normalization (hypertable)."""

    __tablename__ = "raw_observations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(primary_key=True, default=utcnow)
    source: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(256))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    http_status: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[Any] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    collector_run_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_raw_observations_source_fetched", "source", "fetched_at"),)


class Token(Base):
    """A token identified by its exact mint address."""

    __tablename__ = "tokens"

    mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(256))
    decimals: Mapped[int | None] = mapped_column(SmallInteger)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    pair_created_at: Mapped[datetime | None]
    discovery_source: Mapped[str] = mapped_column(String(32))
    classification: Mapped[str | None] = mapped_column(String(32))
    classification_reason: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
