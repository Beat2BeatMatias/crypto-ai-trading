import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shared.db.models import Trade


async def _create_trade(session_factory, **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        ts_open=datetime.now(tz=timezone.utc),
        side="BUY",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("50000.00"),
        status="open",
    )
    defaults.update(kwargs)
    async with session_factory() as s:
        s.add(Trade(**defaults))
        await s.commit()


async def test_daily_stats_counts_open_trades_regardless_of_open_date(client, app_with_db):
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    await _create_trade(
        app_with_db.state.session_factory,
        ts_open=yesterday + timedelta(hours=10),
        status="open",
    )
    await _create_trade(
        app_with_db.state.session_factory,
        ts_open=today + timedelta(hours=1),
        status="open",
    )

    r = await client.get("/api/stats/daily")
    assert r.status_code == 200
    data = r.json()
    assert data["trades_open"] == 2
    assert data["trades_closed"] == 0


async def test_daily_stats_counts_trades_closed_today_even_if_opened_yesterday(client, app_with_db):
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    await _create_trade(
        app_with_db.state.session_factory,
        ts_open=yesterday + timedelta(hours=20),
        ts_close=today + timedelta(hours=2),
        status="closed",
        exit_price=Decimal("51000.00"),
        pnl_usdt=Decimal("1.00"),
        pnl_pct=Decimal("2.0"),
        fees_usdt=Decimal("0.05"),
    )
    await _create_trade(
        app_with_db.state.session_factory,
        ts_open=today + timedelta(hours=3),
        ts_close=today + timedelta(hours=4),
        status="closed",
        exit_price=Decimal("49000.00"),
        pnl_usdt=Decimal("-0.50"),
        pnl_pct=Decimal("-1.0"),
        fees_usdt=Decimal("0.04"),
    )
    await _create_trade(
        app_with_db.state.session_factory,
        ts_open=yesterday + timedelta(hours=8),
        ts_close=yesterday + timedelta(hours=12),
        status="closed",
        exit_price=Decimal("50500.00"),
        pnl_usdt=Decimal("0.25"),
        pnl_pct=Decimal("1.0"),
        fees_usdt=Decimal("0.03"),
    )

    r = await client.get("/api/stats/daily")
    assert r.status_code == 200
    data = r.json()

    assert data["trades_closed"] == 2
    assert data["trades_won"] == 1
    assert data["trades_lost"] == 1
    assert data["pnl_realized"] == 0.5
    assert data["fees_total"] == pytest.approx(0.09, abs=1e-4)


async def test_daily_stats_empty(client):
    r = await client.get("/api/stats/daily")
    assert r.status_code == 200
    data = r.json()
    assert data["trades_open"] == 0
    assert data["trades_closed"] == 0
