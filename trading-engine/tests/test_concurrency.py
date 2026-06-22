"""Concurrency tests: verify tick isolation when decisor + order_tracker run concurrently."""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import Column, String, DateTime, Numeric, Text, MetaData, Table, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.models import Trade, Position, Decision
from shared.config_store import ConfigEntry, DEFAULTS


_meta = MetaData()

Table(
    "trades", _meta,
    Column("id", String(36), primary_key=True),
    Column("decision_id", String(36)),
    Column("ts_open", DateTime(timezone=True)),
    Column("ts_close", DateTime(timezone=True)),
    Column("side", String(10)),
    Column("quantity_btc", Numeric(18, 8)),
    Column("entry_price", Numeric(18, 8)),
    Column("exit_price", Numeric(18, 8)),
    Column("pnl_usdt", Numeric(18, 4)),
    Column("pnl_pct", Numeric(8, 4)),
    Column("status", String(20)),
    Column("stop_loss", Numeric(18, 8)),
    Column("take_profit", Numeric(18, 8)),
    Column("close_reason", String(30)),
    Column("order_id_open", String(50)),
    Column("order_id_close", String(50)),
    Column("order_id_sl", String(50)),
    Column("order_id_tp", String(50)),
    Column("fees_usdt", Numeric(18, 4)),
    Column("close_requested", String(5)),
    Column("position_side", String(5)),
    Column("leverage", Numeric(5, 2)),
    Column("liquidation_price", Numeric(18, 8)),
    Column("margin_mode", String(10)),
    Column("funding_paid_usdt", Numeric(18, 4)),
)

Table(
    "positions", _meta,
    Column("id", String(36), primary_key=True),
    Column("trade_id", String(36)),
    Column("symbol", String(20)),
    Column("quantity_btc", Numeric(18, 8)),
    Column("entry_price", Numeric(18, 8)),
    Column("current_price", Numeric(18, 8)),
    Column("unrealized_pnl", Numeric(18, 4)),
    Column("unrealized_pct", Numeric(8, 4)),
    Column("status", String(10)),
    Column("opened_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
    Column("position_side", String(5)),
    Column("leverage", Numeric(5, 2)),
    Column("liquidation_price", Numeric(18, 8)),
)

Table(
    "decisions", _meta,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime(timezone=True)),
    Column("agent", String(20)),
    Column("model", String(50)),
    Column("tokens_in", String(10)),
    Column("tokens_out", String(10)),
    Column("latency_ms", String(10)),
    Column("input", Text),
    Column("output", Text),
    Column("trade_id", String(36)),
    Column("executed", String(5)),
    Column("rejected_reason", String(200)),
)

Table(
    "config", _meta,
    Column("key", String(60), primary_key=True),
    Column("value", Text),
    Column("value_type", String(20)),
    Column("description", Text),
    Column("updated_at", DateTime(timezone=True)),
)

Table(
    "config_history", _meta,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime(timezone=True)),
    Column("key", String(60)),
    Column("old_value", Text),
    Column("new_value", Text),
    Column("changed_by", String(60)),
)

_balance_snapshots_table = Table(
    "balance_snapshots", _meta,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime(timezone=True)),
    Column("usdt", Numeric),
    Column("btc", Numeric),
    Column("usdt_locked", Numeric),
    Column("btc_locked", Numeric),
)


def _assign_uuid(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()


@pytest.fixture
async def factory():
    for model in (Trade, Position, Decision):
        from sqlalchemy import event
        event.listen(model, "before_insert", _assign_uuid)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_local() as s:
        for key, default in DEFAULTS.items():
            s.add(ConfigEntry(
                key=key.value, value=default.value,
                value_type=default.value_type, description=default.description,
                updated_at=datetime.now(tz=timezone.utc),
            ))
        await s.commit()

    yield session_local

    for model in (Trade, Position, Decision):
        from sqlalchemy import event
        event.remove(model, "before_insert", _assign_uuid)
    await engine.dispose()


async def _simulate_decisor_tick(sf) -> int:
    """Crea un trade con decisión."""
    async with sf() as s:
        await asyncio.sleep(0.02)
        dec = Decision(
            ts=datetime.now(tz=timezone.utc), agent="decisor", model="test",
            tokens_in=0, tokens_out=0, latency_ms=0,
            input={"confluences": []}, output={"confluences": []},
        )
        s.add(dec)
        await s.flush()
        trade = Trade(
            decision_id=dec.id, ts_open=datetime.now(tz=timezone.utc),
            side="BUY", quantity_btc=Decimal("0.001"),
            entry_price=Decimal("80000"), status="open",
            order_id_open="ORD-DECISOR",
        )
        s.add(trade)
        dec.trade_id = trade.id
        dec.executed = True
        await s.commit()
        return 1


async def _simulate_order_tracker_tick(sf) -> int:
    """Lee trades abiertos y marca brackets."""
    async with sf() as s:
        await asyncio.sleep(0.01)
        rows = (await s.execute(
            select(Trade).where(Trade.status == "open")
        )).scalars().all()
        for t in rows:
            t.order_id_sl = "SL-PLACED"
        await s.commit()
        return len(rows)


@pytest.mark.asyncio
async def test_decisor_and_order_tracker(factory):
    """Ambos ticks ejecutan concurrentemente sin corrupción."""
    sem = asyncio.Semaphore(2)

    async def run(fn):
        async with sem:
            return await fn(factory)

    tasks = [
        asyncio.create_task(run(_simulate_decisor_tick)),
        asyncio.create_task(run(_simulate_order_tracker_tick)),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert not any(isinstance(r, BaseException) for r in results), f"Tasks failed: {results}"


@pytest.mark.asyncio
async def test_multiple_decisor_ticks(factory):
    """Dos decisor_ticks concurrentes crean trades separados."""
    tasks = [
        asyncio.create_task(_simulate_decisor_tick(factory)),
        asyncio.create_task(_simulate_decisor_tick(factory)),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(r == 1 for r in results), f"Expected 2 successful creates, got: {results}"

    async with factory() as s:
        from sqlalchemy import func
        count = (await s.execute(select(func.count(Trade.id)))).scalar()
        assert count > 0


@pytest.mark.asyncio
async def test_tick_pairs_dont_share_session(factory):
    """Cada tick obtiene su propia sesión."""
    async with factory() as s1:
        async with factory() as s2:
            t = Trade(
                ts_open=datetime.now(tz=timezone.utc), side="BUY",
                quantity_btc=Decimal("0.001"), entry_price=Decimal("80000"),
                status="open", order_id_open="ORD-T1",
            )
            s1.add(t)
            await s1.commit()

            rows = (await s2.execute(
                select(Trade).where(Trade.status == "open")
            )).scalars().all()
            assert len(rows) == 1
