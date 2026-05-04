import pytest
from shared.config_store import ConfigStore


async def test_list_config_after_seed(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.get("/api/config")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert "max_position_pct" in keys
    assert "kill_switch" in keys


async def test_put_config_updates_value(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.put("/api/config/max_position_pct", json={"value": "0.05"})
    assert r.status_code == 200
    assert r.json()["value"] == "0.05"
    # Verify persisted
    r2 = await client.get("/api/config")
    entry = next(e for e in r2.json() if e["key"] == "max_position_pct")
    assert entry["value"] == "0.05"


async def test_put_config_unknown_key_returns_400(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.put("/api/config/nonexistent_key", json={"value": "x"})
    assert r.status_code == 400
