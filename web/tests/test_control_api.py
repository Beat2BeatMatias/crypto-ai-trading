import pytest
from shared.config_store import ConfigStore, ConfigKey


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
    async with app_with_db.state.session_factory() as s:
        store = ConfigStore(s)
        live_since = await store.get(ConfigKey.LIVE_SINCE_TS)
    assert live_since.strip() != ""


async def test_circuit_breaker_reset_ok(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        await store.set(ConfigKey.ENGINE_PAUSED, "true", changed_by="test")
        await store.set(ConfigKey.ENGINE_PAUSE_REASON, "daily_stop", changed_by="test")

    r = await client.post("/api/circuit-breaker/reset")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "circuit_breaker": "reset"}

    async with app_with_db.state.session_factory() as s:
        store = ConfigStore(s)
        paused = await store.get_typed(ConfigKey.ENGINE_PAUSED)
        reason = await store.get(ConfigKey.ENGINE_PAUSE_REASON)
    assert paused is False
    assert reason == ""


async def test_circuit_breaker_reset_not_seeded_returns_404(client, app_with_db):
    r = await client.post("/api/circuit-breaker/reset")
    assert r.status_code == 404
