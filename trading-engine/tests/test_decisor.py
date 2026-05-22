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
    # GIVEN a Decisor con two_pass_enabled=False para aislar el comportamiento base
    llm = _make_llm_client(_VALID_LLM_RESPONSE)
    pm = _make_prompt_manager()
    decisor = Decisor(
        session=session,
        llm=llm,
        symbol="BTC/USDT",
        prompt_manager=pm,
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=False,
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
    # Con two_pass_enabled=False sólo hay una llamada al LLM
    assert row.tokens_in == 100
    assert row.tokens_out == 50
    assert row.output.get("two_pass_triggered") is False


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


@pytest.mark.asyncio
async def test_two_pass_triggered_when_coherence_warnings_on_c1_c2_c3(session: AsyncSession):
    # GIVEN una respuesta LLM válida que va a generar warnings C2/C3 por el CoherenceChecker
    # (la respuesta declara TRENDING_UP con confluencias que el contexto mínimo no puede confirmar)
    llm = _make_llm_client(_VALID_LLM_RESPONSE)
    pm = _make_prompt_manager()
    decisor = Decisor(
        session=session,
        llm=llm,
        symbol="BTC/USDT",
        prompt_manager=pm,
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=True,
    )

    # WHEN deciding con two_pass_enabled=True
    result = await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN el output sigue siendo BUY (el two-pass mantiene la misma respuesta del mock)
    assert result.action == DecisorAction.BUY

    # AND la fila persistida refleja el two-pass
    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.output["two_pass_triggered"] is True
    # Tokens = pass1 (100) + pass2 (100) = 200
    assert row.tokens_in == 200
    assert row.tokens_out == 100


@pytest.mark.asyncio
async def test_two_pass_disabled_uses_single_llm_call(session: AsyncSession):
    # GIVEN two_pass_enabled=False
    llm = _make_llm_client(_VALID_LLM_RESPONSE)
    pm = _make_prompt_manager()
    decisor = Decisor(
        session=session,
        llm=llm,
        symbol="BTC/USDT",
        prompt_manager=pm,
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=False,
    )

    # WHEN deciding
    await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN sólo se hizo una llamada al LLM
    assert llm.call.call_count == 1

    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert rows[0].output["two_pass_triggered"] is False
    assert rows[0].tokens_in == 100


# ---------------------------------------------------------------------------
# Two-pass C7 — R:R adjustment
# ---------------------------------------------------------------------------

# BUY con R:R insuficiente: price=95000, sl=93000 (risk=2000), tp=96000 (reward=1000) → R:R=0.5
_BUY_BAD_RR = json.dumps({
    "regime": "RANGE",
    "confluences": ["H", "C"],
    "action": "BUY",
    "confidence_base": 0.7,
    "confidence_adjustment": 0.0,
    "confidence": 0.7,
    "stop_loss": 93000.0,
    "take_profit": 96000.0,
    "position_size_pct": 0.05,
    "expected_holding_min": 45,
    "reasoning": "[DECISION] BUY [MERCADO] RANGE [SENALES] H y C [CONFIANZA] 70% [NIVELES] SL $93k TP $96k R:R 1.5",
})

# BUY corregido por el LLM en second pass: tp=98000 → R:R = (98000-95000)/(95000-93000) = 1.5
_BUY_CORRECTED_RR = json.dumps({
    "regime": "RANGE",
    "confluences": ["H", "C"],
    "action": "BUY",
    "confidence_base": 0.7,
    "confidence_adjustment": 0.0,
    "confidence": 0.7,
    "stop_loss": 93000.0,
    "take_profit": 98000.0,
    "position_size_pct": 0.05,
    "expected_holding_min": 45,
    "reasoning": "[DECISION] BUY ajustado [MERCADO] RANGE [SENALES] H y C [CONFIANZA] 70% [NIVELES] SL $93k TP $98k R:R 1.5",
})

# HOLD emitido por el LLM en second pass cuando no hay nivel válido
_HOLD_AFTER_C7 = json.dumps({
    "regime": "RANGE",
    "confluences": [],
    "action": "HOLD",
    "confidence_base": 0.5,
    "confidence_adjustment": 0.0,
    "confidence": 0.5,
    "stop_loss": None,
    "take_profit": None,
    "position_size_pct": 0.0,
    "expected_holding_min": 15,
    "reasoning": "[R:R INSUFICIENTE] No hay resistencia válida por encima del TP mínimo requerido.",
})


@pytest.mark.asyncio
async def test_two_pass_triggered_by_c7_when_rr_insufficient(session: AsyncSession):
    # GIVEN LLM responde BUY con R:R insuficiente en pass 1 y luego lo corrige en pass 2
    mock_resp_pass1 = LLMResponse(
        text=_BUY_BAD_RR, tokens_in=100, tokens_out=50,
        latency_ms=42, provider=LLMProvider.GEMINI_FLASH.value,
    )
    mock_resp_pass2 = LLMResponse(
        text=_BUY_CORRECTED_RR, tokens_in=100, tokens_out=50,
        latency_ms=42, provider=LLMProvider.GEMINI_FLASH.value,
    )
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(side_effect=[mock_resp_pass1, mock_resp_pass2])
    pm = _make_prompt_manager()
    pm.load_user_template = MagicMock(return_value="review {review_warnings_block} {review_c7_block}")

    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=pm, provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[], two_pass_enabled=True,
    )

    # WHEN deciding
    result = await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN se hicieron 2 llamadas al LLM (pass 1 + pass 2)
    assert llm.call.call_count == 2

    # AND la decisión final es el BUY corregido con TP=98000
    assert result.action == DecisorAction.BUY
    assert result.take_profit == pytest.approx(98000.0)

    # AND two_pass_triggered=True en la DB
    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert rows[0].output["two_pass_triggered"] is True


@pytest.mark.asyncio
async def test_c7_two_pass_degrades_to_hold_when_llm_still_fails(session: AsyncSession):
    # GIVEN LLM responde BUY con R:R insuficiente en ambos passes
    mock_resp = LLMResponse(
        text=_BUY_BAD_RR, tokens_in=100, tokens_out=50,
        latency_ms=42, provider=LLMProvider.GEMINI_FLASH.value,
    )
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=mock_resp)
    pm = _make_prompt_manager()
    pm.load_user_template = MagicMock(return_value="review {review_warnings_block} {review_c7_block}")

    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=pm, provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[], two_pass_enabled=True,
    )

    # WHEN deciding
    result = await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN la decisión final es HOLD (C7 bloquea ambos passes)
    assert result.action == DecisorAction.HOLD

    # AND rejected_reason contiene C7
    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert "C7" in (rows[0].rejected_reason or "")


@pytest.mark.asyncio
async def test_c7_two_pass_degrades_to_hold_when_llm_emits_hold(session: AsyncSession):
    # GIVEN LLM responde BUY con R:R insuficiente en pass 1 y luego HOLD en pass 2
    mock_resp_pass1 = LLMResponse(
        text=_BUY_BAD_RR, tokens_in=100, tokens_out=50,
        latency_ms=42, provider=LLMProvider.GEMINI_FLASH.value,
    )
    mock_resp_pass2 = LLMResponse(
        text=_HOLD_AFTER_C7, tokens_in=100, tokens_out=50,
        latency_ms=42, provider=LLMProvider.GEMINI_FLASH.value,
    )
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(side_effect=[mock_resp_pass1, mock_resp_pass2])
    pm = _make_prompt_manager()
    pm.load_user_template = MagicMock(return_value="review {review_warnings_block} {review_c7_block}")

    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=pm, provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[], two_pass_enabled=True,
    )

    # WHEN deciding
    result = await decisor.decide(**_DEFAULT_DECIDE_KWARGS)

    # THEN resultado final es HOLD (el LLM lo emitió correctamente en pass 2)
    assert result.action == DecisorAction.HOLD

    # AND two_pass_triggered=True en la DB
    rows = (await session.execute(select(Decision).where(Decision.agent == "decisor"))).scalars().all()
    assert rows[0].output["two_pass_triggered"] is True
    # No rejected_reason porque el LLM mismo emitió HOLD (no fue forzado por has_critical)
    assert rows[0].rejected_reason is None


def test_build_review_ctx_includes_c7_block_when_c7_present():
    # GIVEN warnings con C7 y original_ctx con datos del ciclo
    from risk.coherence_checker import CoherenceWarning
    from agents.decisor import _build_review_ctx
    from shared.schemas import DecisorOutput, DecisorAction, MarketRegime

    decision = DecisorOutput(
        action=DecisorAction.BUY, regime=MarketRegime.RANGE,
        confluences=["H", "C"], confidence_base=0.7,
        confidence_adjustment=0.0, confidence=0.7,
        stop_loss=93000.0, take_profit=96000.0,
        position_size_pct=0.05, expected_holding_min=45,
        reasoning="test",
    )
    warnings = [CoherenceWarning(
        rule_id="C7", severity="critical",
        message="R:R real=0.50 ≤ min_rr_ratio=1.0",
        evidence={
            "rr_real": 0.5, "min_rr_ratio": 1.0,
            "price": 95000.0, "stop_loss": 93000.0,
            "take_profit": 96000.0, "reward": 1000.0, "risk": 2000.0,
        },
    )]
    original_ctx = {
        "block_d_text": "  EMA50(1h): $93,500  EMA200(1h): $97,000",
        "atr_ref": 500.0,
        "sl_atr_multiplier": 0.4,
    }

    # WHEN
    ctx = _build_review_ctx(decision, warnings, original_ctx)

    # THEN el contexto incluye el bloque C7 con el TP mínimo calculado
    assert ctx["review_has_c7"] is True
    assert "review_c7_block" in ctx
    # tp_min del LLM = 95000 + 2000 × 1.0 = 97000
    assert "97,000" in ctx["review_c7_block"]
    assert "0.50" in ctx["review_c7_block"]
    assert "OPCIÓN A" in ctx["review_c7_block"]
    assert "OPCIÓN B" in ctx["review_c7_block"]
    # tp_min canónico = 95000 + (500 × 0.4) × 1.0 = 95200
    assert "95,200" in ctx["review_c7_block"]
    # Los niveles de precio del ciclo aparecen en el bloque C7
    assert "EMA50(1h)" in ctx["review_c7_block"]
    # El block_d también está disponible como variable de template
    assert ctx["review_block_d"] == "  EMA50(1h): $93,500  EMA200(1h): $97,000"


def test_build_review_ctx_no_c7_block_when_no_c7():
    # GIVEN warnings sin C7
    from risk.coherence_checker import CoherenceWarning
    from agents.decisor import _build_review_ctx
    from shared.schemas import DecisorOutput, DecisorAction, MarketRegime

    decision = DecisorOutput(
        action=DecisorAction.BUY, regime=MarketRegime.RANGE,
        confluences=["H"], confidence_base=0.7,
        confidence_adjustment=0.0, confidence=0.7,
        stop_loss=93000.0, take_profit=98000.0,
        position_size_pct=0.05, expected_holding_min=45,
        reasoning="test",
    )
    warnings = [CoherenceWarning(
        rule_id="C1", severity="warning",
        message="RSI no oversold",
        evidence={},
    )]
    original_ctx = {"block_d_text": "  EMA50(1h): $93,000"}

    # WHEN
    ctx = _build_review_ctx(decision, warnings, original_ctx)

    # THEN review_has_c7 es False y el bloque está vacío
    assert ctx["review_has_c7"] is False
    assert ctx["review_c7_block"] == ""
    assert ctx["review_block_d"] == "  EMA50(1h): $93,000"
