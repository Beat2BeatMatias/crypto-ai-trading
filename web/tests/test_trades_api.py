import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from shared.db.models import Trade, Position


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


async def test_open_trade_includes_scenario_pnl(client, app_with_db):
    trade_id = uuid.uuid4()
    await _create_trade(
        app_with_db.state.session_factory,
        id=trade_id,
        status="open",
        entry_price=Decimal("80000.00"),
        quantity_btc=Decimal("0.001"),
        stop_loss=Decimal("79000.00"),
        take_profit=Decimal("82000.00"),
    )
    async with app_with_db.state.session_factory() as s:
        s.add(Position(
            id=uuid.uuid4(),
            trade_id=trade_id,
            symbol="BTC/USDT",
            quantity_btc=Decimal("0.001"),
            entry_price=Decimal("80000.00"),
            current_price=Decimal("80500.00"),
            unrealized_pnl=Decimal("0.5"),
            unrealized_pct=Decimal("0.625"),
            status="open",
            opened_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        ))
        await s.commit()

    r = await client.get("/api/trades?status=open")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["current_price"] == pytest.approx(80500.0)
    assert data[0]["unrealized_pnl_usdt"] == pytest.approx(0.5, rel=1e-4)
    assert data[0]["sl_pnl_usdt"] == pytest.approx(-1.0, rel=1e-4)
    assert data[0]["tp_pnl_usdt"] == pytest.approx(2.0, rel=1e-4)


async def test_list_trades_excludes_paper_when_live(client, app_with_db):
    from shared.config_store import ConfigStore, ConfigKey

    paper_ts = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    live_ts = datetime(2026, 5, 18, 4, 0, tzinfo=timezone.utc)
    await _create_trade(app_with_db.state.session_factory, ts_open=paper_ts)
    await _create_trade(app_with_db.state.session_factory, ts_open=live_ts)

    async with app_with_db.state.session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        await store.set(ConfigKey.MODE, "LIVE", changed_by="test")
        await store.set(ConfigKey.LIVE_SINCE_TS, "2026-05-18T03:46:01+00:00", changed_by="test")

    r = await client.get("/api/trades")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["ts_open"].startswith("2026-05-18")

    r_all = await client.get("/api/trades?include_paper=true")
    assert r_all.status_code == 200
    assert len(r_all.json()) == 2


async def test_trade_response_includes_futures_fields(client, app_with_db):
    await _create_trade(
        app_with_db.state.session_factory,
        side="SELL",
        position_side="SHORT",
        leverage=Decimal("1"),
        liquidation_price=Decimal("95000.00"),
        margin_mode="isolated",
    )

    r = await client.get("/api/trades")
    assert r.status_code == 200
    data = r.json()[0]
    assert data["position_side"] == "SHORT"
    assert data["leverage"] == pytest.approx(1.0)
    assert data["liquidation_price"] == pytest.approx(95000.0)
    assert data["margin_mode"] == "isolated"
