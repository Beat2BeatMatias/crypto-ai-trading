"""Add usdt_locked and btc_locked to balance_snapshots.

Binance separa el saldo en `free` (disponible) y `locked` (reservado en órdenes
activas como OCO, SL y TP). Sin este cambio el snapshot solo guardaba `free`,
lo que hacía que la UI mostrara un balance menor al real.

Revision ID: 010
Revises: 009
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "balance_snapshots",
        sa.Column("usdt_locked", sa.Numeric(18, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "balance_snapshots",
        sa.Column("btc_locked", sa.Numeric(18, 8), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("balance_snapshots", "btc_locked")
    op.drop_column("balance_snapshots", "usdt_locked")
