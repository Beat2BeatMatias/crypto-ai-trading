# Extraction Raw — Metadata

**Extraction date**: 2026-05-14
**Extractor**: `/meli.reverse-eng` (Meli SDD Kit)
**Mode**: FULL EXTRACTION
**Optimization strategy**: ASSISTED
**FuryMCP**: NOT APPLICABLE (proyecto no-Fury)

## Sources

| Source | Location | Notes |
|--------|----------|-------|
| Existing specs | `existing-specs/` | Design doc 2026-05-02 + plans + README copiados |
| Code analysis | `code-analysis/trading-engine-analysis.md` | Engine bot autónomo |
| Code analysis | `code-analysis/web-frontend-analysis.md` | FastAPI + React |
| Code analysis | `code-analysis/shared-db-backtesting-analysis.md` | Shared + Alembic + backtester |
| MCP Fury | `mcpfury/` (vacío) | Aplicación no usa Fury |

## Repository fingerprint

- Stack backend: **Python 3.12** + FastAPI + SQLAlchemy 2 async + Alembic + APScheduler + CCXT + structlog
- Stack frontend: **React 19** + Vite + TypeScript + Tailwind v4 + react-router-dom 7 + WebSocket nativo
- DB: **PostgreSQL 17** (`TIMESTAMPTZ`, `NUMERIC`, `JSONB`)
- LLM: **Gemini 2.5 Flash** (Decisor) + **Gemini 2.5 Pro** (Supervisor) + **Groq** (fallback)
- Exchange: **Binance Spot** (Testnet o Mainnet)
- Containers: trading-engine, web, frontend (+ postgres oficial)
- Ports: 3100 (frontend), 8100 (web API), 5532 (postgres)
