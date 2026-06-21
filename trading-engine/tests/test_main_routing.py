"""Tests for main.py — orchestrator helper functions."""
import pytest
from datetime import datetime, timezone, date
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, MetaData, Table, Column, String, Text, DateTime, Numeric
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from main import _decisor_interval_elapsed, _compute_risk_metrics, validate_futures_sizing, resolve_engine_symbol, _parse_providers
from shared.db.models import Decision, BalanceSnapshot, Ohlcv
from shared.config_store import ConfigStore, ConfigEntry, DEFAULTS


_sqlite_metadata = MetaData()

_trade_table = Table(
    "trades", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts_close", DateTime),
    Column("status", String(20)),
    Column("pnl_usdt", Numeric),
    Column("side", String(10)),
    Column("quantity_btc", Numeric),
    Column("entry_price", Numeric),
    Column("fees_usdt", Numeric),
)

_bal_snap_table = Table(
    "balance_snapshots", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime),
    Column("usdt", Numeric),
    Column("btc", Numeric),
    Column("usdt_locked", Numeric),
    Column("btc_locked", Numeric),
)

_ohlcv_table = Table(
    "ohlcv", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("time", DateTime),
    Column("timeframe", String(10)),
    Column("market", String(20)),
    Column("close", Numeric),
)

_config_table = Table(
    "config", _sqlite_metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text),
    Column("value_type", String(20)),
    Column("description", Text),
    Column("updated_at", DateTime),
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
async def seed_config(session):
    for key, default in DEFAULTS.items():
        session.add(ConfigEntry(
            key=key.value, value=default.value,
            value_type=default.value_type, description=default.description,
            updated_at=datetime.now(tz=timezone.utc),
        ))
    await session.commit()


class TestValidateFuturesSizing:
    def test_sizing_feasible(self):
        ok, reason = validate_futures_sizing(
            available_margin=10000.0, max_position_pct=0.1,
            leverage=3, min_notional=5.0,
        )
        assert ok
        assert reason == ""

    def test_sizing_infeasible_low_margin(self):
        ok, reason = validate_futures_sizing(
            available_margin=10.0, max_position_pct=0.01,
            leverage=1, min_notional=100.0,
        )
        assert not ok
        assert "sizing_unfeasible" in reason


class TestResolveEngineSymbol:
    def test_futures_symbol(self):
        assert resolve_engine_symbol("futures", "BTC/USDT") == "BTC/USDT:USDT"

    def test_spot_symbol(self):
        assert resolve_engine_symbol("spot", "BTC/USDT") == "BTC/USDT"


class TestComputeRiskMetrics:
    async def test_daily_pnl_zero_when_no_trades(self, session, seed_config):
        daily_pnl, drawdown = await _compute_risk_metrics(session, 1000.0)
        assert daily_pnl == 0.0
        assert isinstance(drawdown, float)

    async def test_daily_pnl_with_trades(self, session, seed_config):
        today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
        from uuid import uuid4
        for pnl in [10.0, -5.0, 3.0]:
            await session.execute(
                _trade_table.insert().values(
                    id=str(uuid4()), ts_close=today_start, status="closed",
                    pnl_usdt=Decimal(str(pnl)), side="BUY",
                    quantity_btc=Decimal("0.01"), entry_price=Decimal("80000"),
                    fees_usdt=Decimal("1.0"),
                )
            )
        await session.commit()
        daily_pnl, drawdown = await _compute_risk_metrics(session, 1000.0)
        expected = (10.0 + -5.0 + 3.0) / 1000.0
        assert daily_pnl == pytest.approx(expected, abs=1e-6)

    async def test_drawdown_from_peak(self, session, seed_config):
        from datetime import timedelta
        peak_ts = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.execute(
            _bal_snap_table.insert().values(
                id="peak", ts=peak_ts, usdt=Decimal("2000"),
                btc=Decimal("0"), usdt_locked=Decimal("0"), btc_locked=Decimal("0"),
            )
        )
        await session.commit()
        _, drawdown = await _compute_risk_metrics(session, 1000.0)
        assert drawdown < 0


class TestDecisorIntervalElapsed:
    async def test_true_when_no_decision(self, session):
        assert await _decisor_interval_elapsed(session, 5) is True

    async def test_true_when_old_decision(self, session):
        from uuid import uuid4
        from datetime import timedelta
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=30)
        dec = Decision(
            id=uuid4(), ts=old_ts, agent="decisor", model="test",
            tokens_in=0, tokens_out=0, latency_ms=0,
            input={}, output={}, executed=False,
        )
        session.add(dec)
        await session.commit()
        assert await _decisor_interval_elapsed(session, 5) is True

    async def test_false_when_recent_decision(self, session):
        from uuid import uuid4
        now = datetime.now(timezone.utc)
        dec = Decision(
            id=uuid4(), ts=now, agent="decisor", model="test",
            tokens_in=0, tokens_out=0, latency_ms=0,
            input={}, output={}, executed=False,
        )
        session.add(dec)
        await session.commit()
        assert await _decisor_interval_elapsed(session, 5) is False


class TestParseProviders:
    def test_parses_csv(self):
        from agents.llm_client import LLMProvider
        result = _parse_providers("gemini-2.5-flash,groq-llama-3.3-70b")
        assert len(result) == 2
        assert result[0] == LLMProvider.GEMINI_FLASH

    def test_skips_invalid(self):
        result = _parse_providers("gemini-2.5-flash,,invalid_provider,groq-llama-3.3-70b")
        assert len(result) == 2

    def test_returns_empty_for_empty(self):
        assert _parse_providers("") == []
