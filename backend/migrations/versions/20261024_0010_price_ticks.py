"""price ticks

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-24 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_ticks",
        sa.Column("token_mint", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("price_usd", sa.Double(), nullable=False),
        sa.Column("block_id", sa.BigInteger(), nullable=True),
        sa.Column("liquidity_usd", sa.Double(), nullable=True),
        sa.PrimaryKeyConstraint("token_mint", "observed_at", name=op.f("pk_price_ticks")),
    )
    op.execute("SELECT create_hypertable('price_ticks', by_range('observed_at', INTERVAL '1 day'))")
    op.execute(
        "ALTER TABLE price_ticks SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'token_mint', "
        "timescaledb.compress_orderby = 'observed_at')"
    )
    op.execute("SELECT add_compression_policy('price_ticks', INTERVAL '1 day')")


def downgrade() -> None:
    op.execute("SELECT remove_compression_policy('price_ticks', if_exists => true)")
    op.drop_table("price_ticks")
