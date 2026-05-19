"""Tests for the outcome attribution background job.

The job orchestrates: query candidate decisions, fetch OHLCV, call the pure
`attribute()` function, and UPSERT the result into `decision_outcomes`.

Uses a local SQLite-compatible MetaData (no JSONB / no Postgres UUID) so the
suite runs without Postgres. The job module itself stays dialect-agnostic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime,
    Numeric, ForeignKey, event, select,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.db.models import Decision, DecisionOutcome, Ohlcv, Trade


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
    Column("ts_open", DateTime, nullable=False),
    Column("ts_close", DateTime),
    Column("side", String(4), nullable=False),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("exit_price", Numeric(18, 8)),
    Column("pnl_usdt", Numeric(18, 4)),
    Column("pnl_pct", Numeric(8, 4)),
    Column("status", String(12), nullable=False),
    Column("close_reason", String(20)),
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
)


def _before_insert_decision(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


def _before_insert_trade(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()


@pytest_asyncio.fixture
async def session():
    event.listen(Decision, "before_insert", _before_insert_decision)
    event.listen(Trade, "before_insert", _before_insert_trade)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s

    event.remove(Decision, "before_insert", _before_insert_decision)
    event.remove(Trade, "before_insert", _before_insert_trade)
    await engine.dispose()


async def test_fetch_candidates_returns_decisions_in_window(session):
    from agents.outcome_attribution_job import _fetch_candidates

    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    session.add(Decision(
        ts=now - timedelta(hours=26), agent="decisor", model="m",
        input={}, output={"action": "HOLD"}, executed=False,
    ))

    fresh = Decision(
        ts=now - timedelta(hours=2), agent="decisor", model="m",
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"}, executed=False,
    )
    session.add(fresh)

    done = Decision(
        ts=now - timedelta(hours=3), agent="decisor", model="m",
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"}, executed=False,
    )
    session.add(done)
    await session.commit()

    session.add(DecisionOutcome(
        decision_id=done.id, horizon_min=240, matured=True,
        classification="GOOD_HOLD", computed_at=now - timedelta(hours=1),
    ))
    await session.commit()

    candidates = await _fetch_candidates(session, now=now)
    ids = {c.id for c in candidates}
    assert fresh.id in ids
    assert done.id not in ids


async def test_upsert_outcome_inserts_then_updates(session):
    from agents.outcome_attribution_job import _upsert_outcome
    from agents.outcome_attribution import DecisionAttribution

    decision = Decision(
        ts=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        agent="decisor", model="m",
        input={}, output={"action": "HOLD"}, executed=False,
    )
    session.add(decision)
    await session.commit()

    attr1 = DecisionAttribution(
        decision_id=decision.id, horizon_min=240, matured=False,
        forward_return_pct=None, mfe_pct=0.1, mae_pct=-0.05,
        time_to_mfe_min=5, time_to_mae_min=2,
        sl_dist_pct=0.3, tp_target_pct=0.39,
        classification="PENDING",
        computed_at=datetime(2026, 5, 18, 13, 0, tzinfo=timezone.utc),
    )
    await _upsert_outcome(session, attr1)
    await session.commit()

    attr2 = DecisionAttribution(
        decision_id=decision.id, horizon_min=240, matured=True,
        forward_return_pct=0.5, mfe_pct=0.5, mae_pct=-0.05,
        time_to_mfe_min=15, time_to_mae_min=2,
        sl_dist_pct=0.3, tp_target_pct=0.39,
        classification="MISSED_OPPORTUNITY",
        computed_at=datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc),
    )
    await _upsert_outcome(session, attr2)
    await session.commit()

    rows = (await session.execute(select(DecisionOutcome))).scalars().all()
    assert len(rows) == 1
    assert rows[0].classification == "MISSED_OPPORTUNITY"
    assert rows[0].matured is True
