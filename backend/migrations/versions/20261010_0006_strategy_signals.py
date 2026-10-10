"""strategy signals

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10 22:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_signals",
        sa.Column("token_mint", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("setup", sa.Boolean(), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_candle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_ref", sa.Double(), nullable=True),
        sa.Column("stop", sa.Double(), nullable=True),
        sa.Column("target", sa.Double(), nullable=True),
        sa.Column("max_hold_hours", sa.Double(), nullable=True),
        sa.Column("cost_pct", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint(
            "token_mint", "evaluated_at", "strategy", name=op.f("pk_strategy_signals")
        ),
    )
    op.execute(
        "SELECT create_hypertable('strategy_signals', by_range('evaluated_at', INTERVAL '1 day'))"
    )
    op.create_index("ix_strategy_signals_at", "strategy_signals", ["evaluated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_strategy_signals_at", table_name="strategy_signals")
    op.drop_table("strategy_signals")
