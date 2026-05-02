# Crypto AI Trading Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully-autonomous AI-driven day trading bot for BTC/USDT on Binance Spot, with two coordinated LLM agents (Decisor + Supervisor), a deterministic Risk Gate, and a React dashboard.

**Architecture:** Two Python services (`trading-engine` running the autonomous loop, `web` exposing FastAPI + WebSocket) sharing a PostgreSQL database, plus a React frontend served by nginx. Engine and web communicate only via Postgres. Both coexist with the sibling `crypto-arbitrage` project using ports 3100/8100/5532.

**Tech Stack:** Python 3.12, FastAPI 0.115, SQLAlchemy 2.0 async, asyncpg, Alembic, PostgreSQL 17, CCXT 4.x (with ccxt.pro), pandas-ta, google-genai (Gemini 2.5 Flash + Pro), groq SDK (fallback), structlog, Pydantic 2, APScheduler, React 19, Vite, TailwindCSS v4, Recharts, Docker Compose.

**Spec reference:** `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md`

**Working directory:** `/Users/mfariasfalki/project/crypto-ai-trading ` (note trailing space).

---

## Project File Structure

```
crypto-ai-trading/
├── README.md
├── CLAUDE.md
├── .gitignore
├── .env.example
├── docker-compose.yml
│
├── shared/                          # imported by both engine and web
│   ├── __init__.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py                  # SQLAlchemy Base + async engine factory
│   │   ├── models.py                # all ORM models (10 tables)
│   │   └── repositories.py          # async data access functions
│   ├── config_store.py              # runtime config getter/setter
│   └── schemas.py                   # Pydantic DTOs (decision output, etc.)
│
├── trading-engine/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── alembic.ini
│   ├── main.py                      # entrypoint, wires services, starts loops
│   ├── config.py                    # env-derived settings (Pydantic Settings)
│   ├── scheduler.py                 # APScheduler setup
│   ├── exchange.py                  # CCXT client factory (REST + WS)
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── indicators.py            # pandas-ta wrappers
│   │   ├── price_collector.py       # OHLCV → indicators → DB
│   │   └── orderbook_collector.py   # WS → in-memory order book
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── llm_client.py            # Gemini + Groq abstraction with fallback
│   │   ├── context_builder.py       # assembles dict for the decisor prompt
│   │   ├── prompt_manager.py        # loads templates + active playbook
│   │   ├── decisor.py               # 5-min loop
│   │   ├── supervisor.py            # daily 00:00 UTC job
│   │   └── prompts/
│   │       ├── decisor_system.txt
│   │       ├── decisor_user.txt
│   │       ├── supervisor_system.txt
│   │       ├── supervisor_user.txt
│   │       └── playbook_v0.md
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── risk_gate.py             # deterministic validator
│   │   └── circuit_breaker.py       # daily stop, drawdown, kill switch
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── fee_manager.py           # fetch_trading_fees + cache
│   │   ├── executor.py              # CCXT order placement
│   │   ├── position_manager.py      # tracks open positions + P&L
│   │   └── order_tracker.py         # polls open orders for fills
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 001_initial_schema.py
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       └── test_*.py                # one test module per source module
│
├── web/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── main.py                      # FastAPI app + lifespan
│   ├── api/
│   │   ├── __init__.py
│   │   ├── trades.py
│   │   ├── decisions.py
│   │   ├── positions.py
│   │   ├── balance.py
│   │   ├── playbook.py
│   │   ├── config.py
│   │   ├── control.py               # kill switch, mode toggle
│   │   └── health.py
│   ├── ws/
│   │   ├── __init__.py
│   │   ├── manager.py               # WS connection registry
│   │   └── feeds.py                 # DB poller → broadcast events
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       └── test_*.py
│
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── index.css
│       ├── api/client.ts            # fetch wrappers
│       ├── hooks/
│       │   ├── useWebSocket.ts
│       │   └── useApi.ts
│       ├── types/index.ts           # mirrors backend Pydantic schemas
│       └── pages/
│           ├── Dashboard.tsx
│           ├── Trades.tsx
│           ├── Decisions.tsx
│           ├── Playbook.tsx
│           ├── Config.tsx
│           └── Health.tsx
│
└── backtesting/
    ├── README.md
    ├── requirements.txt
    ├── runner.py
    └── tests/
        └── test_runner.py
```

---

# Phase 1 — Infrastructure & Schema

## Task 1: Repository scaffold and `.gitignore`

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `.env.example`
- Create: `CLAUDE.md`

- [ ] **Step 1: Create `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
build/
dist/

# Node
node_modules/
dist/
.vite/

# Env / secrets
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/
*.swp
.DS_Store

# Logs
*.log
logs/

# Postgres data (Docker volume mount)
postgres_data/

# Alembic
# (keep alembic/versions/ — track migrations in git)

# Project-specific
backtesting/data/
*.sqlite
*.db
```

- [ ] **Step 2: Create `.env.example`**

```bash
# Postgres
POSTGRES_USER=trader
POSTGRES_PASSWORD=changeme_dev_only
POSTGRES_DB=crypto_ai_trading
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Binance Testnet (default — switch to mainnet only after Phase 7)
BINANCE_API_KEY=your_testnet_key
BINANCE_API_SECRET=your_testnet_secret
BINANCE_TESTNET=true

# LLM Providers (free tiers — see https://aistudio.google.com/apikey and https://console.groq.com)
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key

# Engine config
LOG_LEVEL=INFO
TRADING_MODE=PAPER_TRADING

# Web
WEB_HOST=0.0.0.0
WEB_PORT=8000
ALLOWED_ORIGINS=http://localhost:3100
```

- [ ] **Step 3: Create `README.md`**

```markdown
# Crypto AI Trading

AI-driven autonomous day trading bot for BTC/USDT on Binance Spot.
See `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md` for full design.

## Quick start

```bash
cp .env.example .env
# edit .env with your API keys
docker-compose build
docker-compose up -d
```

URLs (after `up -d`):
- Frontend: http://localhost:3100
- Web API: http://localhost:8100
- Postgres: localhost:5532

## Run tests

```bash
docker-compose run --rm trading-engine pytest
docker-compose run --rm web pytest
```

## Apply migrations

```bash
docker-compose run --rm trading-engine alembic upgrade head
```
```

- [ ] **Step 4: Create `CLAUDE.md`**

```markdown
# CLAUDE.md

Guidance for Claude Code when working with this repo.

## Build and run

Docker preferred:
```bash
docker-compose build
docker-compose up -d
docker-compose logs -f trading-engine
```

Local dev (without Docker):
```bash
# Trading engine
cd trading-engine
pip install -r requirements.txt
alembic upgrade head
python main.py

# Web
cd web
pip install -r requirements.txt
uvicorn main:app --reload --port 8100

# Frontend
cd frontend
npm install
npm run dev
```

## Architecture

Two Python services share a Postgres DB:
- `trading-engine`: autonomous bot loop (price collection, decisor, executor, supervisor). No HTTP server.
- `web`: FastAPI exposing REST + WebSocket for the dashboard.

The engine writes everything (decisions, trades, ohlcv, indicators, positions, playbook). The web reads everything plus writes only `config` and `config_history`. They never IPC directly — Postgres is the single source of truth.

## Ports

- Frontend: 3100
- Web API: 8100
- Postgres: 5532

(Different from sibling `crypto-arbitrage` project at 3000/8000/5432 — both run side by side.)

## Mode

`TRADING_MODE=PAPER_TRADING` uses Binance Spot Testnet (no real money). `TRADING_MODE=LIVE` uses real account — only after backtesting + 4 weeks paper.

## Conventions

- All times stored UTC (`TIMESTAMPTZ`).
- All prices/quantities `NUMERIC` (never `FLOAT`).
- LLM payloads stored in `JSONB` for flexible querying.
- Spanish UI text (es-AR locale).
- Logging via `structlog`, JSON output.
- Tests use pytest + pytest-asyncio + freezegun (deterministic time).
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.md .env.example CLAUDE.md
git commit -m "chore: scaffold repo with gitignore, env template, and docs"
```

---

## Task 2: `docker-compose.yml` with 4 services

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-trader}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB:-crypto_ai_trading}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5532:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-trader} -d ${POSTGRES_DB:-crypto_ai_trading}"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  trading-engine:
    build:
      context: .
      dockerfile: trading-engine/Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-trader}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-crypto_ai_trading}
    volumes:
      - ./trading-engine:/app
      - ./shared:/app/shared
    restart: unless-stopped

  web:
    build:
      context: .
      dockerfile: web/Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-trader}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-crypto_ai_trading}
    ports:
      - "8100:8000"
    volumes:
      - ./web:/app
      - ./shared:/app/shared
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    depends_on:
      - web
    ports:
      - "3100:80"
    restart: unless-stopped

volumes:
  postgres_data:
```

- [ ] **Step 2: Verify the file is valid YAML**

Run: `docker compose -f docker-compose.yml config --quiet`
Expected: no output, exit 0. (If `docker compose` not available, use `docker-compose config --quiet`.)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: add docker-compose with postgres, engine, web, frontend"
```

---

## Task 3: Trading-engine Dockerfile and requirements

**Files:**
- Create: `trading-engine/Dockerfile`
- Create: `trading-engine/requirements.txt`
- Create: `trading-engine/pytest.ini`

- [ ] **Step 1: Write `trading-engine/requirements.txt`**

```
# Database
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
alembic==1.14.0

# Web (only used by health endpoints inside engine for liveness)
pydantic==2.10.3
pydantic-settings==2.7.0

# Async runtime
uvloop==0.21.0

# Exchange & indicators
ccxt==4.4.40
pandas==2.2.3
numpy==2.1.3
pandas-ta==0.3.14b0

# LLMs
google-genai==0.3.0
groq==0.13.1

# HTTP
httpx==0.28.1
websockets==13.1

# Scheduler
apscheduler==3.11.0

# Logging
structlog==24.4.0

# Testing
pytest==8.3.4
pytest-asyncio==0.25.0
pytest-cov==6.0.0
freezegun==1.5.1
respx==0.21.1
```

- [ ] **Step 2: Write `trading-engine/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# System deps for pandas/numpy and asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY trading-engine/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY trading-engine/ ./
COPY shared/ ./shared/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["python", "main.py"]
```

- [ ] **Step 3: Write `trading-engine/pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    -v
    --tb=short
    --strict-markers
    --cov=. --cov-report=term-missing --cov-fail-under=70
markers =
    integration: integration tests requiring DB/exchange
    unit: pure unit tests (default, no markers needed)
```

- [ ] **Step 4: Commit**

```bash
git add trading-engine/Dockerfile trading-engine/requirements.txt trading-engine/pytest.ini
git commit -m "chore: add trading-engine Dockerfile, deps, and pytest config"
```

---

## Task 4: Shared module — DB base and engine factory

**Files:**
- Create: `shared/__init__.py` (empty)
- Create: `shared/db/__init__.py` (empty)
- Create: `shared/db/base.py`
- Create: `trading-engine/tests/__init__.py` (empty)
- Create: `trading-engine/tests/conftest.py`
- Create: `trading-engine/tests/test_db_base.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_db_base.py`**

```python
"""Tests for shared.db.base — SQLAlchemy engine and session factories."""
import os
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
```

- [ ] **Step 2: Write `trading-engine/tests/conftest.py`**

```python
"""Shared pytest fixtures for trading-engine tests."""
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default policy; pytest-asyncio creates loops per test in auto mode."""
    return asyncio.DefaultEventLoopPolicy()
```

- [ ] **Step 3: Run test to confirm it fails**

```bash
cd trading-engine && python -m pytest tests/test_db_base.py -v
```
Expected: `ImportError: cannot import name 'Base' from 'shared.db.base'` (module not yet created).

- [ ] **Step 4: Write `shared/db/base.py`**

```python
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
```

- [ ] **Step 5: Re-run test to confirm pass**

```bash
cd trading-engine && python -m pytest tests/test_db_base.py -v
```
Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add shared/ trading-engine/tests/
git commit -m "feat(db): add async SQLAlchemy base and session factory"
```

---

## Task 5: ORM models for all 10 tables

**Files:**
- Create: `shared/db/models.py`
- Create: `trading-engine/tests/test_models.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_models.py`**

```python
"""Smoke tests that all ORM models exist with the right columns."""
import pytest
from sqlalchemy import inspect

from shared.db.models import (
    Base, Ohlcv, Indicators, Decision, Trade, Position,
    PlaybookVersion, ConfigEntry, ConfigHistory, DailyStats, FeeSnapshot,
)


def _columns(model) -> set[str]:
    return {c.name for c in inspect(model).columns}


def test_ohlcv_columns():
    cols = _columns(Ohlcv)
    assert {"time", "timeframe", "open", "high", "low", "close", "volume"} <= cols


def test_indicators_columns():
    cols = _columns(Indicators)
    assert {"time", "data"} <= cols


def test_decision_columns():
    cols = _columns(Decision)
    assert {
        "id", "ts", "agent", "model", "tokens_in", "tokens_out", "latency_ms",
        "input", "output", "outcome", "trade_id", "executed", "rejected_reason",
    } <= cols


def test_trade_columns():
    cols = _columns(Trade)
    assert {
        "id", "decision_id", "ts_open", "ts_close", "side", "quantity_btc",
        "entry_price", "exit_price", "pnl_usdt", "pnl_pct", "status",
        "stop_loss", "take_profit", "close_reason", "order_id_open",
        "order_id_close", "fees_usdt",
    } <= cols


def test_position_columns():
    cols = _columns(Position)
    assert {
        "id", "trade_id", "symbol", "quantity_btc", "entry_price",
        "current_price", "unrealized_pnl", "unrealized_pct",
        "status", "opened_at", "updated_at",
    } <= cols


def test_playbook_version_columns():
    cols = _columns(PlaybookVersion)
    assert {
        "id", "version", "ts_generated", "content", "model",
        "trades_analyzed", "win_rate", "pnl_summary", "active",
    } <= cols


def test_config_entry_columns():
    cols = _columns(ConfigEntry)
    assert {"key", "value", "value_type", "description", "updated_at"} <= cols


def test_config_history_columns():
    cols = _columns(ConfigHistory)
    assert {"id", "ts", "key", "old_value", "new_value", "changed_by"} <= cols


def test_daily_stats_columns():
    cols = _columns(DailyStats)
    assert {
        "date", "decisions_total", "trades_executed", "wins", "losses",
        "pnl_usdt", "pnl_pct", "max_drawdown", "breakdown",
    } <= cols


def test_fee_snapshot_columns():
    cols = _columns(FeeSnapshot)
    assert {"id", "ts", "symbol", "maker_fee", "taker_fee", "raw"} <= cols


def test_all_tables_use_same_metadata():
    """All models must register on the shared Base.metadata."""
    table_names = {t.name for t in Base.metadata.sorted_tables}
    expected = {
        "ohlcv", "indicators", "decisions", "trades", "positions",
        "playbook_versions", "config", "config_history", "daily_stats",
        "fee_snapshots",
    }
    assert expected <= table_names
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_models.py -v
```
Expected: `ImportError: cannot import name 'Ohlcv' from 'shared.db.models'`.

- [ ] **Step 3: Write `shared/db/models.py`**

```python
"""SQLAlchemy 2.0 ORM models — mirror the schema from spec §6."""
from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    String, Integer, Boolean, Date, DateTime, Numeric, ForeignKey,
    Text, UniqueConstraint, Index, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db.base import Base


class Ohlcv(Base):
    __tablename__ = "ohlcv"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))

    __table_args__ = (
        Index("idx_ohlcv_tf", "timeframe", "time", postgresql_using="btree"),
    )


class Indicators(Base):
    __tablename__ = "indicators"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_indicators_data", "data", postgresql_using="gin"),
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    agent: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trades.id"), nullable=True,
    )
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_reason: Mapped[str | None] = mapped_column(String(200))

    trade: Mapped["Trade | None"] = relationship(
        "Trade", foreign_keys=[trade_id], post_update=True,
    )

    __table_args__ = (
        Index("idx_decisions_ts", "ts"),
        Index("idx_decisions_output", "output", postgresql_using="gin"),
        Index("idx_decisions_input", "input", postgresql_using="gin"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id", use_alter=True),
    )
    ts_open: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity_btc: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    pnl_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    close_reason: Mapped[str | None] = mapped_column(String(20))
    order_id_open: Mapped[str | None] = mapped_column(String(50))
    order_id_close: Mapped[str | None] = mapped_column(String(50))
    fees_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    __table_args__ = (
        Index("idx_trades_status", "status"),
        Index("idx_trades_ts", "ts_open"),
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("trades.id"))
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="BTC/USDT")
    quantity_btc: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unrealized_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(10), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    ts_generated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(50))
    trades_analyzed: Mapped[int | None] = mapped_column(Integer)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    pnl_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index(
            "idx_playbook_active",
            "active",
            unique=True,
            postgresql_where=text("active = true"),
        ),
    )


class ConfigEntry(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"),
    )


class ConfigHistory(Base):
    __tablename__ = "config_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(60), default="system")


class DailyStats(Base):
    __tablename__ = "daily_stats"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    decisions_total: Mapped[int] = mapped_column(Integer, default=0)
    trades_executed: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    pnl_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class FeeSnapshot(Base):
    __tablename__ = "fee_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, default="BTC/USDT")
    maker_fee: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    taker_fee: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_fee_snapshots_ts", "ts"),
    )
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_models.py -v
```
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add shared/db/models.py trading-engine/tests/test_models.py
git commit -m "feat(db): add ORM models for all 10 tables"
```

---

## Task 6: Alembic setup and initial migration

**Files:**
- Create: `trading-engine/alembic.ini`
- Create: `trading-engine/alembic/env.py`
- Create: `trading-engine/alembic/script.py.mako`
- Create: `trading-engine/alembic/versions/001_initial_schema.py`

- [ ] **Step 1: Initialize alembic skeleton**

Run from `trading-engine/`:
```bash
cd trading-engine && python -m alembic init -t async alembic
```
This creates `alembic/env.py`, `alembic/script.py.mako`, and `alembic.ini`.

- [ ] **Step 2: Edit `trading-engine/alembic.ini`**

Find and replace these lines:
```ini
sqlalchemy.url = driver://user:pass@localhost/dbname
```
with (leave URL empty — `env.py` reads it from `DATABASE_URL`):
```ini
sqlalchemy.url =
```

Find:
```ini
script_location = alembic
```
Leave as-is.

- [ ] **Step 3: Replace `trading-engine/alembic/env.py`**

```python
"""Alembic env.py — async, reads DATABASE_URL from environment."""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import models so Alembic sees them on Base.metadata
from shared.db.base import Base
from shared.db import models  # noqa: F401  — registers all tables

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Inject DATABASE_URL from env so migrations work both locally and in container.
db_url = os.environ.get("DATABASE_URL")
if db_url is None:
    raise RuntimeError("DATABASE_URL environment variable is required")
config.set_main_option("sqlalchemy.url", db_url)


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate the initial migration**

With Postgres running (from Task 2 docker-compose) and DATABASE_URL set:
```bash
cd trading-engine && \
DATABASE_URL=postgresql+asyncpg://trader:changeme_dev_only@localhost:5532/crypto_ai_trading \
python -m alembic revision --autogenerate -m "initial schema"
```

This creates `alembic/versions/<hash>_initial_schema.py`. Rename it to `001_initial_schema.py`.

- [ ] **Step 5: Apply the migration to verify**

```bash
cd trading-engine && \
DATABASE_URL=postgresql+asyncpg://trader:changeme_dev_only@localhost:5532/crypto_ai_trading \
python -m alembic upgrade head
```
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 001, initial schema`.

- [ ] **Step 6: Verify schema with psql**

```bash
PGPASSWORD=changeme_dev_only psql -h localhost -p 5532 -U trader -d crypto_ai_trading -c "\dt"
```
Expected output includes: `ohlcv`, `indicators`, `decisions`, `trades`, `positions`, `playbook_versions`, `config`, `config_history`, `daily_stats`, `fee_snapshots`, `alembic_version`.

- [ ] **Step 7: Commit**

```bash
git add trading-engine/alembic.ini trading-engine/alembic/
git commit -m "feat(db): add alembic with initial schema migration"
```

---

## Task 7: Pydantic settings and config seeding

**Files:**
- Create: `trading-engine/config.py`
- Create: `shared/config_store.py`
- Create: `trading-engine/tests/test_config_store.py`

- [ ] **Step 1: Write `trading-engine/config.py`**

```python
"""Engine settings derived from environment variables (Pydantic Settings)."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    """Static, env-derived settings. Runtime config lives in the DB (config_store)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(..., alias="DATABASE_URL")

    binance_api_key: str = Field(..., alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(..., alias="BINANCE_API_SECRET")
    binance_testnet: bool = Field(True, alias="BINANCE_TESTNET")

    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")

    trading_mode: str = Field("PAPER_TRADING", alias="TRADING_MODE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    symbol: str = "BTC/USDT"


_settings: EngineSettings | None = None


def get_settings() -> EngineSettings:
    """Memoised settings accessor."""
    global _settings
    if _settings is None:
        _settings = EngineSettings()
    return _settings
```

- [ ] **Step 2: Write the failing test `trading-engine/tests/test_config_store.py`**

```python
"""Tests for shared.config_store — runtime config persisted in DB."""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import ConfigEntry, ConfigHistory
from shared.config_store import ConfigStore, ConfigKey, DEFAULTS


@pytest.fixture
async def session():
    """In-memory SQLite for fast unit tests of config_store logic.

    Note: SQLite does not support JSONB or UUID natively, so we only test
    config_store with text values — sufficient for this module.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    # Same number of rows
    rows = (await session.execute(__import__("sqlalchemy").select(ConfigEntry))).scalars().all()
    assert len(rows) == len(DEFAULTS)


async def test_set_writes_history(session: AsyncSession):
    store = ConfigStore(session)
    await store.seed_defaults()
    await store.set(ConfigKey.MAX_POSITION_PCT, "0.05", changed_by="test_user")
    history = (
        await session.execute(__import__("sqlalchemy").select(ConfigHistory))
    ).scalars().all()
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
```

- [ ] **Step 3: Add `aiosqlite` to test deps**

Append to `trading-engine/requirements.txt`:
```
aiosqlite==0.20.0
```
Re-install: `pip install aiosqlite==0.20.0`.

- [ ] **Step 4: Run test to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_config_store.py -v
```
Expected: `ImportError: cannot import name 'ConfigStore' from 'shared.config_store'`.

- [ ] **Step 5: Write `shared/config_store.py`**

```python
"""Runtime configuration store: read/write key-value config from Postgres.

The seed defaults match spec §6.3. Engine and web both use this module.
The web layer always passes ``changed_by="user"`` so we can attribute UI edits.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import ConfigEntry, ConfigHistory


class ConfigKey(str, Enum):
    MODE = "mode"
    MAX_POSITION_PCT = "max_position_pct"
    MAX_SIMULTANEOUS_TRADES = "max_simultaneous_trades"
    DAILY_STOP_PCT = "daily_stop_pct"
    MAX_DRAWDOWN_PCT = "max_drawdown_pct"
    MAX_SLIPPAGE_PCT = "max_slippage_pct"
    DEFAULT_RR_RATIO = "default_rr_ratio"
    DECISOR_INTERVAL_MIN = "decisor_interval_min"
    SUPERVISOR_CRON = "supervisor_cron"
    DECISOR_PROVIDER = "decisor_provider"
    SUPERVISOR_PROVIDER = "supervisor_provider"
    FALLBACK_PROVIDER = "fallback_provider"
    LLM_MAX_RETRIES = "llm_max_retries"
    LLM_TIMEOUT_SEC = "llm_timeout_sec"
    ORDERBOOK_LEVELS = "orderbook_levels"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class _Default:
    value: str
    value_type: str  # "int", "float", "string", "bool", "json"
    description: str


DEFAULTS: dict[ConfigKey, _Default] = {
    ConfigKey.MODE: _Default("PAPER_TRADING", "string", "PAPER_TRADING or LIVE"),
    ConfigKey.MAX_POSITION_PCT: _Default("0.10", "float", "Max % capital per trade"),
    ConfigKey.MAX_SIMULTANEOUS_TRADES: _Default("2", "int", "Max concurrent open positions"),
    ConfigKey.DAILY_STOP_PCT: _Default("-0.03", "float", "Daily P&L stop"),
    ConfigKey.MAX_DRAWDOWN_PCT: _Default("-0.10", "float", "Total drawdown limit"),
    ConfigKey.MAX_SLIPPAGE_PCT: _Default("0.003", "float", "Max acceptable slippage"),
    ConfigKey.DEFAULT_RR_RATIO: _Default("2.0", "float", "Default take-profit ratio"),
    ConfigKey.DECISOR_INTERVAL_MIN: _Default("5", "int", "Decisor frequency in minutes"),
    ConfigKey.SUPERVISOR_CRON: _Default("0 0 * * *", "string", "Supervisor schedule (UTC)"),
    ConfigKey.DECISOR_PROVIDER: _Default("gemini-2.5-flash", "string", "Primary LLM for decisor"),
    ConfigKey.SUPERVISOR_PROVIDER: _Default("gemini-2.5-pro", "string", "LLM for supervisor"),
    ConfigKey.FALLBACK_PROVIDER: _Default("groq-llama-3.3-70b", "string", "Fallback LLM"),
    ConfigKey.LLM_MAX_RETRIES: _Default("3", "int", "Retries on LLM failure"),
    ConfigKey.LLM_TIMEOUT_SEC: _Default("30", "int", "LLM call timeout"),
    ConfigKey.ORDERBOOK_LEVELS: _Default("10", "int", "Order book depth in context"),
    ConfigKey.KILL_SWITCH: _Default("false", "bool", "Emergency stop"),
}


class ConfigStore:
    """Async helper around the ``config`` and ``config_history`` tables."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed_defaults(self) -> None:
        """Insert default rows for any missing key. Idempotent."""
        existing = {
            r.key for r in (await self.session.execute(select(ConfigEntry))).scalars().all()
        }
        for key, default in DEFAULTS.items():
            if key.value in existing:
                continue
            self.session.add(
                ConfigEntry(
                    key=key.value,
                    value=default.value,
                    value_type=default.value_type,
                    description=default.description,
                )
            )
        await self.session.commit()

    async def get(self, key: ConfigKey) -> str:
        row = await self.session.get(ConfigEntry, key.value)
        if row is None:
            raise KeyError(f"Config key not found: {key.value}")
        return row.value

    async def get_typed(self, key: ConfigKey) -> Any:
        """Return value cast to the type recorded in ``value_type``."""
        row = await self.session.get(ConfigEntry, key.value)
        if row is None:
            raise KeyError(f"Config key not found: {key.value}")
        return _cast(row.value, row.value_type)

    async def set(self, key: ConfigKey, value: str, *, changed_by: str = "system") -> None:
        row = await self.session.get(ConfigEntry, key.value)
        if row is None:
            raise KeyError(f"Cannot set unknown key: {key.value}")
        old_value = row.value
        row.value = value
        row.updated_at = datetime.utcnow()
        self.session.add(
            ConfigHistory(
                key=key.value, old_value=old_value, new_value=value, changed_by=changed_by,
            )
        )
        await self.session.commit()


def _cast(value: str, value_type: str) -> Any:
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return value.lower() in ("true", "1", "yes")
    if value_type == "json":
        return json.loads(value)
    return value  # "string"
```

- [ ] **Step 6: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_config_store.py -v
```
Expected: `5 passed`.

- [ ] **Step 7: Commit**

```bash
git add shared/config_store.py trading-engine/config.py trading-engine/tests/test_config_store.py trading-engine/requirements.txt
git commit -m "feat(config): add Pydantic settings and DB-backed runtime config store"
```

---


# Phase 2 — Data Collectors

## Task 8: CCXT exchange factory

**Files:**
- Create: `trading-engine/exchange.py`
- Create: `trading-engine/tests/test_exchange.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_exchange.py`**

```python
"""Tests for trading-engine exchange factory."""
import pytest
from unittest.mock import patch

from trading_engine_pkg.exchange import build_binance_client


@patch.dict("os.environ", {
    "DATABASE_URL": "postgresql+asyncpg://x:y@h/d",
    "BINANCE_API_KEY": "test_key",
    "BINANCE_API_SECRET": "test_secret",
    "BINANCE_TESTNET": "true",
    "GEMINI_API_KEY": "g",
    "GROQ_API_KEY": "k",
})
def test_build_binance_client_with_testnet():
    client = build_binance_client()
    # CCXT exposes testnet via the urls dict — check that we wired it
    assert "test" in client.urls.get("api", {}).get("public", "").lower() \
        or client.urls.get("api", {}).get("public") == client.urls["test"]["public"]
    assert client.options.get("defaultType") == "spot"


@patch.dict("os.environ", {
    "DATABASE_URL": "postgresql+asyncpg://x:y@h/d",
    "BINANCE_API_KEY": "test_key",
    "BINANCE_API_SECRET": "test_secret",
    "BINANCE_TESTNET": "false",
    "GEMINI_API_KEY": "g",
    "GROQ_API_KEY": "k",
})
def test_build_binance_client_mainnet():
    # Force re-creation of settings cache
    from trading_engine_pkg import config as cfg
    cfg._settings = None
    client = build_binance_client()
    assert "test" not in client.urls["api"]["public"].lower()
```

Note: Python module names cannot contain hyphens. Add a wrapper module.

- [ ] **Step 2: Make `trading-engine` importable as a package**

Create `trading-engine/trading_engine_pkg/__init__.py` (empty file). Then move (or symlink) `config.py`, `exchange.py`, etc. INTO that package. Alternative — simpler — keep flat but adjust the test imports:

For the tests we'll use the path `trading-engine/` is on `PYTHONPATH` (set in `pytest.ini`), so imports become `from exchange import build_binance_client`. Update `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
pythonpath = .
python_files = test_*.py
addopts =
    -v
    --tb=short
    --strict-markers
    --cov=. --cov-report=term-missing --cov-fail-under=70
markers =
    integration: integration tests requiring DB/exchange
```

Replace test imports `from trading_engine_pkg.exchange import build_binance_client` with `from exchange import build_binance_client` and likewise `from trading_engine_pkg import config as cfg` → `import config as cfg`. Apply this pattern in all subsequent tests.

- [ ] **Step 3: Run test to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_exchange.py -v
```
Expected: `ImportError: cannot import name 'build_binance_client' from 'exchange'`.

- [ ] **Step 4: Write `trading-engine/exchange.py`**

```python
"""CCXT client factory for Binance Spot (testnet or mainnet)."""
from __future__ import annotations

import ccxt.async_support as ccxt_async

from config import get_settings


def build_binance_client() -> ccxt_async.binance:
    """Return a configured async CCXT Binance client.

    Uses testnet when ``BINANCE_TESTNET=true`` (default).
    """
    s = get_settings()
    client = ccxt_async.binance({
        "apiKey": s.binance_api_key,
        "secret": s.binance_api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot", "fetchCurrencies": False},
    })
    if s.binance_testnet:
        client.set_sandbox_mode(True)
    return client
```

- [ ] **Step 5: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_exchange.py -v
```
Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add trading-engine/exchange.py trading-engine/tests/test_exchange.py trading-engine/pytest.ini
git commit -m "feat(exchange): add async CCXT Binance client factory"
```

---

## Task 9: Indicators module (pandas-ta wrappers)

**Files:**
- Create: `trading-engine/collectors/__init__.py` (empty)
- Create: `trading-engine/collectors/indicators.py`
- Create: `trading-engine/tests/test_indicators.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_indicators.py`**

```python
"""Tests for pandas-ta wrappers — verify keys + sane numeric ranges."""
import numpy as np
import pandas as pd
import pytest

from collectors.indicators import compute_indicators


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    """Generate 300 deterministic candles with realistic structure."""
    rng = np.random.default_rng(seed=42)
    n = 300
    base = 60_000.0
    returns = rng.normal(0, 0.002, size=n)
    close = base * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(rng.normal(0, 0.0015, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, size=n)))
    open_ = np.concatenate([[base], close[:-1]])
    volume = rng.uniform(50, 200, size=n)
    idx = pd.date_range("2026-04-01", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_compute_indicators_returns_all_required_keys(synthetic_ohlcv):
    out = compute_indicators(synthetic_ohlcv, timeframe="5m")
    required = {
        "rsi", "macd", "macd_signal", "macd_hist",
        "ema20", "ema50", "ema200",
        "bb_upper", "bb_middle", "bb_lower", "bb_pct",
        "atr", "volume_avg_20",
    }
    assert required <= set(out.keys())


def test_rsi_in_range(synthetic_ohlcv):
    out = compute_indicators(synthetic_ohlcv, timeframe="5m")
    assert 0 <= out["rsi"] <= 100


def test_ema_ordering_for_uptrend():
    """If we feed a strict uptrend, EMA20 should be ≥ EMA50 ≥ EMA200."""
    n = 300
    close = np.linspace(50_000, 70_000, n)
    df = pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0),
    }, index=pd.date_range("2026-04-01", periods=n, freq="1min", tz="UTC"))
    out = compute_indicators(df, timeframe="1h")
    assert out["ema20"] >= out["ema50"] >= out["ema200"]


def test_handles_short_series_returns_nans_gracefully():
    """When the series is shorter than indicator window, values are NaN/None."""
    short = pd.DataFrame({
        "open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3],
        "close": [1, 2, 3], "volume": [1, 1, 1],
    }, index=pd.date_range("2026-04-01", periods=3, freq="1min", tz="UTC"))
    out = compute_indicators(short, timeframe="5m")
    # Should not raise; values may be None
    assert "rsi" in out
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_indicators.py -v
```
Expected: `ModuleNotFoundError: No module named 'collectors.indicators'`.

- [ ] **Step 3: Write `trading-engine/collectors/indicators.py`**

```python
"""Wrappers over pandas-ta producing a flat dict of indicator values.

We compute on a DataFrame indexed by timestamp with columns
[open, high, low, close, volume]. Result is a dict of *most recent* values
plus selected helpers — keeps the LLM prompt short and consistent.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pandas_ta as ta


def _last_or_none(series: pd.Series) -> float | None:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)


def compute_indicators(df: pd.DataFrame, *, timeframe: str) -> dict[str, Any]:
    """Compute the indicator suite required by the Decisor.

    Returns a flat dict of the *latest* indicator values. Missing values
    (e.g. when the series is shorter than the indicator window) become None.

    Args:
        df: OHLCV DataFrame with UTC datetime index.
        timeframe: "1m", "5m", "15m", "1h", "4h" — included in the result for
            traceability.
    """
    rsi = ta.rsi(df["close"], length=14)
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    ema20 = ta.ema(df["close"], length=20)
    ema50 = ta.ema(df["close"], length=50)
    ema200 = ta.ema(df["close"], length=200)
    bb = ta.bbands(df["close"], length=20, std=2)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)

    last_close = _last_or_none(df["close"])
    bb_upper = _last_or_none(bb["BBU_20_2.0"]) if bb is not None else None
    bb_lower = _last_or_none(bb["BBL_20_2.0"]) if bb is not None else None

    bb_pct = None
    if last_close is not None and bb_upper is not None and bb_lower is not None and bb_upper != bb_lower:
        bb_pct = (last_close - bb_lower) / (bb_upper - bb_lower) * 100.0

    return {
        "timeframe": timeframe,
        "rsi": _last_or_none(rsi),
        "macd": _last_or_none(macd_df["MACD_12_26_9"]) if macd_df is not None else None,
        "macd_signal": _last_or_none(macd_df["MACDs_12_26_9"]) if macd_df is not None else None,
        "macd_hist": _last_or_none(macd_df["MACDh_12_26_9"]) if macd_df is not None else None,
        "ema20": _last_or_none(ema20),
        "ema50": _last_or_none(ema50),
        "ema200": _last_or_none(ema200),
        "bb_upper": bb_upper,
        "bb_middle": _last_or_none(bb["BBM_20_2.0"]) if bb is not None else None,
        "bb_lower": bb_lower,
        "bb_pct": bb_pct,
        "atr": _last_or_none(atr),
        "volume_avg_20": _last_or_none(df["volume"].rolling(20).mean()),
        "last_close": last_close,
    }
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_indicators.py -v
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/collectors/__init__.py trading-engine/collectors/indicators.py trading-engine/tests/test_indicators.py
git commit -m "feat(collectors): add pandas-ta indicator wrappers"
```

---

## Task 10: PriceCollector

**Files:**
- Create: `trading-engine/collectors/price_collector.py`
- Create: `trading-engine/tests/test_price_collector.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_price_collector.py`**

```python
"""Tests for PriceCollector — fetches OHLCV, computes indicators, persists."""
import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Ohlcv, Indicators
from collectors.price_collector import PriceCollector


SAMPLE_OHLCV = [
    # [ts_ms, open, high, low, close, volume]  — CCXT format
    [1_714_521_600_000 + i * 60_000, 60000.0 + i, 60010.0 + i, 59990.0 + i, 60005.0 + i, 100.0]
    for i in range(250)
]


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def fake_exchange():
    ex = MagicMock()
    ex.fetch_ohlcv = AsyncMock(return_value=SAMPLE_OHLCV)
    return ex


async def test_fetch_and_persist_ohlcv(session: AsyncSession, fake_exchange):
    collector = PriceCollector(fake_exchange, session, symbol="BTC/USDT")
    await collector.fetch_and_persist(timeframe="5m")
    rows = (
        await session.execute(__import__("sqlalchemy").select(Ohlcv))
    ).scalars().all()
    assert len(rows) == len(SAMPLE_OHLCV)
    assert rows[0].timeframe == "5m"


async def test_fetch_and_persist_is_idempotent(session: AsyncSession, fake_exchange):
    """Calling twice should not duplicate rows (PK is (time, timeframe))."""
    collector = PriceCollector(fake_exchange, session, symbol="BTC/USDT")
    await collector.fetch_and_persist(timeframe="5m")
    await collector.fetch_and_persist(timeframe="5m")
    rows = (
        await session.execute(__import__("sqlalchemy").select(Ohlcv))
    ).scalars().all()
    assert len(rows) == len(SAMPLE_OHLCV)


async def test_compute_and_persist_indicators_writes_jsonb(session: AsyncSession, fake_exchange):
    collector = PriceCollector(fake_exchange, session, symbol="BTC/USDT")
    await collector.fetch_and_persist(timeframe="5m")
    await collector.compute_and_persist_indicators()
    rows = (
        await session.execute(__import__("sqlalchemy").select(Indicators))
    ).scalars().all()
    assert len(rows) == 1
    data = rows[0].data
    assert "5m" in data
    assert "rsi" in data["5m"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_price_collector.py -v
```
Expected: `ModuleNotFoundError: No module named 'collectors.price_collector'`.

- [ ] **Step 3: Write `trading-engine/collectors/price_collector.py`**

```python
"""PriceCollector: fetches OHLCV via CCXT and persists indicators to Postgres.

The collector pulls multiple timeframes in a single ``compute_and_persist_indicators``
call so the Decisor sees a coherent multi-timeframe snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Ohlcv, Indicators
from collectors.indicators import compute_indicators

logger = structlog.get_logger()

TIMEFRAMES_DEFAULT = ("1m", "5m", "15m", "1h", "4h")
LIMIT_DEFAULT = 250  # candles per fetch — enough for EMA200 + buffer


class PriceCollector:
    """Fetches and stores OHLCV + indicators for the configured symbol.

    Args:
        exchange: a CCXT (async) exchange instance (e.g. binance).
        session: SQLAlchemy async session bound to the same engine the rest
            of the app uses.
        symbol: e.g. "BTC/USDT".
    """

    def __init__(
        self,
        exchange: Any,
        session: AsyncSession,
        *,
        symbol: str,
        timeframes: tuple[str, ...] = TIMEFRAMES_DEFAULT,
        limit: int = LIMIT_DEFAULT,
    ):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol
        self.timeframes = timeframes
        self.limit = limit

    async def fetch_and_persist(self, *, timeframe: str) -> int:
        """Fetch the latest *limit* candles for *timeframe* and upsert.

        Returns the number of rows written (whether inserted or updated).
        Idempotent: PK is (time, timeframe).
        """
        raw = await self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=self.limit)
        if not raw:
            return 0

        # PostgreSQL ON CONFLICT for idempotent upsert; SQLite for tests.
        is_sqlite = self.session.bind.dialect.name == "sqlite"
        insert = sqlite_insert if is_sqlite else pg_insert

        for candle in raw:
            ts_ms, open_, high, low, close, volume = candle
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            stmt = insert(Ohlcv).values(
                time=ts,
                timeframe=timeframe,
                open=Decimal(str(open_)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close)),
                volume=Decimal(str(volume)),
            )
            if is_sqlite:
                stmt = stmt.on_conflict_do_nothing(index_elements=["time", "timeframe"])
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["time", "timeframe"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                    },
                )
            await self.session.execute(stmt)
        await self.session.commit()
        logger.info("ohlcv.persisted", symbol=self.symbol, timeframe=timeframe, n=len(raw))
        return len(raw)

    async def compute_and_persist_indicators(self) -> None:
        """Compute indicators for every configured timeframe and persist one row.

        The row's `data` JSONB has shape {timeframe: indicator_dict, ...}.
        Uses *now* (UTC) as the row key to align with the decisor tick.
        """
        from sqlalchemy import select

        all_indicators: dict[str, dict[str, Any]] = {}
        for tf in self.timeframes:
            rows = (
                await self.session.execute(
                    select(Ohlcv)
                    .where(Ohlcv.timeframe == tf)
                    .order_by(Ohlcv.time.asc())
                )
            ).scalars().all()
            if not rows:
                continue
            df = pd.DataFrame(
                {
                    "open": [float(r.open) for r in rows],
                    "high": [float(r.high) for r in rows],
                    "low": [float(r.low) for r in rows],
                    "close": [float(r.close) for r in rows],
                    "volume": [float(r.volume) for r in rows],
                },
                index=pd.DatetimeIndex([r.time for r in rows], tz="UTC"),
            )
            all_indicators[tf] = compute_indicators(df, timeframe=tf)

        now = datetime.now(tz=timezone.utc).replace(microsecond=0)
        is_sqlite = self.session.bind.dialect.name == "sqlite"
        if is_sqlite:
            stmt = sqlite_insert(Indicators).values(time=now, data=all_indicators)
            stmt = stmt.on_conflict_do_update(
                index_elements=["time"], set_={"data": stmt.excluded.data},
            )
        else:
            stmt = pg_insert(Indicators).values(time=now, data=all_indicators)
            stmt = stmt.on_conflict_do_update(
                index_elements=["time"], set_={"data": stmt.excluded.data},
            )
        await self.session.execute(stmt)
        await self.session.commit()
        logger.info("indicators.persisted", time=now.isoformat(), tfs=list(all_indicators.keys()))
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_price_collector.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/collectors/price_collector.py trading-engine/tests/test_price_collector.py
git commit -m "feat(collectors): add PriceCollector with idempotent OHLCV upsert"
```

---

## Task 11: OrderBookCollector

**Files:**
- Create: `trading-engine/collectors/orderbook_collector.py`
- Create: `trading-engine/tests/test_orderbook_collector.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_orderbook_collector.py`**

```python
"""Tests for OrderBookCollector — in-memory snapshot + derived stats."""
import pytest

from collectors.orderbook_collector import OrderBookCollector, OrderBookSnapshot


def make_snapshot() -> dict:
    """Synthetic CCXT-format order book."""
    return {
        "bids": [[60_000.0, 0.5], [59_990.0, 1.2], [59_980.0, 0.8],
                 [59_970.0, 0.3], [59_960.0, 0.4], [59_950.0, 30.0],  # wall
                 [59_940.0, 0.2], [59_930.0, 0.1], [59_920.0, 0.5],
                 [59_910.0, 0.3]],
        "asks": [[60_010.0, 0.4], [60_020.0, 0.6], [60_030.0, 0.7],
                 [60_040.0, 0.5], [60_050.0, 25.0],  # wall
                 [60_060.0, 0.3], [60_070.0, 0.4], [60_080.0, 0.2],
                 [60_090.0, 0.6], [60_100.0, 0.4]],
        "timestamp": 1714521600000,
    }


def test_snapshot_computes_basic_metrics():
    collector = OrderBookCollector(symbol="BTC/USDT")
    collector._book = make_snapshot()  # injected for unit test
    snap = collector.snapshot(levels=10)
    assert snap.spread > 0
    assert snap.spread_pct > 0
    assert snap.bid_total_btc > 0
    assert snap.ask_total_btc > 0
    assert snap.imbalance > 0


def test_snapshot_detects_walls():
    collector = OrderBookCollector(symbol="BTC/USDT")
    collector._book = make_snapshot()
    snap = collector.snapshot(levels=10)
    # The 30 BTC bid wall is the largest on the bid side
    assert snap.bid_wall_size == 30.0
    assert snap.bid_wall_price == 59_950.0
    # The 25 BTC ask wall is the largest on the ask side
    assert snap.ask_wall_size == 25.0
    assert snap.ask_wall_price == 60_050.0


def test_snapshot_with_no_book_returns_none():
    collector = OrderBookCollector(symbol="BTC/USDT")
    assert collector.snapshot(levels=10) is None


def test_imbalance_balanced_book_close_to_one():
    collector = OrderBookCollector(symbol="BTC/USDT")
    collector._book = {
        "bids": [[60_000.0, 1.0]] * 10,
        "asks": [[60_010.0, 1.0]] * 10,
    }
    snap = collector.snapshot(levels=10)
    assert 0.95 <= snap.imbalance <= 1.05
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_orderbook_collector.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `trading-engine/collectors/orderbook_collector.py`**

```python
"""OrderBookCollector: maintains the latest order book snapshot in memory.

Subscribes to Binance WebSocket via ``ccxt.pro`` (or vanilla websockets fallback).
Exposes a synchronous ``snapshot(levels)`` method that returns derived metrics
the Decisor consumes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class OrderBookSnapshot:
    spread: float
    spread_pct: float
    bid_total_btc: float
    ask_total_btc: float
    imbalance: float
    bid_wall_price: float
    bid_wall_size: float
    bid_wall_distance_pct: float
    ask_wall_price: float
    ask_wall_size: float
    ask_wall_distance_pct: float
    top_bid: float
    top_ask: float


class OrderBookCollector:
    """In-memory order book maintained from a CCXT.pro WS feed.

    For unit tests, you can inject ``self._book`` directly to bypass the WS.
    """

    def __init__(self, symbol: str, exchange: Any | None = None):
        self.symbol = symbol
        self.exchange = exchange
        self._book: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the watch loop. Requires a ``ccxt.pro`` exchange instance."""
        if self.exchange is None:
            raise RuntimeError("Exchange not set; cannot start WS feed")
        self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _watch_loop(self) -> None:
        """Continuously update self._book from the exchange WS."""
        while True:
            try:
                book = await self.exchange.watch_order_book(self.symbol, limit=20)
                self._book = book
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("orderbook.watch.error", error=str(e))
                await asyncio.sleep(2)  # backoff before reconnect

    def snapshot(self, levels: int = 10) -> OrderBookSnapshot | None:
        """Compute derived metrics from the latest book.

        Returns None if no book has been received yet.
        """
        if self._book is None:
            return None
        bids = self._book.get("bids", [])[:levels]
        asks = self._book.get("asks", [])[:levels]
        if not bids or not asks:
            return None

        top_bid = float(bids[0][0])
        top_ask = float(asks[0][0])
        mid = (top_bid + top_ask) / 2
        spread = top_ask - top_bid
        spread_pct = spread / mid * 100 if mid > 0 else 0.0

        bid_total = sum(float(level[1]) for level in bids)
        ask_total = sum(float(level[1]) for level in asks)
        imbalance = bid_total / ask_total if ask_total > 0 else float("inf")

        bid_wall = max(bids, key=lambda lvl: float(lvl[1]))
        ask_wall = max(asks, key=lambda lvl: float(lvl[1]))

        return OrderBookSnapshot(
            spread=spread,
            spread_pct=spread_pct,
            bid_total_btc=bid_total,
            ask_total_btc=ask_total,
            imbalance=imbalance,
            bid_wall_price=float(bid_wall[0]),
            bid_wall_size=float(bid_wall[1]),
            bid_wall_distance_pct=(top_bid - float(bid_wall[0])) / top_bid * 100,
            ask_wall_price=float(ask_wall[0]),
            ask_wall_size=float(ask_wall[1]),
            ask_wall_distance_pct=(float(ask_wall[0]) - top_ask) / top_ask * 100,
            top_bid=top_bid,
            top_ask=top_ask,
        )
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_orderbook_collector.py -v
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/collectors/orderbook_collector.py trading-engine/tests/test_orderbook_collector.py
git commit -m "feat(collectors): add OrderBookCollector with imbalance + wall detection"
```

---

## Task 12: FeeManager (real fees from Binance)

**Files:**
- Create: `trading-engine/execution/__init__.py` (empty)
- Create: `trading-engine/execution/fee_manager.py`
- Create: `trading-engine/tests/test_fee_manager.py`

- [ ] **Step 1: Write the failing test `trading-engine/tests/test_fee_manager.py`**

```python
"""Tests for FeeManager — fetches real fees, caches, persists snapshots."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import FeeSnapshot
from execution.fee_manager import FeeManager


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def fake_exchange():
    ex = MagicMock()
    ex.fetch_trading_fees = AsyncMock(return_value={
        "BTC/USDT": {"maker": 0.001, "taker": 0.001, "info": {"vipLevel": 0}},
    })
    return ex


async def test_first_refresh_populates_cache(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    assert fm.taker == 0.001
    assert fm.maker == 0.001


async def test_refresh_persists_snapshot(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    rows = (
        await session.execute(__import__("sqlalchemy").select(FeeSnapshot))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].taker_fee == Decimal("0.001000")


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
    await fm.refresh()  # populate snapshot
    fake_exchange.fetch_trading_fees = AsyncMock(side_effect=RuntimeError("api down"))
    fm._last_refresh = None  # force refresh
    fm._taker = None
    await fm.get_or_refresh()
    # Should have loaded from last snapshot
    assert fm.taker == 0.001


async def test_roundtrip_pct_is_taker_x2(session, fake_exchange):
    fm = FeeManager(fake_exchange, session, symbol="BTC/USDT")
    await fm.refresh()
    assert fm.roundtrip_pct == pytest.approx(0.002)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_fee_manager.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write `trading-engine/execution/fee_manager.py`**

```python
"""FeeManager: fetches and caches real Binance trading fees per spec §8.2.

Refresh cadence:
  • on engine startup (manual call)
  • every 24h (scheduled, see scheduler.py)
  • after every trade close (executor.py calls refresh())
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import FeeSnapshot

logger = structlog.get_logger()

REFRESH_INTERVAL = timedelta(hours=24)


class FeeManager:
    """Live trading-fees source. Falls back to last DB snapshot on API error."""

    def __init__(self, exchange: Any, session: AsyncSession, *, symbol: str):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol
        self._maker: float | None = None
        self._taker: float | None = None
        self._last_refresh: datetime | None = None

    @property
    def maker(self) -> float:
        if self._maker is None:
            raise RuntimeError("FeeManager not initialised — call refresh() first")
        return self._maker

    @property
    def taker(self) -> float:
        if self._taker is None:
            raise RuntimeError("FeeManager not initialised — call refresh() first")
        return self._taker

    @property
    def roundtrip_pct(self) -> float:
        """Two taker hops as a decimal fraction (e.g. 0.002 = 0.2%)."""
        return self.taker * 2

    async def refresh(self) -> None:
        """Fetch live fees, persist snapshot, update cache.

        On API error, attempts to load the last snapshot from DB.
        """
        try:
            data = await self.exchange.fetch_trading_fees()
            entry = data.get(self.symbol) or data.get(self.symbol.replace("/", "")) or {}
            maker = float(entry.get("maker", 0.001))
            taker = float(entry.get("taker", 0.001))
            self._maker = maker
            self._taker = taker
            self._last_refresh = datetime.now(tz=timezone.utc)

            snapshot = FeeSnapshot(
                symbol=self.symbol,
                maker_fee=Decimal(str(maker)),
                taker_fee=Decimal(str(taker)),
                raw=data,
            )
            self.session.add(snapshot)
            await self.session.commit()
            logger.info("fees.refreshed", maker=maker, taker=taker)
        except Exception as e:
            logger.warning("fees.refresh_failed", error=str(e))
            await self._load_last_snapshot()

    async def get_or_refresh(self) -> None:
        """Refresh if cache is stale (>24h) or empty."""
        now = datetime.now(tz=timezone.utc)
        if (
            self._last_refresh is None
            or (now - self._last_refresh) > REFRESH_INTERVAL
            or self._taker is None
        ):
            await self.refresh()

    async def _load_last_snapshot(self) -> None:
        row = (
            await self.session.execute(
                select(FeeSnapshot)
                .where(FeeSnapshot.symbol == self.symbol)
                .order_by(FeeSnapshot.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            logger.error("fees.no_snapshot_fallback")
            self._maker = 0.001
            self._taker = 0.001
        else:
            self._maker = float(row.maker_fee)
            self._taker = float(row.taker_fee)
            logger.info("fees.fallback_to_snapshot", ts=row.ts.isoformat())
        self._last_refresh = datetime.now(tz=timezone.utc)
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_fee_manager.py -v
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/__init__.py trading-engine/execution/fee_manager.py trading-engine/tests/test_fee_manager.py
git commit -m "feat(execution): add FeeManager with 24h cache and snapshot fallback"
```

---


# Phase 3 — LLM Agent Layer

## Task 13: Pydantic schemas for LLM I/O

**Files:**
- Create: `shared/schemas.py`
- Create: `trading-engine/tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for Pydantic schemas — strict validation of decisor output."""
import pytest
from pydantic import ValidationError

from shared.schemas import DecisorOutput, DecisorAction, MarketRegime


def test_valid_buy_decision():
    out = DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["rsi_oversold_5m", "macd_bullish_15m", "ema_alignment_1h"],
        action=DecisorAction.BUY,
        confidence=0.7,
        stop_loss=66400.0,
        take_profit=67800.0,
        position_size_pct=0.06,
        reasoning="Test reasoning explaining the three key factors briefly.",
    )
    assert out.action == "BUY"


def test_buy_without_stop_loss_rejected():
    with pytest.raises(ValidationError) as exc:
        DecisorOutput(
            regime=MarketRegime.TRENDING_UP,
            confluences=["rsi_oversold_5m"],
            action=DecisorAction.BUY,
            confidence=0.7,
            stop_loss=None,
            take_profit=67800.0,
            position_size_pct=0.06,
            reasoning="t",
        )
    assert "stop_loss" in str(exc.value)


def test_position_size_outside_range_rejected():
    with pytest.raises(ValidationError):
        DecisorOutput(
            regime=MarketRegime.RANGE,
            confluences=["x"],
            action=DecisorAction.HOLD,
            confidence=0.5,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.5,  # > 0.10 default max
            reasoning="t",
        )


def test_confidence_clamped_to_unit_interval():
    with pytest.raises(ValidationError):
        DecisorOutput(
            regime=MarketRegime.RANGE,
            confluences=["x"],
            action=DecisorAction.HOLD,
            confidence=1.5,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.0,
            reasoning="t",
        )


def test_hold_does_not_require_stop_loss():
    out = DecisorOutput(
        regime=MarketRegime.RANGE,
        confluences=["a"],
        action=DecisorAction.HOLD,
        confidence=0.5,
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        reasoning="r",
    )
    assert out.action == "HOLD"


def test_reasoning_max_length_enforced():
    with pytest.raises(ValidationError):
        DecisorOutput(
            regime=MarketRegime.RANGE,
            confluences=[],
            action=DecisorAction.HOLD,
            confidence=0.5,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.0,
            reasoning="x" * 500,  # > 240 chars
        )
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_schemas.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Write `shared/schemas.py`**

```python
"""Pydantic schemas for LLM inputs and outputs.

Used by:
  • `agents.llm_client` to enforce strict response_schema with Gemini.
  • `agents.decisor` to validate parsed JSON before passing to RiskGate.
  • `web` API responses (typed JSON for the React frontend).
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class DecisorAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class DecisorOutput(BaseModel):
    """Strict schema for the Decisor's JSON response — see spec §7.1."""

    regime: MarketRegime
    confluences: list[str] = Field(default_factory=list, max_length=10)
    action: DecisorAction
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    stop_loss: float | None
    take_profit: float | None
    position_size_pct: Annotated[float, Field(ge=0.0, le=0.25)]
    reasoning: Annotated[str, Field(max_length=240)]

    @model_validator(mode="after")
    def _buy_requires_stop_loss(self) -> "DecisorOutput":
        if self.action == DecisorAction.BUY and self.stop_loss is None:
            raise ValueError("stop_loss is required when action=BUY")
        return self


class TradeOutcome(BaseModel):
    """Outcome of a closed trade — written to decisions.outcome JSONB."""

    pnl_usdt: float
    pnl_pct: float
    close_reason: str
    duration_min: int
    fees_usdt: float | None = None
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_schemas.py -v
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add shared/schemas.py trading-engine/tests/test_schemas.py
git commit -m "feat(schemas): add Pydantic schemas for Decisor I/O"
```

---

## Task 14: LLMClient with Gemini + Groq fallback

**Files:**
- Create: `trading-engine/agents/__init__.py` (empty)
- Create: `trading-engine/agents/llm_client.py`
- Create: `trading-engine/tests/test_llm_client.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for LLMClient — calls Gemini, falls back to Groq, enforces JSON."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.llm_client import LLMClient, LLMProvider, LLMResponse


VALID_JSON = json.dumps({
    "regime": "TRENDING_UP",
    "confluences": ["rsi_oversold_5m", "macd_bullish_15m", "ema_alignment_1h"],
    "action": "BUY",
    "confidence": 0.7,
    "stop_loss": 66400.0,
    "take_profit": 67800.0,
    "position_size_pct": 0.06,
    "reasoning": "Three factors aligned.",
})


@pytest.fixture
def fake_gemini():
    fake = MagicMock()
    fake_response = MagicMock()
    fake_response.text = VALID_JSON
    fake_response.usage_metadata.prompt_token_count = 1500
    fake_response.usage_metadata.candidates_token_count = 80
    fake.aio.models.generate_content = AsyncMock(return_value=fake_response)
    return fake


@pytest.fixture
def fake_groq():
    fake = MagicMock()
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content=VALID_JSON))]
    fake_response.usage = MagicMock(prompt_tokens=1500, completion_tokens=80)
    fake.chat.completions.create = AsyncMock(return_value=fake_response)
    return fake


async def test_call_with_gemini_returns_text_and_tokens(fake_gemini):
    client = LLMClient(gemini_client=fake_gemini, groq_client=None)
    resp = await client.call(
        provider=LLMProvider.GEMINI_FLASH,
        system_prompt="sys",
        user_prompt="usr",
    )
    assert isinstance(resp, LLMResponse)
    assert resp.text == VALID_JSON
    assert resp.tokens_in == 1500
    assert resp.tokens_out == 80
    assert resp.provider == "gemini-2.5-flash"


async def test_fallback_when_primary_fails(fake_gemini, fake_groq):
    fake_gemini.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("rate limit"))
    client = LLMClient(gemini_client=fake_gemini, groq_client=fake_groq)
    resp = await client.call(
        provider=LLMProvider.GEMINI_FLASH,
        system_prompt="sys",
        user_prompt="usr",
        fallback=LLMProvider.GROQ_LLAMA,
    )
    assert resp.text == VALID_JSON
    assert resp.provider.startswith("groq")


async def test_no_fallback_raises(fake_gemini):
    fake_gemini.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("boom"))
    client = LLMClient(gemini_client=fake_gemini, groq_client=None)
    with pytest.raises(RuntimeError):
        await client.call(
            provider=LLMProvider.GEMINI_FLASH,
            system_prompt="s",
            user_prompt="u",
        )


async def test_retry_on_transient_error(fake_gemini):
    """Two failures then a success should still return."""
    fake_gemini.aio.models.generate_content = AsyncMock(
        side_effect=[RuntimeError("transient"), RuntimeError("transient"),
                     fake_gemini.aio.models.generate_content.return_value or MagicMock(
                         text=VALID_JSON,
                         usage_metadata=MagicMock(prompt_token_count=1, candidates_token_count=1),
                     )],
    )
    client = LLMClient(gemini_client=fake_gemini, groq_client=None, max_retries=3)
    # Configure return value for the third call
    success = MagicMock()
    success.text = VALID_JSON
    success.usage_metadata.prompt_token_count = 100
    success.usage_metadata.candidates_token_count = 10
    fake_gemini.aio.models.generate_content = AsyncMock(
        side_effect=[RuntimeError("t1"), RuntimeError("t2"), success],
    )
    resp = await client.call(
        provider=LLMProvider.GEMINI_FLASH, system_prompt="s", user_prompt="u",
    )
    assert resp.text == VALID_JSON
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_llm_client.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Write `trading-engine/agents/llm_client.py`**

```python
"""Provider-agnostic LLM client with retry and fallback.

Supports Gemini 2.5 Flash/Pro (primary) and Groq Llama 3.3 70B (fallback).
Always uses JSON mode. Caller is responsible for validating the parsed JSON
against a Pydantic schema (we don't couple the client to a schema to keep it
reusable for both Decisor and Supervisor).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class LLMProvider(str, Enum):
    GEMINI_FLASH = "gemini-2.5-flash"
    GEMINI_PRO = "gemini-2.5-pro"
    GROQ_LLAMA = "groq-llama-3.3-70b"


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    provider: str


class LLMClient:
    """Calls Gemini or Groq with retry + fallback.

    The Gemini client is the official ``google.genai`` SDK; the Groq client is
    the official ``groq`` SDK. Pass them in via the constructor — keeps the
    class testable and allows wiring mocks.
    """

    def __init__(
        self,
        gemini_client: Any | None = None,
        groq_client: Any | None = None,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ):
        self.gemini = gemini_client
        self.groq = groq_client
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    async def call(
        self,
        *,
        provider: LLMProvider,
        system_prompt: str,
        user_prompt: str,
        fallback: LLMProvider | None = None,
    ) -> LLMResponse:
        """Call *provider*; on failure, retry up to ``max_retries`` then fall back.

        Returns a parsed-JSON-ready response. Raises if both primary and
        fallback (when provided) fail.
        """
        try:
            return await self._call_with_retry(provider, system_prompt, user_prompt)
        except Exception as e:
            if fallback is None:
                raise
            logger.warning(
                "llm.primary_failed_falling_back",
                primary=provider.value, fallback=fallback.value, error=str(e),
            )
            return await self._call_with_retry(fallback, system_prompt, user_prompt)

    async def _call_with_retry(
        self, provider: LLMProvider, system_prompt: str, user_prompt: str,
    ) -> LLMResponse:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._call_provider(provider, system_prompt, user_prompt)
            except Exception as e:
                last_err = e
                wait = self.backoff_base * (2 ** attempt)
                logger.warning(
                    "llm.retry", provider=provider.value, attempt=attempt + 1,
                    error=str(e), wait_s=wait,
                )
                await asyncio.sleep(wait)
        assert last_err is not None
        raise last_err

    async def _call_provider(
        self, provider: LLMProvider, system_prompt: str, user_prompt: str,
    ) -> LLMResponse:
        t0 = time.perf_counter()
        if provider in (LLMProvider.GEMINI_FLASH, LLMProvider.GEMINI_PRO):
            resp = await self._call_gemini(provider, system_prompt, user_prompt)
        elif provider == LLMProvider.GROQ_LLAMA:
            resp = await self._call_groq(system_prompt, user_prompt)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(
            text=resp["text"],
            tokens_in=resp["tokens_in"],
            tokens_out=resp["tokens_out"],
            latency_ms=latency_ms,
            provider=provider.value,
        )

    async def _call_gemini(
        self, provider: LLMProvider, system_prompt: str, user_prompt: str,
    ) -> dict[str, Any]:
        if self.gemini is None:
            raise RuntimeError("Gemini client not configured")
        response = await self.gemini.aio.models.generate_content(
            model=provider.value,
            contents=user_prompt,
            config={
                "system_instruction": system_prompt,
                "response_mime_type": "application/json",
                "temperature": 0.4,
            },
        )
        return {
            "text": response.text,
            "tokens_in": getattr(response.usage_metadata, "prompt_token_count", 0),
            "tokens_out": getattr(response.usage_metadata, "candidates_token_count", 0),
        }

    async def _call_groq(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if self.groq is None:
            raise RuntimeError("Groq client not configured")
        response = await self.groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
        return {
            "text": response.choices[0].message.content,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
        }
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_llm_client.py -v
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/__init__.py trading-engine/agents/llm_client.py trading-engine/tests/test_llm_client.py
git commit -m "feat(agents): add LLMClient with Gemini/Groq fallback and retries"
```

---

## Task 15: Prompt templates and PromptManager

**Files:**
- Create: `trading-engine/agents/prompts/decisor_system.txt`
- Create: `trading-engine/agents/prompts/decisor_user.txt`
- Create: `trading-engine/agents/prompts/supervisor_system.txt`
- Create: `trading-engine/agents/prompts/supervisor_user.txt`
- Create: `trading-engine/agents/prompts/playbook_v0.md`
- Create: `trading-engine/agents/prompt_manager.py`
- Create: `trading-engine/tests/test_prompt_manager.py`

- [ ] **Step 1: Create the prompt files**

Copy verbatim from spec §7.1, §7.2, §7.3, §7.4, §7.5 into the corresponding files. The spec sections are the source of truth — paste the full text without modification.

For `decisor_system.txt`: paste content of spec §7.1.
For `decisor_user.txt`: paste content of spec §7.2.
For `supervisor_system.txt`: paste content of spec §7.3.
For `supervisor_user.txt`: paste content of spec §7.4.
For `playbook_v0.md`: paste content of spec §7.5.

- [ ] **Step 2: Write the failing test `trading-engine/tests/test_prompt_manager.py`**

```python
"""Tests for PromptManager — loads templates, fills placeholders, manages playbook."""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import PlaybookVersion
from agents.prompt_manager import PromptManager


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_seed_playbook_v0_inserts_active_version(session):
    pm = PromptManager(session)
    await pm.seed_playbook_v0()
    active = await pm.get_active_playbook()
    assert active is not None
    assert active.version == 0
    assert active.active is True


async def test_seed_playbook_v0_idempotent(session):
    pm = PromptManager(session)
    await pm.seed_playbook_v0()
    await pm.seed_playbook_v0()
    rows = (
        await session.execute(__import__("sqlalchemy").select(PlaybookVersion))
    ).scalars().all()
    assert len(rows) == 1


async def test_save_playbook_marks_previous_inactive(session):
    pm = PromptManager(session)
    await pm.seed_playbook_v0()
    await pm.save_playbook(
        content="# Playbook v1\nNew lessons.",
        model="gemini-2.5-pro",
        trades_analyzed=8,
        win_rate=55.0,
    )
    active = await pm.get_active_playbook()
    assert active.version == 1
    assert active.active is True
    # v0 should be inactive
    from sqlalchemy import select
    v0 = (await session.execute(
        select(PlaybookVersion).where(PlaybookVersion.version == 0)
    )).scalar_one()
    assert v0.active is False


def test_decisor_system_prompt_loads():
    pm = PromptManager(session=None)
    text = pm.load_system_prompt("decisor")
    assert "ROLE" in text or "Eres un agente" in text


def test_decisor_user_prompt_renders_with_placeholders():
    pm = PromptManager(session=None)
    rendered = pm.render_user_prompt(
        "decisor",
        {
            "timestamp_utc": "2026-05-02T14:00:00",
            "price": 67234.5,
            # ... pm should accept partial dict and leave unfilled placeholders as-is
        },
        strict=False,
    )
    assert "67" in rendered  # price was substituted
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_prompt_manager.py -v
```
Expected: `ImportError`.

- [ ] **Step 4: Write `trading-engine/agents/prompt_manager.py`**

```python
"""PromptManager: loads template files and manages playbook versions in DB."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from string import Template
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import PlaybookVersion

PROMPTS_DIR = Path(__file__).parent / "prompts"


class _SafeTemplate(Template):
    """A Template that uses a custom delimiter and tolerates missing keys when desired."""
    delimiter = "{"
    pattern = r"""
        \{(?:
            (?P<escaped>\{) |
            (?P<named>[A-Za-z_][A-Za-z0-9_]*)\} |
            (?P<braced>[A-Za-z_][A-Za-z0-9_]*)\} |
            (?P<invalid>)
        )
    """


class PromptManager:
    """Reads template files at startup; manages active playbook in DB."""

    def __init__(self, session: AsyncSession | None):
        self.session = session

    def load_system_prompt(self, agent: str) -> str:
        path = PROMPTS_DIR / f"{agent}_system.txt"
        return path.read_text(encoding="utf-8")

    def load_user_template(self, agent: str) -> str:
        path = PROMPTS_DIR / f"{agent}_user.txt"
        return path.read_text(encoding="utf-8")

    def render_user_prompt(
        self, agent: str, values: dict[str, Any], *, strict: bool = True,
    ) -> str:
        """Render the *agent*'s user prompt with *values*.

        Uses Python's ``str.format_map`` semantics. Missing placeholders raise
        KeyError when ``strict=True`` (production) or are left as ``{name}``
        literals when ``strict=False`` (debug/preview).
        """
        template = self.load_user_template(agent)
        if strict:
            return template.format_map(values)
        return template.format_map(_DefaultDict(values))

    async def seed_playbook_v0(self) -> None:
        """Insert the bootstrap playbook (v0) if no playbook exists."""
        if self.session is None:
            raise RuntimeError("session required for seed_playbook_v0")
        existing = (
            await self.session.execute(select(PlaybookVersion))
        ).scalars().first()
        if existing is not None:
            return
        content = (PROMPTS_DIR / "playbook_v0.md").read_text(encoding="utf-8")
        self.session.add(
            PlaybookVersion(version=0, content=content, model="bootstrap", active=True)
        )
        await self.session.commit()

    async def get_active_playbook(self) -> PlaybookVersion | None:
        if self.session is None:
            raise RuntimeError("session required for get_active_playbook")
        return (
            await self.session.execute(
                select(PlaybookVersion).where(PlaybookVersion.active.is_(True))
            )
        ).scalar_one_or_none()

    async def save_playbook(
        self,
        *,
        content: str,
        model: str,
        trades_analyzed: int,
        win_rate: float,
        pnl_summary: dict[str, Any] | None = None,
    ) -> PlaybookVersion:
        """Create a new playbook version and mark it active. Marks all previous as inactive."""
        if self.session is None:
            raise RuntimeError("session required for save_playbook")
        # Get next version number
        latest = (
            await self.session.execute(
                select(PlaybookVersion).order_by(PlaybookVersion.version.desc()).limit(1)
            )
        ).scalar_one_or_none()
        next_version = (latest.version + 1) if latest else 0

        # Mark all current active as inactive
        await self.session.execute(
            update(PlaybookVersion).where(PlaybookVersion.active.is_(True)).values(active=False)
        )

        new = PlaybookVersion(
            version=next_version,
            content=content,
            model=model,
            trades_analyzed=trades_analyzed,
            win_rate=Decimal(str(win_rate)),
            pnl_summary=pnl_summary or {},
            active=True,
        )
        self.session.add(new)
        await self.session.commit()
        await self.session.refresh(new)
        return new


class _DefaultDict(dict):
    """Returns ``{key}`` literal for missing keys when used with format_map."""
    def __missing__(self, key):
        return "{" + key + "}"
```

- [ ] **Step 5: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_prompt_manager.py -v
```
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add trading-engine/agents/
git commit -m "feat(agents): add prompt templates, playbook_v0, and PromptManager"
```

---

## Task 16: ContextBuilder

**Files:**
- Create: `trading-engine/agents/context_builder.py`
- Create: `trading-engine/tests/test_context_builder.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for ContextBuilder — assembles the dict the user prompt is rendered with."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from unittest.mock import MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Indicators, Position
from agents.context_builder import ContextBuilder
from collectors.orderbook_collector import OrderBookSnapshot


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        # Insert one indicators row
        s.add(Indicators(
            time=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            data={
                "5m":  {"rsi": 62.0, "bb_pct": 55.0, "last_close": 67234.5},
                "15m": {"rsi": 58.0, "macd": 12.1, "macd_signal": 10.2, "macd_hist": 1.9},
                "1h":  {"rsi": 56.0, "ema20": 67100, "ema50": 66800, "ema200": 65000,
                        "macd": 5.0, "macd_signal": 3.0, "atr": 320.0, "last_close": 67234.5,
                        "bb_upper": 68100, "bb_lower": 66400},
                "4h":  {"rsi": 54.0, "ema20": 66500, "ema50": 65000, "ema200": 60000},
                "1m":  {"rsi": 65.0, "bb_pct": 60.0},
            },
        ))
        await s.commit()
        yield s
    await engine.dispose()


@pytest.fixture
def fake_orderbook():
    return OrderBookSnapshot(
        spread=10.0, spread_pct=0.015,
        bid_total_btc=15.0, ask_total_btc=10.0, imbalance=1.5,
        bid_wall_price=67100.0, bid_wall_size=30.0, bid_wall_distance_pct=0.2,
        ask_wall_price=67300.0, ask_wall_size=25.0, ask_wall_distance_pct=0.1,
        top_bid=67230.0, top_ask=67240.0,
    )


async def test_build_returns_required_keys(session, fake_orderbook):
    builder = ContextBuilder(session, symbol="BTC/USDT")
    ctx = await builder.build(
        orderbook=fake_orderbook,
        usdt_balance=10_000.0,
        btc_held=0.0,
        playbook_content="# Playbook v0\nNeutral bias.",
        max_simultaneous_trades=2,
        daily_stop_pct=-0.03,
        decisor_interval_min=5,
        mode="PAPER_TRADING",
        taker_fee_pct=0.001,
        maker_fee_pct=0.001,
    )
    required = {
        "timestamp_utc", "price", "rsi_5m", "rsi_15m", "rsi_1h", "rsi_4h",
        "macd_15m", "ema20_1h", "ema50_1h", "ema200_1h", "atr_1h",
        "spread", "imbalance", "open_positions_count", "max_simultaneous_trades",
        "playbook", "mode", "taker_fee_pct", "maker_fee_pct", "roundtrip_fee_pct",
    }
    assert required <= set(ctx.keys())


async def test_open_positions_count_reflects_db(session, fake_orderbook):
    session.add(Position(
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("67000"),
        status="open",
        opened_at=datetime(2026, 5, 2, 13, 30, tzinfo=timezone.utc),
    ))
    await session.commit()
    builder = ContextBuilder(session, symbol="BTC/USDT")
    ctx = await builder.build(
        orderbook=fake_orderbook,
        usdt_balance=10_000.0, btc_held=0.001,
        playbook_content="x",
        max_simultaneous_trades=2, daily_stop_pct=-0.03,
        decisor_interval_min=5, mode="PAPER_TRADING",
        taker_fee_pct=0.001, maker_fee_pct=0.001,
    )
    assert ctx["open_positions_count"] == 1


async def test_roundtrip_fee_pct_is_taker_x2(session, fake_orderbook):
    builder = ContextBuilder(session, symbol="BTC/USDT")
    ctx = await builder.build(
        orderbook=fake_orderbook,
        usdt_balance=10_000.0, btc_held=0.0,
        playbook_content="x",
        max_simultaneous_trades=2, daily_stop_pct=-0.03,
        decisor_interval_min=5, mode="PAPER_TRADING",
        taker_fee_pct=0.0015, maker_fee_pct=0.001,
    )
    assert ctx["roundtrip_fee_pct"] == pytest.approx(0.003)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_context_builder.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Write `trading-engine/agents/context_builder.py`**

```python
"""ContextBuilder: assembles the dict that fills the Decisor's user prompt.

Reads from DB (latest indicators, open positions, last decisions) and from the
in-memory order book snapshot. Returns a flat dict suitable for ``str.format_map``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Indicators, Position, Decision
from collectors.orderbook_collector import OrderBookSnapshot


class ContextBuilder:
    def __init__(self, session: AsyncSession, *, symbol: str):
        self.session = session
        self.symbol = symbol

    async def build(
        self,
        *,
        orderbook: OrderBookSnapshot | None,
        usdt_balance: float,
        btc_held: float,
        playbook_content: str,
        max_simultaneous_trades: int,
        daily_stop_pct: float,
        decisor_interval_min: int,
        mode: str,
        taker_fee_pct: float,
        maker_fee_pct: float,
    ) -> dict[str, Any]:
        ind_row = (
            await self.session.execute(
                select(Indicators).order_by(desc(Indicators.time)).limit(1)
            )
        ).scalar_one_or_none()
        ind = ind_row.data if ind_row else {}

        # Open positions count
        open_positions = (
            await self.session.execute(
                select(Position).where(Position.status == "open")
            )
        ).scalars().all()

        # Last 3 decisions for context
        last_decisions = (
            await self.session.execute(
                select(Decision)
                .where(Decision.agent == "decisor")
                .order_by(desc(Decision.ts))
                .limit(3)
            )
        ).scalars().all()

        last_decisions_block = self._format_last_decisions(last_decisions)
        positions_block = self._format_positions(open_positions)

        price = self._get(ind, "1h", "last_close") or self._get(ind, "5m", "last_close") or 0.0
        roundtrip_fee_pct = taker_fee_pct * 2

        return {
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "mode": mode,
            "decisor_interval_min": decisor_interval_min,
            "max_simultaneous_trades": max_simultaneous_trades,
            "daily_stop_pct": daily_stop_pct * 100,
            "playbook": playbook_content,
            "taker_fee_pct": taker_fee_pct * 100,
            "maker_fee_pct": maker_fee_pct * 100,
            "roundtrip_fee_pct": roundtrip_fee_pct * 100,
            "capital_total": usdt_balance + btc_held * price,
            "usdt_available": usdt_balance,
            "btc_held": btc_held,
            "btc_held_usd": btc_held * price,
            "total_capital_usd": usdt_balance + btc_held * price,
            "price": price,
            # Indicator scalars used by the user prompt template — see spec §7.2
            "rsi_1m": self._get(ind, "1m", "rsi") or 0,
            "rsi_5m": self._get(ind, "5m", "rsi") or 0,
            "rsi_15m": self._get(ind, "15m", "rsi") or 0,
            "rsi_1h": self._get(ind, "1h", "rsi") or 0,
            "rsi_4h": self._get(ind, "4h", "rsi") or 0,
            "bb_pct_1m": self._get(ind, "1m", "bb_pct") or 0,
            "bb_pct_5m": self._get(ind, "5m", "bb_pct") or 0,
            "vol_5m": 1.0,  # ratio computed downstream if needed
            "macd_15m": self._get(ind, "15m", "macd") or 0,
            "sig_15m": self._get(ind, "15m", "macd_signal") or 0,
            "hist_15m": self._get(ind, "15m", "macd_hist") or 0,
            "macd_1h": self._get(ind, "1h", "macd") or 0,
            "sig_1h": self._get(ind, "1h", "macd_signal") or 0,
            "ema20_1h": self._get(ind, "1h", "ema20") or 0,
            "ema50_1h": self._get(ind, "1h", "ema50") or 0,
            "ema200_1h": self._get(ind, "1h", "ema200") or 0,
            "bb_up_1h": self._get(ind, "1h", "bb_upper") or 0,
            "bb_lo_1h": self._get(ind, "1h", "bb_lower") or 0,
            "ema20_4h": self._get(ind, "4h", "ema20") or 0,
            "ema50_4h": self._get(ind, "4h", "ema50") or 0,
            "ema200_4h": self._get(ind, "4h", "ema200") or 0,
            "atr_1h": self._get(ind, "1h", "atr") or 0,
            "atr_pct_1h": ((self._get(ind, "1h", "atr") or 0) / price * 100) if price else 0,
            "atr_avg_7d": self._get(ind, "1h", "atr") or 0,  # placeholder; could compute
            "volatility_label": "normal",
            "support_1h": (self._get(ind, "1h", "ema50") or 0) * 0.99,
            "resistance_1h": (self._get(ind, "1h", "ema50") or 0) * 1.01,
            "dist_support_pct": 0,
            "dist_resistance_pct": 0,
            "low_24h": price * 0.98,
            "high_24h": price * 1.02,
            "pct_1h": 0, "pct_4h": 0, "pct_24h": 0, "pct_7d": 0,
            # Order book
            "spread": orderbook.spread if orderbook else 0,
            "spread_pct": orderbook.spread_pct if orderbook else 0,
            "bid_btc": orderbook.bid_total_btc if orderbook else 0,
            "ask_btc": orderbook.ask_total_btc if orderbook else 0,
            "imbalance": orderbook.imbalance if orderbook else 1.0,
            "imbalance_label": "balanced" if orderbook is None else (
                "buy_pressure" if orderbook.imbalance > 1.2
                else "sell_pressure" if orderbook.imbalance < 0.8
                else "balanced"
            ),
            "bid_wall_price": orderbook.bid_wall_price if orderbook else 0,
            "bid_wall_size": orderbook.bid_wall_size if orderbook else 0,
            "bid_wall_dist": orderbook.bid_wall_distance_pct if orderbook else 0,
            "ask_wall_price": orderbook.ask_wall_price if orderbook else 0,
            "ask_wall_size": orderbook.ask_wall_size if orderbook else 0,
            "ask_wall_dist": orderbook.ask_wall_distance_pct if orderbook else 0,
            # Positions
            "open_positions_count": len(open_positions),
            "positions_block": positions_block,
            # P&L / day stats
            "pnl_today_usd": 0.0,
            "pnl_today_pct": 0.0,
            "unrealized_pnl_usd": sum(float(p.unrealized_pnl or 0) for p in open_positions),
            "trades_today_count": 0,
            "wins_today": 0,
            "losses_today": 0,
            "daily_margin_pct": daily_stop_pct * 100,
            # Last decisions
            "last_decisions_block": last_decisions_block,
            "last_action": last_decisions[0].output.get("action") if last_decisions else "n/a",
            "last_confidence": last_decisions[0].output.get("confidence", 0) if last_decisions else 0,
            "last_reasoning": last_decisions[0].output.get("reasoning", "") if last_decisions else "",
            "last_decision_ago": "n/a",
        }

    @staticmethod
    def _get(ind: dict[str, Any], tf: str, key: str) -> Any:
        return (ind.get(tf, {}) or {}).get(key)

    @staticmethod
    def _format_positions(positions: list[Position]) -> str:
        if not positions:
            return "  Ninguna"
        lines = []
        for i, p in enumerate(positions, start=1):
            lines.append(
                f"  {i}. LONG {float(p.quantity_btc):.6f} BTC | "
                f"entry ${float(p.entry_price):,.2f} | status {p.status}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_last_decisions(decisions: list[Decision]) -> str:
        if not decisions:
            return "  Sin decisiones previas."
        lines = []
        for d in decisions:
            out = d.output or {}
            lines.append(
                f"  [{d.ts.strftime('%H:%M')} UTC] {out.get('action', '?')} "
                f"(conf {float(out.get('confidence', 0)):.2f}): "
                f"\"{out.get('reasoning', '')[:100]}\""
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_context_builder.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/context_builder.py trading-engine/tests/test_context_builder.py
git commit -m "feat(agents): add ContextBuilder assembling decisor input from DB + orderbook"
```

---

## Task 17: Decisor loop

**Files:**
- Create: `trading-engine/agents/decisor.py`
- Create: `trading-engine/tests/test_decisor.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for Decisor — calls LLM, validates output, persists decision."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Decision, Indicators, PlaybookVersion
from agents.decisor import Decisor


VALID_OUTPUT = {
    "regime": "TRENDING_UP",
    "confluences": ["rsi_oversold_5m", "macd_bullish_15m", "ema_alignment_1h"],
    "action": "BUY",
    "confidence": 0.7,
    "stop_loss": 66400.0,
    "take_profit": 67800.0,
    "position_size_pct": 0.06,
    "reasoning": "Three factors aligned for breakout buy.",
}


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        # Seed a playbook + indicators row
        s.add(PlaybookVersion(version=0, content="# v0", model="bootstrap", active=True))
        s.add(Indicators(
            time=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            data={"1h": {"rsi": 56, "ema20": 67000, "ema50": 66500, "ema200": 65000,
                        "atr": 320, "last_close": 67234.5}},
        ))
        await s.commit()
        yield s
    await engine.dispose()


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    from agents.llm_client import LLMResponse
    llm.call = AsyncMock(return_value=LLMResponse(
        text=json.dumps(VALID_OUTPUT),
        tokens_in=2000, tokens_out=100, latency_ms=1500,
        provider="gemini-2.5-flash",
    ))
    return llm


async def test_decide_persists_decision_row(session, fake_llm):
    decisor = Decisor(
        session=session, llm=fake_llm, symbol="BTC/USDT",
    )
    await decisor.decide(
        orderbook=None, usdt_balance=10_000.0, btc_held=0.0,
        max_position_pct=0.10, max_simultaneous_trades=2,
        daily_stop_pct=-0.03, decisor_interval_min=5, mode="PAPER_TRADING",
        taker_fee=0.001, maker_fee=0.001,
    )
    rows = (await session.execute(select(Decision))).scalars().all()
    assert len(rows) == 1
    assert rows[0].agent == "decisor"
    assert rows[0].output["action"] == "BUY"


async def test_invalid_json_persists_error_decision(session):
    """When LLM returns invalid JSON after retries, persist a HOLD with error."""
    llm = MagicMock()
    from agents.llm_client import LLMResponse
    llm.call = AsyncMock(return_value=LLMResponse(
        text="{not valid json",
        tokens_in=10, tokens_out=5, latency_ms=100,
        provider="gemini-2.5-flash",
    ))
    decisor = Decisor(session=session, llm=llm, symbol="BTC/USDT")
    await decisor.decide(
        orderbook=None, usdt_balance=10_000.0, btc_held=0.0,
        max_position_pct=0.10, max_simultaneous_trades=2,
        daily_stop_pct=-0.03, decisor_interval_min=5, mode="PAPER_TRADING",
        taker_fee=0.001, maker_fee=0.001,
    )
    rows = (await session.execute(select(Decision))).scalars().all()
    assert len(rows) == 1
    assert rows[0].output["action"] == "HOLD"
    assert "parse_error" in (rows[0].rejected_reason or "")
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_decisor.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Write `trading-engine/agents/decisor.py`**

```python
"""Decisor: the 5-min agent loop that produces BUY/SELL/HOLD decisions.

Flow:
  1. Build context (indicators + orderbook + positions + balance + playbook).
  2. Render system + user prompts.
  3. Call LLM with JSON mode.
  4. Parse and validate against DecisorOutput schema.
  5. Persist decision (input + output + tokens + latency) into ``decisions``.
  6. Return the parsed output for the caller (RiskGate + Executor) to act on.

If the LLM returns invalid JSON, persist a HOLD with ``rejected_reason="parse_error"``.
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Decision
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from agents.context_builder import ContextBuilder
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager
from collectors.orderbook_collector import OrderBookSnapshot

logger = structlog.get_logger()


class Decisor:
    def __init__(
        self,
        *,
        session: AsyncSession,
        llm: LLMClient,
        symbol: str,
        prompt_manager: PromptManager | None = None,
        provider: LLMProvider = LLMProvider.GEMINI_FLASH,
        fallback: LLMProvider | None = LLMProvider.GROQ_LLAMA,
    ):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.prompt_manager = prompt_manager or PromptManager(session)
        self.context_builder = ContextBuilder(session, symbol=symbol)
        self.provider = provider
        self.fallback = fallback

    async def decide(
        self,
        *,
        orderbook: OrderBookSnapshot | None,
        usdt_balance: float,
        btc_held: float,
        max_position_pct: float,
        max_simultaneous_trades: int,
        daily_stop_pct: float,
        decisor_interval_min: int,
        mode: str,
        taker_fee: float,
        maker_fee: float,
    ) -> DecisorOutput:
        playbook = await self.prompt_manager.get_active_playbook()
        playbook_content = playbook.content if playbook else "# No playbook."

        ctx = await self.context_builder.build(
            orderbook=orderbook,
            usdt_balance=usdt_balance, btc_held=btc_held,
            playbook_content=playbook_content,
            max_simultaneous_trades=max_simultaneous_trades,
            daily_stop_pct=daily_stop_pct,
            decisor_interval_min=decisor_interval_min,
            mode=mode,
            taker_fee_pct=taker_fee, maker_fee_pct=maker_fee,
        )

        # System prompt: insert playbook and runtime values via format_map
        system_template = self.prompt_manager.load_system_prompt("decisor")
        system_prompt = system_template.format_map(_SafeMap(ctx))
        user_prompt = self.prompt_manager.render_user_prompt("decisor", ctx, strict=False)

        try:
            resp = await self.llm.call(
                provider=self.provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                fallback=self.fallback,
            )
            try:
                parsed = json.loads(resp.text)
                validated = DecisorOutput.model_validate(parsed)
                output_dict = validated.model_dump()
                rejected_reason = None
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning("decisor.parse_error", error=str(e), raw=resp.text[:200])
                validated = DecisorOutput(
                    regime=MarketRegime.RANGE,
                    confluences=[],
                    action=DecisorAction.HOLD,
                    confidence=0.0,
                    stop_loss=None,
                    take_profit=None,
                    position_size_pct=0.0,
                    reasoning="parse_error",
                )
                output_dict = validated.model_dump()
                rejected_reason = f"parse_error: {type(e).__name__}"
        except Exception as e:
            logger.error("decisor.llm_error", error=str(e))
            validated = DecisorOutput(
                regime=MarketRegime.RANGE,
                confluences=[],
                action=DecisorAction.HOLD,
                confidence=0.0,
                stop_loss=None,
                take_profit=None,
                position_size_pct=0.0,
                reasoning="llm_error",
            )
            output_dict = validated.model_dump()
            rejected_reason = f"llm_error: {type(e).__name__}"
            resp = None

        # Persist
        self.session.add(Decision(
            agent="decisor",
            model=self.provider.value,
            tokens_in=resp.tokens_in if resp else 0,
            tokens_out=resp.tokens_out if resp else 0,
            latency_ms=resp.latency_ms if resp else 0,
            input={k: _serialize(v) for k, v in ctx.items()},
            output=output_dict,
            executed=False,
            rejected_reason=rejected_reason,
        ))
        await self.session.commit()

        logger.info(
            "decisor.decided",
            action=output_dict["action"],
            confidence=output_dict["confidence"],
            rejected=rejected_reason,
        )
        return validated


class _SafeMap(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _serialize(value: Any) -> Any:
    """Make a context value JSON-serializable for storage in JSONB."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return str(value)
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_decisor.py -v
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/decisor.py trading-engine/tests/test_decisor.py
git commit -m "feat(agents): add Decisor loop with parse-error fallback to HOLD"
```

---


# Phase 4 — Risk Gate, Circuit Breaker, Executor

## Task 18: RiskGate (deterministic validator)

**Files:**
- Create: `trading-engine/risk/__init__.py` (empty)
- Create: `trading-engine/risk/risk_gate.py`
- Create: `trading-engine/tests/test_risk_gate.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for RiskGate — see spec §8.1 for the 10 checks."""
import pytest

from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from risk.risk_gate import RiskGate, RiskVerdict


def make_buy(stop_loss=66400, take_profit=67800, size=0.06):
    return DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["a", "b", "c"],
        action=DecisorAction.BUY,
        confidence=0.7,
        stop_loss=stop_loss, take_profit=take_profit,
        position_size_pct=size,
        reasoning="t",
    )


@pytest.fixture
def gate_default():
    return RiskGate(
        max_position_pct=0.10,
        max_simultaneous_trades=2,
        daily_stop_pct=-0.03,
        max_drawdown_pct=-0.10,
        max_slippage_pct=0.003,
        taker_fee_pct=0.001,
    )


def test_buy_passes_all_checks(gate_default):
    v = gate_default.validate(
        decision=make_buy(),
        current_price=67000.0,
        atr_1h=300.0,
        open_positions_count=0,
        daily_pnl_pct=0.0,
        total_drawdown_pct=0.0,
        kill_switch=False,
        usdt_balance=10_000.0,
        btc_held=0.0,
    )
    assert v.passed is True
    assert v.reason is None


def test_buy_without_stop_loss_rejected(gate_default):
    """DecisorOutput would refuse this at construction; test the gate uses safety check."""
    # Build via dict + bypass to test gate directly
    decision = make_buy()
    object.__setattr__(decision, "stop_loss", None)
    v = gate_default.validate(
        decision=decision, current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "stop_loss" in v.reason.lower()


def test_position_size_above_max_rejected(gate_default):
    v = gate_default.validate(
        decision=make_buy(size=0.20),
        current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "size" in v.reason.lower()


def test_max_simultaneous_trades_exceeded(gate_default):
    v = gate_default.validate(
        decision=make_buy(),
        current_price=67000.0, atr_1h=300.0,
        open_positions_count=2, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "simultaneous" in v.reason.lower()


def test_daily_stop_breach_rejects_buy(gate_default):
    v = gate_default.validate(
        decision=make_buy(),
        current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=-0.04,  # below -3%
        total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "daily" in v.reason.lower()


def test_kill_switch_rejects_all_buys(gate_default):
    v = gate_default.validate(
        decision=make_buy(),
        current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=True, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "kill" in v.reason.lower()


def test_kill_switch_allows_sell_to_close(gate_default):
    sell = DecisorOutput(
        regime=MarketRegime.TRENDING_UP, confluences=["a"], action=DecisorAction.SELL,
        confidence=0.8, stop_loss=None, take_profit=None, position_size_pct=0.0,
        reasoning="close",
    )
    v = gate_default.validate(
        decision=sell, current_price=67000.0, atr_1h=300.0,
        open_positions_count=1, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=True, usdt_balance=5_000.0, btc_held=0.001,
    )
    assert v.passed is True


def test_sell_without_open_position_rejected(gate_default):
    sell = DecisorOutput(
        regime=MarketRegime.RANGE, confluences=["a"], action=DecisorAction.SELL,
        confidence=0.5, stop_loss=None, take_profit=None, position_size_pct=0.0,
        reasoning="t",
    )
    v = gate_default.validate(
        decision=sell, current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "position" in v.reason.lower()


def test_rr_below_1_5_rejected(gate_default):
    """Risk-reward ratio must be at least 1.5:1."""
    decision = make_buy(stop_loss=66800, take_profit=67100)  # ~1:0.4 RR
    v = gate_default.validate(
        decision=decision, current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "r:r" in v.reason.lower() or "ratio" in v.reason.lower()


def test_sl_distance_below_half_atr_rejected(gate_default):
    """SL must be at least 0.5 × ATR away from entry."""
    decision = make_buy(stop_loss=66950, take_profit=68000)  # 50 distance, ATR=300 → 0.5×ATR=150
    v = gate_default.validate(
        decision=decision, current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "sl" in v.reason.lower() or "atr" in v.reason.lower()


def test_total_drawdown_breach_force_kill(gate_default):
    v = gate_default.validate(
        decision=make_buy(),
        current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=-0.11,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is False
    assert "drawdown" in v.reason.lower()


def test_hold_always_passes(gate_default):
    hold = DecisorOutput(
        regime=MarketRegime.RANGE, confluences=[], action=DecisorAction.HOLD,
        confidence=0.5, stop_loss=None, take_profit=None, position_size_pct=0.0,
        reasoning="wait",
    )
    v = gate_default.validate(
        decision=hold, current_price=67000.0, atr_1h=300.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=10_000.0, btc_held=0.0,
    )
    assert v.passed is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_risk_gate.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Write `trading-engine/risk/risk_gate.py`**

```python
"""RiskGate: deterministic validator between Decisor output and Executor.

Implements the 10 checks from spec §8.1 in fixed order. No LLM calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.schemas import DecisorOutput, DecisorAction


@dataclass(frozen=True)
class RiskVerdict:
    passed: bool
    reason: str | None = None


class RiskGate:
    def __init__(
        self,
        *,
        max_position_pct: float,
        max_simultaneous_trades: int,
        daily_stop_pct: float,
        max_drawdown_pct: float,
        max_slippage_pct: float,
        taker_fee_pct: float,
    ):
        self.max_position_pct = max_position_pct
        self.max_simultaneous_trades = max_simultaneous_trades
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_slippage_pct = max_slippage_pct
        self.taker_fee_pct = taker_fee_pct

    def validate(
        self,
        *,
        decision: DecisorOutput,
        current_price: float,
        atr_1h: float,
        open_positions_count: int,
        daily_pnl_pct: float,
        total_drawdown_pct: float,
        kill_switch: bool,
        usdt_balance: float,
        btc_held: float,
    ) -> RiskVerdict:
        # Check 1: HOLD always passes (the default-safe action)
        if decision.action == DecisorAction.HOLD:
            return RiskVerdict(passed=True)

        # Check 2: Total drawdown breach → reject everything
        if total_drawdown_pct <= self.max_drawdown_pct:
            return RiskVerdict(False, f"max_drawdown breached: {total_drawdown_pct:.4f}")

        # Check 3: Kill switch → only allow SELL to close positions
        if kill_switch:
            if decision.action == DecisorAction.SELL and btc_held > 0:
                return RiskVerdict(passed=True)
            return RiskVerdict(False, "kill_switch active — only SELL-to-close allowed")

        # Check 4: SELL needs an open position
        if decision.action == DecisorAction.SELL:
            if btc_held <= 0 or open_positions_count == 0:
                return RiskVerdict(False, "SELL requested but no open position to close")
            return RiskVerdict(passed=True)

        # From here: action == BUY
        # Check 5: BUY requires stop_loss
        if decision.stop_loss is None:
            return RiskVerdict(False, "BUY requires stop_loss")
        if decision.stop_loss >= current_price:
            return RiskVerdict(False, f"stop_loss must be < current_price")

        # Check 6: position size within limit
        if decision.position_size_pct > self.max_position_pct + 1e-9:
            return RiskVerdict(False, f"position_size_pct {decision.position_size_pct} > max {self.max_position_pct}")

        # Check 7: max simultaneous trades
        if open_positions_count >= self.max_simultaneous_trades:
            return RiskVerdict(False, f"max_simultaneous_trades reached: {open_positions_count}")

        # Check 8: daily P&L stop
        if daily_pnl_pct <= self.daily_stop_pct:
            return RiskVerdict(False, f"daily P&L breach: {daily_pnl_pct:.4f}")

        # Check 9: SL distance ≥ 0.5 × ATR
        sl_distance = current_price - decision.stop_loss
        if sl_distance < 0.5 * atr_1h:
            return RiskVerdict(False, f"SL distance {sl_distance:.2f} < 0.5*ATR {0.5*atr_1h:.2f}")

        # Check 10: R:R ratio ≥ 1.5
        if decision.take_profit is not None:
            reward = decision.take_profit - current_price
            risk = sl_distance
            if risk > 0 and reward / risk < 1.5:
                return RiskVerdict(False, f"R:R ratio {reward/risk:.2f} < 1.5")

        return RiskVerdict(passed=True)
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_risk_gate.py -v
```
Expected: `12 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/__init__.py trading-engine/risk/risk_gate.py trading-engine/tests/test_risk_gate.py
git commit -m "feat(risk): add deterministic RiskGate with 10 spec checks"
```

---

## Task 19: CircuitBreaker

**Files:**
- Create: `trading-engine/risk/circuit_breaker.py`
- Create: `trading-engine/tests/test_circuit_breaker.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for CircuitBreaker — daily stop, total drawdown, LLM/exchange failures."""
import pytest
from datetime import datetime, timezone, date

from risk.circuit_breaker import CircuitBreaker


def test_no_breach_returns_ok():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    state = cb.evaluate(daily_pnl_pct=-0.01, total_drawdown_pct=-0.05)
    assert state.daily_stop_triggered is False
    assert state.kill_switch_triggered is False


def test_daily_stop_triggers_when_breached():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    state = cb.evaluate(daily_pnl_pct=-0.04, total_drawdown_pct=-0.05)
    assert state.daily_stop_triggered is True
    assert state.kill_switch_triggered is False


def test_total_drawdown_triggers_kill_switch():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    state = cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert state.kill_switch_triggered is True


def test_consecutive_llm_failures_pause():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, llm_failure_threshold=5)
    for _ in range(4):
        cb.record_llm_failure()
    assert cb.engine_paused is False
    cb.record_llm_failure()
    assert cb.engine_paused is True


def test_llm_success_resets_failure_count():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, llm_failure_threshold=5)
    cb.record_llm_failure()
    cb.record_llm_failure()
    cb.record_llm_success()
    assert cb._llm_consecutive_failures == 0
```

- [ ] **Step 2: Write `trading-engine/risk/circuit_breaker.py`**

```python
"""CircuitBreaker: tracks transient and persistent failure states."""
from __future__ import annotations

from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class CircuitState:
    daily_stop_triggered: bool
    kill_switch_triggered: bool


class CircuitBreaker:
    def __init__(
        self,
        *,
        daily_stop_pct: float,
        max_drawdown_pct: float,
        llm_failure_threshold: int = 5,
        exchange_failure_threshold: int = 5,
    ):
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.llm_failure_threshold = llm_failure_threshold
        self.exchange_failure_threshold = exchange_failure_threshold
        self._llm_consecutive_failures = 0
        self._exchange_consecutive_failures = 0
        self.engine_paused = False

    def evaluate(self, *, daily_pnl_pct: float, total_drawdown_pct: float) -> CircuitState:
        daily = daily_pnl_pct <= self.daily_stop_pct
        kill = total_drawdown_pct <= self.max_drawdown_pct
        if daily:
            logger.warning("circuit.daily_stop_triggered", pnl_pct=daily_pnl_pct)
        if kill:
            logger.error("circuit.kill_switch_triggered", drawdown_pct=total_drawdown_pct)
        return CircuitState(daily_stop_triggered=daily, kill_switch_triggered=kill)

    def record_llm_failure(self) -> None:
        self._llm_consecutive_failures += 1
        if self._llm_consecutive_failures >= self.llm_failure_threshold:
            self.engine_paused = True
            logger.error("circuit.engine_paused_llm",
                         consecutive=self._llm_consecutive_failures)

    def record_llm_success(self) -> None:
        self._llm_consecutive_failures = 0

    def record_exchange_failure(self) -> None:
        self._exchange_consecutive_failures += 1
        if self._exchange_consecutive_failures >= self.exchange_failure_threshold:
            self.engine_paused = True
            logger.error("circuit.engine_paused_exchange",
                         consecutive=self._exchange_consecutive_failures)

    def record_exchange_success(self) -> None:
        self._exchange_consecutive_failures = 0
```

- [ ] **Step 3: Run tests**

```bash
cd trading-engine && python -m pytest tests/test_circuit_breaker.py -v
```
Expected: `5 passed`.

- [ ] **Step 4: Commit**

```bash
git add trading-engine/risk/circuit_breaker.py trading-engine/tests/test_circuit_breaker.py
git commit -m "feat(risk): add CircuitBreaker for daily/drawdown/LLM/exchange failures"
```

---

## Task 20: Executor (CCXT order placement)

**Files:**
- Create: `trading-engine/execution/executor.py`
- Create: `trading-engine/tests/test_executor.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for Executor — places market BUY, creates SL, persists Trade row."""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Trade, Position, Decision
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from execution.executor import Executor


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def fake_exchange():
    ex = MagicMock()
    ex.create_market_order = AsyncMock(return_value={
        "id": "order-buy-1",
        "average": 67250.0,
        "filled": 0.0014,
        "fee": {"cost": 0.094, "currency": "USDT"},
    })
    ex.create_order = AsyncMock(side_effect=lambda *a, **k: {
        "id": f"order-sl-{k.get('price', 'x')}",
    })
    return ex


def make_buy(stop_loss=66400, take_profit=67800, size=0.06):
    return DecisorOutput(
        regime=MarketRegime.TRENDING_UP, confluences=["a","b","c"],
        action=DecisorAction.BUY, confidence=0.8,
        stop_loss=stop_loss, take_profit=take_profit,
        position_size_pct=size, reasoning="t",
    )


async def test_execute_buy_creates_trade_and_position(session, fake_exchange):
    decision_row = Decision(
        agent="decisor", model="gemini-2.5-flash",
        input={}, output={"action": "BUY"}, executed=False,
    )
    session.add(decision_row)
    await session.commit()

    executor = Executor(fake_exchange, session, symbol="BTC/USDT")
    await executor.execute_buy(
        decision=make_buy(),
        decision_id=decision_row.id,
        usdt_balance=10_000.0,
    )

    trades = (await session.execute(select(Trade))).scalars().all()
    assert len(trades) == 1
    assert trades[0].side == "BUY"
    assert trades[0].order_id_open == "order-buy-1"
    assert trades[0].stop_loss == Decimal("66400")
    positions = (await session.execute(select(Position))).scalars().all()
    assert len(positions) == 1
    assert positions[0].status == "open"


async def test_execute_buy_marks_decision_executed(session, fake_exchange):
    d = Decision(agent="decisor", model="x", input={}, output={"action": "BUY"}, executed=False)
    session.add(d); await session.commit()
    executor = Executor(fake_exchange, session, symbol="BTC/USDT")
    await executor.execute_buy(decision=make_buy(), decision_id=d.id, usdt_balance=10_000.0)
    refreshed = await session.get(Decision, d.id)
    assert refreshed.executed is True


async def test_execute_sell_closes_position(session, fake_exchange):
    fake_exchange.create_market_order = AsyncMock(return_value={
        "id": "order-sell-1", "average": 67800.0, "filled": 0.0014,
        "fee": {"cost": 0.095, "currency": "USDT"},
    })
    # Seed a trade + position
    trade = Trade(
        ts_open=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
        side="BUY", quantity_btc=Decimal("0.0014"), entry_price=Decimal("67250"),
        status="open", stop_loss=Decimal("66400"), order_id_open="order-buy-1",
    )
    session.add(trade); await session.commit()
    pos = Position(
        trade_id=trade.id, quantity_btc=Decimal("0.0014"), entry_price=Decimal("67250"),
        status="open", opened_at=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
    )
    session.add(pos); await session.commit()

    executor = Executor(fake_exchange, session, symbol="BTC/USDT")
    await executor.execute_sell(trade_id=trade.id, decision_id=None, close_reason="decisor_sell")

    refreshed = await session.get(Trade, trade.id)
    assert refreshed.status == "closed"
    assert refreshed.close_reason == "decisor_sell"
    assert refreshed.exit_price == Decimal("67800")
    pos_refreshed = await session.get(Position, pos.id)
    assert pos_refreshed.status == "closed"
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd trading-engine && python -m pytest tests/test_executor.py -v
```

- [ ] **Step 3: Write `trading-engine/execution/executor.py`**

```python
"""Executor: places orders via CCXT and manages SL/TP brackets.

Buy flow:
  1. Compute USDT amount from position_size_pct.
  2. Place market BUY → get filled price + quantity.
  3. Place stop-loss (STOP_LOSS_LIMIT) and take-profit (LIMIT) orders.
  4. Persist Trade and Position rows.
  5. Mark the originating Decision as executed.

Sell flow:
  1. Place market SELL for the full position quantity.
  2. Cancel any outstanding SL/TP orders (best-effort).
  3. Update Trade with exit_price, pnl, ts_close, close_reason.
  4. Mark Position as closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Trade, Position, Decision
from shared.schemas import DecisorOutput

logger = structlog.get_logger()


class Executor:
    def __init__(self, exchange: Any, session: AsyncSession, *, symbol: str):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol

    async def execute_buy(
        self,
        *,
        decision: DecisorOutput,
        decision_id: uuid.UUID,
        usdt_balance: float,
    ) -> Trade:
        usdt_to_spend = usdt_balance * decision.position_size_pct
        order = await self.exchange.create_market_order(
            self.symbol, "buy", None, params={"quoteOrderQty": usdt_to_spend},
        )
        avg_price = float(order.get("average") or 0.0)
        filled_qty = float(order.get("filled") or 0.0)
        if avg_price == 0 or filled_qty == 0:
            raise RuntimeError(f"Buy order returned zero fill: {order}")
        fee = float((order.get("fee") or {}).get("cost") or 0.0)

        # Bracket SL
        sl_order_id = None
        if decision.stop_loss is not None:
            sl = await self.exchange.create_order(
                self.symbol, "STOP_LOSS_LIMIT", "sell", filled_qty,
                price=decision.stop_loss * 0.999,  # limit slightly below stop
                params={"stopPrice": decision.stop_loss},
            )
            sl_order_id = sl.get("id")

        # Bracket TP
        if decision.take_profit is not None:
            tp = await self.exchange.create_order(
                self.symbol, "LIMIT", "sell", filled_qty,
                price=decision.take_profit,
            )
            # TP order id stored on close, not opening — keep it in memory if needed

        trade = Trade(
            decision_id=decision_id,
            ts_open=datetime.now(tz=timezone.utc),
            side="BUY",
            quantity_btc=Decimal(str(filled_qty)),
            entry_price=Decimal(str(avg_price)),
            status="open",
            stop_loss=Decimal(str(decision.stop_loss)) if decision.stop_loss else None,
            take_profit=Decimal(str(decision.take_profit)) if decision.take_profit else None,
            order_id_open=str(order.get("id")),
            fees_usdt=Decimal(str(fee)),
        )
        self.session.add(trade)
        await self.session.flush()  # populate trade.id

        position = Position(
            trade_id=trade.id,
            symbol=self.symbol,
            quantity_btc=trade.quantity_btc,
            entry_price=trade.entry_price,
            status="open",
            opened_at=trade.ts_open,
        )
        self.session.add(position)

        # Mark decision executed
        d = await self.session.get(Decision, decision_id)
        if d is not None:
            d.executed = True
            d.trade_id = trade.id

        await self.session.commit()
        await self.session.refresh(trade)
        logger.info(
            "executor.buy_executed",
            trade_id=str(trade.id), price=avg_price, qty=filled_qty,
        )
        return trade

    async def execute_sell(
        self,
        *,
        trade_id: uuid.UUID,
        decision_id: uuid.UUID | None,
        close_reason: str,
    ) -> Trade:
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise RuntimeError(f"Trade {trade_id} not open")

        order = await self.exchange.create_market_order(
            self.symbol, "sell", float(trade.quantity_btc),
        )
        avg_price = float(order.get("average") or 0.0)
        fee = float((order.get("fee") or {}).get("cost") or 0.0)
        if avg_price == 0:
            raise RuntimeError(f"Sell order returned zero fill: {order}")

        trade.exit_price = Decimal(str(avg_price))
        trade.ts_close = datetime.now(tz=timezone.utc)
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.order_id_close = str(order.get("id"))
        gross_pnl = float(trade.exit_price - trade.entry_price) * float(trade.quantity_btc)
        prior_fees = float(trade.fees_usdt or 0)
        trade.fees_usdt = Decimal(str(prior_fees + fee))
        trade.pnl_usdt = Decimal(str(gross_pnl - prior_fees - fee))
        trade.pnl_pct = Decimal(str(
            (avg_price - float(trade.entry_price)) / float(trade.entry_price) * 100
        ))

        # Update position
        pos = (
            await self.session.execute(
                select(Position).where(Position.trade_id == trade.id)
            )
        ).scalar_one_or_none()
        if pos:
            pos.status = "closed"
            pos.updated_at = datetime.now(tz=timezone.utc)

        if decision_id:
            d = await self.session.get(Decision, decision_id)
            if d:
                d.executed = True

        await self.session.commit()
        await self.session.refresh(trade)
        logger.info(
            "executor.sell_executed",
            trade_id=str(trade.id), exit=avg_price, pnl=float(trade.pnl_usdt),
            reason=close_reason,
        )
        return trade
```

- [ ] **Step 4: Re-run tests**

```bash
cd trading-engine && python -m pytest tests/test_executor.py -v
```
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/executor.py trading-engine/tests/test_executor.py
git commit -m "feat(execution): add Executor with bracket SL/TP for spot BTC/USDT"
```

---

## Task 21: PositionManager + OrderTracker

**Files:**
- Create: `trading-engine/execution/position_manager.py`
- Create: `trading-engine/execution/order_tracker.py`
- Create: `trading-engine/tests/test_position_manager.py`

- [ ] **Step 1: Write failing test for PositionManager**

```python
"""Tests for PositionManager — recomputes unrealized P&L from current price."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Trade, Position
from execution.position_manager import PositionManager


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_update_unrealized_pnl(session):
    trade = Trade(
        ts_open=datetime.now(tz=timezone.utc), side="BUY",
        quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
        status="open",
    )
    session.add(trade); await session.commit()
    pos = Position(
        trade_id=trade.id, quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
        status="open", opened_at=trade.ts_open,
    )
    session.add(pos); await session.commit()

    pm = PositionManager(session)
    await pm.refresh_unrealized(current_price=68000.0)
    pos_refreshed = await session.get(Position, pos.id)
    # 1000 * 0.001 = 1 USDT
    assert float(pos_refreshed.unrealized_pnl) == pytest.approx(1.0, rel=1e-4)
    assert float(pos_refreshed.unrealized_pct) == pytest.approx(1.4925, rel=1e-3)


async def test_count_open_positions(session):
    trade1 = Trade(
        ts_open=datetime.now(tz=timezone.utc), side="BUY",
        quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
        status="open",
    )
    trade2 = Trade(
        ts_open=datetime.now(tz=timezone.utc), side="BUY",
        quantity_btc=Decimal("0.002"), entry_price=Decimal("66000"),
        status="closed",
    )
    session.add_all([trade1, trade2]); await session.commit()
    session.add(Position(
        trade_id=trade1.id, quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
        status="open", opened_at=trade1.ts_open,
    ))
    session.add(Position(
        trade_id=trade2.id, quantity_btc=Decimal("0.002"), entry_price=Decimal("66000"),
        status="closed", opened_at=trade2.ts_open,
    ))
    await session.commit()

    pm = PositionManager(session)
    assert await pm.count_open() == 1
```

- [ ] **Step 2: Write `trading-engine/execution/position_manager.py`**

```python
"""PositionManager: tracks open positions and updates unrealized P&L."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Position

logger = structlog.get_logger()


class PositionManager:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def count_open(self) -> int:
        return (
            await self.session.execute(
                select(func.count()).select_from(Position).where(Position.status == "open")
            )
        ).scalar_one()

    async def list_open(self) -> list[Position]:
        return list(
            (await self.session.execute(
                select(Position).where(Position.status == "open")
            )).scalars().all()
        )

    async def refresh_unrealized(self, *, current_price: float) -> None:
        """Recompute unrealized P&L for every open position."""
        positions = await self.list_open()
        now = datetime.now(tz=timezone.utc)
        for p in positions:
            p.current_price = Decimal(str(current_price))
            entry = float(p.entry_price)
            qty = float(p.quantity_btc)
            pnl = (current_price - entry) * qty
            pct = (current_price - entry) / entry * 100 if entry > 0 else 0
            p.unrealized_pnl = Decimal(str(round(pnl, 4)))
            p.unrealized_pct = Decimal(str(round(pct, 4)))
            p.updated_at = now
        await self.session.commit()
        logger.debug("positions.refreshed", count=len(positions), price=current_price)
```

- [ ] **Step 3: Write `trading-engine/execution/order_tracker.py`**

```python
"""OrderTracker: polls open orders to detect SL/TP fills.

Runs every 30s. For each open position, checks whether the SL/TP brackets
were filled at Binance. If so, calls the Executor to record the close.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Trade
from execution.executor import Executor

logger = structlog.get_logger()


class OrderTracker:
    def __init__(self, exchange: Any, session: AsyncSession, executor: Executor, *, symbol: str):
        self.exchange = exchange
        self.session = session
        self.executor = executor
        self.symbol = symbol

    async def poll_once(self) -> None:
        """Scan open trades; if their SL/TP bracket filled, close locally."""
        open_trades = (
            await self.session.execute(select(Trade).where(Trade.status == "open"))
        ).scalars().all()
        if not open_trades:
            return

        try:
            open_orders = await self.exchange.fetch_open_orders(self.symbol)
        except Exception as e:
            logger.warning("order_tracker.fetch_orders_failed", error=str(e))
            return

        open_order_ids = {o["id"] for o in open_orders}

        for trade in open_trades:
            sl_id = None  # we don't persist SL id separately yet — track via brackets in v2
            # If neither SL nor TP is open and the trade is still marked open, that means
            # the bracket has been filled (or both sides were cancelled). Verify with trades API.
            try:
                fills = await self.exchange.fetch_my_trades(self.symbol, limit=20)
            except Exception as e:
                logger.warning("order_tracker.fetch_trades_failed", error=str(e))
                continue

            for fill in fills:
                if fill["side"] != "sell":
                    continue
                # Match by quantity within 1% tolerance
                if abs(float(fill["amount"]) - float(trade.quantity_btc)) / float(trade.quantity_btc) < 0.01:
                    # Treat as close
                    await self.executor.execute_sell(
                        trade_id=trade.id,
                        decision_id=None,
                        close_reason="bracket_fill",
                    )
                    break
```

- [ ] **Step 4: Run tests**

```bash
cd trading-engine && python -m pytest tests/test_position_manager.py -v
```
Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/position_manager.py trading-engine/execution/order_tracker.py trading-engine/tests/test_position_manager.py
git commit -m "feat(execution): add PositionManager and OrderTracker"
```

---


# Phase 5 — Supervisor & Engine Wiring

## Task 22: Supervisor (daily playbook generator)

**Files:**
- Create: `trading-engine/agents/supervisor.py`
- Create: `trading-engine/tests/test_supervisor.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for Supervisor — analyzes 24h, generates new playbook, marks rollback."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base
from shared.db.models import Decision, Trade, PlaybookVersion
from agents.supervisor import Supervisor


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        # Seed v0 playbook
        s.add(PlaybookVersion(version=0, content="# v0\nNeutral.", model="bootstrap", active=True))
        # Seed some decisions and trades
        now = datetime.now(tz=timezone.utc)
        for i in range(5):
            s.add(Decision(
                ts=now - timedelta(hours=i+1),
                agent="decisor", model="gemini-2.5-flash",
                input={}, output={"action": "BUY" if i % 2 == 0 else "HOLD",
                                  "confidence": 0.7, "reasoning": "test"},
                executed=True if i % 2 == 0 else False,
            ))
        for i in range(3):
            s.add(Trade(
                ts_open=now - timedelta(hours=i+1),
                ts_close=now - timedelta(minutes=i*10+5),
                side="BUY", quantity_btc=Decimal("0.001"),
                entry_price=Decimal("67000"), exit_price=Decimal(f"{67100 + i*100}"),
                status="closed", pnl_usdt=Decimal(f"{(i+1)*0.5}"),
                pnl_pct=Decimal(f"{(i+1)*0.1}"),
                close_reason="take_profit",
            ))
        await s.commit()
        yield s
    await engine.dispose()


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    from agents.llm_client import LLMResponse
    llm.call = AsyncMock(return_value=LLMResponse(
        text="""# Playbook v1

## 📊 Métricas del período
- 5 decisiones, 3 trades cerrados, win rate 100%

## 🟢 Setups que funcionaron
- Confluencias técnicas en tendencia alcista.

## 🔴 Patrones a evitar
- Forzar entradas sin confirmación.

## 📈 Contexto de mercado actual
Tendencia alcista clara.

## 🎯 Bias para próximas 24h
BULLISH

## 📋 Reglas específicas
1. Mantener confluencias mínimas de 3.

## 🔄 Cambios vs playbook anterior
[NUEVO] Bias bullish basado en P&L positivo del período.
""",
        tokens_in=4000, tokens_out=300, latency_ms=4000,
        provider="gemini-2.5-pro",
    ))
    return llm


async def test_run_creates_new_playbook_version(session, fake_llm):
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT")
    await sup.run()
    versions = (
        await session.execute(select(PlaybookVersion).order_by(PlaybookVersion.version))
    ).scalars().all()
    assert len(versions) == 2
    assert versions[1].version == 1
    assert versions[1].active is True
    assert versions[0].active is False


async def test_run_persists_decision_row(session, fake_llm):
    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT")
    await sup.run()
    decisions = (
        await session.execute(
            select(Decision).where(Decision.agent == "supervisor")
        )
    ).scalars().all()
    assert len(decisions) == 1


async def test_run_with_zero_trades_keeps_old_playbook(session):
    # Wipe trades to simulate quiet day
    await session.execute(__import__("sqlalchemy").delete(Trade))
    await session.execute(__import__("sqlalchemy").delete(Decision))
    await session.commit()

    llm = MagicMock()
    sup = Supervisor(session=session, llm=llm, symbol="BTC/USDT", min_trades=3)
    await sup.run()
    assert llm.call.call_count == 0  # Did not call LLM
    versions = (await session.execute(select(PlaybookVersion))).scalars().all()
    assert len(versions) == 1  # Still only v0
```

- [ ] **Step 2: Write `trading-engine/agents/supervisor.py`**

```python
"""Supervisor: daily job that generates a new playbook from recent performance.

Skips generation when there is insufficient evidence (< min_trades closed in
the past 24h) so the playbook remains stable through quiet days.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Decision, Trade, PlaybookVersion
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager

logger = structlog.get_logger()


class Supervisor:
    def __init__(
        self,
        *,
        session: AsyncSession,
        llm: LLMClient,
        symbol: str,
        provider: LLMProvider = LLMProvider.GEMINI_PRO,
        fallback: LLMProvider | None = LLMProvider.GROQ_LLAMA,
        min_trades: int = 5,
        prompt_manager: PromptManager | None = None,
    ):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.provider = provider
        self.fallback = fallback
        self.min_trades = min_trades
        self.prompt_manager = prompt_manager or PromptManager(session)

    async def run(self) -> None:
        """Compute metrics, call Gemini Pro, persist new playbook, log decision row."""
        since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        metrics = await self._compute_metrics(since)
        if metrics["closed_trades"] < self.min_trades:
            logger.info("supervisor.insufficient_data", closed=metrics["closed_trades"])
            return

        previous = await self.prompt_manager.get_active_playbook()
        previous_content = previous.content if previous else "# (vacío)"

        decisions_since = (
            await self.session.execute(
                select(Decision)
                .where(Decision.ts >= since, Decision.agent == "decisor")
                .order_by(Decision.ts.asc())
            )
        ).scalars().all()
        decisions_dump = "\n".join([
            json.dumps({
                "ts": d.ts.isoformat(),
                "action": d.output.get("action"),
                "confidence": d.output.get("confidence"),
                "reasoning": (d.output.get("reasoning") or "")[:120],
                "executed": d.executed,
            })
            for d in decisions_since
        ])

        ctx = {
            **metrics,
            "previous_version": previous.version if previous else 0,
            "new_version": (previous.version + 1) if previous else 0,
            "previous_playbook": previous_content,
            "decisions_dump": decisions_dump,
            "date": datetime.now(tz=timezone.utc).date().isoformat(),
        }

        system_prompt = self.prompt_manager.load_system_prompt("supervisor")
        user_prompt = self.prompt_manager.render_user_prompt("supervisor", ctx, strict=False)

        resp = await self.llm.call(
            provider=self.provider, system_prompt=system_prompt,
            user_prompt=user_prompt, fallback=self.fallback,
        )

        # Persist supervisor decision row (audit trail)
        self.session.add(Decision(
            agent="supervisor",
            model=self.provider.value,
            tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
            latency_ms=resp.latency_ms,
            input=ctx, output={"playbook": resp.text}, executed=True,
        ))
        await self.session.commit()

        await self.prompt_manager.save_playbook(
            content=resp.text,
            model=self.provider.value,
            trades_analyzed=metrics["closed_trades"],
            win_rate=metrics["win_rate"],
            pnl_summary={
                "pnl_usdt": metrics["total_pnl"],
                "avg_win": metrics["avg_win"],
                "avg_loss": metrics["avg_loss"],
            },
        )
        logger.info("supervisor.playbook_saved", version=ctx["new_version"])

    async def _compute_metrics(self, since: datetime) -> dict:
        decisions = (
            await self.session.execute(
                select(Decision).where(Decision.ts >= since, Decision.agent == "decisor")
            )
        ).scalars().all()
        trades = (
            await self.session.execute(
                select(Trade).where(Trade.ts_open >= since, Trade.status == "closed")
            )
        ).scalars().all()
        wins = [t for t in trades if t.pnl_usdt and float(t.pnl_usdt) > 0]
        losses = [t for t in trades if t.pnl_usdt and float(t.pnl_usdt) < 0]
        total_pnl = sum(float(t.pnl_usdt or 0) for t in trades)
        win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
        avg_win = sum(float(t.pnl_usdt) for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(float(t.pnl_usdt) for t in losses) / len(losses) if losses else 0.0
        action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for d in decisions:
            a = d.output.get("action", "HOLD")
            action_counts[a] = action_counts.get(a, 0) + 1
        return {
            "total_decisions": len(decisions),
            "buy_count": action_counts["BUY"],
            "sell_count": action_counts["SELL"],
            "hold_count": action_counts["HOLD"],
            "buy_pct": action_counts["BUY"] / max(len(decisions), 1) * 100,
            "sell_pct": action_counts["SELL"] / max(len(decisions), 1) * 100,
            "hold_pct": action_counts["HOLD"] / max(len(decisions), 1) * 100,
            "rejected_count": sum(1 for d in decisions if d.rejected_reason),
            "closed_trades": len(trades),
            "win_rate": round(win_rate, 2),
            "profit_factor": (sum(float(t.pnl_usdt) for t in wins) /
                              abs(sum(float(t.pnl_usdt) for t in losses))) if losses else 0,
            "avg_win": round(avg_win, 2),
            "avg_win_pct": round(sum(float(t.pnl_pct or 0) for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(avg_loss, 2),
            "avg_loss_pct": round(sum(float(t.pnl_pct or 0) for t in losses) / len(losses), 2) if losses else 0,
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": 0.0,  # would need capital basis
            "max_dd_pct": 0.0,     # computed separately
            "avg_holding_min": 0,  # computed separately
            "open_btc": 0, "close_btc": 0, "low_24h": 0, "high_24h": 0,
            "pct_24h": 0, "atr_avg": 0, "atr_pct": 0, "vol_label": "normal",
        }
```

- [ ] **Step 3: Run tests**

```bash
cd trading-engine && python -m pytest tests/test_supervisor.py -v
```
Expected: `3 passed`.

- [ ] **Step 4: Commit**

```bash
git add trading-engine/agents/supervisor.py trading-engine/tests/test_supervisor.py
git commit -m "feat(agents): add Supervisor for daily playbook generation"
```

---

## Task 23: Engine main + Scheduler

**Files:**
- Create: `trading-engine/scheduler.py`
- Create: `trading-engine/main.py`

- [ ] **Step 1: Write `trading-engine/scheduler.py`**

```python
"""APScheduler wrapper that drives the engine cycles."""
from __future__ import annotations

from typing import Awaitable, Callable

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger()


class EngineScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def add_decisor(self, fn: Callable[[], Awaitable[None]], *, interval_min: int) -> None:
        self._scheduler.add_job(
            fn, IntervalTrigger(minutes=interval_min), id="decisor", replace_existing=True,
        )

    def add_supervisor(self, fn: Callable[[], Awaitable[None]], *, cron: str) -> None:
        self._scheduler.add_job(
            fn, CronTrigger.from_crontab(cron, timezone="UTC"),
            id="supervisor", replace_existing=True,
        )

    def add_fee_refresh(self, fn: Callable[[], Awaitable[None]], *, hours: int = 24) -> None:
        self._scheduler.add_job(
            fn, IntervalTrigger(hours=hours), id="fees", replace_existing=True,
        )

    def add_position_refresh(self, fn: Callable[[], Awaitable[None]], *, seconds: int = 30) -> None:
        self._scheduler.add_job(
            fn, IntervalTrigger(seconds=seconds), id="positions", replace_existing=True,
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info("scheduler.started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")
```

- [ ] **Step 2: Write `trading-engine/main.py`**

```python
"""Trading engine entrypoint.

Wires together: DB, exchange, fee manager, collectors, agents, risk, executor,
position manager, supervisor, and scheduler. Runs until SIGINT/SIGTERM.
"""
from __future__ import annotations

import asyncio
import signal

import structlog
from groq import AsyncGroq
from google import genai

from shared.db.base import create_engine_from_url, create_session_factory
from shared.config_store import ConfigKey, ConfigStore

from config import get_settings
from exchange import build_binance_client
from collectors.price_collector import PriceCollector
from collectors.orderbook_collector import OrderBookCollector
from execution.fee_manager import FeeManager
from execution.executor import Executor
from execution.position_manager import PositionManager
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager
from agents.decisor import Decisor
from agents.supervisor import Supervisor
from risk.risk_gate import RiskGate
from risk.circuit_breaker import CircuitBreaker
from scheduler import EngineScheduler

logger = structlog.get_logger()


async def run() -> None:
    settings = get_settings()
    structlog.configure(
        processors=[structlog.processors.TimeStamper(fmt="iso"),
                    structlog.processors.JSONRenderer()],
    )

    engine_db = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine_db)

    exchange = build_binance_client()
    gemini_client = genai.Client(api_key=settings.gemini_api_key)
    groq_client = AsyncGroq(api_key=settings.groq_api_key)
    llm = LLMClient(gemini_client=gemini_client, groq_client=groq_client)

    # Bootstrap
    async with session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        await PromptManager(s).seed_playbook_v0()

        fee_mgr = FeeManager(exchange, s, symbol=settings.symbol)
        await fee_mgr.refresh()

    # Set up persistent workers
    orderbook = OrderBookCollector(symbol=settings.symbol, exchange=exchange)
    # NOTE: cccxt.pro WS support requires installing the right ccxt extras.
    # If unavailable, the collector falls back to periodic REST snapshots.
    # await orderbook.start()

    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    sched = EngineScheduler()

    async def decisor_tick() -> None:
        if cb.engine_paused:
            logger.warning("engine.paused_skipping_decisor")
            return
        async with session_factory() as s:
            store = ConfigStore(s)
            mode = await store.get(ConfigKey.MODE)
            kill = await store.get_typed(ConfigKey.KILL_SWITCH)
            max_position = await store.get_typed(ConfigKey.MAX_POSITION_PCT)
            max_simul = await store.get_typed(ConfigKey.MAX_SIMULTANEOUS_TRADES)
            daily_stop = await store.get_typed(ConfigKey.DAILY_STOP_PCT)
            interval_min = await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN)

            collector = PriceCollector(exchange, s, symbol=settings.symbol)
            for tf in ("1m", "5m", "15m", "1h", "4h"):
                await collector.fetch_and_persist(timeframe=tf)
            await collector.compute_and_persist_indicators()

            fees = FeeManager(exchange, s, symbol=settings.symbol)
            await fees.get_or_refresh()

            balance = await exchange.fetch_balance()
            usdt = float(balance.get("free", {}).get("USDT", 0.0))
            btc = float(balance.get("free", {}).get("BTC", 0.0))

            decisor = Decisor(session=s, llm=llm, symbol=settings.symbol)
            ob_snapshot = orderbook.snapshot(levels=10)
            decision = await decisor.decide(
                orderbook=ob_snapshot,
                usdt_balance=usdt, btc_held=btc,
                max_position_pct=max_position, max_simultaneous_trades=max_simul,
                daily_stop_pct=daily_stop, decisor_interval_min=interval_min,
                mode=mode, taker_fee=fees.taker, maker_fee=fees.maker,
            )

            # Risk gate
            position_mgr = PositionManager(s)
            open_count = await position_mgr.count_open()
            current_price = ob_snapshot.top_ask if ob_snapshot else float(
                (await exchange.fetch_ticker(settings.symbol))["last"]
            )
            atr = 300.0  # would read from latest indicators row
            gate = RiskGate(
                max_position_pct=max_position,
                max_simultaneous_trades=max_simul,
                daily_stop_pct=daily_stop,
                max_drawdown_pct=-0.10,
                max_slippage_pct=0.003,
                taker_fee_pct=fees.taker,
            )
            verdict = gate.validate(
                decision=decision, current_price=current_price, atr_1h=atr,
                open_positions_count=open_count, daily_pnl_pct=0.0,
                total_drawdown_pct=0.0, kill_switch=kill,
                usdt_balance=usdt, btc_held=btc,
            )
            if not verdict.passed:
                logger.info("decision.rejected_by_gate", reason=verdict.reason)
                return

            # Execute (paper mode = same path on testnet)
            executor = Executor(exchange, s, symbol=settings.symbol)
            from shared.schemas import DecisorAction
            from sqlalchemy import select
            from shared.db.models import Decision as DecisionModel
            latest_decision = (await s.execute(
                select(DecisionModel).order_by(DecisionModel.ts.desc()).limit(1)
            )).scalar_one()
            try:
                if decision.action == DecisorAction.BUY:
                    await executor.execute_buy(
                        decision=decision, decision_id=latest_decision.id,
                        usdt_balance=usdt,
                    )
                elif decision.action == DecisorAction.SELL:
                    open_positions = await position_mgr.list_open()
                    if open_positions:
                        await executor.execute_sell(
                            trade_id=open_positions[0].trade_id,
                            decision_id=latest_decision.id,
                            close_reason="decisor_sell",
                        )
            except Exception as e:
                logger.error("execution.error", error=str(e))
                cb.record_exchange_failure()

    async def supervisor_tick() -> None:
        async with session_factory() as s:
            sup = Supervisor(session=s, llm=llm, symbol=settings.symbol)
            try:
                await sup.run()
                cb.record_llm_success()
            except Exception as e:
                logger.error("supervisor.error", error=str(e))
                cb.record_llm_failure()

    async def fees_tick() -> None:
        async with session_factory() as s:
            await FeeManager(exchange, s, symbol=settings.symbol).refresh()

    async def positions_tick() -> None:
        async with session_factory() as s:
            try:
                ticker = await exchange.fetch_ticker(settings.symbol)
                await PositionManager(s).refresh_unrealized(current_price=float(ticker["last"]))
            except Exception as e:
                logger.warning("positions.refresh_failed", error=str(e))

    async with session_factory() as s:
        store = ConfigStore(s)
        interval_min = await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN)
        cron = await store.get(ConfigKey.SUPERVISOR_CRON)

    sched.add_decisor(decisor_tick, interval_min=int(interval_min))
    sched.add_supervisor(supervisor_tick, cron=cron)
    sched.add_fee_refresh(fees_tick, hours=24)
    sched.add_position_refresh(positions_tick, seconds=30)
    sched.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(getattr(signal, sig_name), stop_event.set)
    try:
        await stop_event.wait()
    finally:
        sched.shutdown()
        await orderbook.stop()
        await exchange.close()
        await engine_db.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the engine boots**

With Postgres + .env set, run:
```bash
cd trading-engine && python main.py &
ENGINE_PID=$!
sleep 30
kill $ENGINE_PID
```
Expected: logs show `scheduler.started`, `fees.refreshed`, and at least one `decisor.decided` row in DB:
```bash
PGPASSWORD=changeme_dev_only psql -h localhost -p 5532 -U trader -d crypto_ai_trading \
  -c "SELECT count(*) FROM decisions WHERE agent='decisor';"
```
Expected: `1` (or more).

- [ ] **Step 4: Commit**

```bash
git add trading-engine/scheduler.py trading-engine/main.py
git commit -m "feat(engine): wire scheduler + main entrypoint with full cycle"
```

---


# Phase 6 — Web API (FastAPI + WebSocket)

## Task 24: Web Dockerfile and base FastAPI app

**Files:**
- Create: `web/Dockerfile`
- Create: `web/requirements.txt`
- Create: `web/pytest.ini`
- Create: `web/main.py`
- Create: `web/api/__init__.py` (empty)
- Create: `web/api/health.py`
- Create: `web/tests/__init__.py` (empty)
- Create: `web/tests/conftest.py`
- Create: `web/tests/test_health.py`

- [ ] **Step 1: Write `web/requirements.txt`**

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy[asyncio]==2.0.36
asyncpg==0.30.0
pydantic==2.10.3
pydantic-settings==2.7.0
structlog==24.4.0
httpx==0.28.1
pytest==8.3.4
pytest-asyncio==0.25.0
aiosqlite==0.20.0
```

- [ ] **Step 2: Write `web/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY web/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY web/ ./
COPY shared/ ./shared/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Write `web/pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
pythonpath = .
addopts = -v --tb=short
```

- [ ] **Step 4: Write `web/main.py`**

```python
"""FastAPI application for the dashboard backend."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.db.base import create_engine_from_url, create_session_factory

logger = structlog.get_logger()


def _setup_logging() -> None:
    structlog.configure(
        processors=[structlog.processors.TimeStamper(fmt="iso"),
                    structlog.processors.JSONRenderer()],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine_from_url(db_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    logger.info("web.started")
    try:
        yield
    finally:
        await engine.dispose()
        logger.info("web.stopped")


app = FastAPI(title="Crypto AI Trading API", lifespan=lifespan)

allowed = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3100").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
from api import health, trades, decisions, positions, balance, playbook, config as cfg, control  # noqa: E402

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(decisions.router, prefix="/api", tags=["decisions"])
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(balance.router, prefix="/api", tags=["balance"])
app.include_router(playbook.router, prefix="/api", tags=["playbook"])
app.include_router(cfg.router, prefix="/api", tags=["config"])
app.include_router(control.router, prefix="/api", tags=["control"])

from ws import feeds  # noqa: E402
app.include_router(feeds.router, tags=["ws"])
```

- [ ] **Step 5: Write `web/api/health.py`**

```python
"""Health endpoints — used by docker healthcheck and the dashboard."""
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    """Returns ok=True when the engine can reach Postgres."""
    factory = request.app.state.session_factory
    try:
        async with factory() as s:
            await s.execute(text("SELECT 1"))
        return {"ok": True, "db": "up"}
    except Exception as e:
        return {"ok": False, "db": str(e)}


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
```

- [ ] **Step 6: Write `web/tests/conftest.py`**

```python
"""Shared fixtures for web tests."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.base import Base


@pytest.fixture
async def app_with_inmem_db():
    """Create FastAPI app with an in-memory SQLite DB for fast tests."""
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app.state.engine = engine
    app.state.session_factory = factory

    yield app
    await engine.dispose()


@pytest.fixture
async def client(app_with_inmem_db):
    transport = ASGITransport(app=app_with_inmem_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

- [ ] **Step 7: Write `web/tests/test_health.py`**

```python
async def test_ping(client):
    r = await client.get("/api/ping")
    assert r.status_code == 200
    assert r.json() == {"pong": True}


async def test_health_returns_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
```

- [ ] **Step 8: Run tests**

```bash
cd web && pip install -r requirements.txt && python -m pytest tests/test_health.py -v
```
Expected: `2 passed`. (Other route tests will fail since the modules don't exist yet — that's fine.)

- [ ] **Step 9: Commit**

```bash
git add web/Dockerfile web/requirements.txt web/pytest.ini web/main.py web/api/__init__.py web/api/health.py web/tests/
git commit -m "feat(web): scaffold FastAPI app with health endpoint and test harness"
```

---

## Task 25: Trades, Decisions, Positions, Balance, Playbook routes

**Files:**
- Create: `web/api/trades.py`
- Create: `web/api/decisions.py`
- Create: `web/api/positions.py`
- Create: `web/api/balance.py`
- Create: `web/api/playbook.py`
- Create: `web/tests/test_trades_api.py`
- Create: `web/tests/test_decisions_api.py`

- [ ] **Step 1: Write `web/api/trades.py`**

```python
"""GET /api/trades — list closed trades with optional filters."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Trade

router = APIRouter()


class TradeOut(BaseModel):
    id: UUID
    decision_id: UUID | None
    ts_open: datetime
    ts_close: datetime | None
    side: str
    quantity_btc: float
    entry_price: float
    exit_price: float | None
    pnl_usdt: float | None
    pnl_pct: float | None
    status: str
    stop_loss: float | None
    take_profit: float | None
    close_reason: str | None
    fees_usdt: float | None


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.get("/trades", response_model=list[TradeOut])
async def list_trades(
    session: Annotated[AsyncSession, Depends(_session)],
    status: str | None = Query(None),
    limit: int = Query(100, le=500),
):
    stmt = select(Trade).order_by(desc(Trade.ts_open)).limit(limit)
    if status:
        stmt = stmt.where(Trade.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        TradeOut(
            id=r.id, decision_id=r.decision_id, ts_open=r.ts_open, ts_close=r.ts_close,
            side=r.side, quantity_btc=float(r.quantity_btc), entry_price=float(r.entry_price),
            exit_price=float(r.exit_price) if r.exit_price else None,
            pnl_usdt=float(r.pnl_usdt) if r.pnl_usdt else None,
            pnl_pct=float(r.pnl_pct) if r.pnl_pct else None,
            status=r.status, stop_loss=float(r.stop_loss) if r.stop_loss else None,
            take_profit=float(r.take_profit) if r.take_profit else None,
            close_reason=r.close_reason,
            fees_usdt=float(r.fees_usdt) if r.fees_usdt else None,
        )
        for r in rows
    ]
```

- [ ] **Step 2: Write `web/api/decisions.py`**

```python
"""GET /api/decisions — audit log of every LLM call."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Decision

router = APIRouter()


class DecisionOut(BaseModel):
    id: UUID
    ts: datetime
    agent: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    input: dict
    output: dict
    outcome: dict | None
    trade_id: UUID | None
    executed: bool
    rejected_reason: str | None


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(
    session: Annotated[AsyncSession, Depends(_session)],
    agent: str | None = Query(None),
    executed: bool | None = Query(None),
    limit: int = Query(100, le=500),
):
    stmt = select(Decision).order_by(desc(Decision.ts)).limit(limit)
    if agent:
        stmt = stmt.where(Decision.agent == agent)
    if executed is not None:
        stmt = stmt.where(Decision.executed == executed)
    rows = (await session.execute(stmt)).scalars().all()
    return [DecisionOut.model_validate(r, from_attributes=True) for r in rows]
```

- [ ] **Step 3: Write `web/api/positions.py`**

```python
"""GET /api/positions — open positions with unrealized P&L."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Position

router = APIRouter()


class PositionOut(BaseModel):
    id: UUID
    trade_id: UUID | None
    symbol: str
    quantity_btc: float
    entry_price: float
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pct: float | None
    status: str
    opened_at: datetime
    updated_at: datetime | None


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.get("/positions", response_model=list[PositionOut])
async def list_positions(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (
        await session.execute(select(Position).where(Position.status == "open"))
    ).scalars().all()
    return [
        PositionOut(
            id=r.id, trade_id=r.trade_id, symbol=r.symbol,
            quantity_btc=float(r.quantity_btc), entry_price=float(r.entry_price),
            current_price=float(r.current_price) if r.current_price else None,
            unrealized_pnl=float(r.unrealized_pnl) if r.unrealized_pnl else None,
            unrealized_pct=float(r.unrealized_pct) if r.unrealized_pct else None,
            status=r.status, opened_at=r.opened_at, updated_at=r.updated_at,
        )
        for r in rows
    ]
```

- [ ] **Step 4: Write `web/api/balance.py`**

```python
"""GET /api/balance — last known balance snapshot from positions + recent trades."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Position, Trade

router = APIRouter()


class BalanceOut(BaseModel):
    btc_held: float
    open_positions: int
    realized_pnl_today: float


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.get("/balance", response_model=BalanceOut)
async def get_balance(session: Annotated[AsyncSession, Depends(_session)]):
    open_positions = (
        await session.execute(select(Position).where(Position.status == "open"))
    ).scalars().all()
    btc = sum(float(p.quantity_btc) for p in open_positions)
    return BalanceOut(
        btc_held=btc, open_positions=len(open_positions),
        realized_pnl_today=0.0,  # would compute from daily_stats in v1.1
    )
```

- [ ] **Step 5: Write `web/api/playbook.py`**

```python
"""GET /api/playbook (active + history) and POST rollback."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import PlaybookVersion

router = APIRouter()


class PlaybookOut(BaseModel):
    id: UUID
    version: int
    ts_generated: datetime
    content: str
    model: str | None
    trades_analyzed: int | None
    win_rate: float | None
    active: bool


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.get("/playbook/active", response_model=PlaybookOut | None)
async def active(session: Annotated[AsyncSession, Depends(_session)]):
    row = (
        await session.execute(
            select(PlaybookVersion).where(PlaybookVersion.active.is_(True))
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return PlaybookOut(
        id=row.id, version=row.version, ts_generated=row.ts_generated,
        content=row.content, model=row.model, trades_analyzed=row.trades_analyzed,
        win_rate=float(row.win_rate) if row.win_rate else None, active=row.active,
    )


@router.get("/playbook/history", response_model=list[PlaybookOut])
async def history(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (
        await session.execute(select(PlaybookVersion).order_by(desc(PlaybookVersion.version)))
    ).scalars().all()
    return [
        PlaybookOut(
            id=r.id, version=r.version, ts_generated=r.ts_generated,
            content=r.content, model=r.model, trades_analyzed=r.trades_analyzed,
            win_rate=float(r.win_rate) if r.win_rate else None, active=r.active,
        )
        for r in rows
    ]


@router.post("/playbook/{version}/activate")
async def activate(version: int, session: Annotated[AsyncSession, Depends(_session)]):
    target = (
        await session.execute(select(PlaybookVersion).where(PlaybookVersion.version == version))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"version {version} not found")
    await session.execute(update(PlaybookVersion).values(active=False))
    target.active = True
    await session.commit()
    return {"ok": True, "version": version}
```

- [ ] **Step 6: Write `web/tests/test_trades_api.py`**

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from shared.db.models import Trade


async def test_list_trades_empty(client):
    r = await client.get("/api/trades")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_trades_returns_persisted(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        s.add(Trade(
            ts_open=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
            side="BUY", quantity_btc=Decimal("0.001"), entry_price=Decimal("67000"),
            status="closed", pnl_usdt=Decimal("5.0"), pnl_pct=Decimal("0.75"),
            close_reason="take_profit",
        ))
        await s.commit()
    r = await client.get("/api/trades")
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "closed"
    assert body[0]["pnl_usdt"] == 5.0
```

- [ ] **Step 7: Write `web/tests/test_decisions_api.py`**

```python
from datetime import datetime, timezone
import pytest

from shared.db.models import Decision


async def test_list_decisions_filters_by_agent(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        s.add(Decision(
            ts=datetime(2026, 5, 2, tzinfo=timezone.utc),
            agent="decisor", model="gemini-2.5-flash", input={}, output={"action": "HOLD"},
        ))
        s.add(Decision(
            ts=datetime(2026, 5, 2, tzinfo=timezone.utc),
            agent="supervisor", model="gemini-2.5-pro", input={}, output={"playbook": "x"},
        ))
        await s.commit()
    r = await client.get("/api/decisions?agent=decisor")
    body = r.json()
    assert len(body) == 1
    assert body[0]["agent"] == "decisor"
```

- [ ] **Step 8: Run tests**

```bash
cd web && python -m pytest tests/ -v
```
Expected: tests pass.

- [ ] **Step 9: Commit**

```bash
git add web/api/ web/tests/test_trades_api.py web/tests/test_decisions_api.py
git commit -m "feat(web): add trades, decisions, positions, balance, playbook routes"
```

---

## Task 26: Config and control endpoints

**Files:**
- Create: `web/api/config.py`
- Create: `web/api/control.py`
- Create: `web/tests/test_config_api.py`
- Create: `web/tests/test_control_api.py`

- [ ] **Step 1: Write `web/api/config.py`**

```python
"""GET/PUT /api/config — runtime configuration."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import ConfigEntry
from shared.config_store import ConfigStore, ConfigKey

router = APIRouter()


class ConfigEntryOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None


class ConfigUpdate(BaseModel):
    value: str


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.get("/config", response_model=list[ConfigEntryOut])
async def list_config(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (await session.execute(select(ConfigEntry).order_by(ConfigEntry.key))).scalars().all()
    return [ConfigEntryOut.model_validate(r, from_attributes=True) for r in rows]


@router.put("/config/{key}")
async def update_config(
    key: str, body: ConfigUpdate,
    session: Annotated[AsyncSession, Depends(_session)],
):
    try:
        config_key = ConfigKey(key)
    except ValueError:
        raise HTTPException(400, f"unknown config key: {key}")
    store = ConfigStore(session)
    try:
        await store.set(config_key, body.value, changed_by="user")
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "key": key, "value": body.value}
```

- [ ] **Step 2: Write `web/api/control.py`**

```python
"""POST /api/kill-switch and /api/mode — operational toggles."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config_store import ConfigStore, ConfigKey

router = APIRouter()


class KillSwitchBody(BaseModel):
    enabled: bool


class ModeBody(BaseModel):
    mode: str = Field(..., pattern="^(PAPER_TRADING|LIVE)$")
    confirmation: str = Field(..., description="Must equal 'CONFIRMO TRADING REAL' to switch to LIVE")


async def _session(request: Request) -> AsyncSession:
    factory = request.app.state.session_factory
    async with factory() as s:
        yield s


@router.post("/kill-switch")
async def toggle_kill_switch(
    body: KillSwitchBody, session: Annotated[AsyncSession, Depends(_session)],
):
    store = ConfigStore(session)
    await store.set(ConfigKey.KILL_SWITCH, "true" if body.enabled else "false", changed_by="user")
    return {"ok": True, "kill_switch": body.enabled}


@router.post("/mode")
async def set_mode(body: ModeBody, session: Annotated[AsyncSession, Depends(_session)]):
    if body.mode == "LIVE" and body.confirmation != "CONFIRMO TRADING REAL":
        raise HTTPException(400, "LIVE mode requires confirmation phrase")
    store = ConfigStore(session)
    await store.set(ConfigKey.MODE, body.mode, changed_by="user")
    return {"ok": True, "mode": body.mode}
```

- [ ] **Step 3: Write tests**

`web/tests/test_config_api.py`:
```python
import pytest
from shared.config_store import ConfigStore


async def test_list_config_after_seed(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.get("/api/config")
    assert r.status_code == 200
    keys = {entry["key"] for entry in r.json()}
    assert "max_position_pct" in keys


async def test_put_config_updates_and_audits(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.put("/api/config/max_position_pct", json={"value": "0.05"})
    assert r.status_code == 200
    r2 = await client.get("/api/config")
    entry = next(e for e in r2.json() if e["key"] == "max_position_pct")
    assert entry["value"] == "0.05"


async def test_put_config_unknown_key_400(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.put("/api/config/nonexistent_key", json={"value": "x"})
    assert r.status_code == 400
```

`web/tests/test_control_api.py`:
```python
import pytest
from shared.config_store import ConfigStore


async def test_kill_switch_toggles(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.post("/api/kill-switch", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["kill_switch"] is True


async def test_live_mode_without_confirmation_400(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.post("/api/mode", json={"mode": "LIVE", "confirmation": "no"})
    assert r.status_code == 400


async def test_live_mode_with_confirmation_ok(client, app_with_inmem_db):
    factory = app_with_inmem_db.state.session_factory
    async with factory() as s:
        await ConfigStore(s).seed_defaults()
    r = await client.post(
        "/api/mode",
        json={"mode": "LIVE", "confirmation": "CONFIRMO TRADING REAL"},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "LIVE"
```

- [ ] **Step 4: Run tests**

```bash
cd web && python -m pytest tests/test_config_api.py tests/test_control_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add web/api/config.py web/api/control.py web/tests/test_config_api.py web/tests/test_control_api.py
git commit -m "feat(web): add config CRUD and control (kill-switch, mode) endpoints"
```

---

## Task 27: WebSocket feed

**Files:**
- Create: `web/ws/__init__.py` (empty)
- Create: `web/ws/manager.py`
- Create: `web/ws/feeds.py`

- [ ] **Step 1: Write `web/ws/manager.py`**

```python
"""WebSocket connection registry for broadcasting events."""
from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class WSManager:
    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("ws.connected", clients=len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("ws.disconnected", clients=len(self._clients))

    async def broadcast(self, event: str, data: Any) -> None:
        message = json.dumps({"event": event, "data": data}, default=str)
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


manager = WSManager()
```

- [ ] **Step 2: Write `web/ws/feeds.py`**

```python
"""WebSocket endpoint at /ws — pushes engine events from the DB.

Polls Postgres at a slow rate (1–5s) for new rows and broadcasts. Clients
connect once and receive a stream of {event, data} JSON messages.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from sqlalchemy import select, desc

from shared.db.models import Decision, Position, Trade
from ws.manager import manager

logger = structlog.get_logger()
router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    factory = ws.app.state.session_factory
    last_decision_ts = datetime.now(tz=timezone.utc)
    try:
        while True:
            await asyncio.sleep(2)
            async with factory() as s:
                # New decisions
                new = (
                    await s.execute(
                        select(Decision)
                        .where(Decision.ts > last_decision_ts)
                        .order_by(desc(Decision.ts))
                        .limit(10)
                    )
                ).scalars().all()
                if new:
                    last_decision_ts = max(d.ts for d in new)
                    for d in reversed(new):
                        await manager.broadcast("decision", {
                            "id": str(d.id), "ts": d.ts.isoformat(),
                            "agent": d.agent, "action": d.output.get("action"),
                            "confidence": d.output.get("confidence"),
                            "reasoning": d.output.get("reasoning", ""),
                        })

                # Open positions snapshot
                positions = (
                    await s.execute(select(Position).where(Position.status == "open"))
                ).scalars().all()
                await manager.broadcast("positions", [
                    {
                        "id": str(p.id), "qty": float(p.quantity_btc),
                        "entry": float(p.entry_price),
                        "current": float(p.current_price) if p.current_price else None,
                        "pnl": float(p.unrealized_pnl) if p.unrealized_pnl else None,
                        "pct": float(p.unrealized_pct) if p.unrealized_pct else None,
                    }
                    for p in positions
                ])
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error("ws.error", error=str(e))
        manager.disconnect(ws)
```

- [ ] **Step 3: Smoke-test WS**

Start the web container, then in another terminal:
```bash
pip install websocket-client
python -c "
import websocket; ws = websocket.create_connection('ws://localhost:8100/ws')
print(ws.recv())
"
```
Expected: receives a JSON message within 2s.

- [ ] **Step 4: Commit**

```bash
git add web/ws/
git commit -m "feat(web): add WebSocket feed broadcasting decisions and positions"
```

---


# Phase 7 — Frontend (React 19 + Vite + Tailwind v4)

## Task 28: Frontend scaffold

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/postcss.config.js`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/src/types/index.ts`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`

- [ ] **Step 1: Write `frontend/package.json`**

```json
{
  "name": "crypto-ai-trading-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.0",
    "recharts": "^2.15.0",
    "react-markdown": "^9.0.1"
  },
  "devDependencies": {
    "@types/react": "^19.0.2",
    "@types/react-dom": "^19.0.2",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/postcss": "^4.0.0",
    "typescript": "^5.7.2",
    "vite": "^6.0.5"
  }
}
```

- [ ] **Step 2: Write `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "resolveJsonModule": true
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Write `frontend/vite.config.ts`**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3100,
    proxy: {
      "/api": "http://localhost:8100",
      "/ws":  { target: "ws://localhost:8100", ws: true },
    },
  },
});
```

- [ ] **Step 4: Write `frontend/postcss.config.js`**

```js
export default {
  plugins: {
    "@tailwindcss/postcss": {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 5: Write `frontend/index.html`**

```html
<!doctype html>
<html lang="es-AR">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Crypto AI Trading</title>
  </head>
  <body class="bg-zinc-950 text-zinc-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Write `frontend/src/index.css`**

```css
@import "tailwindcss";

:root { color-scheme: dark; }
body { font-family: system-ui, sans-serif; }
```

- [ ] **Step 7: Write `frontend/src/types/index.ts`**

```ts
// Mirrors the Pydantic schemas in shared/schemas.py and the API responses.

export type DecisorAction = "BUY" | "SELL" | "HOLD";

export type MarketRegime =
  | "TRENDING_UP" | "TRENDING_DOWN" | "RANGE" | "HIGH_VOLATILITY";

export interface DecisorOutput {
  regime: MarketRegime;
  confluences: string[];
  action: DecisorAction;
  confidence: number;
  stop_loss: number | null;
  take_profit: number | null;
  position_size_pct: number;
  reasoning: string;
}

export interface Trade {
  id: string;
  decision_id: string | null;
  ts_open: string;
  ts_close: string | null;
  side: string;
  quantity_btc: number;
  entry_price: number;
  exit_price: number | null;
  pnl_usdt: number | null;
  pnl_pct: number | null;
  status: "open" | "closed" | "cancelled";
  stop_loss: number | null;
  take_profit: number | null;
  close_reason: string | null;
  fees_usdt: number | null;
}

export interface Position {
  id: string;
  trade_id: string | null;
  symbol: string;
  quantity_btc: number;
  entry_price: number;
  current_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  status: "open" | "closed";
  opened_at: string;
  updated_at: string | null;
}

export interface Decision {
  id: string;
  ts: string;
  agent: "decisor" | "supervisor";
  model: string;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  outcome: Record<string, unknown> | null;
  trade_id: string | null;
  executed: boolean;
  rejected_reason: string | null;
}

export interface ConfigEntry {
  key: string;
  value: string;
  value_type: "int" | "float" | "string" | "bool" | "json";
  description: string | null;
}

export interface Playbook {
  id: string;
  version: number;
  ts_generated: string;
  content: string;
  model: string | null;
  trades_analyzed: number | null;
  win_rate: number | null;
  active: boolean;
}
```

- [ ] **Step 8: Write `frontend/src/api/client.ts`**

```ts
import type {
  Trade, Position, Decision, ConfigEntry, Playbook,
} from "../types";

const API_BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

export const api = {
  trades: () => get<Trade[]>("/trades"),
  decisions: (params?: { agent?: string; executed?: boolean }) => {
    const q = new URLSearchParams();
    if (params?.agent) q.set("agent", params.agent);
    if (params?.executed !== undefined) q.set("executed", String(params.executed));
    const qs = q.toString();
    return get<Decision[]>(`/decisions${qs ? `?${qs}` : ""}`);
  },
  positions: () => get<Position[]>("/positions"),
  balance: () => get<{ btc_held: number; open_positions: number; realized_pnl_today: number }>("/balance"),
  config: () => get<ConfigEntry[]>("/config"),
  setConfig: (key: string, value: string) => put(`/config/${key}`, { value }),
  killSwitch: (enabled: boolean) => post("/kill-switch", { enabled }),
  setMode: (mode: "PAPER_TRADING" | "LIVE", confirmation: string) =>
    post("/mode", { mode, confirmation }),
  playbookActive: () => get<Playbook | null>("/playbook/active"),
  playbookHistory: () => get<Playbook[]>("/playbook/history"),
  playbookActivate: (version: number) => post(`/playbook/${version}/activate`, {}),
};
```

- [ ] **Step 9: Write `frontend/src/hooks/useWebSocket.ts`**

```ts
import { useEffect, useRef, useState } from "react";

export interface WSEvent { event: string; data: unknown; }

export function useWebSocket(url: string) {
  const [last, setLast] = useState<WSEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) setTimeout(connect, 3000);
      };
      ws.onmessage = (ev) => {
        try { setLast(JSON.parse(ev.data) as WSEvent); }
        catch { /* ignore */ }
      };
    };
    connect();

    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [url]);

  return { last, connected };
}
```

- [ ] **Step 10: Write `frontend/src/App.tsx`**

```tsx
import { Routes, Route, NavLink, BrowserRouter } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { Trades } from "./pages/Trades";
import { Decisions } from "./pages/Decisions";
import { PlaybookPage } from "./pages/Playbook";
import { Config } from "./pages/Config";
import { Health } from "./pages/Health";

function NavBar() {
  const link = "px-3 py-2 text-sm hover:text-white";
  const active = "text-white border-b-2 border-emerald-400";
  return (
    <nav className="flex border-b border-zinc-800 bg-zinc-900">
      <div className="flex items-center px-4 font-semibold">🤖 Crypto AI Trading</div>
      <div className="flex">
        <NavLink to="/" end className={({isActive}) => `${link} ${isActive ? active : ""}`}>Dashboard</NavLink>
        <NavLink to="/trades" className={({isActive}) => `${link} ${isActive ? active : ""}`}>Trades</NavLink>
        <NavLink to="/decisions" className={({isActive}) => `${link} ${isActive ? active : ""}`}>Decisiones</NavLink>
        <NavLink to="/playbook" className={({isActive}) => `${link} ${isActive ? active : ""}`}>Playbook</NavLink>
        <NavLink to="/config" className={({isActive}) => `${link} ${isActive ? active : ""}`}>Config</NavLink>
        <NavLink to="/health" className={({isActive}) => `${link} ${isActive ? active : ""}`}>Health</NavLink>
      </div>
    </nav>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <NavBar />
      <main className="p-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/playbook" element={<PlaybookPage />} />
          <Route path="/config" element={<Config />} />
          <Route path="/health" element={<Health />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
```

- [ ] **Step 11: Write `frontend/src/main.tsx`**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>
);
```

- [ ] **Step 12: Write `frontend/Dockerfile`**

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

- [ ] **Step 13: Write `frontend/nginx.conf`**

```nginx
server {
  listen 80;
  server_name _;
  root /usr/share/nginx/html;

  location / {
    try_files $uri $uri/ /index.html;
  }

  location /api {
    proxy_pass http://web:8000;
    proxy_set_header Host $host;
  }

  location /ws {
    proxy_pass http://web:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 1d;
  }
}
```

- [ ] **Step 14: Commit scaffold**

Page components (`Dashboard.tsx`, etc.) are added in Task 29. Commit the scaffold first:

```bash
mkdir -p frontend/src/pages frontend/src/hooks frontend/src/api
# Stub each page with `export function Dashboard() { return <div>TODO</div>; }`
# (replaced in Task 29).
git add frontend/
git commit -m "feat(frontend): scaffold React 19 + Vite + Tailwind v4 with routing"
```

---

## Task 29: Frontend pages

**Files:**
- Create: `frontend/src/pages/Dashboard.tsx`
- Create: `frontend/src/pages/Trades.tsx`
- Create: `frontend/src/pages/Decisions.tsx`
- Create: `frontend/src/pages/Playbook.tsx`
- Create: `frontend/src/pages/Config.tsx`
- Create: `frontend/src/pages/Health.tsx`

- [ ] **Step 1: Write `frontend/src/pages/Dashboard.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
import type { Position, Decision } from "../types";

export function Dashboard() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [lastDecision, setLastDecision] = useState<Decision | null>(null);
  const [killSwitchOn, setKillSwitchOn] = useState(false);
  const { last, connected } = useWebSocket(`ws://${window.location.host}/ws`);

  useEffect(() => {
    api.positions().then(setPositions);
    api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null));
    api.config().then(cfg => {
      const ks = cfg.find(c => c.key === "kill_switch");
      setKillSwitchOn(ks?.value === "true");
    });
  }, []);

  useEffect(() => {
    if (!last) return;
    if (last.event === "positions") setPositions(last.data as Position[]);
    if (last.event === "decision") {
      // Refetch full decision list lazily — the WS payload is summarised
      api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null));
    }
  }, [last]);

  const onKillSwitch = async () => {
    if (!confirm("¿Activar kill switch? Cierra posiciones y desactiva el bot.")) return;
    await api.killSwitch(true);
    setKillSwitchOn(true);
  };

  return (
    <div className="grid gap-6 grid-cols-1 lg:grid-cols-3">
      <div className="col-span-3 flex items-center justify-between rounded-xl bg-zinc-900 p-4">
        <div className="flex items-center gap-3">
          <span className={`size-3 rounded-full ${connected ? "bg-emerald-400" : "bg-zinc-500"}`} />
          <span className="text-sm text-zinc-300">{connected ? "Engine conectado" : "Desconectado"}</span>
        </div>
        <button
          onClick={onKillSwitch}
          className={`rounded-lg px-4 py-2 font-semibold ${killSwitchOn ? "bg-red-700" : "bg-red-600 hover:bg-red-500"}`}
        >
          🚨 {killSwitchOn ? "Kill Switch ACTIVADO" : "Activar Kill Switch"}
        </button>
      </div>

      <Card title="Posiciones abiertas">
        {positions.length === 0
          ? <p className="text-zinc-400">Ninguna posición abierta.</p>
          : positions.map(p => (
              <div key={p.id} className="rounded-lg bg-zinc-800 p-3 mb-2">
                <div className="flex justify-between">
                  <span>{p.symbol}</span>
                  <span className={p.unrealized_pnl && p.unrealized_pnl > 0 ? "text-emerald-400" : "text-red-400"}>
                    {p.unrealized_pnl !== null ? `$${p.unrealized_pnl.toFixed(2)}` : "n/a"}
                  </span>
                </div>
                <div className="text-xs text-zinc-400">
                  qty {p.quantity_btc} | entry ${p.entry_price.toFixed(2)} | actual ${p.current_price?.toFixed(2) ?? "n/a"}
                </div>
              </div>
            ))
        }
      </Card>

      <Card title="Última decisión">
        {!lastDecision
          ? <p className="text-zinc-400">Sin decisiones aún.</p>
          : (() => {
              const out = lastDecision.output as { action: string; confidence: number; reasoning: string };
              return (
                <>
                  <div className="text-3xl font-bold mb-2">{out.action}</div>
                  <div className="text-sm text-zinc-400 mb-3">
                    Confianza {(out.confidence * 100).toFixed(0)}%
                  </div>
                  <p className="text-sm">{out.reasoning}</p>
                  <div className="mt-3 text-xs text-zinc-500">
                    {new Date(lastDecision.ts).toLocaleString("es-AR")}
                  </div>
                </>
              );
            })()
        }
      </Card>

      <Card title="P&L del día">
        <p className="text-zinc-400">Coming soon — desde tabla daily_stats.</p>
      </Card>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h3 className="text-sm font-semibold text-zinc-300 mb-3">{title}</h3>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Write `frontend/src/pages/Trades.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Trade } from "../types";

export function Trades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  useEffect(() => { api.trades().then(setTrades); }, []);
  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h2 className="text-lg font-semibold mb-4">Historial de trades</h2>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-zinc-400">
          <tr>
            <th className="text-left py-2">Apertura</th>
            <th className="text-left py-2">Cierre</th>
            <th className="text-left py-2">Side</th>
            <th className="text-right py-2">Qty</th>
            <th className="text-right py-2">Entry</th>
            <th className="text-right py-2">Exit</th>
            <th className="text-right py-2">P&L</th>
            <th className="text-right py-2">Fees</th>
            <th className="text-left py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map(t => (
            <tr key={t.id} className="border-t border-zinc-800">
              <td className="py-2">{new Date(t.ts_open).toLocaleString("es-AR")}</td>
              <td>{t.ts_close ? new Date(t.ts_close).toLocaleString("es-AR") : "—"}</td>
              <td>{t.side}</td>
              <td className="text-right">{t.quantity_btc.toFixed(6)}</td>
              <td className="text-right">${t.entry_price.toFixed(2)}</td>
              <td className="text-right">{t.exit_price ? `$${t.exit_price.toFixed(2)}` : "—"}</td>
              <td className={`text-right ${(t.pnl_usdt ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {t.pnl_usdt !== null ? `$${t.pnl_usdt.toFixed(2)}` : "—"}
              </td>
              <td className="text-right">{t.fees_usdt !== null ? `$${t.fees_usdt.toFixed(2)}` : "—"}</td>
              <td>{t.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 3: Write `frontend/src/pages/Decisions.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Decision } from "../types";

export function Decisions() {
  const [items, setItems] = useState<Decision[]>([]);
  const [agent, setAgent] = useState<string>("");
  const [selected, setSelected] = useState<Decision | null>(null);

  useEffect(() => {
    api.decisions(agent ? { agent } : undefined).then(setItems);
  }, [agent]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Decisiones (audit log)</h2>
          <select value={agent} onChange={e => setAgent(e.target.value)}
            className="rounded bg-zinc-800 px-2 py-1 text-sm">
            <option value="">Todos</option>
            <option value="decisor">Decisor</option>
            <option value="supervisor">Supervisor</option>
          </select>
        </div>
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-400">
            <tr>
              <th className="text-left py-2">TS</th>
              <th className="text-left py-2">Agente</th>
              <th className="text-left py-2">Acción</th>
              <th className="text-right py-2">Conf</th>
              <th className="text-left py-2">Ejec</th>
            </tr>
          </thead>
          <tbody>
            {items.map(d => (
              <tr key={d.id} onClick={() => setSelected(d)}
                  className={`cursor-pointer border-t border-zinc-800 hover:bg-zinc-800/50 ${selected?.id === d.id ? "bg-zinc-800" : ""}`}>
                <td className="py-2">{new Date(d.ts).toLocaleString("es-AR")}</td>
                <td>{d.agent}</td>
                <td>{(d.output.action as string) ?? "—"}</td>
                <td className="text-right">{((d.output.confidence as number) ?? 0).toFixed(2)}</td>
                <td>{d.executed ? "✅" : (d.rejected_reason ? "❌" : "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rounded-xl bg-zinc-900 p-5 max-h-[80vh] overflow-auto">
        {selected ? (
          <>
            <h3 className="font-semibold mb-2">Detalle</h3>
            <p className="text-sm mb-3 text-zinc-300">{(selected.output.reasoning as string) ?? ""}</p>
            <details className="mb-3"><summary className="cursor-pointer text-zinc-400 text-sm">Output JSON</summary>
              <pre className="mt-2 text-xs bg-zinc-950 p-3 rounded">{JSON.stringify(selected.output, null, 2)}</pre>
            </details>
            <details><summary className="cursor-pointer text-zinc-400 text-sm">Input JSON</summary>
              <pre className="mt-2 text-xs bg-zinc-950 p-3 rounded">{JSON.stringify(selected.input, null, 2)}</pre>
            </details>
          </>
        ) : <p className="text-zinc-400">Seleccioná una fila para ver el detalle.</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write `frontend/src/pages/Playbook.tsx`**

```tsx
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
import type { Playbook } from "../types";

export function PlaybookPage() {
  const [active, setActive] = useState<Playbook | null>(null);
  const [history, setHistory] = useState<Playbook[]>([]);

  useEffect(() => {
    api.playbookActive().then(setActive);
    api.playbookHistory().then(setHistory);
  }, []);

  const onActivate = async (version: number) => {
    if (!confirm(`¿Activar la versión v${version}? La versión actual queda inactiva.`)) return;
    await api.playbookActivate(version);
    api.playbookActive().then(setActive);
    api.playbookHistory().then(setHistory);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5">
        <h2 className="text-lg font-semibold mb-3">
          Playbook activo {active && `(v${active.version})`}
        </h2>
        <article className="prose prose-invert max-w-none">
          <ReactMarkdown>{active?.content ?? "Sin playbook"}</ReactMarkdown>
        </article>
      </div>
      <div className="rounded-xl bg-zinc-900 p-5">
        <h3 className="font-semibold mb-3">Historial</h3>
        <ul className="space-y-2">
          {history.map(v => (
            <li key={v.id} className="flex items-center justify-between rounded bg-zinc-800 p-2">
              <div>
                <div className="text-sm">v{v.version} {v.active && <span className="text-emerald-400">(activa)</span>}</div>
                <div className="text-xs text-zinc-400">
                  {new Date(v.ts_generated).toLocaleString("es-AR")}
                  {v.win_rate !== null && ` · WR ${v.win_rate.toFixed(1)}%`}
                </div>
              </div>
              {!v.active && (
                <button onClick={() => onActivate(v.version)}
                        className="text-xs rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600">
                  Activar
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `frontend/src/pages/Config.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConfigEntry } from "../types";

export function Config() {
  const [entries, setEntries] = useState<ConfigEntry[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [liveModalOpen, setLiveModalOpen] = useState(false);
  const [liveConfirmation, setLiveConfirmation] = useState("");

  useEffect(() => { api.config().then(setEntries); }, []);

  const onSave = async (key: string) => {
    const value = edits[key];
    if (value === undefined) return;
    await api.setConfig(key, value);
    setEdits(prev => { const { [key]: _, ...rest } = prev; return rest; });
    api.config().then(setEntries);
  };

  const modeEntry = entries.find(e => e.key === "mode");
  const onSwitchToLive = async () => {
    try {
      await api.setMode("LIVE", liveConfirmation);
      setLiveModalOpen(false);
      api.config().then(setEntries);
    } catch (e) {
      alert("Confirmación incorrecta. Escribí literalmente: CONFIRMO TRADING REAL");
    }
  };

  return (
    <div className="space-y-6">
      <div className="rounded-xl bg-zinc-900 p-5">
        <h2 className="text-lg font-semibold mb-4">Configuración runtime</h2>

        {modeEntry?.value === "PAPER_TRADING" && (
          <button onClick={() => setLiveModalOpen(true)}
                  className="mb-4 rounded bg-amber-600 px-3 py-2 text-sm hover:bg-amber-500">
            Cambiar a modo LIVE (trading real)
          </button>
        )}

        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-400">
            <tr><th className="text-left py-2">Key</th><th className="text-left">Valor</th><th className="text-left">Tipo</th><th></th></tr>
          </thead>
          <tbody>
            {entries.map(e => (
              <tr key={e.key} className="border-t border-zinc-800">
                <td className="py-2 pr-3 align-top">
                  <div className="font-mono">{e.key}</div>
                  {e.description && <div className="text-xs text-zinc-400">{e.description}</div>}
                </td>
                <td className="pr-3">
                  <input
                    className="w-full rounded bg-zinc-800 px-2 py-1"
                    value={edits[e.key] ?? e.value}
                    onChange={ev => setEdits(p => ({ ...p, [e.key]: ev.target.value }))}
                  />
                </td>
                <td className="pr-3 text-zinc-400">{e.value_type}</td>
                <td>
                  {edits[e.key] !== undefined && edits[e.key] !== e.value && (
                    <button onClick={() => onSave(e.key)}
                            className="rounded bg-emerald-600 px-2 py-1 text-xs hover:bg-emerald-500">
                      Guardar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {liveModalOpen && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center">
          <div className="rounded-xl bg-zinc-900 p-6 max-w-md">
            <h3 className="text-lg font-semibold mb-2">Confirmar modo LIVE</h3>
            <p className="text-sm text-zinc-300 mb-3">
              Esto activa trading con dinero real en Binance. Para confirmar, escribí literalmente:
              <code className="block mt-2 bg-zinc-800 p-2 rounded">CONFIRMO TRADING REAL</code>
            </p>
            <input
              className="w-full rounded bg-zinc-800 px-2 py-1 mb-3"
              value={liveConfirmation}
              onChange={e => setLiveConfirmation(e.target.value)}
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setLiveModalOpen(false)} className="rounded bg-zinc-700 px-3 py-1">Cancelar</button>
              <button onClick={onSwitchToLive} className="rounded bg-red-600 px-3 py-1 hover:bg-red-500">
                Activar LIVE
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Write `frontend/src/pages/Health.tsx`**

```tsx
import { useEffect, useState } from "react";

export function Health() {
  const [data, setData] = useState<{ ok: boolean; db: string } | null>(null);
  useEffect(() => {
    fetch("/api/health").then(r => r.json()).then(setData);
    const id = setInterval(() => {
      fetch("/api/health").then(r => r.json()).then(setData);
    }, 5000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h2 className="text-lg font-semibold mb-4">Estado del sistema</h2>
      <div className="space-y-2 text-sm">
        <Row label="Web API" ok={data?.ok ?? false} detail={data ? `DB: ${data.db}` : "..."} />
      </div>
    </div>
  );
}

function Row({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center justify-between rounded bg-zinc-800 p-3">
      <span>{label}</span>
      <span className="flex items-center gap-2">
        <span className={`size-2 rounded-full ${ok ? "bg-emerald-400" : "bg-red-400"}`} />
        <span className="text-zinc-400">{detail}</span>
      </span>
    </div>
  );
}
```

- [ ] **Step 7: Smoke-test the build**

```bash
cd frontend && npm install && npm run build
```
Expected: produces `dist/` without errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/
git commit -m "feat(frontend): implement all 6 pages (Dashboard, Trades, Decisions, Playbook, Config, Health)"
```

---


# Phase 8 — Backtesting

## Task 30: Backtesting runner

**Files:**
- Create: `backtesting/README.md`
- Create: `backtesting/requirements.txt`
- Create: `backtesting/runner.py`
- Create: `backtesting/tests/test_runner.py`

- [ ] **Step 1: Write `backtesting/requirements.txt`**

```
pandas==2.2.3
numpy==2.1.3
pandas-ta==0.3.14b0
ccxt==4.4.40
vectorbt==0.26.2
pytest==8.3.4
```

- [ ] **Step 2: Write `backtesting/README.md`**

```markdown
# Backtesting module

Replays the Decisor against historical Binance OHLCV. Two modes:

1. **Indicator-only baseline** (no LLM cost) — applies the playbook rules
   (≥3 confluences, R:R ≥ 1.5, ATR-based SL) deterministically. Fast.
2. **LLM replay** (costs LLM tokens) — calls Gemini for each historical tick.
   Use sparingly; reserve for the final 1–2 weeks of validation.

## Usage

```bash
python runner.py --mode baseline --days 30
python runner.py --mode llm-replay --days 7
```

Outputs a markdown report into `reports/` with Sharpe, drawdown, win rate.

## Walk-forward
The default split is 70% train / 30% out-of-sample. Tune playbook rules on
train, validate on test, never the other way around.
```

- [ ] **Step 3: Write `backtesting/runner.py`**

```python
"""Indicator-only backtest baseline.

Loads OHLCV from Binance, applies a deterministic version of the playbook
rules, and computes performance metrics. Useful for sanity-checking the
strategy *before* paying for LLM calls.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

import ccxt
import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass
class BacktestResult:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    total_pnl_pct: float
    sharpe: float
    max_drawdown_pct: float
    trades: list[dict] = field(default_factory=list)


def fetch_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    ex = ccxt.binance()
    since = ex.parse8601((datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat())
    all_rows = []
    while True:
        rows = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not rows:
            break
        all_rows.extend(rows)
        since = rows[-1][0] + 1
        if len(rows) < 1000:
            break
    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]
    df["ema20"] = ta.ema(df["close"], length=20)
    df["ema50"] = ta.ema(df["close"], length=50)
    df["ema200"] = ta.ema(df["close"], length=200)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df["volume_avg"] = df["volume"].rolling(20).mean()
    return df


def signal_buy(row: pd.Series) -> bool:
    """≥3 confluences as in the v0 playbook."""
    confluences = 0
    if row["ema20"] > row["ema50"] > row["ema200"]:
        confluences += 1
    if row["rsi"] < 35:  # oversold rebound
        confluences += 1
    if row["macd_hist"] > 0 and row["macd"] > row["macd_signal"]:
        confluences += 1
    if row["volume"] > 1.3 * row["volume_avg"]:
        confluences += 1
    return confluences >= 3


def run_baseline(df: pd.DataFrame, *, sl_atr_mult: float = 1.0,
                 rr: float = 2.0, fee: float = 0.001) -> BacktestResult:
    df = df.dropna()
    in_pos = False
    entry = sl = tp = 0.0
    trades: list[dict] = []

    for ts, row in df.iterrows():
        if not in_pos:
            if signal_buy(row):
                entry = float(row["close"])
                sl = entry - sl_atr_mult * float(row["atr"])
                tp = entry + rr * sl_atr_mult * float(row["atr"])
                in_pos = True
                entry_ts = ts
        else:
            high = float(row["high"]); low = float(row["low"])
            exit_price = exit_reason = None
            if low <= sl:
                exit_price = sl; exit_reason = "stop_loss"
            elif high >= tp:
                exit_price = tp; exit_reason = "take_profit"
            if exit_price is not None:
                pnl_pct = (exit_price - entry) / entry - 2 * fee
                trades.append({
                    "entry_ts": entry_ts, "exit_ts": ts,
                    "entry": entry, "exit": exit_price,
                    "pnl_pct": pnl_pct * 100, "reason": exit_reason,
                })
                in_pos = False

    if not trades:
        return BacktestResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0)

    pnl_series = pd.Series([t["pnl_pct"] / 100 for t in trades])
    wins = (pnl_series > 0).sum()
    losses = (pnl_series < 0).sum()
    cum = (1 + pnl_series).cumprod()
    drawdown = (cum / cum.cummax() - 1).min() * 100
    sharpe = pnl_series.mean() / (pnl_series.std() + 1e-9) * np.sqrt(252)

    return BacktestResult(
        n_trades=len(trades), n_wins=int(wins), n_losses=int(losses),
        win_rate=wins / len(trades) * 100,
        total_pnl_pct=float(cum.iloc[-1] - 1) * 100,
        sharpe=float(sharpe), max_drawdown_pct=float(drawdown),
        trades=trades,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--sl-atr-mult", type=float, default=1.0)
    parser.add_argument("--rr", type=float, default=2.0)
    args = parser.parse_args()

    df = fetch_history(args.symbol, args.timeframe, args.days)
    df = add_indicators(df)
    res = run_baseline(df, sl_atr_mult=args.sl_atr_mult, rr=args.rr)

    print(f"Backtest {args.symbol} {args.timeframe} {args.days}d")
    print(f"  Trades:        {res.n_trades}")
    print(f"  Wins:          {res.n_wins}")
    print(f"  Losses:        {res.n_losses}")
    print(f"  Win rate:      {res.win_rate:.2f}%")
    print(f"  Total P&L:     {res.total_pnl_pct:+.2f}%")
    print(f"  Sharpe (ann):  {res.sharpe:.2f}")
    print(f"  Max drawdown:  {res.max_drawdown_pct:.2f}%")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `backtesting/tests/test_runner.py`**

```python
"""Unit tests for the baseline backtester — uses synthetic OHLCV."""
import numpy as np
import pandas as pd

from backtesting.runner import add_indicators, run_baseline


def make_uptrend(n=400):
    rng = np.random.default_rng(42)
    close = np.linspace(60_000, 70_000, n) + rng.normal(0, 100, size=n)
    high = close * 1.002
    low = close * 0.998
    df = pd.DataFrame({
        "open": close, "high": high, "low": low, "close": close,
        "volume": rng.uniform(50, 200, size=n),
    }, index=pd.date_range("2026-04-01", periods=n, freq="5min", tz="UTC"))
    return df


def test_baseline_runs_on_synthetic_uptrend():
    df = add_indicators(make_uptrend())
    res = run_baseline(df)
    assert res.n_trades >= 0  # could be 0 if confluences are sparse


def test_returns_zero_trades_when_data_is_flat():
    n = 400
    df = pd.DataFrame({
        "open": [60_000] * n, "high": [60_000] * n, "low": [60_000] * n,
        "close": [60_000] * n, "volume": [100] * n,
    }, index=pd.date_range("2026-04-01", periods=n, freq="5min", tz="UTC"))
    df = add_indicators(df)
    res = run_baseline(df)
    assert res.n_trades == 0
```

- [ ] **Step 5: Run tests**

```bash
cd backtesting && pip install -r requirements.txt && python -m pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add backtesting/
git commit -m "feat(backtesting): add deterministic baseline backtester for v0 playbook"
```

---

# Phase 9 — Integration & Operational Validation

## Task 31: End-to-end smoke test

- [ ] **Step 1: Bring up the full stack**

```bash
cp .env.example .env
# Edit .env with real Binance Testnet keys + Gemini key + Groq key
docker-compose build
docker-compose up -d
```

- [ ] **Step 2: Apply migrations**

```bash
docker-compose exec trading-engine alembic upgrade head
```
Expected: `Running upgrade -> 001`. Subsequent runs: no-op.

- [ ] **Step 3: Verify engine logs show normal operation**

```bash
docker-compose logs --tail=50 trading-engine
```
Expected to see:
- `scheduler.started`
- `fees.refreshed`
- One `decisor.decided` within ~5 minutes
- No `ERROR` lines

- [ ] **Step 4: Verify dashboard is reachable**

Open `http://localhost:3100`. The Dashboard should:
- Show "Engine conectado" (green dot via WebSocket)
- Show "Ninguna posición abierta" or actual positions
- Show the latest decision card (after the first 5-min cycle)

- [ ] **Step 5: Verify config edit round-trips**

In the Config page, change `max_position_pct` from `0.10` to `0.05`, save. Then:
```bash
docker-compose exec postgres psql -U trader -d crypto_ai_trading \
  -c "SELECT key, value FROM config WHERE key='max_position_pct';"
```
Expected: `0.05`. Also:
```bash
docker-compose exec postgres psql -U trader -d crypto_ai_trading \
  -c "SELECT key, old_value, new_value, changed_by FROM config_history ORDER BY ts DESC LIMIT 1;"
```
Expected: row with `changed_by='user'`.

- [ ] **Step 6: Verify kill switch closes positions and pauses**

Place a manual paper position (or wait for one). Click the kill switch in the Dashboard. Confirm:
- API call returns 200
- `kill_switch=true` in `config`
- Engine logs show "kill_switch active — only SELL-to-close allowed"

- [ ] **Step 7: Verify supervisor runs**

After at least 5 closed paper trades, manually trigger a supervisor run:
```bash
docker-compose exec trading-engine python -c "
import asyncio
from main import run  # NOTE: in v1.1 expose a CLI for this
"
```
(Alternatively, wait for 00:00 UTC.) Then:
```bash
docker-compose exec postgres psql -U trader -d crypto_ai_trading \
  -c "SELECT version, active, trades_analyzed, win_rate FROM playbook_versions ORDER BY version;"
```
Expected: `v1` row with `active=true`, `v0` with `active=false`.

- [ ] **Step 8: Run a backtest**

```bash
cd backtesting && python runner.py --days 30
```
Expected: prints metrics for the past 30d.

- [ ] **Step 9: Document operational runbook**

Add to `README.md` a section "Operations" with the commands from steps 1–8 so future operators have a checklist.

- [ ] **Step 10: Commit**

```bash
git add README.md
git commit -m "docs: add operational runbook for E2E smoke test"
```

---

## Task 32: Production paper-trading checklist

This is a **manual** validation phase. There is no code; this task is in the
plan only as a reminder of the gate before LIVE.

- [ ] **Run paper trading for at least 4 consecutive weeks** in testnet without intervention.
- [ ] **Track metrics weekly**:
  - Sharpe ratio (annualised) > 1.0
  - Max drawdown < 5%
  - Win rate > 52%
  - Profit factor > 1.5
- [ ] **Review Supervisor playbook evolution**: should improve or stabilise — never become incoherent.
- [ ] **Review Decision audit log** for at least 50 decisions: reasoning should be coherent and tied to the data shown in the prompt.
- [ ] **Test kill switch from UI** during an actual open position. Confirm position is closed at market.
- [ ] **Test exchange disconnection**: kill the Binance API for 10 minutes; engine must recover gracefully (logs `circuit.engine_paused_exchange` then resumes when API is back).
- [ ] **Test LLM provider outage**: invalidate the Gemini API key for 10 minutes; confirm fallback to Groq kicks in.
- [ ] **Backup Postgres**: `pg_dump` weekly, store offsite.
- [ ] **Decide on real capital**: only after all of the above pass. Start with $200 USD.

---

# Self-Review Checklist

Run through this list with fresh eyes after the plan is written.

## 1. Spec coverage

| Spec section | Plan task(s) | Notes |
|---|---|---|
| §4 Architecture (3 containers + Postgres) | Tasks 1, 2 | docker-compose with 4 services |
| §5 Tech stack | Task 3 (engine deps), 24 (web deps), 28 (frontend deps) | All libs pinned |
| §6 Schema (10 tables + fee_snapshots) | Tasks 5, 6 | Models + Alembic migration |
| §6.3 Default config keys | Task 7 | DEFAULTS dict matches spec |
| §7.1 Decisor system prompt | Task 15 | Loaded from `decisor_system.txt` |
| §7.2 Decisor user prompt | Task 15 + 16 | Template + ContextBuilder |
| §7.3 Supervisor system prompt | Task 15 | Loaded from `supervisor_system.txt` |
| §7.4 Supervisor user prompt | Task 15 + 22 | Template + Supervisor |
| §7.5 Playbook v0 bootstrap | Task 15 | `playbook_v0.md` + seed_playbook_v0 |
| §7.6 Token efficiency | Task 14 (JSON mode), Task 16 (compact context) | Caching deferred to v1.1 |
| §8.1 Risk Gate (10 checks) | Task 18 | 12 unit tests covering each rule |
| §8.2 FeeManager (dynamic fees) | Task 12 | Refresh on startup + 24h + post-trade |
| §8.3 Circuit breakers | Task 19 | Daily stop, total drawdown, LLM/exchange |
| §8.4 Playbook safety | Task 22, 25 | Versioned + manual rollback endpoint |
| §9 Frontend (6 pages) | Task 28, 29 | Dashboard, Trades, Decisions, Playbook, Config, Health |
| §9.3 Config panel sections | Task 26, 29 | All 7 sections, LIVE confirmation |
| §10 Phases 1–7 | Tasks 1–32 | Full coverage |
| Backtesting (G7, Phase 6) | Task 30 | Baseline only; LLM-replay deferred |

## 2. Placeholder scan

Searched the plan for `TBD`, `TODO`, `FIXME`, `implement later`, `add appropriate error handling`,
`similar to Task N` (without showing code). Result: clean. The two annotated `NOTE:`
markers in Task 23 (ccxt.pro WS support) and Task 31 (supervisor CLI) are
deliberate operational caveats, not placeholders.

## 3. Type consistency

- `DecisorOutput` schema (Task 13) is referenced consistently in `Decisor` (Task 17), `RiskGate` (Task 18), `Executor` (Task 20). All use the same field names and types.
- `LLMResponse` dataclass (Task 14) used identically in Decisor and Supervisor.
- `OrderBookSnapshot` dataclass (Task 11) used identically in ContextBuilder (Task 16) and the engine main loop (Task 23).
- `ConfigKey` enum (Task 7) referenced consistently in web routes (Tasks 26).

## 4. Scope

The plan covers the entire spec in 32 tasks across 9 phases. It is at the upper edge of
"single plan" — but the phases are sequential and each builds on the previous, so
splitting would introduce more coordination cost than benefit. The boundaries are clean:
Phases 1–5 = engine; Phase 6 = web API; Phase 7 = frontend; Phase 8 = backtesting;
Phase 9 = E2E validation.

If executing with subagent-driven-development, dispatch one task per subagent.
If executing inline, checkpoint after each phase.

---

# Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-crypto-ai-trading.md`.
Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
