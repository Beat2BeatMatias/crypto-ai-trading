"""Tests for confluence registry service."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, Table, Column, String, Integer, Boolean, DateTime, Text, ForeignKey, select
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.confluence_registry_ops import EXTENDED_LETTERS

from agents.confluence_registry import (
    active_registry_codes,
    evaluate_verify_spec,
    promote_eligible_candidates,
    render_registry_block,
    upsert_candidate,
    verify_spec_testable,
)
from agents.postmortem_schemas import CandidatePayload, LessonNormalized
from shared.db.models import ConfluenceCandidate, ConfluenceRegistry

_sqlite_metadata = MetaData()

Table(
    "confluence_candidates", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("pattern_tag", String(64), nullable=False, unique=True),
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

Table(
    "confluence_registry", _sqlite_metadata,
    Column("code", String(1), primary_key=True),
    Column("slug", String(64), nullable=False, unique=True),
    Column("title", String(128), nullable=False),
    Column("definition_md", Text, nullable=False),
    Column("verify_spec", JSON, nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("promoted_from", String(36), ForeignKey("confluence_candidates.id")),
    Column("created_at", DateTime, nullable=False),
    Column("deactivated_at", DateTime),
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_evaluate_verify_spec_all_and_any():
    spec = {
        "all": [{"ctx": "rsi_15m", "lt": 40}],
        "any": [{"ctx": "hist_15m", "lt": 0}, {"ctx": "hist_1h", "lt": 0}],
    }
    assert evaluate_verify_spec(spec, {"rsi_15m": 30, "hist_15m": -1}) is True
    assert evaluate_verify_spec(spec, {"rsi_15m": 50, "hist_15m": -1}) is False


def test_verify_spec_testable_rejects_unknown_ctx():
    assert verify_spec_testable({"all": [{"ctx": "unknown_key", "exists": True}]}) is False
    assert verify_spec_testable({"all": [{"ctx": "rsi_15m", "lt": 35}]}) is True


def test_extended_letters_exclude_static_ij():
    assert "I" not in EXTENDED_LETTERS
    assert "J" not in EXTENDED_LETTERS
    assert "K" in EXTENDED_LETTERS


def test_active_registry_codes_excludes_static_collisions():
    from datetime import datetime, timezone
    from shared.db.models import ConfluenceRegistry

    now = datetime.now(tz=timezone.utc)
    entries = [
        ConfluenceRegistry(
            code="I",
            slug="bad_i",
            title="Should not appear",
            definition_md="x",
            verify_spec={},
            active=True,
            created_at=now,
        ),
        ConfluenceRegistry(
            code="K",
            slug="ok_k",
            title="OK",
            definition_md="y",
            verify_spec={},
            active=True,
            created_at=now,
        ),
    ]
    assert active_registry_codes(entries) == frozenset({"K"})


def test_render_registry_block_empty():
    assert "ninguna confluencia promovida" in render_registry_block([])


@pytest.mark.asyncio
async def test_upsert_candidate_increments_occurrence(session: AsyncSession):
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    decision_id = uuid.uuid4()
    normalized = LessonNormalized(
        route="candidate",
        pattern_tag="macd_hist_decay_range",
        confidence=0.75,
        candidate=CandidatePayload(
            title="MACD Hist Decay Range",
            definition_md="RANGE con hist negativo",
            verify_spec={"all": [{"ctx": "hist_15m", "lt": 0}]},
            proposed_code_letter="I",
        ),
        block_k_line="[candidate] test",
        dedupe_key="candidate:macd_hist_decay_range",
    )
    row = await upsert_candidate(session, normalized=normalized, decision_id=decision_id, now=now)
    await session.commit()
    assert row is not None
    assert row.occurrence_count == 1

    row2 = await upsert_candidate(
        session,
        normalized=normalized,
        decision_id=uuid.uuid4(),
        now=now + timedelta(hours=1),
    )
    await session.commit()
    assert row2.occurrence_count == 2


@pytest.mark.asyncio
async def test_promote_eligible_candidates_assigns_first_extended_letter(session: AsyncSession):
    now = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    cand_id = uuid.uuid4()
    session.add(ConfluenceCandidate(
        id=cand_id,
        pattern_tag="vol_div_range",
        title="Volume Divergence Range",
        definition_md="RANGE + vol bajo",
        verify_spec={"all": [{"ctx": "volume_ratio", "lt": 0.8}]},
        occurrence_count=3,
        first_seen_at=now - timedelta(days=2),
        last_seen_at=now,
        source_decision_ids=[str(uuid.uuid4())],
        status="open",
    ))
    await session.commit()

    promoted = await promote_eligible_candidates(
        session,
        min_occurrences=3,
        window_days=7,
        max_active=5,
        now=now,
    )
    await session.commit()

    assert len(promoted) == 1
    assert promoted[0]["code"] == "K"

    registry = (await session.execute(select(ConfluenceRegistry))).scalars().all()
    assert len(registry) == 1
    assert registry[0].code == "K"
    assert active_registry_codes(registry) == frozenset({"K"})


@pytest.mark.asyncio
async def test_promote_skips_when_max_active_reached(session: AsyncSession):
    now = datetime(2026, 5, 24, tzinfo=timezone.utc)
    session.add(ConfluenceRegistry(
        code="K",
        slug="existing",
        title="Existing",
        definition_md="def",
        verify_spec={"all": [{"ctx": "rsi_15m", "lt": 30}]},
        active=True,
        created_at=now,
    ))
    session.add(ConfluenceCandidate(
        id=uuid.uuid4(),
        pattern_tag="new_pattern",
        title="New",
        definition_md="def",
        verify_spec={"all": [{"ctx": "rsi_15m", "lt": 30}]},
        occurrence_count=5,
        first_seen_at=now,
        last_seen_at=now,
        source_decision_ids=[str(uuid.uuid4())],
        status="open",
    ))
    await session.commit()

    promoted = await promote_eligible_candidates(
        session,
        min_occurrences=3,
        max_active=1,
        now=now,
    )
    assert promoted == []
