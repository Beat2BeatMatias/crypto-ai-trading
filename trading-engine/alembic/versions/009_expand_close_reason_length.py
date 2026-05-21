"""Expand close_reason column from VARCHAR(20) to VARCHAR(30).

Needed to support the 'force_closed_notional' close reason (22 chars)
introduced to identify trades closed by the engine because their position
value is below the Binance NOTIONAL minimum filter.

Revision ID: 009
Revises: 008
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "trades", "close_reason",
        type_=sa.String(30),
        existing_type=sa.String(20),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "trades", "close_reason",
        type_=sa.String(20),
        existing_type=sa.String(30),
        nullable=True,
    )
