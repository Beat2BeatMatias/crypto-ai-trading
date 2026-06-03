"""SQLAlchemy 2.0 ORM models — mirror the schema from spec §6."""
from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    String, Integer, Boolean, Date, DateTime, Numeric, ForeignKey,
    Text, UniqueConstraint, Index, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base


class Ohlcv(Base):
    __tablename__ = "ohlcv"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))

    __table_args__ = (
        Index("idx_ohlcv_tf", "timeframe", "time", postgresql_using="btree"),
    )


class Indicators(Base):
    __tablename__ = "indicators"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_indicators_data", "data", postgresql_using="gin"),
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    agent: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trades.id"), nullable=True,
    )
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_reason: Mapped[str | None] = mapped_column(String(200))

    trade: Mapped["Trade | None"] = relationship(
        "Trade", foreign_keys=[trade_id], post_update=True,
    )

    __table_args__ = (
        Index("idx_decisions_ts", "ts"),
        Index("idx_decisions_output", "output", postgresql_using="gin"),
        Index("idx_decisions_input", "input", postgresql_using="gin"),
    )


class DecisionOutcome(Base):
    __tablename__ = "decision_outcomes"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    horizon_min: Mapped[int] = mapped_column(Integer, nullable=False)
    matured: Mapped[bool] = mapped_column(Boolean, nullable=False)
    forward_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    mfe_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    mae_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    time_to_mfe_min: Mapped[int | None] = mapped_column(Integer)
    time_to_mae_min: Mapped[int | None] = mapped_column(Integer)
    sl_dist_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    tp_target_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    postmortem_status: Mapped[str | None] = mapped_column(String(16))
    lesson_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    lesson_normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    postmortem_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    decision: Mapped["Decision"] = relationship("Decision", foreign_keys=[decision_id])

    __table_args__ = (
        Index("idx_decision_outcomes_classification", "classification", "computed_at"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id", use_alter=True),
    )
    ts_open: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity_btc: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    pnl_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    close_reason: Mapped[str | None] = mapped_column(String(30))
    order_id_open: Mapped[str | None] = mapped_column(String(50))
    order_id_close: Mapped[str | None] = mapped_column(String(50))
    order_id_sl: Mapped[str | None] = mapped_column(String(50))
    order_id_tp: Mapped[str | None] = mapped_column(String(50))
    fees_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    close_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    position_side: Mapped[str] = mapped_column(String(5), nullable=False, server_default="LONG")
    leverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default="1")
    liquidation_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    margin_mode: Mapped[str | None] = mapped_column(String(10), server_default="isolated")
    funding_paid_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    __table_args__ = (
        Index("idx_trades_status", "status"),
        Index("idx_trades_ts", "ts_open"),
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.id"))
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="BTC/USDT")
    quantity_btc: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unrealized_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(10), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position_side: Mapped[str | None] = mapped_column(String(5), server_default="LONG")
    leverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), server_default="1")
    liquidation_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    ts_generated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(50))
    trades_analyzed: Mapped[int | None] = mapped_column(Integer)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pnl_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index(
            "idx_playbook_active",
            "active",
            unique=True,
            postgresql_where=text("active = true"),
        ),
    )


class ConfigEntry(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"),
    )


class ConfigHistory(Base):
    __tablename__ = "config_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(60), default="system")


class DailyStats(Base):
    __tablename__ = "daily_stats"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    decisions_total: Mapped[int] = mapped_column(Integer, default=0)
    trades_executed: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    pnl_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FeeSnapshot(Base):
    __tablename__ = "fee_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="BTC/USDT")
    maker_fee: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    taker_fee: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_fee_snapshots_ts", "ts"),
    )


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    usdt: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    btc: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    # Saldos bloqueados en órdenes activas (OCO, SL, TP, etc.)
    usdt_locked: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, server_default=text("0"))
    btc_locked: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, server_default=text("0"))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="binance")
    margin_balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    available_margin: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    __table_args__ = (
        Index("idx_balance_snapshots_ts", "ts"),
    )


class ConfluenceCandidate(Base):
    __tablename__ = "confluence_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    pattern_tag: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    proposed_code: Mapped[str | None] = mapped_column(String(1))
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_md: Mapped[str] = mapped_column(Text, nullable=False)
    verify_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_decision_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_confluence_candidates_status", "status", "occurrence_count"),
    )


class ConfluenceRegistry(Base):
    __tablename__ = "confluence_registry"

    code: Mapped[str] = mapped_column(String(1), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_md: Mapped[str] = mapped_column(Text, nullable=False)
    verify_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    promoted_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confluence_candidates.id"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
