"""Tests for PriceCollector — fetches OHLCV, computes indicators, persists."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select, JSON, MetaData, Table, Column, DateTime, String, Numeric
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Ohlcv, Indicators
from collectors.price_collector import PriceCollector


SAMPLE_OHLCV = [
    [1_714_521_600_000 + i * 60_000, 60000.0 + i, 60010.0 + i, 59990.0 + i, 60005.0 + i, 100.0]
    for i in range(250)
]


def _sqlite_safe_metadata() -> MetaData:
    """Return a MetaData with only the tables used by PriceCollector, using JSON instead of JSONB."""
    meta = MetaData()
    Table(
        "ohlcv", meta,
        Column("time", DateTime(timezone=True), primary_key=True),
        Column("timeframe", String(4), primary_key=True),
        Column("market", String(8), primary_key=True),
        Column("open", Numeric(18, 8)),
        Column("high", Numeric(18, 8)),
        Column("low", Numeric(18, 8)),
        Column("close", Numeric(18, 8)),
        Column("volume", Numeric(24, 8)),
    )
    Table(
        "indicators", meta,
        Column("time", DateTime(timezone=True), primary_key=True),
        Column("data", JSON, nullable=False),
    )
    return meta


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    meta = _sqlite_safe_metadata()
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def fake_exchange():
    ex = MagicMock()
    ex.fetch_ohlcv = AsyncMock(return_value=SAMPLE_OHLCV)
    return ex


async def test_fetch_and_persist_ohlcv(session, fake_exchange):
    collector = PriceCollector(fake_exchange, session, symbol="BTC/USDT")
    count = await collector.fetch_and_persist(timeframe="5m")
    assert count == len(SAMPLE_OHLCV)
    rows = (await session.execute(select(Ohlcv))).scalars().all()
    assert len(rows) == len(SAMPLE_OHLCV)
    assert rows[0].timeframe == "5m"


async def test_fetch_and_persist_is_idempotent(session, fake_exchange):
    collector = PriceCollector(fake_exchange, session, symbol="BTC/USDT")
    await collector.fetch_and_persist(timeframe="5m")
    await collector.fetch_and_persist(timeframe="5m")
    rows = (await session.execute(select(Ohlcv))).scalars().all()
    assert len(rows) == len(SAMPLE_OHLCV)


async def test_compute_and_persist_indicators_writes_row(session, fake_exchange):
    collector = PriceCollector(fake_exchange, session, symbol="BTC/USDT")
    await collector.fetch_and_persist(timeframe="5m")
    await collector.compute_and_persist_indicators()
    rows = (await session.execute(select(Indicators))).scalars().all()
    assert len(rows) == 1
    data = rows[0].data
    assert "5m" in data
    assert "rsi" in data["5m"]
