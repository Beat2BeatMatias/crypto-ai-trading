"""Tests for PositionManager — unrealized PnL refresh and open-position count."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, Column, DateTime, MetaData, Numeric, String, Table
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from execution.position_manager import PositionManager
from shared.db.models import Position

# ---------------------------------------------------------------------------
# SQLite-compatible schema for the positions table
# ---------------------------------------------------------------------------
_meta = MetaData()

Table(
    "positions", _meta,
    Column("id", String(36), primary_key=True),
    Column("trade_id", String(36)),
    Column("symbol", String(20), nullable=False, default="BTC/USDT"),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("current_price", Numeric(18, 8)),
    Column("unrealized_pnl", Numeric(18, 4)),
    Column("unrealized_pct", Numeric(8, 4)),
    Column("status", String(10), default="open"),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)


def _assign_uuid(mapper, connection, target):  # noqa: ARG001
    if target.id is None:
        target.id = uuid.uuid4()


@pytest.fixture
async def session():
    event.listen(Position, "before_insert", _assign_uuid)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    event.remove(Position, "before_insert", _assign_uuid)
    await engine.dispose()


async def test_refresh_unrealized(session):
    # GIVEN one open position: entry=67000, qty=0.001
    session.add(Position(
        quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
        status="open", opened_at=datetime.now(tz=timezone.utc),
    ))
    await session.commit()

    # WHEN current price is 68000
    pm = PositionManager(session)
    await pm.refresh_unrealized(current_price=68000.0)

    # THEN unrealized_pnl = (68000 - 67000) * 0.001 = 1.0
    rows = (await session.execute(select(Position))).scalars().all()
    assert float(rows[0].unrealized_pnl) == pytest.approx(1.0, rel=1e-4)


async def test_count_open(session):
    # GIVEN one open and one closed position
    session.add(Position(
        quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
        status="open", opened_at=datetime.now(tz=timezone.utc),
    ))
    session.add(Position(
        quantity_btc=Decimal("0.001"), entry_price=Decimal("66000"),
        status="closed", opened_at=datetime.now(tz=timezone.utc),
    ))
    await session.commit()

    # WHEN count_open is called
    pm = PositionManager(session)

    # THEN only the open position is counted
    assert await pm.count_open() == 1
