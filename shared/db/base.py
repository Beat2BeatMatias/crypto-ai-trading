"""SQLAlchemy 2.0 async engine and session factories."""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_engine_from_url(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine.

    Args:
        url: SQLAlchemy URL, must use the asyncpg driver
            (e.g. postgresql+asyncpg://user:pass@host:5432/db).
        echo: when True, log every SQL statement (development only).
    """
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to *engine*.

    Sessions are not automatically committed; call ``await session.commit()``
    explicitly. ``expire_on_commit=False`` avoids the common gotcha where
    accessing an attribute after commit triggers an awaitable lazy-load.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
