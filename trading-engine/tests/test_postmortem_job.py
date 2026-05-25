"""Tests for outcome post-mortem background job."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, Table, Column, String, Integer, Boolean, DateTime, Numeric, ForeignKey, Text, select, event
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.llm_client import LLMClient, LLMProvider, LLMResponse
from agents.postmortem_job import outcome_postmortem_tick
from shared.db.models import Decision, DecisionOutcome

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
    Column("decision_id", String(36), ForeignKey("decisions.id"), nullable=True),
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
    Column("order_id_sl", String(50)),
    Column("order_id_tp", String(50)),
    Column("fees_usdt", Numeric(18, 4)),
    Column("close_requested", Boolean, nullable=False, default=False),
)

_decision_outcomes_table = Table(
    "decision_outcomes", _sqlite_metadata,
    Column("decision_id", String(36), ForeignKey("decisions.id"), primary_key=True),
    Column("horizon_min", Integer, nullable=False),
    Column("matured", Boolean, nullable=False),
    Column("forward_return_pct", Numeric(10, 5)),
    Column("mfe_pct", Numeric(10, 5)),
    Column("mae_pct", Numeric(10, 5)),
    Column("time_to_mfe_min", Integer),
    Column("time_to_mae_min", Integer),
    Column("sl_dist_pct", Numeric(10, 5)),
    Column("tp_target_pct", Numeric(10, 5)),
    Column("classification", String(32), nullable=False),
    Column("computed_at", DateTime, nullable=False),
    Column("postmortem_status", String(16)),
    Column("lesson_raw", JSON),
    Column("lesson_normalized", JSON),
    Column("postmortem_at", DateTime),
)

_confluence_candidates_table = Table(
    "confluence_candidates", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("pattern_tag", String(64), nullable=False, unique=True),
    Column("proposed_code", String(1)),
    Column("title", String(128), nullable=False),
    Column("definition_md", Text, nullable=False),
    Column("verify_spec", JSON, nullable=False),
    Column("occurrence_count", Integer, nullable=False),
    Column("first_seen_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("source_decision_ids", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("promoted_at", DateTime),
    Column("reject_reason", Text),
)

_confluence_registry_table = Table(
    "confluence_registry", _sqlite_metadata,
    Column("code", String(1), primary_key=True),
    Column("slug", String(64), nullable=False, unique=True),
    Column("title", String(128), nullable=False),
    Column("definition_md", Text, nullable=False),
    Column("verify_spec", JSON, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("promoted_from", String(36), ForeignKey("confluence_candidates.id")),
    Column("created_at", DateTime, nullable=False),
    Column("deactivated_at", DateTime),
)


def _before_insert_decision(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def session():
    event.listen(Decision, "before_insert", _before_insert_decision)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s, factory

    event.remove(Decision, "before_insert", _before_insert_decision)
    await engine.dispose()


def _session_factory(factory):
    @asynccontextmanager
    async def _cm():
        async with factory() as s:
            yield s
    return _cm


def _make_llm() -> LLMClient:
    import json as _json
    payload = _json.dumps({
        "version": 1,
        "classification": "BAD_BUY",
        "severity_score": 0.5,
        "root_cause_tag": "test",
        "summary": "summary",
        "decision_snapshot": {
            "regime_declared": "RANGE",
            "action": "BUY",
            "confidence": 0.7,
            "confluences_declared": ["H"],
            "reasoning_excerpt": "x",
        },
        "forward_evidence": {"mfe_pct": 0.1, "mae_pct": -0.2},
        "misread_indicators": [],
        "ignored_signals": [],
        "confluence_analysis": {"misapplied_codes": ["H"], "should_have_used": [], "notes": ""},
        "proposed_pattern": None,
        "would_change": {"action": "HOLD", "rationale": "wait"},
        "hindsight_guardrails_passed": True,
    })
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=LLMResponse(
        text=payload,
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
        provider=LLMProvider.GEMINI_FLASH.value,
    ))
    return llm


@pytest.mark.asyncio
async def test_postmortem_tick_persists_lesson(session):
    s, factory = session
    now = datetime.now(tz=timezone.utc)
    decision = Decision(
        ts=now,
        agent="decisor",
        model="test",
        input={"price": 100.0, "rsi_15m": 30.0},
        output={"action": "BUY", "confidence": 0.7, "confluences": ["H"]},
        executed=True,
    )
    s.add(decision)
    await s.commit()
    s.add(DecisionOutcome(
        decision_id=decision.id,
        horizon_min=240,
        matured=True,
        classification="BAD_BUY",
        mfe_pct=Decimal("0.1"),
        mae_pct=Decimal("-0.5"),
        tp_target_pct=Decimal("0.4"),
        sl_dist_pct=Decimal("0.3"),
        computed_at=now,
    ))
    await s.commit()

    await outcome_postmortem_tick(
        session_factory=_session_factory(factory),
        llm=_make_llm(),
        max_per_tick=5,
    )

    async with factory() as check:
        outcome = (await check.execute(
            select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)
        )).scalar_one()
    assert outcome.postmortem_status == "completed"
    assert outcome.lesson_raw is not None
    assert outcome.lesson_raw["root_cause_tag"] == "test"
    assert outcome.lesson_normalized is not None
    assert outcome.lesson_normalized["route"] in ("remap", "candidate", "guidance")
    assert outcome.postmortem_at is not None


@pytest.mark.asyncio
async def test_postmortem_tick_skips_good_hold(session):
    s, factory = session
    now = datetime.now(tz=timezone.utc)
    decision = Decision(
        ts=now,
        agent="decisor",
        model="test",
        input={},
        output={"action": "HOLD"},
        executed=False,
    )
    s.add(decision)
    await s.commit()
    s.add(DecisionOutcome(
        decision_id=decision.id,
        horizon_min=240,
        matured=True,
        classification="GOOD_HOLD",
        computed_at=now,
    ))
    await s.commit()

    llm = _make_llm()
    await outcome_postmortem_tick(
        session_factory=_session_factory(factory),
        llm=llm,
        max_per_tick=5,
    )
    llm.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_postmortem_tick_retries_previously_failed(session):
    s, factory = session
    now = datetime.now(tz=timezone.utc)
    decision = Decision(
        ts=now,
        agent="decisor",
        model="test",
        input={"price": 100.0},
        output={"action": "HOLD", "confidence": 0.6, "confluences": ["H"]},
        executed=False,
    )
    s.add(decision)
    await s.commit()
    s.add(DecisionOutcome(
        decision_id=decision.id,
        horizon_min=240,
        matured=True,
        classification="MISSED_OPPORTUNITY",
        mfe_pct=Decimal("0.5"),
        tp_target_pct=Decimal("0.4"),
        computed_at=now,
        postmortem_status="failed",
        postmortem_at=now,
    ))
    await s.commit()

    await outcome_postmortem_tick(
        session_factory=_session_factory(factory),
        llm=_make_llm(),
        max_per_tick=5,
    )

    async with factory() as check:
        outcome = (await check.execute(
            select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)
        )).scalar_one()
    assert outcome.postmortem_status == "completed"
    assert outcome.lesson_raw is not None
    assert "_meta" not in outcome.lesson_raw


@pytest.mark.asyncio
async def test_postmortem_tick_validation_error_leaves_retryable(session):
    s, factory = session
    now = datetime.now(tz=timezone.utc)
    decision = Decision(
        ts=now,
        agent="decisor",
        model="test",
        input={"price": 100.0},
        output={"action": "BUY", "confidence": 0.7, "confluences": ["H"]},
        executed=True,
    )
    s.add(decision)
    await s.commit()
    s.add(DecisionOutcome(
        decision_id=decision.id,
        horizon_min=240,
        matured=True,
        classification="BAD_BUY",
        computed_at=now,
    ))
    await s.commit()

    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=LLMResponse(
        text='{"classification":"BAD_BUY","severity_score":0.5,"summary":"x"}',
        tokens_in=1,
        tokens_out=1,
        latency_ms=1,
        provider=LLMProvider.GEMINI_FLASH.value,
    ))

    await outcome_postmortem_tick(
        session_factory=_session_factory(factory),
        llm=llm,
        max_per_tick=5,
    )

    async with factory() as check:
        outcome = (await check.execute(
            select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)
        )).scalar_one()
    assert outcome.postmortem_status is None
    assert outcome.lesson_raw["_meta"]["attempts"] == 1


@pytest.mark.asyncio
async def test_postmortem_tick_skips_decisions_outside_window(session):
    s, factory = session
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    decision = Decision(
        ts=now - timedelta(hours=30),
        agent="decisor",
        model="test",
        input={"price": 100.0},
        output={"action": "BUY", "confidence": 0.7, "confluences": ["H"]},
        executed=True,
    )
    s.add(decision)
    await s.commit()
    s.add(DecisionOutcome(
        decision_id=decision.id,
        horizon_min=240,
        matured=True,
        classification="BAD_BUY",
        computed_at=now - timedelta(hours=26),
    ))
    await s.commit()

    llm = _make_llm()
    await outcome_postmortem_tick(
        session_factory=_session_factory(factory),
        llm=llm,
        max_per_tick=5,
        window_hours=25,
        now_fn=lambda: now,
    )
    llm.call.assert_not_awaited()
