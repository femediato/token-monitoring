"""paper stop management: initial stop, ATR and high-water mark per position

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: positions opened before paper-v2 never move their stop.
    op.add_column("paper_positions", sa.Column("initial_stop", sa.Double(), nullable=True))
    op.add_column("paper_positions", sa.Column("atr", sa.Double(), nullable=True))
    op.add_column("paper_positions", sa.Column("high_price", sa.Double(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_positions", "high_price")
    op.drop_column("paper_positions", "atr")
    op.drop_column("paper_positions", "initial_stop")
