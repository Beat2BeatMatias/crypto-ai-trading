"""Tests for shared.db.base — SQLAlchemy engine and session factories."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.base import Base, create_engine_from_url, create_session_factory


def test_create_engine_from_url_returns_async_engine():
    engine = create_engine_from_url("postgresql+asyncpg://user:pass@host:5432/db")
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.host == "host"


def test_create_session_factory_yields_async_session():
    engine = create_engine_from_url("postgresql+asyncpg://user:pass@host:5432/db")
    factory = create_session_factory(engine)
    session = factory()
    assert isinstance(session, AsyncSession)


def test_base_is_declarative_base():
    """Models will inherit from Base — must have a metadata attribute."""
    assert hasattr(Base, "metadata")
    assert Base.metadata is not None
