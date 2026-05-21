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
                     take_profit: float | None = None,
                     order_id_sl: str | None = None,
                     order_id_tp: str | None = None) -> Trade:
    trade = Trade(
        ts_open=datetime(2026, 5, 19, 12, 39, 28, tzinfo=timezone.utc),
        side="BUY",
        quantity_btc=Decimal(str(qty)),
        entry_price=Decimal(str(entry)),
        status="open",
        stop_loss=Decimal(str(stop_loss)) if stop_loss else None,
        take_profit=Decimal(str(take_profit)) if take_profit else None,
        order_id_open="ORD-OPEN",
        order_id_sl=order_id_sl,
        order_id_tp=order_id_tp,
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


# ---------------------------------------------------------------------------
# Tests — Bracket fill detection (fills parciales y match por order_id)
# ---------------------------------------------------------------------------

async def test_bracket_fill_detected_from_partial_fills_same_order_id(session):
    # GIVEN un trade de 0.00012 BTC y Binance devuelve 3 sub-fills del mismo order_id
    # que suman exactamente 0.00012 BTC (caso real: Binance parte fills grandes)
    trade = _make_open_trade(session, qty=0.00012, stop_loss=76721.08)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 77000.0})  # > SL, no activa guardian
    exchange.fetch_my_trades = AsyncMock(return_value=[
        {"side": "sell", "order": "ORD-SL-PARTIAL", "amount": 4e-05,
         "price": 76791.21, "timestamp": 9_999_999_999_999,
         "fee": {"cost": 0.003}},
        {"side": "sell", "order": "ORD-SL-PARTIAL", "amount": 7e-05,
         "price": 76791.21, "timestamp": 9_999_999_999_999,
         "fee": {"cost": 0.005}},
        {"side": "sell", "order": "ORD-SL-PARTIAL", "amount": 1e-05,
         "price": 76791.21, "timestamp": 9_999_999_999_999,
         "fee": {"cost": 0.001}},
    ])

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cerró correctamente (suma de sub-fills = 0.00012 ≈ qty del trade)
    await session.refresh(trade)
    assert trade.status == "closed"
    assert trade.close_reason == "sl_triggered"
    assert float(trade.exit_price) == pytest.approx(76791.21, rel=1e-4)


async def test_bracket_fill_matched_by_order_id_sl_exact(session):
    # GIVEN un trade con order_id_sl conocido y un fill con ese mismo order_id
    # cuya cantidad difiere en más del 2% (no matchearía por cantidad)
    trade = _make_open_trade(session, qty=0.00013, stop_loss=76721.08,
                             order_id_sl="KNOWN-SL-ORDER")
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 77000.0})
    exchange.fetch_my_trades = AsyncMock(return_value=[
        # Cantidad distinta al trade (0.00010 vs 0.00013, diferencia >2%)
        # Sin match por ID, este fill sería ignorado; con match por ID, cierra el trade
        {"side": "sell", "order": "KNOWN-SL-ORDER", "amount": 0.00010,
         "price": 76700.0, "timestamp": 9_999_999_999_999,
         "fee": {"cost": 0.008}},
    ])

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cierra por match exacto de order_id (no por cantidad)
    await session.refresh(trade)
    assert trade.status == "closed"
    assert trade.close_reason == "sl_triggered"


async def test_bracket_fill_matched_by_qty_fallback_when_known_id_not_found(session):
    # GIVEN un trade con order_id_sl="ORDER-A" pero el fill llega con order_id="ORDER-B"
    # (caso: el bracket se ejecutó pero Binance asignó un ID diferente al esperado).
    # La cantidad coincide con el trade (±2%), así que el fallback por cantidad debe actuar.
    trade = _make_open_trade(session, qty=0.00013, stop_loss=76721.08,
                             order_id_sl="ORDER-A")
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 77500.0})  # > SL, no activa guardian
    exchange.fetch_my_trades = AsyncMock(return_value=[
        # order_id diferente al registrado, pero cantidad idéntica → fallback por qty
        {"side": "sell", "order": "ORDER-B", "amount": 0.00013,
         "price": 76800.0, "timestamp": 9_999_999_999_999,
         "fee": {"cost": 0.01}},
    ])

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cierra por fallback de cantidad (el ID exacto no matcheó,
    # pero el fill de cantidad equivalente es suficiente evidencia de cierre)
    await session.refresh(trade)
    assert trade.status == "closed"
    assert trade.close_reason == "sl_triggered"


async def test_bracket_fill_qty_fallback_when_no_bracket_ids_in_db(session):
    # GIVEN un trade SIN order_id_sl ni order_id_tp en BD (brackets no se guardaron)
    # y un fill cuya cantidad coincide con el trade (±2%)
    trade = _make_open_trade(session, qty=0.00013, stop_loss=76721.08,
                             order_id_sl=None)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 77500.0})
    exchange.fetch_my_trades = AsyncMock(return_value=[
        {"side": "sell", "order": "ANY-ORDER", "amount": 0.00013,
         "price": 76700.0, "timestamp": 9_999_999_999_999,
         "fee": {"cost": 0.01}},
    ])

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cierra por fallback de cantidad (no hay IDs conocidos)
    await session.refresh(trade)
    assert trade.status == "closed"


# ---------------------------------------------------------------------------
# Tests — execute_sell con balance insuficiente
# ---------------------------------------------------------------------------

async def test_execute_sell_adjusts_qty_when_btc_balance_insufficient(session):
    # GIVEN un trade de 0.00013 BTC pero solo 0.000097 BTC libre en Binance
    # (caso real: el BTC fue consumido por bracket de otro trade)
    trade = _make_open_trade(session, qty=0.00013)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_balance = AsyncMock(return_value={
        "free": {"BTC": 0.000097, "USDT": 100.0},
        "total": {"BTC": 0.000097, "USDT": 100.0},
    })
    # El sell se ejecuta con la cantidad ajustada
    exchange.create_market_order = AsyncMock(return_value={
        "id": "ORD-SELL-ADJ", "average": 76700.0, "filled": 0.000097,
        "fee": {"cost": 0.007},
    })

    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN ejecutamos el sell
    closed = await executor.execute_sell(
        trade_id=trade.id, decision_id=None, close_reason="sl_triggered",
    )

    # THEN el trade se cierra con la cantidad disponible (no falla por insufficient balance)
    assert closed.status == "closed"
    # AND se usó la cantidad real disponible en la llamada al exchange
    call_args = exchange.create_market_order.call_args
    qty_used = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("amount")
    assert qty_used == pytest.approx(0.000097, rel=1e-3)


async def test_execute_sell_raises_when_btc_balance_near_zero(session):
    # GIVEN un trade abierto pero BTC libre en cuenta = 0 (todo consumido)
    trade = _make_open_trade(session, qty=0.00013)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_balance = AsyncMock(return_value={
        "free": {"BTC": 0.0, "USDT": 100.0},
        "total": {"BTC": 0.0},
    })

    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN intentamos cerrar el trade
    # THEN se lanza RuntimeError con mensaje claro (no llama al exchange)
    with pytest.raises(RuntimeError, match="BTC insuficiente"):
        await executor.execute_sell(
            trade_id=trade.id, decision_id=None, close_reason="sl_triggered",
        )
    exchange.create_market_order.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — TP Guardian (software fallback cuando bracket de TP no fue colocado)
# ---------------------------------------------------------------------------

async def test_tp_guardian_triggers_when_no_bracket_and_price_above_tp(session):
    # GIVEN un trade sin order_id_tp (bracket falló al crear) y el precio supera el TP
    trade = _make_open_trade(
        session,
        entry=77000.0,
        stop_loss=76500.0,
        take_profit=78000.0,
        order_id_sl="ORD-SL-1",
        order_id_tp=None,   # <-- bracket de TP no fue colocado
    )
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 78100.0})  # > TP 78000

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade se cierra con tp_triggered
    await session.refresh(trade)
    assert trade.status == "closed"
    assert trade.close_reason == "tp_triggered"


async def test_tp_guardian_does_not_trigger_when_bracket_tp_exists(session):
    # GIVEN un trade CON order_id_tp activo (bracket colocado en Binance)
    # El broker se encargará de ejecutarlo; el guardian no debe intervenir.
    trade = _make_open_trade(
        session,
        entry=77000.0,
        stop_loss=76500.0,
        take_profit=78000.0,
        order_id_sl="ORD-SL-1",
        order_id_tp="ORD-TP-1",  # <-- bracket activo
    )
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 78200.0})  # > TP

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade NO se cierra por el guardian (el bracket en Binance lo gestiona)
    await session.refresh(trade)
    assert trade.status == "open"


async def test_tp_guardian_does_not_trigger_when_price_below_tp(session):
    # GIVEN un trade sin bracket de TP pero el precio aún NO alcanzó el TP
    trade = _make_open_trade(
        session,
        entry=77000.0,
        stop_loss=76500.0,
        take_profit=78000.0,
        order_id_tp=None,
    )
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 77500.0})  # < TP 78000

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade permanece abierto
    await session.refresh(trade)
    assert trade.status == "open"


async def test_tp_guardian_does_not_trigger_when_no_tp_configured(session):
    # GIVEN un trade sin take_profit configurado (trade sin TP)
    trade = _make_open_trade(
        session,
        entry=77000.0,
        stop_loss=76500.0,
        take_profit=None,
        order_id_tp=None,
    )
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_ticker = AsyncMock(return_value={"last": 99999.0})  # precio muy alto

    executor = Executor(exchange, session, symbol="BTC/USDT")
    tracker = OrderTracker(exchange, session, executor, symbol="BTC/USDT")

    # WHEN polleamos
    await tracker.poll_once()

    # THEN el trade NO se cierra (no hay TP para comparar)
    await session.refresh(trade)
    assert trade.status == "open"


async def test_execute_sell_proceeds_with_trade_qty_if_balance_check_fails(session):
    # GIVEN un trade abierto y el fetch_balance lanza excepción (exchange caído)
    trade = _make_open_trade(session, qty=0.00013)
    await session.commit()
    await session.refresh(trade)

    exchange = _make_exchange_no_fills()
    exchange.fetch_balance = AsyncMock(side_effect=Exception("exchange timeout"))
    exchange.create_market_order = AsyncMock(return_value={
        "id": "ORD-SELL-FALLBACK", "average": 76700.0, "filled": 0.00013,
        "fee": {"cost": 0.01},
    })

    executor = Executor(exchange, session, symbol="BTC/USDT")

    # WHEN ejecutamos el sell (balance check falló)
    closed = await executor.execute_sell(
        trade_id=trade.id, decision_id=None, close_reason="sl_triggered",
    )

    # THEN el sell se ejecuta de todas formas con la cantidad del trade (fallback)
    assert closed.status == "closed"
    call_args = exchange.create_market_order.call_args
    qty_used = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("amount")
    assert qty_used == pytest.approx(0.00013, rel=1e-3)
