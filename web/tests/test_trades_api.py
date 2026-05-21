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


async def test_trade_response_includes_bracket_order_ids(client, app_with_db):
    # GIVEN un trade con order_id_sl y order_id_tp seteados (OCO exitosa)
    await _create_trade(
        app_with_db.state.session_factory,
        order_id_sl="SL-12345",
        order_id_tp="TP-67890",
    )

    # WHEN pedimos la lista de trades
    r = await client.get("/api/trades")

    # THEN la respuesta incluye los IDs de los brackets
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["order_id_sl"] == "SL-12345"
    assert data[0]["order_id_tp"] == "TP-67890"


async def test_trade_response_bracket_ids_null_when_not_placed(client, app_with_db):
    # GIVEN un trade sin brackets (OCO falló, guardian de software lo cubre)
    await _create_trade(app_with_db.state.session_factory)

    # WHEN pedimos la lista de trades
    r = await client.get("/api/trades")

    # THEN los campos bracket son null (no ausentes)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert "order_id_sl" in data[0]
    assert "order_id_tp" in data[0]
    assert data[0]["order_id_sl"] is None
    assert data[0]["order_id_tp"] is None
