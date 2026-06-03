"""Add market column to ohlcv (spot | futures)."""
from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ohlcv",
        sa.Column("market", sa.String(8), nullable=False, server_default="spot"),
    )
    op.drop_constraint("ohlcv_pkey", "ohlcv", type_="primary")
    op.create_primary_key("ohlcv_pkey", "ohlcv", ["time", "timeframe", "market"])
    op.create_index("idx_ohlcv_market_tf", "ohlcv", ["market", "timeframe", "time"])
    op.alter_column("ohlcv", "market", server_default=None)


def downgrade() -> None:
    op.drop_index("idx_ohlcv_market_tf", table_name="ohlcv")
    op.drop_constraint("ohlcv_pkey", "ohlcv", type_="primary")
    op.create_primary_key("ohlcv_pkey", "ohlcv", ["time", "timeframe"])
    op.drop_column("ohlcv", "market")
