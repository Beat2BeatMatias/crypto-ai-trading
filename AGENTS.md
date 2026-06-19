# AGENTS.md — Crypto AI Trading

## Quick start (Docker)

```bash
cp .env.example .env
docker compose build && docker compose up -d
docker compose run --rm trading-engine alembic upgrade head
docker compose logs -f trading-engine
```

## Architecture

Two Python services share a Postgres DB — **no IPC**, Postgres is the single source of truth:

- `trading-engine/` — autonomous bot loop (no HTTP server). Entrypoint: `main.py`.
- `web/` — FastAPI (REST + WebSocket) on port 8100. Entrypoint: `web/main.py`.
- `shared/` — mounted into both containers. Models, schemas, config, confidence, position sizing live here.
- `frontend/` — React + Vite + Tailwind on port 3100. `npm run dev` for local dev.

Services never communicate directly. The engine writes everything; the web reads everything + writes only `config` and `config_history`.

## Key commands (Docker)

| Action | Command |
|--------|---------|
| Run all engine tests | `docker compose run --rm trading-engine pytest` |
| Run all web tests | `docker compose run --rm web pytest` |
| Run single test | `docker compose run --rm trading-engine pytest tests/test_confidence.py::test_name -v` |
| Alembic migrations | `docker compose run --rm trading-engine alembic upgrade head` |
| Create migration | `docker compose run --rm trading-engine alembic revision --autogenerate -m "desc"` |
| Restart engine | `docker compose restart trading-engine` |

**Engine tests** require `pytest-cov` (see `pytest.ini` — `--cov-fail-under=70`). Coverage config is in `pytest.ini`. **Web tests** have no coverage requirement.

## Config changes (runtime)

Config lives in DB table `config`. Defaults in `shared/config_store.py`. Apply changes:

```bash
# Ver valor actual
docker compose exec postgres psql -U trader -d crypto_ai_trading \
  -c "SELECT key, value FROM config WHERE key IN ('min_confluences_buy','min_confluences_short','min_rr_ratio','cooldown_after_sell_min');"

# Cambiar valor (ej: min_confluences_buy de 2 → 1)
docker compose exec postgres psql -U trader -d crypto_ai_trading \
  -c "UPDATE config SET value = '1', updated_at = NOW() WHERE key = 'min_confluences_buy';"

# Los cambios se reflejan en el próximo ciclo del decisor (no requiere restart)
```

## Local dev (no Docker)

```bash
# Engine
cd trading-engine && pip install -r requirements.txt && alembic upgrade head && python main.py

# Web
cd web && pip install -r requirements.txt && uvicorn main:app --reload --port 8100

# Frontend
cd frontend && npm install && npm run dev
```

SQLite auto-creates tables in web (dev mode). Postgres requires Alembic.

## Project structure

- `trading-engine/agents/` — Decisor, Supervisor, LLM client, context builder, outcome attribution, post-mortem, prompts
- `trading-engine/collectors/` — price & orderbook collectors
- `trading-engine/execution/` — executor, order tracker, position manager, fee manager
- `trading-engine/risk/` — risk gate, circuit breaker, coherence checker
- `trading-engine/alembic/versions/` — DB migrations (numbered 001–020+)
- `shared/` — data models, config store, confidence formula, schemas
- `shared/db/models.py` — all SQLAlchemy models (single file)
- `shared/confidence.py` — server-side confidence_base formula
- `docs/specs/` — canonical specs (functional, technical, data model, API contracts, risk, patterns, gaps)

## Conventions

- All times UTC (`TIMESTAMPTZ`), all prices/quantities `NUMERIC` (never `FLOAT`)
- LLM payloads stored as `JSONB` (GIN-indexed)
- UI text in Spanish (es-AR)
- Logging via `structlog` (JSON output)
- Tests use `pytest-asyncio` (auto mode), `freezegun` for deterministic time, `aiosqlite` for in-memory DB
- Config keys defined in `shared/config_store.py` enum `ConfigKey` with `_Default` (value, type, description). DB table `config` is source of truth at runtime.
- Every config change writes to `config_history` for audit trail
- `/meli.start` → `/meli.spec` → `/meli.plan` → `/meli.build` → `/meli.finish` workflow for spec-driven changes. Specs in Spanish.
- Confidence formula: `clip(conf_base_table(count) × regime_factor + confidence_adjustment, 0, 1)`. Configurable via `conf_base_*` and `peso_regime_*` keys.
- Use `docker compose` (V2 plugin), not `docker-compose` (standalone binary, often not installed).
