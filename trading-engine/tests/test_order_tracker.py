"""Tests for OrderTracker — bracket fill detection y SL guardian."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event, MetaData, Table, Column, String, DateTime, Numeric, Boolean, Integer, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select as sa_select

from execution.executor import Executor
from execution.order_tracker import OrderTracker
from shared.db.models import Trade, Position, Decision, Ohlcv


# ---------------------------------------------------------------------------
# SQLite-compatible schema
# ---------------------------------------------------------------------------
_meta = MetaData()

Table(
    "decisions", _meta,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime(timezone=True)),
    Column("agent", String(20), nullable=False),
    Column("model", String(50), nullable=False),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("latency_ms", Integer),
    Column("input", Text, nullable=False),
    Column("output", Text, nullable=False),
    Column("outcome", Text),
    Column("trade_id", String(36)),
    Column("executed", Boolean, default=False),
    Column("rejected_reason", String(200)),
)

Table(
    "trades", _meta,
    Column("id", String(36), primary_key=True),
    Column("decision_id", String(36)),
    Column("ts_open", DateTime(timezone=True), nullable=False),
    Column("ts_close", DateTime(timezone=True)),
    Column("side", String(4), nullable=False),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("exit_price", Numeric(18, 8)),
    Column("pnl_usdt", Numeric(18, 4)),
    Column("pnl_pct", Numeric(8, 4)),
    Column("status", String(12), nullable=False),
    Column("stop_loss", Numeric(18, 8)),
    Column("take_profit", Numeric(18, 8)),
    Column("close_reason", String(20)),
    Column("order_id_open", String(50)),
    Column("order_id_close", String(50)),
    Column("order_id_sl", String(50)),
    Column("order_id_tp", String(50)),
    Column("fees_usdt", Numeric(18, 4)),
    Column("close_requested", Boolean, default=False),
)

Table(
    "positions", _meta,
    Column("id", String(36), primary_key=True),
    Column("trade_id", String(36)),
    Column("symbol", String(20), nullable=False),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("current_price", Numeric(18, 8)),
    Column("unrealized_pnl", Numeric(18, 4)),
    Column("unrealized_pct", Numeric(8, 4)),
    Column("status", String(10), default="open"),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)

Table(
    "ohlcv", _meta,
    Column("time", DateTime(timezone=True), primary_key=True),
    Column("timeframe", String(4), primary_key=True),
    Column("open", Numeric(18, 8)),
    Column("high", Numeric(18, 8)),
    Column("low", Numeric(18, 8)),
    Column("close", Numeric(18, 8)),
    Column("volume", Numeric(24, 8)),
)


def _assign_uuid(mapper, connection, target):  # noqa: ARG001
    if target.id is None:
        target.id = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    for model in (Trade, Position, Decision):
        event.listen(model, "before_insert", _assign_uuid)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s

    for model in (Trade, Position, Decision):
        event.remove(model, "before_insert", _assign_uuid)

    await engine.dispose()


def _make_open_trade(session: AsyncSession, *, stop_loss: float = 76721.08,
                     qty: float = 0.00013, entry: float = 76898.09,
                     order_id_sl: str | None = None) -> Trade:
    trade = Trade(
        ts_open=datetime(2026, 5, 19, 12, 39, 28, tzinfo=timezone.utc),
        side="BUY",
        quantity_btc=Decimal(str(qty)),
        entry_price=Decimal(str(entry)),
        status="open",
        stop_loss=Decimal(str(stop_loss)),
        order_id_open="ORD-OPEN",
        order_id_sl=order_id_sl,
        fees_usdt=Decimal("0.05"),
    )
    session.add(trade)
    return trade


def _make_exchange_no_fills() -> MagicMock:
    ex = MagicMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": 77000.0})
    ex.fetch_my_trades = AsyncMock(return_value=[])
    ex.create_market_order = AsyncMock(return_value={
        "id": "ORD-SELL-1", "average": 76500.0, "filled": 0.00013, "fee": {"cost": 0.01},
    })
    ex.cancel_order = AsyncMock(return_value={})
    return ex


async def _insert_ohlcv_row(session: AsyncSession, *, low: float, timeframe: str = "1m") -> None:
    row = Ohlcv(
        time=datetime(2026, 5, 19, 14, 44, 0, tzinfo=timezone.utc),
        timeframe=timeframe,
        open=Decimal("76500.0"),
        high=Decimal("76600.0"),
        low=Decimal(str(low)),
        close=Decimal("76550.0"),
        volume=Decimal("10.0"),
    )
    session.add(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Tests — SL Guardian
# ---------------------------------------------------------------------------

async def test_sl_guardian_triggers_when_ticker_below_sl(session):
    # GIVEN un trade abierto sin bracket y ticker por debajo del SL
    trade = _make_open_trade(session)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 76500.0})  # < SL 76721.08

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cerró con sl_triggered
    await session.refresh(trade)
    assert trade.status == "closed"
    assert trade.close_reason == "sl_triggered"


async def test_sl_guardian_triggers_when_candle_low_below_sl_even_if_ticker_above(session):
    # GIVEN un trade abierto sin bracket, ticker por ENCIMA del SL pero low de vela por DEBAJO
    trade = _make_open_trade(session, stop_loss=76721.08)
    await session.commit()
    await session.refresh(trade)

    # Ticker rebotó, está por encima del SL
    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 76800.0})  # > SL

    # Pero el low de la última vela 1m estuvo por debajo del SL
    await _insert_ohlcv_row(session, low=76600.0)  # < SL 76721.08

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cierra igualmente (el low lo detectó)
    await session.refresh(trade)
    assert trade.status == "closed"
    assert trade.close_reason == "sl_triggered"


async def test_sl_guardian_does_not_trigger_when_both_ticker_and_low_above_sl(session):
    # GIVEN un trade abierto, ticker y low de vela por encima del SL
    trade = _make_open_trade(session, stop_loss=76721.08)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 76900.0})  # > SL

    await _insert_ohlcv_row(session, low=76750.0)  # > SL 76721.08

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade NO se cierra
    await session.refresh(trade)
    assert trade.status == "open"


async def test_sl_guardian_does_not_trigger_when_no_sl_configured(session):
    # GIVEN un trade sin stop_loss configurado
    trade = _make_open_trade(session, stop_loss=0.0)
    trade.stop_loss = None
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 70000.0})  # muy por debajo

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade NO se cierra (no hay SL para comparar)
    await session.refresh(trade)
    assert trade.status == "open"


async def test_fetch_last_candle_low_returns_none_when_no_ohlcv(session):
    # GIVEN sin datos OHLCV en la BD
    exchange = _make_exchange_no_fills()
    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN consultamos el low de la última vela
    result = await tracker._fetch_last_candle_low()

    # THEN retorna None sin lanzar excepción
    assert result is None
