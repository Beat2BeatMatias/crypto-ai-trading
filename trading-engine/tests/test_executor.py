"""Tests for Executor — buy, sell, and Decision marking."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event
from sqlalchemy import (
    Boolean, Column, DateTime, MetaData, Numeric, String, Table, Text, Integer,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select as sa_select

from execution.executor import Executor
from shared.db.models import Trade, Position, Decision
from shared.schemas import DecisorAction, DecisorOutput, MarketRegime

# ---------------------------------------------------------------------------
# SQLite-compatible schema — same table names, Python-side UUID defaults
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


def _assign_uuid(mapper, connection, target):  # noqa: ARG001
    """Assign a Python-side UUID if the model's id is not set (SQLite compat)."""
    if target.id is None:
        target.id = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    # Register before-insert listeners so SQLite gets Python-side UUIDs
    for model in (Trade, Position, Decision):
        event.listen(model, "before_insert", _assign_uuid)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s

    # Clean up listeners
    for model in (Trade, Position, Decision):
        event.remove(model, "before_insert", _assign_uuid)

    await engine.dispose()


def _make_exchange(order_id: str = "ORD-001", avg_price: float = 67000.0,
                   filled: float = 0.001, fee: float = 0.07) -> MagicMock:
    order = {
        "id": order_id,
        "average": avg_price,
        "filled": filled,
        "fee": {"cost": fee},
    }
    ex = MagicMock()
    ex.create_market_order = AsyncMock(return_value=order)
    ex.create_order = AsyncMock(return_value={"id": f"{order_id}-sl"})
    return ex


def _make_buy_decision(stop_loss: float = 66400.0, take_profit: float = 67800.0,
                        size: float = 0.06) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["a", "b", "c"],
        action=DecisorAction.BUY,
        confidence=0.7,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_pct=size,
        reasoning="test buy",
    )


async def _insert_decision(session: AsyncSession, decision_id: uuid.UUID) -> None:
    """Insert a minimal Decision row so FK-based lookups resolve."""
    d = Decision(
        id=decision_id,
        ts=datetime.now(tz=timezone.utc),
        agent="test",
        model="test",
        input={},
        output={},
        executed=False,
    )
    session.add(d)
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_execute_buy_creates_trade_and_position(session):
    # GIVEN a buy decision with a new decision_id
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    exchange = _make_exchange(order_id="ORD-BUY-1", avg_price=67000.0, filled=0.001, fee=0.07)
    executor = Executor(exchange, session, symbol="BTC/USDT")
    decision = _make_buy_decision()

    # WHEN executing the buy
    trade = await executor.execute_buy(decision=decision, decision_id=decision_id,
                                        usdt_balance=10000.0)

    # THEN a Trade row exists with the correct order_id_open
    trades = (await session.execute(sa_select(Trade))).scalars().all()
    assert len(trades) == 1
    assert trades[0].order_id_open == "ORD-BUY-1"
    assert trades[0].status == "open"
    assert float(trades[0].entry_price) == pytest.approx(67000.0)
    # AND los IDs de las órdenes bracket quedan persistidos
    assert trades[0].order_id_sl == "ORD-BUY-1-sl"
    assert trades[0].order_id_tp == "ORD-BUY-1-sl"

    # AND a Position row was created
    positions = (await session.execute(sa_select(Position))).scalars().all()
    assert len(positions) == 1
    assert positions[0].status == "open"


async def test_execute_buy_marks_decision_executed(session):
    # GIVEN a decision row in the DB
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    exchange = _make_exchange(order_id="ORD-BUY-2", avg_price=67000.0, filled=0.001, fee=0.07)
    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN buy is executed
    await executor.execute_buy(decision=_make_buy_decision(), decision_id=decision_id,
                                usdt_balance=10000.0)

    # THEN Decision.executed is True
    d = await session.get(Decision, decision_id)
    assert d is not None
    assert d.executed is True


async def test_execute_buy_trade_saved_even_if_sl_bracket_fails(session):
    # GIVEN un exchange donde la orden market BUY funciona pero el bracket SL falla
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    exchange = _make_exchange(order_id="ORD-BUY-NOSL", avg_price=67000.0, filled=0.001, fee=0.07)
    exchange.create_order = AsyncMock(side_effect=Exception("STOP_LOSS_LIMIT not supported"))
    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN ejecutamos el buy (bracket fallará)
    trade = await executor.execute_buy(decision=_make_buy_decision(), decision_id=decision_id,
                                        usdt_balance=10000.0)

    # THEN el Trade queda guardado en BD con executed=True
    trades = (await session.execute(sa_select(Trade))).scalars().all()
    assert len(trades) == 1
    assert trades[0].status == "open"
    assert float(trades[0].entry_price) == pytest.approx(67000.0)
    assert trades[0].order_id_sl is None
    assert trades[0].order_id_tp is None

    # AND la Decision queda marcada como executed
    d = await session.get(Decision, decision_id)
    assert d is not None
    assert d.executed is True


async def test_execute_buy_uses_stop_loss_limit_order_type(session):
    # GIVEN un exchange que captura el tipo de orden usado para el SL
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    exchange = _make_exchange(order_id="ORD-BUY-SL", avg_price=67000.0, filled=0.001, fee=0.07)
    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN ejecutamos el buy
    await executor.execute_buy(decision=_make_buy_decision(stop_loss=66400.0),
                                decision_id=decision_id, usdt_balance=10000.0)

    # THEN el bracket SL se coloca con tipo STOP_LOSS_LIMIT (no STOP_MARKET)
    calls = exchange.create_order.call_args_list
    sl_call = calls[0]
    assert sl_call.args[1] == "STOP_LOSS_LIMIT", (
        f"Se esperaba STOP_LOSS_LIMIT pero se usó: {sl_call.args[1]}"
    )
    # AND el price límite es ligeramente menor al stop price (0.15% de slippage)
    sl_limit_price = sl_call.args[4] if len(sl_call.args) > 4 else sl_call.kwargs.get("price")
    assert sl_limit_price is not None
    assert sl_limit_price < 66400.0
    assert sl_limit_price == pytest.approx(66400.0 * 0.9985, rel=1e-3)


async def test_execute_buy_sl_bracket_retries_on_failure(session):
    # GIVEN un exchange donde el bracket SL falla una vez y luego tiene éxito
    # (el TP también usa create_order; aquí lo deshabilitamos para aislar el SL)
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    exchange = _make_exchange(order_id="ORD-RETRY", avg_price=67000.0, filled=0.001, fee=0.07)
    sl_attempts = []

    async def _create_order_side_effect(symbol, order_type, side, qty, *args, **kwargs):
        if order_type == "STOP_LOSS_LIMIT":
            sl_attempts.append(1)
            if len(sl_attempts) == 1:
                raise Exception("transient error")
            return {"id": "ORD-RETRY-sl"}
        return {"id": "ORD-TP"}

    exchange.create_order = AsyncMock(side_effect=_create_order_side_effect)
    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN ejecutamos el buy
    trade = await executor.execute_buy(decision=_make_buy_decision(), decision_id=decision_id,
                                        usdt_balance=10000.0)

    # THEN el bracket SL quedó colocado en el segundo intento
    await session.refresh(trade)
    assert trade.order_id_sl == "ORD-RETRY-sl"
    assert len(sl_attempts) == 2


async def test_execute_buy_sl_bracket_logs_error_after_all_retries_exhausted(session):
    # GIVEN un exchange donde el bracket SL falla en todos los intentos
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    exchange = _make_exchange(order_id="ORD-NOSL", avg_price=67000.0, filled=0.001, fee=0.07)
    sl_attempts = []

    async def _create_order_always_fails(symbol, order_type, side, qty, *args, **kwargs):
        if order_type == "STOP_LOSS_LIMIT":
            sl_attempts.append(1)
            raise Exception("permanent error")
        return {"id": "ORD-TP"}

    exchange.create_order = AsyncMock(side_effect=_create_order_always_fails)
    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN ejecutamos el buy
    trade = await executor.execute_buy(decision=_make_buy_decision(), decision_id=decision_id,
                                        usdt_balance=10000.0)

    # THEN el trade está guardado pero sin bracket SL
    await session.refresh(trade)
    assert trade.status == "open"
    assert trade.order_id_sl is None
    # AND se intentó exactamente _SL_BRACKET_RETRIES veces
    from execution.executor import _SL_BRACKET_RETRIES
    assert len(sl_attempts) == _SL_BRACKET_RETRIES


async def test_execute_sell_closes_trade_and_computes_pnl(session):
    # GIVEN an open trade created via execute_buy
    decision_id = uuid.uuid4()
    await _insert_decision(session, decision_id)
    buy_exchange = _make_exchange(order_id="ORD-BUY-3", avg_price=67000.0, filled=0.001, fee=0.07)
    executor = Executor(buy_exchange, session, symbol="BTC/USDT")
    trade = await executor.execute_buy(decision=_make_buy_decision(), decision_id=decision_id,
                                        usdt_balance=10000.0)

    # WHEN a sell order fills at 68000
    sell_order = {
        "id": "ORD-SELL-3",
        "average": 68000.0,
        "filled": 0.001,
        "fee": {"cost": 0.068},
    }
    executor.exchange.create_market_order = AsyncMock(return_value=sell_order)
    closed = await executor.execute_sell(trade_id=trade.id, decision_id=None,
                                          close_reason="take_profit")

    # THEN trade is closed with positive pnl_usdt
    assert closed.status == "closed"
    assert closed.close_reason == "take_profit"
    # gross = (68000 - 67000) * 0.001 = 1.0; net = 1.0 - 0.07 - 0.068 = 0.862
    assert float(closed.pnl_usdt) == pytest.approx(0.862, rel=1e-3)
