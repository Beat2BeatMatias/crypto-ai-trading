from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from shared.db.models import BalanceSnapshot
from shared.config_store import ConfigKey
from shared.config_store import ConfigStore


async def test_balance_includes_margin_fields(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        s.add(BalanceSnapshot(
            ts=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            usdt=Decimal("1000"),
            btc=Decimal("0"),
            margin_balance=Decimal("1200.50"),
            available_margin=Decimal("950.25"),
        ))
        await s.commit()

    r = await client.get("/api/balance")
    assert r.status_code == 200
    data = r.json()
    assert data["margin_balance"] == pytest.approx(1200.50)
    assert data["available_margin"] == pytest.approx(950.25)


async def test_balance_margin_null_when_not_set(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        s.add(BalanceSnapshot(
            ts=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            usdt=Decimal("500"),
            btc=Decimal("0.01"),
        ))
        await s.commit()

    r = await client.get("/api/balance")
    assert r.status_code == 200
    data = r.json()
    assert data["margin_balance"] is None
    assert data["available_margin"] is None
    assert data["futures"] is None


@pytest.mark.asyncio
async def test_balance_futures_live_when_config_futures(client, app_with_db):
    async with app_with_db.state.session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        await store.set(ConfigKey.TRADING_PRODUCT, "futures", changed_by="test")
        await s.commit()

    mock_live = {
        "available_margin": 842.5,
        "margin_balance": 900.0,
        "margin_locked": 57.5,
    }
    with patch(
        "binance_futures_balance.fetch_futures_margin_balance",
        new=AsyncMock(return_value=mock_live),
    ):
        r = await client.get("/api/balance")

    assert r.status_code == 200
    data = r.json()
    assert data["futures"] is not None
    assert data["futures"]["source"] == "live"
    assert data["futures"]["available_margin"] == pytest.approx(842.5)
    assert data["futures"]["margin_balance"] == pytest.approx(900.0)
    assert data["futures"]["margin_locked"] == pytest.approx(57.5)
    assert data["available_margin"] == pytest.approx(842.5)
    assert data["usdt"] == pytest.approx(842.5)
    assert data["btc_exchange"] == 0.0
    assert data["btc_locked"] == 0.0
