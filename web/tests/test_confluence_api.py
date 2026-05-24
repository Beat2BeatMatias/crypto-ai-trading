from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


async def test_list_confluence_candidates(client, app_with_db):
    from shared.db.models import ConfluenceCandidate

    factory = app_with_db.state.session_factory
    now = datetime.now(tz=timezone.utc)
    async with factory() as s:
        s.add(ConfluenceCandidate(
            id=uuid4(),
            pattern_tag="vol_div_range",
            title="Volume Divergence",
            definition_md="RANGE con volumen bajo",
            verify_spec={"all": [{"ctx": "volume_ratio", "lt": 0.8}]},
            occurrence_count=2,
            first_seen_at=now,
            last_seen_at=now,
            source_decision_ids=[str(uuid4())],
            status="open",
        ))
        await s.commit()

    resp = await client.get("/api/confluence/candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pattern_tag"] == "vol_div_range"
    assert body[0]["occurrence_count"] == 2


async def test_list_confluence_registry_active_only(client, app_with_db):
    from shared.db.models import ConfluenceRegistry

    factory = app_with_db.state.session_factory
    now = datetime.now(tz=timezone.utc)
    async with factory() as s:
        s.add(ConfluenceRegistry(
            code="I",
            slug="vol_div_range",
            title="Volume Divergence",
            definition_md="def",
            verify_spec={"all": [{"ctx": "volume_ratio", "lt": 0.8}]},
            active=True,
            created_at=now,
        ))
        s.add(ConfluenceRegistry(
            code="J",
            slug="inactive_pattern",
            title="Inactive",
            definition_md="def",
            verify_spec={"all": [{"ctx": "rsi_15m", "lt": 30}]},
            active=False,
            created_at=now,
            deactivated_at=now,
        ))
        await s.commit()

    resp = await client.get("/api/confluence/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["code"] == "I"

    resp_all = await client.get("/api/confluence/registry?active_only=false")
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 2


async def test_promote_candidate_creates_registry_entry(client, app_with_db):
    from shared.db.models import ConfluenceCandidate

    factory = app_with_db.state.session_factory
    now = datetime.now(tz=timezone.utc)
    cand_id = uuid4()
    async with factory() as s:
        s.add(ConfluenceCandidate(
            id=cand_id,
            pattern_tag="test_pattern",
            title="Test Pattern",
            definition_md="def operacional",
            verify_spec={"all": [{"ctx": "rsi_15m", "lt": 35}]},
            occurrence_count=3,
            first_seen_at=now,
            last_seen_at=now,
            source_decision_ids=[str(uuid4())],
            status="open",
        ))
        await s.commit()

    resp = await client.post(f"/api/confluence/candidates/{cand_id}/promote")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "I"
    assert body["title"] == "Test Pattern"

    reg = await client.get("/api/confluence/registry")
    assert reg.status_code == 200
    assert len(reg.json()) == 1


async def test_reject_candidate_updates_status(client, app_with_db):
    from shared.db.models import ConfluenceCandidate

    factory = app_with_db.state.session_factory
    now = datetime.now(tz=timezone.utc)
    cand_id = uuid4()
    async with factory() as s:
        s.add(ConfluenceCandidate(
            id=cand_id,
            pattern_tag="reject_me",
            title="Reject",
            definition_md="def",
            verify_spec={"all": [{"ctx": "rsi_15m", "lt": 35}]},
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            source_decision_ids=[str(uuid4())],
            status="open",
        ))
        await s.commit()

    resp = await client.post(
        f"/api/confluence/candidates/{cand_id}/reject",
        json={"reason": "patrón redundante"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["reject_reason"] == "patrón redundante"


async def test_deactivate_registry_entry(client, app_with_db):
    from shared.db.models import ConfluenceRegistry

    factory = app_with_db.state.session_factory
    now = datetime.now(tz=timezone.utc)
    async with factory() as s:
        s.add(ConfluenceRegistry(
            code="I",
            slug="active_one",
            title="Active",
            definition_md="def",
            verify_spec={"all": [{"ctx": "rsi_15m", "lt": 30}]},
            active=True,
            created_at=now,
        ))
        await s.commit()

    resp = await client.post("/api/confluence/registry/I/deactivate")
    assert resp.status_code == 200
    assert resp.json()["active"] is False

    active = await client.get("/api/confluence/registry?active_only=true")
    assert active.status_code == 200
    assert active.json() == []
