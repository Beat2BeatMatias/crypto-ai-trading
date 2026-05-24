import os
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import JSON, String, Column, ForeignKey, MetaData, Table
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID, ARRAY

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

_PG_SPECIFIC_DEFAULTS = {"gen_random_uuid()", "now()"}


def _server_default_text(sd) -> str:
    """Extract the raw text from a server_default, or return empty string."""
    if sd is None:
        return ""
    arg = getattr(sd, "arg", None)
    if arg is None:
        return ""
    # arg may be a string or a ClauseElement
    if isinstance(arg, str):
        return arg
    try:
        return str(arg.compile(compile_kwargs={"literal_binds": True}))
    except Exception:
        return str(arg)


def _build_sqlite_meta(base_meta) -> MetaData:
    """Return a fresh MetaData with Postgres-specific constructs removed."""
    meta = MetaData()
    for table in base_meta.sorted_tables:
        cols = []
        for orig_col in table.columns:
            col_type = orig_col.type
            if isinstance(col_type, JSONB):
                col_type = JSON()
            elif isinstance(col_type, PG_UUID):
                col_type = String(36)
            elif isinstance(col_type, ARRAY):
                col_type = JSON()

            # Strip server_defaults that SQLite cannot parse
            server_default = orig_col.server_default
            if server_default is not None:
                sd_text = _server_default_text(server_default)
                if any(pg in sd_text for pg in _PG_SPECIFIC_DEFAULTS):
                    server_default = None

            fks = [
                ForeignKey(
                    str(fk.target_fullname),
                    use_alter=fk.parent.table.name == "decisions",
                )
                for fk in orig_col.foreign_keys
            ]

            cols.append(Column(
                orig_col.name,
                col_type,
                *fks,
                primary_key=orig_col.primary_key,
                nullable=orig_col.nullable,
                server_default=server_default,
            ))

        Table(table.name, meta, *cols, extend_existing=True)

    return meta


@pytest.fixture
async def app_with_db():
    from main import app
    from shared.db.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sqlite_meta = _build_sqlite_meta(Base.metadata)

    async with engine.begin() as conn:
        await conn.run_sync(sqlite_meta.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app.state.engine = engine
    app.state.session_factory = factory
    yield app
    await engine.dispose()


@pytest.fixture
async def client(app_with_db):
    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
