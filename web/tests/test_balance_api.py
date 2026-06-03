from datetime import datetime, timezone
from decimal import Decimal
import pytest
from shared.db.models import BalanceSnapshot


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
