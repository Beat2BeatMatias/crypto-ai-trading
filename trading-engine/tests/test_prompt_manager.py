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
    assert "{playbook}" in prompt


def test_render_user_prompt_with_strict_false_leaves_unknown_placeholders():
    # GIVEN a PromptManager without a session
    manager = PromptManager(None)

    # WHEN rendering with only a subset of values and strict=False
    values = {
        "timestamp_utc": "2025-01-01 12:00:00",
        "price": 95000.0,
        "pct_1h": 0.5,
        "pct_4h": 1.2,
        "pct_24h": -0.3,
        "rsi_5m": 45.0,
        "bb_pct_5m": 60.0,
        "rsi_15m": 50.0,
        "macd_15m": 10.0,
        "sig_15m": 8.0,
        "hist_15m": 2.0,
        "rsi_1h": 55.0,
        "macd_1h": 5.0,
        "sig_1h": 4.0,
        "ema20_1h": 94000.0,
        "ema50_1h": 93000.0,
        "ema200_1h": 90000.0,
        "rsi_4h": 60.0,
        "ema20_4h": 93500.0,
        "ema50_4h": 92000.0,
        "atr_1h": 500.0,
        "atr_pct_1h": 0.53,
        "spread": 1.5,
        "spread_pct": 0.0016,
        "imbalance": 0.3,
        "imbalance_label": "BID_HEAVY",
        "bid_wall_price": 94500.0,
        "bid_wall_size": 5.0,
        "ask_wall_price": 95500.0,
        "ask_wall_size": 3.2,
        "open_positions_count": 0,
        "max_simultaneous_trades": 2,
        "positions_block": "Sin posiciones abiertas.",
        "usdt_available": 1000.0,
        "btc_held": 0.0,
        "pnl_today_usd": 0.0,
        "pnl_today_pct": 0.0,
        "daily_stop_pct": "2.0",
        # intentionally omit last_decisions_block
    }

    # THEN the rendered prompt contains filled values and keeps missing placeholders
    rendered = manager.render_user_prompt("decisor", values, strict=False)
    assert "95,000.00" in rendered
    assert "{last_decisions_block}" in rendered
