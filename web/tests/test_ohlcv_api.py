from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.db.models import Ohlcv


async def _create_candle(session_factory, **kwargs):
    defaults = dict(
        time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        timeframe="5m",
        market="spot",
        open=Decimal("50000.00"),
        high=Decimal("50200.00"),
        low=Decimal("49900.00"),
        close=Decimal("50100.00"),
        volume=Decimal("1.5"),
    )
    defaults.update(kwargs)
    async with session_factory() as s:
        s.add(Ohlcv(**defaults))
        await s.commit()


async def test_list_ohlcv_empty(client):
    r = await client.get("/api/ohlcv")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_ohlcv_returns_candles_in_chronological_order(client, app_with_db):
    base_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(3):
        await _create_candle(
            app_with_db.state.session_factory,
            time=base_ts + timedelta(minutes=5 * i),
            close=Decimal(f"{50000 + i * 100}"),
        )

    r = await client.get("/api/ohlcv?timeframe=5m&limit=10")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    closes = [c["close"] for c in data]
    assert closes == [50000.0, 50100.0, 50200.0]


async def test_list_ohlcv_filters_by_market(client, app_with_db):
    base_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    await _create_candle(
        app_with_db.state.session_factory,
        time=base_ts,
        market="spot",
        close=Decimal("50000"),
    )
    await _create_candle(
        app_with_db.state.session_factory,
        time=base_ts + timedelta(minutes=5),
        market="futures",
        close=Decimal("50100"),
    )

    r = await client.get("/api/ohlcv?timeframe=5m&market=futures")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["close"] == 50100.0


async def test_list_ohlcv_filters_by_timeframe(client, app_with_db):
    await _create_candle(app_with_db.state.session_factory, timeframe="5m")
    await _create_candle(
        app_with_db.state.session_factory,
        timeframe="1h",
        time=datetime(2025, 1, 1, 13, 0, tzinfo=timezone.utc),
    )

    r = await client.get("/api/ohlcv?timeframe=1h")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1


async def test_list_ohlcv_respects_limit(client, app_with_db):
    base_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(5):
        await _create_candle(
            app_with_db.state.session_factory,
            time=base_ts + timedelta(minutes=5 * i),
        )

    r = await client.get("/api/ohlcv?limit=2")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2


async def test_list_ohlcv_returns_latest_when_limit_lower_than_total(client, app_with_db):
    base_ts = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(5):
        await _create_candle(
            app_with_db.state.session_factory,
            time=base_ts + timedelta(minutes=5 * i),
            close=Decimal(f"{50000 + i * 100}"),
        )

    r = await client.get("/api/ohlcv?limit=2")

    closes = [c["close"] for c in r.json()]
    assert closes == [50300.0, 50400.0]


async def test_list_ohlcv_invalid_timeframe_returns_422(client):
    r = await client.get("/api/ohlcv?timeframe=2m")
    assert r.status_code == 422


async def test_list_ohlcv_limit_out_of_range_returns_422(client):
    r = await client.get("/api/ohlcv?limit=0")
    assert r.status_code == 422

    r2 = await client.get("/api/ohlcv?limit=2000")
    assert r2.status_code == 422
