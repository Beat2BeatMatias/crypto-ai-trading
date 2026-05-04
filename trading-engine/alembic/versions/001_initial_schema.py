"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ohlcv",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("open", sa.Numeric(18, 8), nullable=True),
        sa.Column("high", sa.Numeric(18, 8), nullable=True),
        sa.Column("low", sa.Numeric(18, 8), nullable=True),
        sa.Column("close", sa.Numeric(18, 8), nullable=True),
        sa.Column("volume", sa.Numeric(24, 8), nullable=True),
        sa.PrimaryKeyConstraint("time", "timeframe"),
    )
    op.create_index("idx_ohlcv_tf", "ohlcv", ["timeframe", "time"])

    op.create_table(
        "indicators",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("time"),
    )

    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ts_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_close", sa.DateTime(timezone=True), nullable=True),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity_btc", sa.Numeric(18, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("pnl_usdt", sa.Numeric(18, 4), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("stop_loss", sa.Numeric(18, 8), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 8), nullable=True),
        sa.Column("close_reason", sa.String(20), nullable=True),
        sa.Column("order_id_open", sa.String(50), nullable=True),
        sa.Column("order_id_close", sa.String(50), nullable=True),
        sa.Column("fees_usdt", sa.Numeric(18, 4), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_trades_status", "trades", ["status"])
    op.create_index("idx_trades_ts", "trades", ["ts_open"])

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("agent", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("executed", sa.Boolean(), nullable=True),
        sa.Column("rejected_reason", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"], use_alter=True, name="fk_decisions_trade"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_decisions_ts", "decisions", ["ts"])

    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("quantity_btc", sa.Numeric(18, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 8), nullable=False),
        sa.Column("current_price", sa.Numeric(18, 8), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(18, 4), nullable=True),
        sa.Column("unrealized_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("status", sa.String(10), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "playbook_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ts_generated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("trades_analyzed", sa.Integer(), nullable=True),
        sa.Column("win_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("pnl_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )

    op.create_table(
        "config",
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_type", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "config_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(60), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "daily_stats",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("decisions_total", sa.Integer(), nullable=True),
        sa.Column("trades_executed", sa.Integer(), nullable=True),
        sa.Column("wins", sa.Integer(), nullable=True),
        sa.Column("losses", sa.Integer(), nullable=True),
        sa.Column("pnl_usdt", sa.Numeric(18, 4), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(8, 4), nullable=True),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("date"),
    )

    op.create_table(
        "fee_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("maker_fee", sa.Numeric(8, 6), nullable=False),
        sa.Column("taker_fee", sa.Numeric(8, 6), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_fee_snapshots_ts", "fee_snapshots", ["ts"])


def downgrade() -> None:
    op.drop_table("fee_snapshots")
    op.drop_table("daily_stats")
    op.drop_table("config_history")
    op.drop_table("config")
    op.drop_table("playbook_versions")
    op.drop_table("positions")
    op.drop_table("decisions")
    op.drop_table("trades")
    op.drop_table("indicators")
    op.drop_table("ohlcv")
