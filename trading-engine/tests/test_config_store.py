"""Tests for shared.config_store — runtime config persisted in DB."""
import pytest
from sqlalchemy import select, MetaData, Table, Column, String, Text, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.models import ConfigEntry, ConfigHistory
from shared.config_store import ConfigStore, ConfigKey, DEFAULTS

# SQLite-compatible DDL for the two config tables (no JSONB, no now() server_default)
_sqlite_metadata = MetaData()

_config_table = Table(
    "config", _sqlite_metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text, nullable=False),
    Column("value_type", String(20), nullable=False),
    Column("description", Text),
    Column("updated_at", DateTime),
)

_config_history_table = Table(
    "config_history", _sqlite_metadata,
    Column("id", String(36), primary_key=True, default=lambda: str(__import__("uuid").uuid4())),
    Column("ts", DateTime),
    Column("key", String(60), nullable=False),
    Column("old_value", Text),
    Column("new_value", Text, nullable=False),
    Column("changed_by", String(60)),
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


async def test_seed_defaults_inserts_all_keys(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    for key in DEFAULTS:
        assert await store.get(key) == DEFAULTS[key].value


async def test_seed_defaults_is_idempotent(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    await store.seed_defaults()
    rows = (await session.execute(select(ConfigEntry))).scalars().all()
    assert len(rows) == len(DEFAULTS)


async def test_set_writes_history(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    await store.set(ConfigKey.MAX_POSITION_PCT, "0.05", changed_by="test_user")
    history = (await session.execute(select(ConfigHistory))).scalars().all()
    assert len(history) == 1
    assert history[0].key == ConfigKey.MAX_POSITION_PCT.value
    assert history[0].new_value == "0.05"
    assert history[0].changed_by == "test_user"


async def test_get_typed_returns_correct_type(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    assert isinstance(await store.get_typed(ConfigKey.MAX_POSITION_PCT), float)
    assert isinstance(await store.get_typed(ConfigKey.MAX_SIMULTANEOUS_TRADES), int)
    assert isinstance(await store.get_typed(ConfigKey.KILL_SWITCH), bool)


async def test_kill_switch_default_is_false(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    assert await store.get_typed(ConfigKey.KILL_SWITCH) is False


async def test_futures_config_defaults(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    assert DEFAULTS[ConfigKey.TRADING_PRODUCT].value == "spot"
    assert await store.get(ConfigKey.TRADING_PRODUCT) == "spot"
    assert await store.get_typed(ConfigKey.MAX_LEVERAGE) == 1
    assert await store.get(ConfigKey.MARGIN_MODE) == "isolated"
    assert ConfigKey.FUNDING_RATE_MAX_PCT in DEFAULTS
    assert ConfigKey.LIQUIDATION_BUFFER_ATR in DEFAULTS


async def test_get_many_returns_all_requested_keys(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    result = await store.get_many([
        ConfigKey.MAX_POSITION_PCT,
        ConfigKey.MAX_SIMULTANEOUS_TRADES,
        ConfigKey.KILL_SWITCH,
    ])
    assert len(result) == 3
    assert isinstance(result["max_position_pct"], float)
    assert isinstance(result["max_simultaneous_trades"], int)
    assert isinstance(result["kill_switch"], bool)
    assert result["kill_switch"] is False


async def test_get_many_skips_missing_keys(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    result = await store.get_many([
        ConfigKey.MAX_POSITION_PCT,
        ConfigKey.DRAWDOWN_RESET_TS,
        ConfigKey.TRADING_PRODUCT,
    ])
    assert "max_position_pct" in result
    assert "drawdown_reset_ts" in result
    assert "trading_product" in result


async def test_get_many_single_key(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    result = await store.get_many([ConfigKey.MODE])
    assert len(result) == 1
    assert result["mode"] == "PAPER_TRADING"


async def test_get_many_empty_list(session: AsyncSession):
    store = ConfigStore(session)
    result = await store.get_many([])
    assert result == {}


async def test_get_many_types_are_correct(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    result = await store.get_many([
        ConfigKey.MAX_POSITION_PCT,
        ConfigKey.MAX_SIMULTANEOUS_TRADES,
        ConfigKey.DECISOR_PROVIDER,
        ConfigKey.ENGINE_PAUSED,
    ])
    assert isinstance(result["max_position_pct"], float)
    assert isinstance(result["max_simultaneous_trades"], int)
    assert isinstance(result["decisor_provider"], str)
    assert isinstance(result["engine_paused"], bool)
