import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from shared.db.models import Trade


async def _create_trade(session_factory, **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        ts_open=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        side="BUY",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("50000.00"),
        status="open",
    )
    defaults.update(kwargs)
    async with session_factory() as s:
        s.add(Trade(**defaults))
        await s.commit()


async def test_list_trades_empty(client):
    r = await client.get("/api/trades")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_trades_returns_trade(client, app_with_db):
    await _create_trade(app_with_db.state.session_factory)
    r = await client.get("/api/trades")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["side"] == "BUY"
    assert data[0]["status"] == "open"


async def test_list_trades_filter_by_status(client, app_with_db):
    await _create_trade(app_with_db.state.session_factory, status="open")
    await _create_trade(app_with_db.state.session_factory, status="closed")
    r = await client.get("/api/trades?status=open")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["status"] == "open"
