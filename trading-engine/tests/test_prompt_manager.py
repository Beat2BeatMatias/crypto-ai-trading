"""Tests for PromptManager — prompt loading, rendering, and playbook persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime,
    Numeric, Text, select, func, event,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import Session

from shared.db.models import PlaybookVersion
from agents.prompt_manager import PromptManager

# SQLite-compatible DDL for playbook_versions — avoids JSONB and PostgreSQL specifics
_sqlite_metadata = MetaData()

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


def _set_uuid_before_insert(mapper, connection, target):
    """Generate UUID and timestamp at Python level — needed for SQLite compatibility."""
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts_generated is None:
        target.ts_generated = datetime.now(timezone.utc)


@pytest.fixture
async def session():
    """Provide a fresh in-memory SQLite session for each test."""
    # Register the before_insert listener so SQLite gets a Python-generated UUID
    event.listen(PlaybookVersion, "before_insert", _set_uuid_before_insert)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess

    # Clean up listener after each test to avoid accumulation
    event.remove(PlaybookVersion, "before_insert", _set_uuid_before_insert)
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_playbook_v0_inserts_active_v0(session: AsyncSession):
    # GIVEN a fresh database with no playbook entries
    manager = PromptManager(session)

    # WHEN seeding v0
    await manager.seed_playbook_v0()

    # THEN a single active playbook at version 0 exists
    playbook = await manager.get_active_playbook()
    assert playbook is not None
    assert playbook.version == 0
    assert playbook.active is True
    assert playbook.model == "bootstrap"
    assert "Playbook v0" in playbook.content


@pytest.mark.asyncio
async def test_seed_playbook_v0_is_idempotent(session: AsyncSession):
    # GIVEN a database where v0 was already seeded
    manager = PromptManager(session)
    await manager.seed_playbook_v0()

    # WHEN seeding again
    await manager.seed_playbook_v0()

    # THEN still only one playbook exists
    count = (await session.execute(
        select(func.count()).select_from(PlaybookVersion)
    )).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_save_playbook_marks_previous_inactive(session: AsyncSession):
    # GIVEN a seeded v0 playbook that is currently active
    manager = PromptManager(session)
    await manager.seed_playbook_v0()

    # WHEN saving a new playbook
    new_pb = await manager.save_playbook(
        content="# Playbook v1\n## Test",
        model="gemini-2.5-flash",
        trades_analyzed=10,
        win_rate=0.6,
        pnl_summary={"total": 100.0},
    )

    # THEN the new playbook is active and v0 is not
    assert new_pb.active is True
    assert new_pb.version == 1

    active = await manager.get_active_playbook()
    assert active is not None
    assert active.version == 1

    all_versions = (await session.execute(
        select(PlaybookVersion).order_by(PlaybookVersion.version)
    )).scalars().all()
    assert len(all_versions) == 2
    assert all_versions[0].active is False
    assert all_versions[1].active is True


def test_load_system_prompt_loads_decisor_file():
    # GIVEN a PromptManager without a session (no DB needed for file loading)
    manager = PromptManager(None)

    # WHEN loading the decisor system prompt
    prompt = manager.load_system_prompt("decisor")

    # THEN the prompt contains key content markers
    assert "BTC/USDT" in prompt
    assert "REGLAS ABSOLUTAS" in prompt
    assert "OUTPUT" in prompt
    # {playbook} se trasladó al user prompt (Bloque J), no al system prompt
    assert "ETIQUETAS INTERPRETATIVAS" in prompt


def test_render_user_prompt_with_strict_false_leaves_unknown_placeholders():
    # GIVEN a PromptManager without a session
    manager = PromptManager(None)

    # WHEN rendering with a subset of the new block-based keys and strict=False
    # El user prompt ahora usa bloques (A-J); aquí sólo proveemos los keys de
    # Bloque B + H + I para verificar que los valores conocidos se renderizan
    # y los desconocidos se dejan como "{key}".
    values = {
        "timestamp_utc": "2025-01-01 12:00:00",
        "mode": "PAPER_TRADING",
        "price": 95000.0,
        "pct_1h": 0.5,
        "pct_4h": 1.2,
        "pct_24h": -0.3,
        "atr_ref_tf": "15m",
        "atr_ref": 500.0,
        "atr_ref_pct": 0.53,
        "atr_avg_7d": 480.0,
        "atr_expanding": "False",
        "volatility_label": "NORMAL",
        "atr_ref_min": 350.0,
        "atr_ref_max": 750.0,
        "block_a_profile": "HIBRIDO",
        "capital_total": 1000.0,
        "usdt_available": 1000.0,
        "btc_held": 0.0,
        "pnl_today_usd": 0.0,
        "pnl_today_pct": 0.0,
        "daily_stop_pct": 2.0,
        "unrealized_pnl_usd": 0.0,
        "current_drawdown_pct": 0.0,
        "trades_today_count": 0,
        "wins_today": 0,
        "losses_today": 0,
        "open_positions_count": 0,
        "max_simultaneous_trades": 2,
        "positions_block": "  Ninguna",
        "max_position_pct": 0.1,
        "min_position_size": 0.0001,
        "min_rr_ratio": 1.3,
        "sl_atr_multiplier": 0.5,
        "sl_atr_max_multiplier": 1.5,
        "roundtrip_fee_pct": 0.0,
        "min_fees_to_tp_ratio": 3.0,
        "min_confluences_buy": 2,
        "cooldown_after_sell_min": 5,
        # Omitir intencionalmente los bloques C/D/E/F/G/J para verificar strict=False
    }

    # THEN los valores presentes se renderizan y los bloques ausentes se dejan como {key}
    rendered = manager.render_user_prompt("decisor", values, strict=False)
    assert "95,000.00" in rendered
    # Los bloques no provistos quedan como placeholders sin resolver
    assert "{last_decisions_block}" in rendered
    assert "{block_c_text}" in rendered
    assert "{playbook}" in rendered


def test_decisor_system_prompt_risk_sizing_and_no_dead_confidence_formula():
    import pathlib
    prompt_text = (
        pathlib.Path(__file__).parent.parent / "agents" / "prompts" / "decisor_system.txt"
    ).read_text(encoding="utf-8")
    assert "riesgo fijo por trade" in prompt_text or "risk_per_trade_pct" in prompt_text
    assert "CÁLCULO DE CONFIDENCE (7 pasos" not in prompt_text
    assert "base y total las calcula el servidor" in prompt_text
    assert "confidence ≥ 0.85 + ≥3 confluencias" not in prompt_text
