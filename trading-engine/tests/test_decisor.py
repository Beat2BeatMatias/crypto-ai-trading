"""Tests for Decisor — LLM-driven trade decision loop."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime,
    Numeric, Text, select, event,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.models import Indicators, Position, Decision, PlaybookVersion
from shared.schemas import DecisorAction
from agents.decisor import Decisor
from agents.llm_client import LLMClient, LLMProvider, LLMResponse
from agents.prompt_manager import PromptManager


# ---------------------------------------------------------------------------
# SQLite-compatible schema
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
    Column("order_id_sl", String(50)),   # migration 007
    Column("order_id_tp", String(50)),   # migration 007
    Column("fees_usdt", Numeric(18, 4)),
    Column("close_requested", Boolean, default=False),  # migration 002
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

_config_table = Table(
    "config", _sqlite_metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text, nullable=False),
    Column("value_type", String(20), nullable=False),
    Column("description", Text),
    Column("updated_at", DateTime),
)

_config_history_table = Table(
    "config_history", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("key", String(60), nullable=False),
    Column("old_value", Text),
    Column("new_value", Text, nullable=False),
    Column("changed_by", String(60), default="system"),
)


# ---------------------------------------------------------------------------
# ORM before-insert hooks for SQLite UUID/timestamp generation
# ---------------------------------------------------------------------------

def _before_insert_indicators(mapper, connection, target):
    if target.time is None:
        target.time = datetime.now(timezone.utc)


def _before_insert_decision(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


def _before_insert_playbook(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts_generated is None:
        target.ts_generated = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    """Fresh in-memory SQLite session seeded with Indicators and PlaybookVersion."""
    event.listen(Indicators, "before_insert", _before_insert_indicators)
    event.listen(Decision, "before_insert", _before_insert_decision)
    event.listen(PlaybookVersion, "before_insert", _before_insert_playbook)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        # Seed Indicators with 1h data so ContextBuilder gets a valid price
        sess.add(Indicators(
            time=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            data={
                "1h": {
                    "last_close": 95000.0,
                    "rsi": 55.0,
                    "ema50": 93000.0,
                    "atr": 500.0,
                },
            },
        ))
        # Seed an active playbook so PromptManager.get_active_playbook() returns something
        sess.add(PlaybookVersion(
            version=0,
            content="# Playbook v0\nTest playbook.",
            model="bootstrap",
            active=True,
        ))
        await sess.commit()
        yield sess

    event.remove(Indicators, "before_insert", _before_insert_indicators)
    event.remove(Decision, "before_insert", _before_insert_decision)
    event.remove(PlaybookVersion, "before_insert", _before_insert_playbook)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_client(response_text: str) -> LLMClient:
    """Return a LLMClient whose call() always returns *response_text*."""
    mock_resp = LLMResponse(
        text=response_text,
        tokens_in=100,
        tokens_out=50,
        latency_ms=42,
        provider=LLMProvider.GEMINI_FLASH.value,
    )
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=mock_resp)
    return llm


def _make_prompt_manager() -> PromptManager:
    """Return a PromptManager that doesn't need DB for prompt file loading."""
    pm = MagicMock(spec=PromptManager)
    pm.get_active_playbook = AsyncMock(return_value=None)
    pm.load_system_prompt = MagicMock(return_value="System prompt {playbook}")
    pm.render_user_prompt = MagicMock(return_value="User prompt")
    return pm


_VALID_LLM_RESPONSE = json.dumps({
    "regime": "TRENDING_UP",
    "confluences": ["B", "C", "G"],
    "action": "BUY",
    "confidence_base": 0.85,
    "confidence_adjustment": 0.0,
    "confidence": 0.85,
    "stop_loss": 93000.0,
    "take_profit": 98000.0,
    "position_size_pct": 0.10,
    "expected_holding_min": 30,
    "reasoning": "Strong uptrend confirmed by multiple indicators.",
})

_DEFAULT_DECIDE_KWARGS = dict(
    orderbook=None,
    usdt_balance=1000.0,
    btc_held=0.0,
    max_position_pct=0.10,
    max_simultaneous_trades=2,
    daily_stop_pct=0.02,
    decisor_interval_min=5,
    mode="PAPER_TRADING",
    taker_fee=0.001,
    maker_fee=0.001,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_llm_response_persists_decision_with_action_buy(session: AsyncSession):
    # GIVEN a Decisor with a mock LLM returning a valid BUY JSON
    llm = _make_llm_client(_VALID_LLM_RESPONSE)
    pm = _make_prompt_manager()
    decisor = Decisor(
        session=session,
        llm=llm,
        symbol="BTC/USDT",
        prompt_manager=pm,
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
    )

    # WHEN deciding
    result = await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN the returned DecisorOutput has action=BUY
    assert result.action == DecisorAction.BUY
    assert result.confidence == pytest.approx(0.85)

    # AND a Decision row was persisted with action=BUY and no rejected_reason
    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.output["action"] == "BUY"
    assert row.rejected_reason is None
    assert row.executed is False
    assert row.model == LLMProvider.GEMINI_FLASH.value
    assert row.tokens_in == 100
    assert row.tokens_out == 50


@pytest.mark.asyncio
async def test_invalid_json_from_llm_persists_decision_with_action_hold(session: AsyncSession):
    # GIVEN a Decisor with a mock LLM returning invalid JSON
    llm = _make_llm_client("this is not valid JSON {{{")
    pm = _make_prompt_manager()
    decisor = Decisor(
        session=session,
        llm=llm,
        symbol="BTC/USDT",
        prompt_manager=pm,
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
    )

    # WHEN deciding
    result = await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN the returned DecisorOutput has action=HOLD (fallback)
    assert result.action == DecisorAction.HOLD
    assert result.confidence == pytest.approx(0.0)

    # AND a Decision row was persisted with action=HOLD and rejected_reason containing "parse_error"
    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.output["action"] == "HOLD"
    assert row.rejected_reason is not None
    assert "parse_error" in row.rejected_reason
