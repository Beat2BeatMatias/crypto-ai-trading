async def test_ping(client):
    r = await client.get("/api/ping")
    assert r.status_code == 200
    assert r.json() == {"pong": True}

async def test_health_returns_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
