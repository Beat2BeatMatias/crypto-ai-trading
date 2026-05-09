"""Tests for Supervisor — daily playbook generator."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime,
    Numeric, Text, select, delete, event,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.models import Decision, Trade, PlaybookVersion
from agents.supervisor import Supervisor
from agents.llm_client import LLMResponse


# ---------------------------------------------------------------------------
# SQLite-compatible schema (mirrors test_decisor.py pattern)
# ---------------------------------------------------------------------------

_sqlite_metadata = MetaData()

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
    Column("close_requested", Boolean, default=False),
)

_ohlcv_table = Table(
    "ohlcv", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("timeframe", String(4), primary_key=True),
    Column("open", Numeric(18, 8)),
    Column("high", Numeric(18, 8)),
    Column("low", Numeric(18, 8)),
    Column("close", Numeric(18, 8)),
    Column("volume", Numeric(24, 8)),
)

_indicators_table = Table(
    "indicators", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("data", JSON, nullable=False),
)

_playbook_versions_table = Table(
    "playbook_versions", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("version", Integer, nullable=False, unique=True),
    Column("ts_generated", DateTime),
    Column("content", Text, nullable=False),
    Column("model", String(50)),
    Column("trades_analyzed", Integer),
    Column("win_rate", Numeric(5, 2)),
    Column("pnl_summary", JSON),
    Column("active", Boolean, default=False),
)


# ---------------------------------------------------------------------------
# ORM before-insert hooks for SQLite UUID/timestamp generation
# ---------------------------------------------------------------------------

def _before_insert_decision(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


def _before_insert_trade(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()


def _before_insert_playbook(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts_generated is None:
        target.ts_generated = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    """Fresh in-memory SQLite session seeded with playbook v0 + decisions + trades."""
    event.listen(Decision, "before_insert", _before_insert_decision)
    event.listen(Trade, "before_insert", _before_insert_trade)
    event.listen(PlaybookVersion, "before_insert", _before_insert_playbook)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        # Seed playbook v0
        s.add(PlaybookVersion(version=0, content="# v0\nNeutral.", model="bootstrap", active=True))
        now = datetime.now(tz=timezone.utc)
        # Seed 5 decisor decisions
        for i in range(5):
            s.add(Decision(
                ts=now - timedelta(hours=i + 1),
                agent="decisor",
                model="gemini-2.5-flash",
                input={},
                output={"action": "BUY", "confidence": 0.7, "reasoning": "test"},
                executed=True,
            ))
        # Seed 5 closed trades with positive PnL
        for i in range(5):
            s.add(Trade(
                ts_open=now - timedelta(hours=i + 1),
                ts_close=now - timedelta(minutes=i * 10 + 5),
                side="BUY",
                quantity_btc=Decimal("0.001"),
                entry_price=Decimal("67000"),
                exit_price=Decimal(str(67100 + i * 50)),
                status="closed",
                pnl_usdt=Decimal(str((i + 1) * 0.5)),
                pnl_pct=Decimal(str((i + 1) * 0.1)),
                close_reason="take_profit",
            ))
        await s.commit()
        yield s

    event.remove(Decision, "before_insert", _before_insert_decision)
    event.remove(Trade, "before_insert", _before_insert_trade)
    event.remove(PlaybookVersion, "before_insert", _before_insert_playbook)
    await engine.dispose()


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    llm.call = AsyncMock(return_value=LLMResponse(
        text=(
            "# Playbook v1\n\n"
            "## Metricas\nWin rate 100%.\n\n"
            "## Setups que funcionaron\nConfluencias tecnicas.\n\n"
            "## Patrones a evitar\nForzar entradas.\n\n"
            "## Contexto\nAlcista.\n\n"
            "## Bias\nBULLISH\n\n"
            "## Reglas\n1. Usar 3 confluencias.\n\n"
            "## Cambios\n[NUEVO] Bias bullish."
        ),
        tokens_in=4000,
        tokens_out=300,
        latency_ms=4000,
        provider="gemini-2.5-pro",
    ))
    return llm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_run_creates_new_playbook_version(session, fake_llm):
    # GIVEN a Supervisor with enough trades and a mock LLM
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)

    # WHEN run() is called
    await sup.run()

    # THEN a new playbook version is created and the old one is deactivated
    versions = (await session.execute(
        select(PlaybookVersion).order_by(PlaybookVersion.version)
    )).scalars().all()
    assert len(versions) == 2
    assert versions[1].version == 1
    assert versions[1].active is True
    assert versions[0].active is False


async def test_run_persists_supervisor_decision_row(session, fake_llm):
    # GIVEN a Supervisor with enough trades
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)

    # WHEN run() is called
    await sup.run()

    # THEN exactly one supervisor Decision row is persisted
    sup_decisions = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalars().all()
    assert len(sup_decisions) == 1


async def test_run_with_zero_trades_calls_llm_in_diagnostic_mode(session, fake_llm):
    # GIVEN no closed trades exist
    await session.execute(delete(Trade))
    await session.commit()

    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=3)

    # WHEN run() is called without enough trades
    await sup.run()

    # THEN the LLM is still called (diagnostic mode)
    assert fake_llm.call.call_count >= 1


async def test_run_with_zero_trades_saves_playbook_in_diagnostic_mode(session, fake_llm):
    # GIVEN no closed trades exist
    await session.execute(delete(Trade))
    await session.commit()

    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=3)

    # WHEN run() is called in diagnostic mode
    await sup.run()

    # THEN a new playbook version is saved
    versions = (await session.execute(
        select(PlaybookVersion).order_by(PlaybookVersion.version)
    )).scalars().all()
    assert len(versions) == 2
    assert versions[1].active is True
    assert versions[1].trades_analyzed == 0


async def test_run_with_zero_trades_marks_decision_as_diagnostic(session, fake_llm):
    # GIVEN no closed trades exist
    await session.execute(delete(Trade))
    await session.commit()

    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=3)

    # WHEN run() is called
    await sup.run()

    # THEN the supervisor Decision row records mode="diagnostic" and executed=True
    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    assert sup_decision.executed is True
    assert sup_decision.output.get("mode") == "diagnostic"
