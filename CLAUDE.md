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
