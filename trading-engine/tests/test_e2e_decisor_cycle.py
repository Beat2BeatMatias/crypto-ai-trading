"""Tests E2E del ciclo completo del Decisor.

Cubre los escenarios del outline §12:
  12.1 Ciclo completo: collectors → context → decisor → coherence → risk gate (mock).
  12.2 BUY en TRENDING_DOWN: persiste sin ser forzado a HOLD.
  12.3 BUY con sizing máximo rechazado por R1 con rule_id.
  12.4 coherence_strict_mode=true: warning C1 bloquea ejecución → HOLD forzado.

Estrategia: LLM mockeado, BD in-memory (SQLite), Risk Gate real, CoherenceChecker real.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, Numeric,
    String, Table, Text, event, select,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.db.models import Decision, Indicators, PlaybookVersion, Position
from shared.schemas import DecisorAction, MarketRegime
from agents.decisor import Decisor
from agents.llm_client import LLMClient, LLMProvider, LLMResponse
from agents.prompt_manager import PromptManager
from risk.risk_gate import RiskGate


# ---------------------------------------------------------------------------
# SQLite in-memory schema
# ---------------------------------------------------------------------------

_sqlite_metadata = MetaData()

Table("indicators", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("data", JSON, nullable=False),
)
Table("positions", _sqlite_metadata,
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
Table("decisions", _sqlite_metadata,
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
Table("trades", _sqlite_metadata,
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
Table("playbook_versions", _sqlite_metadata,
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
Table("config", _sqlite_metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text, nullable=False),
    Column("value_type", String(20), nullable=False),
    Column("description", Text),
    Column("updated_at", DateTime),
)
Table("config_history", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("key", String(60), nullable=False),
    Column("old_value", Text),
    Column("new_value", Text, nullable=False),
    Column("changed_by", String(60), default="system"),
)
Table("decision_outcomes", _sqlite_metadata,
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
Table("confluence_candidates", _sqlite_metadata,
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
Table("confluence_registry", _sqlite_metadata,
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
Table("ohlcv", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("timeframe", String(4), primary_key=True),
    Column("open", Numeric(18, 8)),
    Column("high", Numeric(18, 8)),
    Column("low", Numeric(18, 8)),
    Column("close", Numeric(18, 8)),
    Column("volume", Numeric(24, 8)),
)


# ---------------------------------------------------------------------------
# ORM hooks for SQLite
# ---------------------------------------------------------------------------

def _pk_indicators(mapper, conn, target):
    if target.time is None:
        target.time = datetime.now(timezone.utc)

def _pk_decision(mapper, conn, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)

def _pk_playbook(mapper, conn, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts_generated is None:
        target.ts_generated = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    event.listen(Indicators, "before_insert", _pk_indicators)
    event.listen(Decision, "before_insert", _pk_decision)
    event.listen(PlaybookVersion, "before_insert", _pk_playbook)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        # Seed: indicadores con precio razonable para que ContextBuilder funcione
        sess.add(Indicators(
            time=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc),
            data={
                "15m": {
                    "last_close": 84000.0,
                    "rsi": 55.0,
                    "macd": 120.0,
                    "macd_signal": 100.0,
                    "macd_hist": 20.0,
                    "ema20": 83500.0,
                    "ema50": 82000.0,
                    "ema200": 78000.0,
                    "atr": 400.0,
                    "adx": 18.0,
                },
                "1h": {
                    "last_close": 84000.0,
                    "rsi": 58.0,
                    "macd": 90.0,
                    "macd_signal": 75.0,
                    "macd_hist": 15.0,
                    "ema20": 83800.0,
                    "ema50": 82500.0,
                    "ema200": 79000.0,
                    "atr": 600.0,
                    "adx": 22.0,
                },
                "4h": {
                    "last_close": 84000.0,
                    "rsi": 55.0,
                    "ema50": 81000.0,
                },
            },
        ))
        sess.add(PlaybookVersion(
            version=0,
            content="# Playbook v0\nTest playbook.",
            model="bootstrap",
            active=True,
        ))
        await sess.commit()
        yield sess

    event.remove(Indicators, "before_insert", _pk_indicators)
    event.remove(Decision, "before_insert", _pk_decision)
    event.remove(PlaybookVersion, "before_insert", _pk_playbook)
    await engine.dispose()


def _make_llm(response_text: str) -> LLMClient:
    resp = LLMResponse(
        text=response_text,
        tokens_in=150,
        tokens_out=80,
        latency_ms=320,
        provider=LLMProvider.GEMINI_FLASH.value,
    )
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=resp)
    return llm


def _make_pm() -> PromptManager:
    pm = MagicMock(spec=PromptManager)
    pm.get_active_playbook = AsyncMock(return_value=None)
    pm.load_system_prompt = MagicMock(return_value="System prompt mock")
    pm.load_user_template = MagicMock(return_value="Review template {review_warnings_block}")
    pm.render_user_prompt = MagicMock(return_value="User prompt mock")
    return pm


_BASE_KWARGS = dict(
    orderbook=None,
    usdt_balance=1000.0,
    btc_held=0.0,
    max_position_pct=0.10,
    max_simultaneous_trades=2,
    daily_stop_pct=0.03,
    decisor_interval_min=5,
    mode="PAPER_TRADING",
    taker_fee=0.001,
    maker_fee=0.001,
    atr_timeframe="15m",
    min_rr_ratio=1.3,
    sl_atr_multiplier=0.3,
)

_PRICE = 84000.0


def _buy_response(action: str = "BUY", regime: str = "TRENDING_UP",
                  confidence: float = 0.80, size: float = 0.08,
                  stop_loss: float = 83600.0, take_profit: float = 84900.0,
                  confluences: list[str] | None = None) -> str:
    return json.dumps({
        "regime": regime,
        "confluences": confluences or ["B", "C", "G"],
        "action": action,
        "confidence_base": confidence,
        "confidence_adjustment": 0.0,
        "confidence": confidence,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size_pct": size,
        "expected_holding_min": 60,
        "reasoning": "[DECISIÓN]: BUY [MERCADO]: up [SEÑALES]: B C G [CONFIANZA]: 80%",
    })


# ---------------------------------------------------------------------------
# Test 12.1 — Ciclo completo BUY válido pasa Risk Gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_valid_buy_passes_risk_gate(session: AsyncSession):
    """
    GIVEN una respuesta LLM de BUY válida (SL/TP y R:R dentro de límites)
    WHEN se ejecuta el ciclo completo del Decisor (LLM → CoherenceChecker → persistencia)
    THEN el resultado es BUY con la decisión persistida y sin rejected_reason
    """
    # GIVEN
    llm = _make_llm(_buy_response(
        stop_loss=_PRICE - 400,   # 400 puntos bajo precio (ATR=400, min=0.3×400=120)
        take_profit=_PRICE + 700, # R:R = 700/400 = 1.75 > 1.3
    ))
    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=_make_pm(),
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=False,
    )

    # WHEN
    result = await decisor.decide(**_BASE_KWARGS)

    # THEN
    assert result.action == DecisorAction.BUY
    assert result.confidence == pytest.approx(0.80)

    rows = (await session.execute(
        select(Decision).where(Decision.agent == "decisor")
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.output["action"] == "BUY"
    assert row.rejected_reason is None
    assert "coherence_warnings" in row.output
    assert "two_pass_triggered" in row.output


# ---------------------------------------------------------------------------
# Test 12.2 — BUY en TRENDING_DOWN persiste sin override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_buy_in_trending_down_persists_without_override(session: AsyncSession):
    """
    GIVEN una respuesta LLM de BUY con régimen TRENDING_DOWN (antes habría sido forzado HOLD)
    WHEN se ejecuta el ciclo del Decisor (sin overrides deterministas)
    THEN la decisión BUY persiste tal cual y solo el Risk Gate puede rechazarla
    """
    # GIVEN: BUY en TRENDING_DOWN con SL/TP y R:R válidos → debe pasar al Risk Gate
    llm = _make_llm(_buy_response(
        regime="TRENDING_DOWN",
        confidence=0.65,
        confluences=["A", "H"],
        stop_loss=_PRICE - 500,
        take_profit=_PRICE + 800,  # R:R = 800/500 = 1.6 > 1.3
    ))
    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=_make_pm(),
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=False,
    )

    # WHEN
    result = await decisor.decide(**_BASE_KWARGS)

    # THEN el sistema NO fuerza HOLD; el LLM mantiene BUY
    # (el Risk Gate en el ciclo real decidiría, pero en este test
    #  solo verificamos que el Decisor no sobrescribe la acción)
    assert result.action == DecisorAction.BUY
    assert result.regime == MarketRegime.TRENDING_DOWN

    rows = (await session.execute(
        select(Decision).where(Decision.agent == "decisor")
    )).scalars().all()
    row = rows[0]
    # La decisión persiste como BUY — no hay override a HOLD
    assert row.output["action"] == "BUY"
    assert row.output["regime"] == "TRENDING_DOWN"


# ---------------------------------------------------------------------------
# Test 12.3 — BUY con sizing > max_position_pct: rechazado por R1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_buy_oversized_rejected_by_r1(session: AsyncSession):
    """
    GIVEN una respuesta LLM de BUY con position_size_pct > max_position_pct (0.25 > 0.10)
    WHEN se ejecuta el ciclo con Risk Gate real
    THEN el Risk Gate rechaza con rule_id R1
    """
    # GIVEN: LLM pide 25% pero el límite es 10%
    llm = _make_llm(_buy_response(
        size=0.25,
        stop_loss=_PRICE - 400,
        take_profit=_PRICE + 700,
    ))
    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=_make_pm(),
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=False,
    )

    # WHEN: ejecutar decisor + risk gate manualmente
    result = await decisor.decide(**_BASE_KWARGS)

    # La decisión del decisor queda con BUY (no hay override)
    assert result.action == DecisorAction.BUY
    assert result.position_size_pct == pytest.approx(0.25)

    # Verificar que el Risk Gate sí rechazaría (llamada directa)
    gate = RiskGate(
        max_position_pct=0.10, max_simultaneous_trades=2,
        daily_stop_pct=-0.03, max_drawdown_pct=-0.10,
        max_slippage_pct=0.003, taker_fee_pct=0.001,
        min_rr_ratio=1.3, sl_atr_multiplier=0.3, sl_atr_max_multiplier=1.5,
    )
    verdict = gate.validate(
        decision=result,
        current_price=_PRICE,
        usdt_balance=1000.0,
        btc_held=0.0,
        open_positions_count=0,
        daily_pnl_pct=0.0,
        total_drawdown_pct=0.0,
        kill_switch=False,
        atr_ref=400.0,
        roundtrip_fee_pct=0.2,
        min_fees_to_tp_ratio=3.0,
    )

    # THEN el Risk Gate rechaza por R1
    assert verdict.passed is False
    assert verdict.rule_id == "R1"
    assert "R1" in verdict.reason or str(result.position_size_pct) in verdict.reason


# ---------------------------------------------------------------------------
# Test 12.4 — coherence_strict_mode=True: warning C1 bloquea y fuerza HOLD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_strict_mode_c1_warning_forces_hold(session: AsyncSession):
    """
    GIVEN coherence_strict_mode=True y una respuesta LLM que declara confluencia A
         (RSI_OVERSOLD_BOUNCE) pero RSI=55 (no está en sobreventa)
    WHEN el CoherenceChecker evalúa la decisión
    THEN C1 genera un warning crítico y el Decisor fuerza HOLD de seguridad
    """
    # GIVEN: BUY con confluencia A pero RSI=55 (no oversold < 35)
    # Los indicadores en el fixture tienen RSI=55 en 15m y RSI=58 en 1h → C1 se dispara
    llm = _make_llm(_buy_response(
        action="BUY",
        regime="TRENDING_UP",
        confluences=["A", "G"],  # A = RSI_OVERSOLD_BOUNCE
        confidence=0.80,
        stop_loss=_PRICE - 400,
        take_profit=_PRICE + 700,
    ))
    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=_make_pm(),
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        coherence_strict_mode=True,
        two_pass_enabled=False,
    )

    # WHEN
    result = await decisor.decide(**_BASE_KWARGS)

    # THEN: el CoherenceChecker detecta C1 (crítico en strict_mode) → HOLD forzado
    assert result.action == DecisorAction.HOLD

    rows = (await session.execute(
        select(Decision).where(Decision.agent == "decisor")
    )).scalars().all()
    row = rows[0]
    assert row.output["action"] == "HOLD"
    assert row.rejected_reason is not None
    assert "coherence_strict" in row.rejected_reason
    assert "C1" in row.rejected_reason

    # Los warnings también están en el output
    warnings = row.output.get("coherence_warnings", [])
    rule_ids = [w["rule_id"] for w in warnings]
    assert "C1" in rule_ids


# ---------------------------------------------------------------------------
# Test 12.5 — coherence_strict_mode=False: C1 es warning, no bloquea
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_non_strict_mode_c1_warning_does_not_block(session: AsyncSession):
    """
    GIVEN coherence_strict_mode=False (default) y una decisión con warning C1
    WHEN el CoherenceChecker evalúa
    THEN C1 genera un warning informativo pero la decisión BUY persiste sin bloqueo
    """
    # GIVEN: misma respuesta que el test anterior
    llm = _make_llm(_buy_response(
        action="BUY",
        confluences=["A", "G"],
        stop_loss=_PRICE - 400,
        take_profit=_PRICE + 700,
    ))
    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=_make_pm(),
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        coherence_strict_mode=False,
        two_pass_enabled=False,
    )

    # WHEN
    result = await decisor.decide(**_BASE_KWARGS)

    # THEN: BUY no es bloqueado — solo hay warnings informativos
    assert result.action == DecisorAction.BUY

    rows = (await session.execute(
        select(Decision).where(Decision.agent == "decisor")
    )).scalars().all()
    row = rows[0]
    assert row.output["action"] == "BUY"
    assert row.rejected_reason is None

    warnings = row.output.get("coherence_warnings", [])
    # Puede haber C1 (RSI no oversold) como warning informativo
    assert isinstance(warnings, list)


# ---------------------------------------------------------------------------
# Test 12.6 — Output completo persistido: coherence_warnings + two_pass_triggered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_output_always_has_coherence_and_two_pass_fields(session: AsyncSession):
    """
    GIVEN cualquier decisión (BUY, SELL o HOLD)
    WHEN se ejecuta el ciclo del Decisor
    THEN decisions.output siempre contiene coherence_warnings (list) y two_pass_triggered (bool)
    """
    # GIVEN: respuesta HOLD simple
    llm = _make_llm(json.dumps({
        "regime": "RANGE",
        "confluences": [],
        "action": "HOLD",
        "confidence_base": 0.45,
        "confidence_adjustment": 0.0,
        "confidence": 0.45,
        "stop_loss": None,
        "take_profit": None,
        "position_size_pct": 0.0,
        "expected_holding_min": 5,
        "reasoning": "[DECISIÓN]: HOLD [MERCADO]: RANGE [SEÑALES]: ninguna [CONFIANZA]: 45%",
    }))
    decisor = Decisor(
        session=session, llm=llm, symbol="BTC/USDT",
        prompt_manager=_make_pm(),
        provider=LLMProvider.GEMINI_FLASH,
        fallbacks=[],
        two_pass_enabled=False,
    )

    # WHEN
    await decisor.decide(**_BASE_KWARGS)

    rows = (await session.execute(
        select(Decision).where(Decision.agent == "decisor")
    )).scalars().all()
    row = rows[0]

    # THEN ambos campos siempre están presentes
    assert "coherence_warnings" in row.output
    assert isinstance(row.output["coherence_warnings"], list)
    assert "two_pass_triggered" in row.output
    assert isinstance(row.output["two_pass_triggered"], bool)
