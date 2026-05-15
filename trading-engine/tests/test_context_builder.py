"""Tests for ContextBuilder — assembles the decisor input context dict."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime,
    Numeric, Text, event,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.models import Indicators, Position, Decision
from agents.context_builder import ContextBuilder


# ---------------------------------------------------------------------------
# SQLite-compatible schema (no JSONB, no PostgreSQL server_defaults)
# ---------------------------------------------------------------------------

_sqlite_metadata = MetaData()

_indicators_table = Table(
    "indicators", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("data", JSON, nullable=False),
)

_positions_table = Table(
    "positions", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("trade_id", String(36), nullable=True),
    Column("symbol", String(20), nullable=False, default="BTC/USDT"),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("current_price", Numeric(18, 8)),
    Column("unrealized_pnl", Numeric(18, 4)),
    Column("unrealized_pct", Numeric(8, 4)),
    Column("status", String(10), default="open"),
    Column("opened_at", DateTime, nullable=False),
    Column("updated_at", DateTime),
)

_decisions_table = Table(
    "decisions", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("agent", String(20), nullable=False),
    Column("model", String(50), nullable=False),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("latency_ms", Integer),
    Column("input", JSON, nullable=False),
    Column("output", JSON, nullable=False),
    Column("outcome", JSON),
    Column("trade_id", String(36), nullable=True),
    Column("executed", Boolean, default=False),
    Column("rejected_reason", String(200)),
)

# Trades table stub — mantener en sync con migrations 001 + 002.
_trades_table = Table(
    "trades", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("decision_id", String(36), nullable=True),
    Column("ts_open", DateTime, nullable=False),
    Column("ts_close", DateTime),
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
    Column("fees_usdt", Numeric(18, 4)),
    Column("close_requested", Boolean, default=False),  # migration 002
)


# ---------------------------------------------------------------------------
# ORM event hooks — generate PKs / timestamps at Python level for SQLite
# ---------------------------------------------------------------------------

def _before_insert_indicators(mapper, connection, target):
    if target.time is None:
        target.time = datetime.now(timezone.utc)


def _before_insert_position(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.opened_at is None:
        target.opened_at = datetime.now(timezone.utc)


def _before_insert_decision(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    """Fresh in-memory SQLite session seeded with one Indicators row."""
    event.listen(Indicators, "before_insert", _before_insert_indicators)
    event.listen(Position, "before_insert", _before_insert_position)
    event.listen(Decision, "before_insert", _before_insert_decision)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        # Seed one Indicators row with 1h timeframe data
        sess.add(Indicators(
            time=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            data={
                "1h": {
                    "last_close": 95000.0,
                    "rsi": 55.0,
                    "ema20": 94000.0,
                    "ema50": 93000.0,
                    "ema200": 90000.0,
                    "macd": 10.0,
                    "macd_signal": 8.0,
                    "macd_hist": 2.0,
                    "bb_upper": 96000.0,
                    "bb_lower": 92000.0,
                    "atr": 500.0,
                },
                "5m": {
                    "last_close": 94900.0,
                    "rsi": 48.0,
                    "bb_pct": 0.55,
                },
            },
        ))
        await sess.commit()
        yield sess

    event.remove(Indicators, "before_insert", _before_insert_indicators)
    event.remove(Position, "before_insert", _before_insert_position)
    event.remove(Decision, "before_insert", _before_insert_decision)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper to call build() with sensible defaults
# ---------------------------------------------------------------------------

async def _build(session: AsyncSession, **overrides) -> dict:
    builder = ContextBuilder(session, symbol="BTC/USDT")
    defaults = dict(
        orderbook=None,
        usdt_balance=1000.0,
        btc_held=0.0,
        playbook_content="# Playbook v0",
        max_simultaneous_trades=2,
        daily_stop_pct=0.02,
        decisor_interval_min=5,
        mode="PAPER_TRADING",
        taker_fee_pct=0.001,
        maker_fee_pct=0.001,
        current_drawdown_pct=0.0,
    )
    defaults.update(overrides)
    return await builder.build(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_returns_all_required_keys(session: AsyncSession):
    # GIVEN a fresh DB with one Indicators row

    # WHEN building the context
    ctx = await _build(session)

    # THEN all required keys are present
    required_keys = [
        "timestamp_utc", "price", "rsi_5m", "rsi_1h",
        "spread", "imbalance", "open_positions_count",
        "playbook", "mode", "taker_fee_pct", "roundtrip_fee_pct",
    ]
    for key in required_keys:
        assert key in ctx, f"Missing key: {key}"

    # Spot-check some values
    assert ctx["price"] == 95000.0
    assert ctx["rsi_1h"] == 55.0
    assert ctx["rsi_5m"] == 48.0
    assert ctx["mode"] == "PAPER_TRADING"
    assert ctx["playbook"] == "# Playbook v0"
    assert ctx["spread"] == 0          # no orderbook
    assert ctx["imbalance"] == 1.0     # no orderbook default
    assert ctx["open_positions_count"] == 0


@pytest.mark.asyncio
async def test_open_positions_count_reflects_db(session: AsyncSession):
    # GIVEN one open Position row in the DB
    session.add(Position(
        symbol="BTC/USDT",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("95000.00"),
        status="open",
        opened_at=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    ))
    await session.commit()

    # WHEN building the context
    ctx = await _build(session)

    # THEN open_positions_count equals 1
    assert ctx["open_positions_count"] == 1


@pytest.mark.asyncio
async def test_roundtrip_fee_pct_equals_taker_times_two(session: AsyncSession):
    # GIVEN a taker fee of 0.1 % (0.001 as a fraction)

    # WHEN building the context
    ctx = await _build(session, taker_fee_pct=0.001)

    # THEN roundtrip_fee_pct is taker * 2 expressed as a percentage
    assert ctx["taker_fee_pct"] == pytest.approx(0.1)       # 0.001 * 100
    assert ctx["roundtrip_fee_pct"] == pytest.approx(0.2)   # 0.001 * 2 * 100


@pytest.mark.asyncio
async def test_atr_timeframe_key_in_context(session: AsyncSession):
    # GIVEN atr_timeframe="5m"
    ctx = await _build(session, atr_timeframe="5m")

    # THEN explicit atr_timeframe key is present
    assert "atr_timeframe" in ctx
    assert ctx["atr_timeframe"] == "5m"


@pytest.mark.asyncio
async def test_current_drawdown_pct_passed_through(session: AsyncSession):
    # GIVEN current_drawdown_pct=-0.05
    ctx = await _build(session, current_drawdown_pct=-0.05)

    # THEN it is present in the context
    assert ctx["current_drawdown_pct"] == pytest.approx(-0.05)


@pytest.mark.asyncio
async def test_volume_keys_default_to_zero_when_no_data(session: AsyncSession):
    # GIVEN no volume data in indicators (fixture only has 1h data, no volume_current)
    ctx = await _build(session, atr_timeframe="1h")

    # THEN volume keys are present with 0.0 defaults
    assert "volume_current" in ctx
    assert "volume_avg20" in ctx
    assert "volume_ratio" in ctx
    assert ctx["volume_current"] == pytest.approx(0.0)
    assert ctx["volume_avg20"] == pytest.approx(0.0)
    assert ctx["volume_ratio"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_new_config_v2_keys_present_with_defaults(session: AsyncSession):
    # GIVEN no calibration overrides
    ctx = await _build(session)

    # THEN all 6 new config vars are present with their defaults
    assert ctx["min_fees_to_tp_ratio"] == pytest.approx(3.0)
    assert ctx["min_confluences_buy"] == 2
    assert ctx["cooldown_after_sell_min"] == 15
    assert ctx["subjective_adj_max"] == pytest.approx(0.10)
    assert ctx["expected_holding_max_min"] == 240
    assert ctx["confluence_weak_factor"] == pytest.approx(0.5)
