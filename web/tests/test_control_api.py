import pytest
from shared.config_store import ConfigStore


async def test_kill_switch_enable(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.post("/api/kill-switch", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["kill_switch"] is True


async def test_live_mode_wrong_confirmation_returns_400(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.post("/api/mode", json={"mode": "LIVE", "confirmation": "wrong"})
    assert r.status_code == 400


async def test_live_mode_correct_confirmation_ok(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.post("/api/mode", json={
        "mode": "LIVE", "confirmation": "CONFIRMO TRADING REAL"
    })
    assert r.status_code == 200
    assert r.json()["mode"] == "LIVE"
