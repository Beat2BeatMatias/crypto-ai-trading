"""add balance_snapshots table

Revision ID: 003
Revises: 002
Create Date: 2026-05-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "balance_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("usdt", sa.Numeric(18, 4), nullable=False),
        sa.Column("btc", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="binance"),
    )
    op.create_index("idx_balance_snapshots_ts", "balance_snapshots", ["ts"])


def downgrade() -> None:
    op.drop_index("idx_balance_snapshots_ts", table_name="balance_snapshots")
    op.drop_table("balance_snapshots")
