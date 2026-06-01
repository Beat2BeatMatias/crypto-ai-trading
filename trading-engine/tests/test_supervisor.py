"""Tests for Supervisor — daily ratification + playbook generator (§F5.bis.5)."""
from __future__ import annotations

import json
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

from shared.db.models import Decision, Trade, PlaybookVersion, ConfigHistory, ConfigEntry
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

_decision_outcomes_table = Table(
    "decision_outcomes", _sqlite_metadata,
    Column("decision_id", String(36), primary_key=True),
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
    Column("order_id_sl", String(50)),
    Column("order_id_tp", String(50)),
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

_config_history_table = Table(
    "config_history", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("key", String(50), nullable=False),
    Column("old_value", String(500)),
    Column("new_value", String(500)),
    Column("changed_by", String(20)),
)

_config_table = Table(
    "config", _sqlite_metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text, nullable=False),
    Column("value_type", String(20), nullable=False),
    Column("description", Text),
    Column("updated_at", DateTime),
)

_fee_snapshots_table = Table(
    "fee_snapshots", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("symbol", String(20), nullable=False, default="BTC/USDT"),
    Column("maker_fee", Numeric(8, 6), nullable=False),
    Column("taker_fee", Numeric(8, 6), nullable=False),
    Column("raw", JSON, nullable=False),
)

_balance_snapshots_table = Table(
    "balance_snapshots", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("usdt", Numeric(18, 4), nullable=False),
    Column("btc", Numeric(18, 8), nullable=False),
    Column("usdt_locked", Numeric(18, 4), nullable=False, server_default="0"),
    Column("btc_locked", Numeric(18, 8), nullable=False, server_default="0"),
    Column("source", String(20), nullable=False, default="binance"),
)

_confluence_candidates_table = Table(
    "confluence_candidates", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("pattern_tag", String(64), nullable=False),
    Column("proposed_code", String(1)),
    Column("title", String(128), nullable=False),
    Column("definition_md", Text, nullable=False),
    Column("verify_spec", JSON, nullable=False),
    Column("occurrence_count", Integer, nullable=False, default=1),
    Column("first_seen_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("source_decision_ids", JSON, nullable=False),
    Column("status", String(16), nullable=False, default="open"),
    Column("promoted_at", DateTime),
    Column("reject_reason", Text),
)

_confluence_registry_table = Table(
    "confluence_registry", _sqlite_metadata,
    Column("code", String(1), primary_key=True),
    Column("slug", String(64), nullable=False),
    Column("title", String(128), nullable=False),
    Column("definition_md", Text, nullable=False),
    Column("verify_spec", JSON, nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("promoted_from", String(36)),
    Column("created_at", DateTime, nullable=False),
    Column("deactivated_at", DateTime),
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


def _before_insert_config_history(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    """Fresh in-memory SQLite session seeded with playbook v0 + decisions + trades."""
    event.listen(Decision, "before_insert", _before_insert_decision)
    event.listen(Trade, "before_insert", _before_insert_trade)
    event.listen(PlaybookVersion, "before_insert", _before_insert_playbook)
    event.listen(ConfigHistory, "before_insert", _before_insert_config_history)

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
    event.remove(ConfigHistory, "before_insert", _before_insert_config_history)
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


# ---------------------------------------------------------------------------
# Ratification flow (§F5.bis.5 + §2.7.4)
# ---------------------------------------------------------------------------

_VALID_PLAYBOOK_MARKDOWN = (
    "# Playbook v5 — 2026-05-10 UTC\n\n"
    "## Métricas del período\nWR 60%.\n\n"
    "## Setups que funcionaron\n- G+B en TRENDING_UP.\n\n"
    "## Patrones a evitar\n- BUY sin volumen.\n\n"
    "## Contexto de mercado actual\nAlcista moderado.\n\n"
    "## Régimen esperado próximas 24h\nTRENDING_UP\n\n"
    "## Reglas específicas\n1. Exigir 3 confluencias.\n\n"
    "## Cambios vs playbook anterior\nPrimer playbook.\n\n"
    "## Limitaciones del análisis\nSin limitaciones identificadas en este período.\n"
)


def _llm_with_sequence(*responses: str) -> MagicMock:
    """LLM mock whose .call() returns the given texts in order."""
    llm = MagicMock()
    llm.call = AsyncMock(side_effect=[
        LLMResponse(text=t, tokens_in=100, tokens_out=50, latency_ms=200, provider="gemini-2.5-pro")
        for t in responses
    ])
    return llm


async def _seed_active_playbook(
    session,
    *,
    version: int = 5,
    ts_generated: datetime | None = None,
    win_rate: float | None = 60.0,
    content: str = _VALID_PLAYBOOK_MARKDOWN,
) -> PlaybookVersion:
    """Reset the active playbook to a controlled state for ratification tests."""
    await session.execute(delete(PlaybookVersion))
    pb = PlaybookVersion(
        version=version,
        ts_generated=ts_generated or datetime.now(timezone.utc),
        content=content,
        model="gemini-2.5-pro",
        trades_analyzed=20,
        win_rate=Decimal(str(win_rate)) if win_rate is not None else None,
        active=True,
    )
    session.add(pb)
    await session.commit()
    return pb


async def test_supervisor_ratifies_when_llm_returns_ratify_true(session):
    # GIVEN a fresh active playbook with baseline close to the period WR (100%)
    # so the deterministic WR-delta guardrail does not short-circuit.
    await _seed_active_playbook(session, win_rate=90.0)
    eval_json = json.dumps({
        "ratify": True,
        "reason": "Métricas dentro del rango baseline; régimen estable.",
        "suggested_change_summary": None,
    })
    llm = _llm_with_sequence(eval_json)
    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=5)

    # WHEN run() is called
    await sup.run()

    # THEN no new playbook version is created
    versions = (await session.execute(select(PlaybookVersion))).scalars().all()
    assert len(versions) == 1
    assert versions[0].version == 5
    assert versions[0].active is True

    # AND exactly one supervisor decision is persisted with ratified=True (AC-14)
    sup_decisions = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalars().all()
    assert len(sup_decisions) == 1
    out = sup_decisions[0].output
    assert out["ratified"] is True
    assert out["ratify_reason"].startswith("Métricas")
    assert out["force_regen_reason"] is None
    assert out["mode"] == "normal"
    assert out.get("playbook") is None  # no new playbook in output

    # AND the LLM was called exactly once (eval phase only, no regeneration, no config)
    assert llm.call.call_count == 1


async def test_supervisor_force_regen_when_playbook_age_exceeds_max(session, fake_llm):
    # GIVEN an active playbook older than max_playbook_age_days (default 7)
    # AND a baseline WR close to the period WR (so age fires first, not WR delta).
    old_ts = datetime.now(timezone.utc) - timedelta(days=10)
    await _seed_active_playbook(session, ts_generated=old_ts, win_rate=90.0)
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)

    # WHEN run() is called
    await sup.run()

    # THEN a new playbook version is created
    versions = (await session.execute(
        select(PlaybookVersion).order_by(PlaybookVersion.version)
    )).scalars().all()
    assert len(versions) == 2
    assert versions[1].active is True

    # AND the decision records the deterministic force_regen_reason (AC-13)
    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["ratified"] is False
    assert out["ratify_reason"] is None
    assert "playbook_age_days" in out["force_regen_reason"]
    assert "max_playbook_age_days" in out["force_regen_reason"]

    # AND the LLM was called exactly once (regeneration only — eval was short-circuited)
    assert fake_llm.call.call_count == 1


async def test_supervisor_force_regen_when_wr_delta_exceeds_threshold(session, fake_llm):
    # GIVEN an active playbook with baseline WR 20%, but current period WR is 100%
    # (seeded trades are all winners → WR 100%; |100-20|=80 > 15 default threshold)
    await _seed_active_playbook(session, win_rate=20.0)
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)

    await sup.run()

    versions = (await session.execute(select(PlaybookVersion))).scalars().all()
    assert len(versions) == 2

    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["ratified"] is False
    assert "wr_24h" in out["force_regen_reason"]
    assert "baseline_wr" in out["force_regen_reason"]
    # LLM eval skipped (deterministic short-circuit) → 1 call total (regeneration)
    assert fake_llm.call.call_count == 1


async def test_supervisor_force_regen_when_kill_switch_triggered_in_period(session, fake_llm):
    # GIVEN an active playbook within normal bounds (baseline ~ period WR)
    # + a kill_switch trigger in the period.
    await _seed_active_playbook(session, win_rate=90.0)
    session.add(ConfigHistory(
        ts=datetime.now(timezone.utc) - timedelta(hours=2),
        key="kill_switch",
        old_value="false",
        new_value="true",
        changed_by="operator",
    ))
    await session.commit()

    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    await sup.run()

    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["ratified"] is False
    assert out["force_regen_reason"] == "kill_switch_was_triggered_in_period"
    assert fake_llm.call.call_count == 1


async def test_supervisor_force_regen_when_no_active_playbook(session, fake_llm):
    # GIVEN there is no active playbook at all
    await session.execute(delete(PlaybookVersion))
    await session.commit()
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)

    await sup.run()

    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["ratified"] is False
    assert out["force_regen_reason"] == "no_active_playbook"


async def test_supervisor_eval_json_parse_failure_defaults_to_regenerate(session):
    # GIVEN an LLM that returns invalid JSON for the eval call, then a valid playbook
    # AND a baseline that does not trigger the WR-delta guardrail.
    await _seed_active_playbook(session, win_rate=90.0)
    llm = _llm_with_sequence("not a json", _VALID_PLAYBOOK_MARKDOWN)
    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=5)

    await sup.run()

    # THEN a new playbook version is created (safe default = regenerate)
    versions = (await session.execute(select(PlaybookVersion))).scalars().all()
    assert len(versions) == 2

    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["ratified"] is False
    assert out["force_regen_reason"].startswith("eval_llm_error")


async def test_supervisor_ratify_emits_no_playbook_updated_signal(session):
    # GIVEN a ratification (verified separately) the active playbook ts_generated
    # must NOT change (no insert, no update of the active row).
    pb = await _seed_active_playbook(session, win_rate=90.0)
    original_ts = pb.ts_generated

    eval_json = json.dumps({"ratify": True, "reason": "ok", "suggested_change_summary": None})
    llm = _llm_with_sequence(eval_json)
    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=5)
    await sup.run()

    refreshed = (await session.execute(
        select(PlaybookVersion).where(PlaybookVersion.version == 5)
    )).scalar_one()
    assert refreshed.ts_generated == original_ts
    assert refreshed.active is True


async def test_ratify_verdict_includes_age_and_baseline_in_decision(session):
    # GIVEN an active playbook with known age + baseline close to the period WR.
    old_ts = datetime.now(timezone.utc) - timedelta(days=3)
    await _seed_active_playbook(session, ts_generated=old_ts, win_rate=95.5)
    eval_json = json.dumps({"ratify": True, "reason": "estable", "suggested_change_summary": None})
    llm = _llm_with_sequence(eval_json)
    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=5)

    await sup.run()

    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["playbook_age_days"] == 3
    assert out["playbook_win_rate_baseline"] == 95.5


# ---------------------------------------------------------------------------
# Config suggestions — v1.3 LLM-Centric (toggles + removed legacy keys)
# ---------------------------------------------------------------------------

async def test_apply_suggestions_accepts_coherence_strict_mode_toggle(session, fake_llm):
    # GIVEN a Supervisor and a current config with strict_mode off (entry seeded)
    session.add(ConfigEntry(key="coherence_strict_mode", value="false",
                            value_type="bool", description="test"))
    await session.commit()
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    current_config = {"coherence_strict_mode": False}

    suggestions = {
        "suggestions": [
            {"key": "coherence_strict_mode", "current": "false", "suggested": "true",
             "reason": "Tasa C1/C2/C3 > 25% en últimos ciclos."},
        ],
        "summary": "Activar strict_mode por hallucinations recurrentes.",
    }

    # WHEN we apply the suggestions
    applied, rejected = await sup._apply_config_suggestions(suggestions, current_config)

    # THEN the toggle is applied and the config_entry persisted
    assert len(applied) == 1
    assert applied[0]["key"] == "coherence_strict_mode"
    assert len(rejected) == 0

    entry = (await session.execute(
        select(ConfigHistory).where(ConfigHistory.key == "coherence_strict_mode")
    )).scalar_one()
    assert entry.new_value == "true"
    assert entry.changed_by == "supervisor"


async def test_apply_suggestions_accepts_two_pass_disable(session, fake_llm):
    # GIVEN a Supervisor with two_pass currently enabled (entry seeded)
    session.add(ConfigEntry(key="two_pass_enabled", value="true",
                            value_type="bool", description="test"))
    await session.commit()
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    current_config = {"two_pass_enabled": True}

    suggestions = {
        "suggestions": [
            {"key": "two_pass_enabled", "current": "true", "suggested": False,
             "reason": "Auto-correcciones frecuentes sin mejora en outcome."},
        ],
        "summary": "",
    }

    # WHEN we apply the suggestions
    applied, rejected = await sup._apply_config_suggestions(suggestions, current_config)

    # THEN the toggle is disabled and persisted
    assert len(applied) == 1
    assert len(rejected) == 0
    entry = (await session.execute(
        select(ConfigHistory).where(ConfigHistory.key == "two_pass_enabled")
    )).scalar_one()
    assert entry.new_value == "false"


async def test_apply_suggestions_rejects_invalid_bool_for_toggle(session, fake_llm):
    # GIVEN a Supervisor and an invalid bool suggestion
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    current_config = {"coherence_strict_mode": False}

    suggestions = {
        "suggestions": [
            {"key": "coherence_strict_mode", "current": "false", "suggested": "maybe",
             "reason": "value ambiguous"},
        ],
        "summary": "",
    }

    # WHEN we apply
    applied, rejected = await sup._apply_config_suggestions(suggestions, current_config)

    # THEN the suggestion is rejected and nothing is persisted
    assert len(applied) == 0
    assert len(rejected) == 1
    assert "booleano" in rejected[0]["reject_reason"]
    persisted = (await session.execute(
        select(ConfigHistory).where(ConfigHistory.key == "coherence_strict_mode")
    )).scalars().all()
    assert len(persisted) == 0


async def test_apply_suggestions_rejects_removed_legacy_keys(session, fake_llm):
    # GIVEN a Supervisor and suggestions for keys no longer auto-adjustable
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    current_config = {"min_confluences_buy": 2, "rsi_overbought_1h": 70}

    suggestions = {
        "suggestions": [
            {"key": "min_confluences_buy", "current": 2, "suggested": 3,
             "reason": "Demasiados trades perdedores."},
            {"key": "rsi_overbought_1h", "current": 70, "suggested": 75,
             "reason": "Mercado sobrecomprado."},
        ],
        "summary": "",
    }

    # WHEN we apply
    applied, rejected = await sup._apply_config_suggestions(suggestions, current_config)

    # THEN both suggestions are rejected with the "no elegible" reason
    assert len(applied) == 0
    assert len(rejected) == 2
    for r in rejected:
        assert "no elegible" in r["reject_reason"]


async def test_apply_suggestions_rejects_atr_timeframe(session, fake_llm):
    # GIVEN a Supervisor and a suggestion to modify atr_timeframe
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    current_config = {"atr_timeframe": "15m"}

    suggestions = {
        "suggestions": [
            {"key": "atr_timeframe", "current": "15m", "suggested": "5m",
             "reason": "Mayor granularidad para scalping."},
        ],
        "summary": "",
    }

    # WHEN we apply
    applied, rejected = await sup._apply_config_suggestions(suggestions, current_config)

    # THEN the suggestion is rejected — el Supervisor no puede modificar el timeframe del ATR
    assert len(applied) == 0
    assert len(rejected) == 1
    assert "no elegible" in rejected[0]["reject_reason"]

    # AND nothing is persisted in config_history
    persisted = (await session.execute(
        select(ConfigHistory).where(ConfigHistory.key == "atr_timeframe")
    )).scalars().all()
    assert len(persisted) == 0


async def test_apply_suggestions_rejects_decisor_interval_min(session, fake_llm):
    # GIVEN a Supervisor and a suggestion to modify decisor_interval_min
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=5)
    current_config = {"decisor_interval_min": 10}

    suggestions = {
        "suggestions": [
            {"key": "decisor_interval_min", "current": 10, "suggested": 5,
             "reason": "Alta volatilidad detectada."},
        ],
        "summary": "",
    }

    # WHEN we apply
    applied, rejected = await sup._apply_config_suggestions(suggestions, current_config)

    # THEN the suggestion is rejected — el Supervisor no puede modificar el intervalo de decisión
    assert len(applied) == 0
    assert len(rejected) == 1
    assert "no elegible" in rejected[0]["reject_reason"]

    # AND nothing is persisted in config_history
    persisted = (await session.execute(
        select(ConfigHistory).where(ConfigHistory.key == "decisor_interval_min")
    )).scalars().all()
    assert len(persisted) == 0


def test_normalize_bool_accepts_canonical_forms():
    from agents.supervisor import Supervisor
    assert Supervisor._normalize_bool(True) is True
    assert Supervisor._normalize_bool(False) is False
    assert Supervisor._normalize_bool("true") is True
    assert Supervisor._normalize_bool("FALSE") is False
    assert Supervisor._normalize_bool("1") is True
    assert Supervisor._normalize_bool("0") is False
    assert Supervisor._normalize_bool(1) is True
    assert Supervisor._normalize_bool(0) is False
    assert Supervisor._normalize_bool("yes") is None
    assert Supervisor._normalize_bool("maybe") is None
    assert Supervisor._normalize_bool(None) is None
    assert Supervisor._normalize_bool(2) is None


async def test_supervisor_force_regen_when_regime_changes(session):
    # GIVEN an active playbook declaring TRENDING_UP regime (content has it)
    # AND a baseline WR close to the period WR (so regime fires first, not WR delta).
    await _seed_active_playbook(session, win_rate=90.0)

    # AND decisions in the period are dominated by RANGE regime
    await session.execute(delete(Decision))
    now = datetime.now(timezone.utc)
    for i in range(8):
        session.add(Decision(
            ts=now - timedelta(minutes=i * 5 + 1),
            agent="decisor", model="gemini-2.5-flash",
            input={},
            output={"action": "HOLD", "confidence": 0.55, "regime": "RANGE"},
            executed=True,
        ))
    await session.commit()

    eval_json = json.dumps({"ratify": True, "reason": "estable", "suggested_change_summary": None})
    llm = _llm_with_sequence(eval_json, _VALID_PLAYBOOK_MARKDOWN)
    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=5)

    await sup.run()

    sup_decision = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalar_one()
    out = sup_decision.output
    assert out["ratified"] is False
    assert "regime_changed" in out["force_regen_reason"]
    assert "TRENDING_UP" in out["force_regen_reason"]
    assert "RANGE" in out["force_regen_reason"]
    # LLM eval skipped → 1 call (regeneration only)
    assert llm.call.call_count == 1


# ---------------------------------------------------------------------------
# Safe bounds tests
# ---------------------------------------------------------------------------

from agents.supervisor import _SAFE_BOUNDS


def test_safe_bounds_allow_sl_atr_multiplier_up_to_2():
    lo, hi = _SAFE_BOUNDS["sl_atr_multiplier"]
    assert lo <= 1.0 <= hi
    assert hi >= 2.0


def test_safe_bounds_min_rr_allows_2():
    lo, hi = _SAFE_BOUNDS["min_rr_ratio"]
    assert lo <= 2.0 <= hi


# ---------------------------------------------------------------------------
# P2-T2: Closed-loop — baseline de métricas + auto-revert
# ---------------------------------------------------------------------------

async def test_supervisor_stores_baseline_when_config_applied(session):
    """Cuando el supervisor aplica una sugerencia de config, Decision.output
    debe tener 'config_applied_baseline' con win_rate, profit_factor y applied_keys."""
    # GIVEN: ConfigEntry seeded con un valor que el supervisor puede cambiar
    await session.execute(delete(ConfigEntry).where(ConfigEntry.key == "min_rr_ratio"))
    await session.execute(delete(ConfigEntry).where(ConfigEntry.key == "default_rr_ratio"))
    session.add(ConfigEntry(key="min_rr_ratio", value="1.5", value_type="float",
                            description="test", updated_at=datetime.now(tz=timezone.utc)))
    session.add(ConfigEntry(key="default_rr_ratio", value="2.5", value_type="float",
                            description="test", updated_at=datetime.now(tz=timezone.utc)))
    await session.commit()

    # LLM responde: regenerar + sugerencia de config válida
    config_suggestion = json.dumps({
        "suggestions": [
            {"key": "min_rr_ratio", "current": 1.5, "suggested": 2.0, "reason": "mejorar RR"},
        ],
        "summary": "subir R:R",
    })
    llm = _llm_with_sequence(
        '{"ratify": false, "reason": "métricas bajas"}',
        (
            "# Playbook v2\n\n"
            "## Métricas del período\n- test\n\n"
            "## Setups que funcionaron\n- A\n\n"
            "## Patrones a evitar\n- B\n\n"
            "## Contexto de mercado actual\n- RANGE\n\n"
            "## Régimen esperado próximas 24h\nNEUTRAL\n\n"
            "## Reglas específicas\n1. test\n\n"
            "## Cambios vs playbook anterior\n- test\n"
        ),
        config_suggestion,
    )

    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=0)
    current_cfg = {"min_rr_ratio": 1.5, "default_rr_ratio": 2.5}
    await sup.run(current_config=current_cfg)

    # THEN: la Decision del supervisor tiene el baseline
    decisions = (await session.execute(
        select(Decision).where(Decision.agent == "supervisor")
    )).scalars().all()
    assert len(decisions) >= 1
    sup_dec = decisions[-1]
    baseline = sup_dec.output.get("config_applied_baseline")
    assert baseline is not None, f"Falta config_applied_baseline en: {sup_dec.output}"
    assert "win_rate" in baseline
    assert "profit_factor" in baseline
    assert "min_rr_ratio" in baseline.get("applied_keys", [])


async def test_supervisor_reverts_config_when_metrics_degraded(session):
    """Si la Decision previa tiene un baseline con WR=60% y el WR actual es 0%
    (bajó > 10pp), el supervisor revierte la clave aplicada."""
    import uuid as _uuid

    # GIVEN: ConfigEntry con valor actual (post-cambio del supervisor anterior)
    await session.execute(delete(ConfigEntry).where(ConfigEntry.key == "min_rr_ratio"))
    await session.execute(delete(ConfigEntry).where(ConfigEntry.key == "default_rr_ratio"))
    session.add(ConfigEntry(key="min_rr_ratio", value="2.0", value_type="float",
                            description="test", updated_at=datetime.now(tz=timezone.utc)))
    session.add(ConfigEntry(key="default_rr_ratio", value="2.5", value_type="float",
                            description="test", updated_at=datetime.now(tz=timezone.utc)))
    await session.commit()

    # Simular Decision previa con baseline (win_rate=60%)
    prev_ts = datetime.now(tz=timezone.utc) - timedelta(hours=20)
    session.add(Decision(
        ts=prev_ts,
        agent="supervisor",
        model="test",
        tokens_in=10, tokens_out=10, latency_ms=100,
        input={},
        output={
            "ratified": False,
            "config_applied_baseline": {
                "win_rate": 60.0,
                "profit_factor": 1.8,
                "applied_keys": ["min_rr_ratio"],
                "ts": prev_ts.isoformat(),
            },
        },
        executed=False,
    ))
    # Simular entrada en config_history para poder revertir
    session.add(ConfigHistory(
        id=_uuid.uuid4(),
        ts=prev_ts,
        key="min_rr_ratio",
        old_value="1.5",
        new_value="2.0",
        changed_by="supervisor",
    ))
    await session.commit()

    # Borrar los trades existentes para que WR actual sea 0% (< 60% - 10pp = 50%)
    await session.execute(delete(Trade))
    await session.commit()

    # LLM responde: ratificar (pero el revert ocurre antes, al inicio de run())
    llm = _llm_with_sequence(
        '{"ratify": true, "reason": "ok"}',
        json.dumps({"suggestions": [], "summary": ""}),
    )

    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=0)
    current_cfg = {"min_rr_ratio": 2.0, "default_rr_ratio": 2.5}
    # win_rate actual = 0% (no hay trades) → bajó 60pp > threshold 10pp → revert
    await sup.run(current_config=current_cfg)

    # THEN: min_rr_ratio fue revertido a "1.5"
    revert_entries = (await session.execute(
        select(ConfigHistory)
        .where(
            ConfigHistory.key == "min_rr_ratio",
            ConfigHistory.changed_by == "supervisor:revert",
        )
    )).scalars().all()
    assert len(revert_entries) >= 1, "No se encontró entrada de revert en config_history"
    assert revert_entries[-1].new_value == "1.5"
