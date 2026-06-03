"""add futures directional fields

Revision ID: 016
Revises: 015
"""
from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("position_side", sa.String(5), nullable=False, server_default="LONG"),
    )
    op.add_column(
        "trades",
        sa.Column("leverage", sa.Numeric(5, 2), nullable=True, server_default="1"),
    )
    op.add_column("trades", sa.Column("liquidation_price", sa.Numeric(18, 8), nullable=True))
    op.add_column(
        "trades",
        sa.Column("margin_mode", sa.String(10), nullable=True, server_default="isolated"),
    )
    op.add_column("trades", sa.Column("funding_paid_usdt", sa.Numeric(18, 4), nullable=True))
    op.add_column(
        "positions",
        sa.Column("position_side", sa.String(5), nullable=True, server_default="LONG"),
    )
    op.add_column(
        "positions",
        sa.Column("leverage", sa.Numeric(5, 2), nullable=True, server_default="1"),
    )
    op.add_column("positions", sa.Column("liquidation_price", sa.Numeric(18, 8), nullable=True))
    op.add_column("balance_snapshots", sa.Column("margin_balance", sa.Numeric(18, 4), nullable=True))
    op.add_column("balance_snapshots", sa.Column("available_margin", sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("balance_snapshots", "available_margin")
    op.drop_column("balance_snapshots", "margin_balance")
    op.drop_column("positions", "liquidation_price")
    op.drop_column("positions", "leverage")
    op.drop_column("positions", "position_side")
    op.drop_column("trades", "funding_paid_usdt")
    op.drop_column("trades", "margin_mode")
    op.drop_column("trades", "liquidation_price")
    op.drop_column("trades", "leverage")
    op.drop_column("trades", "position_side")
