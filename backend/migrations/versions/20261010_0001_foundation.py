"""Foundation: timescaledb, collector_runs, raw_observations, tokens

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    run_status = postgresql.ENUM("running", "success", "failed", name="run_status")
    op.create_table(
        "collector_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("job", sa.String(64), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.Column("status", run_status, nullable=False),
        sa.Column("rows_written", sa.Integer, nullable=False),
        sa.Column("api_credits_used", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_collector_runs_job_started", "collector_runs", ["job", "started_at"])

    op.create_table(
        "raw_observations",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("fetched_at", TS, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(256), nullable=False),
        sa.Column("params", postgresql.JSONB),
        sa.Column("http_status", sa.SmallInteger, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("collector_run_id", sa.BigInteger),
        sa.PrimaryKeyConstraint("id", "fetched_at", name="pk_raw_observations"),
    )
    op.create_index(
        "ix_raw_observations_source_fetched", "raw_observations", ["source", "fetched_at"]
    )
    op.execute(
        "SELECT create_hypertable('raw_observations', by_range('fetched_at', INTERVAL '1 day'))"
    )

    op.create_table(
        "tokens",
        sa.Column("mint", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(64)),
        sa.Column("name", sa.String(256)),
        sa.Column("decimals", sa.SmallInteger),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("pair_created_at", TS),
        sa.Column("discovery_source", sa.String(32), nullable=False),
        sa.Column("classification", sa.String(32)),
        sa.Column("classification_reason", sa.String(128)),
        sa.Column("updated_at", TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tokens")
    op.drop_table("raw_observations")
    op.drop_index("ix_collector_runs_job_started", table_name="collector_runs")
    op.drop_table("collector_runs")
    op.execute("DROP TYPE IF EXISTS run_status")
