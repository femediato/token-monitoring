"""paper trading

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-11 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("starting_equity_usd", sa.Double(), nullable=False),
        sa.Column("cash_usd", sa.Double(), nullable=False),
        sa.Column("params", JSON, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_accounts")),
        sa.UniqueConstraint("name", name=op.f("uq_paper_accounts_name")),
    )
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("strategy", sa.String(length=16), nullable=False),
        sa.Column("token_mint", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=16), nullable=False),
        sa.Column("paper_version", sa.String(length=16), nullable=False),
        sa.Column("signal_at", TS, nullable=False),
        sa.Column("signal_bar", TS, nullable=False),
        sa.Column("entry_ref", sa.Double(), nullable=False),
        sa.Column("stop", sa.Double(), nullable=False),
        sa.Column("target", sa.Double(), nullable=False),
        sa.Column("max_hold_hours", sa.Double(), nullable=False),
        sa.Column("quoted_cost_pct", sa.Double(), nullable=False),
        sa.Column("size_usd", sa.Double(), nullable=False),
        sa.Column("planned_risk_usd", sa.Double(), nullable=False),
        sa.Column("sizing_binding", sa.String(length=32), nullable=False),
        sa.Column("entry_at", TS, nullable=True),
        sa.Column("entry_price", sa.Double(), nullable=True),
        sa.Column("entry_cost_pct", sa.Double(), nullable=True),
        sa.Column("qty", sa.Double(), nullable=True),
        sa.Column("exit_by", TS, nullable=True),
        sa.Column("last_price", sa.Double(), nullable=True),
        sa.Column("last_price_at", TS, nullable=True),
        sa.Column("pending_exit", sa.String(length=16), nullable=True),
        sa.Column("exit_attempts", sa.Integer(), nullable=False),
        sa.Column("exit_at", TS, nullable=True),
        sa.Column("exit_reason", sa.String(length=24), nullable=True),
        sa.Column("exit_trigger_price", sa.Double(), nullable=True),
        sa.Column("exit_price", sa.Double(), nullable=True),
        sa.Column("exit_cost_pct", sa.Double(), nullable=True),
        sa.Column("proceeds_usd", sa.Double(), nullable=True),
        sa.Column("pnl_usd", sa.Double(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_positions")),
    )
    op.create_index(
        "ix_paper_positions_account_status", "paper_positions", ["account_id", "status"]
    )
    op.create_index("ix_paper_positions_signal_at", "paper_positions", ["signal_at"])
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.Column("quoted_at", TS, nullable=True),
        sa.Column("in_amount_raw", sa.Numeric(40, 0), nullable=True),
        sa.Column("out_amount_raw", sa.Numeric(40, 0), nullable=True),
        sa.Column("usd", sa.Double(), nullable=True),
        sa.Column("qty", sa.Double(), nullable=True),
        sa.Column("ref_price", sa.Double(), nullable=True),
        sa.Column("fill_price", sa.Double(), nullable=True),
        sa.Column("cost_pct", sa.Double(), nullable=True),
        sa.Column("fee_usd", sa.Double(), nullable=True),
        sa.Column("price_impact_pct", sa.Double(), nullable=True),
        sa.Column("route_labels", JSON, nullable=True),
        sa.Column("trigger_price", sa.Double(), nullable=True),
        sa.Column("raw_ids", JSON, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model_price", sa.Double(), nullable=True),
        sa.Column("model_note", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_fills")),
    )
    op.create_index("ix_paper_fills_position", "paper_fills", ["position_id"])
    op.create_table(
        "paper_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=True),
        sa.Column("at", TS, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=16), nullable=True),
        sa.Column("token_mint", sa.String(length=64), nullable=True),
        sa.Column("signal_bar", TS, nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("data", JSON, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_events")),
    )
    op.create_index("ix_paper_events_account_at", "paper_events", ["account_id", "at"])
    op.create_index("ix_paper_events_position", "paper_events", ["position_id"])
    op.create_index(
        "uq_paper_events_signal",
        "paper_events",
        ["account_id", "strategy", "token_mint", "signal_bar"],
        unique=True,
        postgresql_where=sa.text("kind = 'signal'"),
    )
    op.create_table(
        "paper_equity",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("cash_usd", sa.Double(), nullable=False),
        sa.Column("positions_usd", sa.Double(), nullable=False),
        sa.Column("equity_usd", sa.Double(), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "at", name=op.f("pk_paper_equity")),
    )


def downgrade() -> None:
    for t in ("paper_equity", "paper_events", "paper_fills", "paper_positions", "paper_accounts"):
        op.drop_table(t)
