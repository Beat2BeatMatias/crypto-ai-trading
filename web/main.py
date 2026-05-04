from __future__ import annotations
import os
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.db.base import create_engine_from_url, create_session_factory

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine_from_url(db_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    # Auto-create tables in SQLite (dev/test mode — Postgres uses Alembic migrations)
    if "sqlite" in db_url:
        from shared.db.base import Base
        from shared.db import models  # noqa: F401 — registers all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("web.sqlite_tables_created")

    logger.info("web.started")
    try:
        yield
    finally:
        await engine.dispose()
        logger.info("web.stopped")

app = FastAPI(title="Crypto AI Trading API", lifespan=lifespan)
allowed = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3100").split(",")
app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

from api import health, trades, decisions, positions, balance, playbook
from api import config as cfg_api
from api import control
from ws import feeds

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(decisions.router, prefix="/api", tags=["decisions"])
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(balance.router, prefix="/api", tags=["balance"])
app.include_router(playbook.router, prefix="/api", tags=["playbook"])
app.include_router(cfg_api.router, prefix="/api", tags=["config"])
app.include_router(control.router, prefix="/api", tags=["control"])
app.include_router(feeds.router, tags=["ws"])
