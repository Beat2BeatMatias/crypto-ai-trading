import uuid
from datetime import datetime, timezone
import pytest
from shared.db.models import Decision


async def _create_decision(session_factory, **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        ts=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        agent="decisor",
        model="gemini-2.5-flash",
        input={"price": 50000},
        output={"action": "BUY", "confidence": 0.8},
        executed=False,
    )
    defaults.update(kwargs)
    async with session_factory() as s:
        s.add(Decision(**defaults))
        await s.commit()


async def test_list_decisions_empty(client):
    r = await client.get("/api/decisions")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_decisions_returns_decision(client, app_with_db):
    await _create_decision(app_with_db.state.session_factory)
    r = await client.get("/api/decisions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["agent"] == "decisor"
    assert data[0]["executed"] is False


async def test_list_decisions_filter_by_executed(client, app_with_db):
    await _create_decision(app_with_db.state.session_factory, executed=True)
    await _create_decision(app_with_db.state.session_factory, executed=False)
    r = await client.get("/api/decisions?executed=true")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["executed"] is True


async def test_list_decisions_excludes_paper_when_live(client, app_with_db):
    from shared.config_store import ConfigStore, ConfigKey

    paper_ts = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    live_ts = datetime(2026, 5, 18, 4, 0, tzinfo=timezone.utc)
    await _create_decision(app_with_db.state.session_factory, ts=paper_ts)
    await _create_decision(app_with_db.state.session_factory, ts=live_ts)

    async with app_with_db.state.session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        await store.set(ConfigKey.MODE, "LIVE", changed_by="test")
        await store.set(ConfigKey.LIVE_SINCE_TS, "2026-05-18T03:46:01+00:00", changed_by="test")

    r = await client.get("/api/decisions")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["ts"].startswith("2026-05-18")

    r_all = await client.get("/api/decisions?include_paper=true")
    assert r_all.status_code == 200
    assert len(r_all.json()) == 2
