"""Add order_id_sl and order_id_tp columns to trades table.

Persists the bracket order IDs (SL/TP) placed on Binance so the OrderTracker
can cancel them before executing a software SL guardian sell, and to allow
post-hoc auditing of which bracket orders were placed and filled.

Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("order_id_sl", sa.String(50), nullable=True))
    op.add_column("trades", sa.Column("order_id_tp", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("trades", "order_id_tp")
    op.drop_column("trades", "order_id_sl")
