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

_GOOD_INPUT = {
    "price": 100.0, "atr_ref_pct": 1.0,
    "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3,
}

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
    Column("position_side", String(5), default="LONG"),
    Column("leverage", Numeric(5, 2), default=1),
    Column("liquidation_price", Numeric(18, 8)),
    Column("margin_mode", String(10), default="isolated"),
    Column("funding_paid_usdt", Numeric(18, 4)),
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

_config_table = Table(
    "config", _sqlite_metadata,
    Column("key", String(60), primary_key=True),
    Column("value", String(500), nullable=False),
    Column("value_type", String(20), nullable=False),
    Column("description", String(500)),
    Column("updated_at", DateTime),
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
    # should be exactly the fresh decision without any outcome
    assert len(candidates) == 1
    assert candidates[0].id == fresh.id


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


class _SessionContext:
    """Async context manager wrapping an existing session for tests."""
    def __init__(self, session): self._s = session
    async def __aenter__(self): return self._s
    async def __aexit__(self, *_): return None


async def test_fetch_candidates_includes_unknown_with_trade_id(session):
    """UNKNOWN con trade_id asociado debe ser candidato para reproceso.

    Reproduce el escenario real: el job corrió mientras el trade estaba abierto
    (clasificó UNKNOWN), luego el trade se cerró con pérdida. El job debe
    volver a evaluarlo en el siguiente tick para reclasificarlo como BAD_BUY.
    """
    from agents.outcome_attribution_job import _fetch_candidates

    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    trade = Trade(
        ts_open=now - timedelta(hours=2),
        ts_close=now - timedelta(hours=1),
        side="BUY",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("77371.12"),
        exit_price=Decimal("77153.28"),
        pnl_pct=Decimal("-0.2816"),
        pnl_usdt=Decimal("-0.02"),
        status="closed",
        close_reason="sl_triggered",
    )
    session.add(trade)
    await session.commit()

    decision = Decision(
        ts=now - timedelta(hours=2),
        agent="decisor", model="m",
        input=_GOOD_INPUT,
        output={"action": "BUY"},
        executed=True,
        trade_id=trade.id,
    )
    session.add(decision)
    await session.commit()

    session.add(DecisionOutcome(
        decision_id=decision.id, horizon_min=240, matured=False,
        classification="UNKNOWN", computed_at=now - timedelta(hours=1, minutes=30),
    ))
    await session.commit()

    candidates = await _fetch_candidates(session, now=now)
    assert any(c.id == decision.id for c in candidates)


async def test_fetch_candidates_excludes_unknown_without_trade_id(session):
    """UNKNOWN sin trade_id (input faltante, nunca clasificable) NO debe reprocesarse.

    Evita el loop infinito de evaluar indefinidamente decisiones que siempre
    retornarán UNKNOWN por datos de input insuficientes.
    """
    from agents.outcome_attribution_job import _fetch_candidates

    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    decision_no_trade = Decision(
        ts=now - timedelta(hours=2),
        agent="decisor", model="m",
        input={},
        output={"action": "HOLD"},
        executed=False,
        trade_id=None,
    )
    session.add(decision_no_trade)
    await session.commit()

    session.add(DecisionOutcome(
        decision_id=decision_no_trade.id, horizon_min=240, matured=False,
        classification="UNKNOWN", computed_at=now - timedelta(hours=1),
    ))
    await session.commit()

    candidates = await _fetch_candidates(session, now=now)
    assert not any(c.id == decision_no_trade.id for c in candidates)


async def test_outcome_attribution_tick_reclassifies_unknown_buy_after_trade_closes(session):
    """Regression: BUY ejecutado clasificado UNKNOWN (trade aún abierto en el 1er tick)
    debe reclasificarse como BAD_BUY en el siguiente tick una vez que el trade cierra
    con pnl_pct negativo.
    """
    from agents.outcome_attribution_job import outcome_attribution_tick

    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    now_after_close = t0 + timedelta(hours=5)

    trade = Trade(
        ts_open=t0 + timedelta(seconds=7),
        ts_close=t0 + timedelta(hours=2),
        side="BUY",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("100.0"),
        exit_price=Decimal("99.7"),
        pnl_pct=Decimal("-0.30"),
        pnl_usdt=Decimal("-0.03"),
        status="closed",
        close_reason="sl_triggered",
    )
    session.add(trade)
    await session.commit()

    decision = Decision(
        ts=t0, agent="decisor", model="m",
        input=_GOOD_INPUT,
        output={"action": "BUY"},
        executed=True,
        trade_id=trade.id,
    )
    session.add(decision)

    for i in range(1, 241):
        session.add(Ohlcv(
            time=t0 + timedelta(minutes=i), timeframe="1m",
            open=Decimal("100.0"), high=Decimal("100.1"),
            low=Decimal("99.5"), close=Decimal("99.6"),
            volume=Decimal("1.0"),
        ))
    await session.commit()

    session.add(DecisionOutcome(
        decision_id=decision.id, horizon_min=240, matured=False,
        classification="UNKNOWN", computed_at=t0 + timedelta(hours=1),
    ))
    await session.commit()

    def factory():
        return _SessionContext(session)

    await outcome_attribution_tick(
        session_factory=factory, horizon_min=240, now_fn=lambda: now_after_close,
    )

    outcome = (await session.execute(
        select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)
    )).scalar_one()
    assert outcome.classification == "BAD_BUY"


async def test_outcome_attribution_tick_classifies_pending_and_finalized_decisions(session):
    from agents.outcome_attribution_job import outcome_attribution_tick

    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(hours=5)
    decision = Decision(
        ts=t0, agent="decisor", model="m",
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"}, executed=False,
    )
    session.add(decision)
    for i in range(1, 241):
        session.add(Ohlcv(
            time=t0 + timedelta(minutes=i), timeframe="1m",
            open=Decimal("100.0"), high=Decimal("100.5"),
            low=Decimal("99.95"), close=Decimal("100.4"),
            volume=Decimal("1.0"),
        ))
    await session.commit()

    def factory():
        return _SessionContext(session)

    await outcome_attribution_tick(
        session_factory=factory, horizon_min=240, now_fn=lambda: now,
    )

    outcome = (await session.execute(
        select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)
    )).scalar_one()
    assert outcome.classification == "MISSED_OPPORTUNITY"
