from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest
from freezegun import freeze_time
from sqlalchemy import select, MetaData, Table, Column, String, DateTime, Numeric, Text
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from shared.db.models import FeeSnapshot
from execution.fee_manager import FeeManager

# SQLite-compatible DDL — no JSONB, no PostgreSQL server_default, no UUID PK
_sqlite_metadata = MetaData()

_fee_snapshots_table = Table(
    "fee_snapshots", _sqlite_metadata,
    Column("id", String(36), primary_key=True, default=lambda: str(__import__("uuid").uuid4())),
    Column("ts", DateTime),
    Column("symbol", String(20), nullable=False),
    Column("maker_fee", Numeric(8, 6), nullable=False),
    Column("taker_fee", Numeric(8, 6), nullable=False),
    Column("raw", JSON, nullable=False),
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()

@pytest.fixture
def fake_exchange():
    ex = MagicMock()
    ex.fetch_trading_fees = AsyncMock(return_value={
        "BTC/USDT": {"maker": 0.001, "taker": 0.001, "info": {}},
    })
    return ex

async def test_refresh_populates_cache(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    assert fm.taker == pytest.approx(0.001)
    assert fm.maker == pytest.approx(0.001)

async def test_refresh_persists_snapshot(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    rows = (await session.execute(select(FeeSnapshot))).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].taker_fee) == pytest.approx(0.001)

async def test_get_or_refresh_uses_cache_within_24h(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    fake_exchange.fetch_trading_fees.reset_mock()
    await fm.get_or_refresh()
    assert fake_exchange.fetch_trading_fees.call_count == 0

async def test_get_or_refresh_refreshes_after_24h(session, fake_exchange):
    with freeze_time("2026-05-01 12:00:00") as frozen:
        fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
        await fm.refresh()
        fake_exchange.fetch_trading_fees.reset_mock()
        frozen.move_to("2026-05-02 13:00:00")
        await fm.get_or_refresh()
    assert fake_exchange.fetch_trading_fees.call_count == 1

async def test_fallback_to_last_snapshot_on_error(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    fake_exchange.fetch_trading_fees = AsyncMock(side_effect=RuntimeError("api down"))
    fm._last_refresh = None
    fm._taker = None
    await fm.get_or_refresh()
    assert fm.taker == pytest.approx(0.001)

async def test_roundtrip_pct_is_taker_x2(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    assert fm.roundtrip_pct == pytest.approx(0.002)
