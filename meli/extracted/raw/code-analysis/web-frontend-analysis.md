# Web API + Frontend — Analysis

Análisis exhaustivo de `web/` y `frontend/`. Cada hallazgo respaldado por `archivo:línea`.

> **Nota**: material crudo. La síntesis final está en `meli/specs/technical-spec.md` §5 (API) y §6 (Frontend).

## Parte A — Web (FastAPI)

### 1. Bootstrap (`web/main.py`)

- `FastAPI(title="Crypto AI Trading API", lifespan=lifespan)` (`web/main.py:38`).
- Lifespan (`web/main.py:12-36`): lee `DATABASE_URL`; crea engine + session_factory; si `sqlite` ejecuta `Base.metadata.create_all` (test only); arranca `ticker_broadcaster` task; dispose engine en shutdown.
- CORS: `ALLOWED_ORIGINS` env var, default `http://localhost:3100`, `allow_credentials=True`, `*` methods/headers.
- Routers: todos con `prefix="/api"` excepto WS sin prefix.
- **Sin autenticación** en ningún endpoint.

### 2. Endpoints REST (18 endpoints + 1 WS)

| Método | Path | Handler | Body / Query |
|--------|------|---------|--------------|
| GET | `/api/health` | `health.health` (`web/api/health.py:7`) | — |
| GET | `/api/ping` | `health.ping` (`health.py:50`) | — |
| GET | `/api/config` | `config.list_config` (`config.py:31`) | excluye `supervisor_run_now` |
| PUT | `/api/config/{key}` | `config.update_config` (`config.py:38`) | `ConfigUpdate{value:str}` |
| POST | `/api/kill-switch` | `control.toggle_kill_switch` (`control.py:24`) | `{enabled:bool}` |
| POST | `/api/mode` | `control.set_mode` (`control.py:35`) | `{mode, confirmation}` — LIVE exige `confirmation=="CONFIRMO TRADING REAL"` |
| POST | `/api/supervisor/run` | `control.run_supervisor_now` (`control.py:47`) | — |
| GET | `/api/decisions` | `decisions.list_decisions` (`decisions.py:31`) | `?agent`, `?executed`, `?limit` |
| GET | `/api/trades` | `trades.list_trades` (`trades.py:47`) | `?status`, `?limit` |
| POST | `/api/trades/{id}/close` | `trades.request_trade_close` (`trades.py:56`) | path |
| GET | `/api/positions` | `positions.list_positions` (`positions.py:29`) | — |
| GET | `/api/balance` | `balance.get_balance` (`balance.py:24`) | `realized_pnl_today=0.0` hardcoded (TODO) |
| GET | `/api/playbook/active` | `playbook.active` (`playbook.py:26`) | — |
| GET | `/api/playbook/history` | `playbook.history` (`playbook.py:35`) | — |
| POST | `/api/playbook/{v}/activate` | `playbook.activate` (`playbook.py:42`) | path |
| PATCH | `/api/playbook/{v}/content` | `playbook.edit_content` (`playbook.py:57`) | `{content:str}` |
| GET | `/api/stats/daily` | `stats.daily_stats` (`stats.py:37`) | agrega on-the-fly |
| GET | `/api/config/suggestions` | `suggestions.get_config_suggestions` (`suggestions.py:16`) | — |

**Sin endpoints `/internal/*`.**

### 3. WebSocket `/ws`

- `web/ws/feeds.py:44` — `@router.websocket("/ws")`.
- Cliente recibe `{"event": "...", "data": ...}` (`web/ws/manager.py:23-24`).
- Eventos emitidos:
  - `ticker` cada 5s (`feeds.py:27-41`)
  - `decision` cada 2s si hay nuevas (`feeds.py:51-65`)
  - `positions` cada 2s (`feeds.py:66-84`)
- Cliente NO envía mensajes.
- Servidor descarta sockets muertos en `broadcast` (`manager.py:29-32`).
- 🟠 Faltan: `trade_opened`, `trade_closed`, `playbook_updated`, `kill_switch_triggered` (D-009).

### 4. Schemas Pydantic (`web/api/*.py`)

| Modelo | Archivo |
|--------|---------|
| `ConfigEntryOut`, `ConfigUpdate` | `web/api/config.py:12-20` |
| `KillSwitchBody`, `ModeBody` | `web/api/control.py:10-16` |
| `DecisionOut` | `web/api/decisions.py:12-25` |
| `TradeOut` | `web/api/trades.py:12-28` (⚠️ no incluye `order_id_open/close`) |
| `PositionOut` | `web/api/positions.py:12-23` |
| `DailyStatsOut` | `web/api/stats.py:13-29` |
| `BalanceOut` | `web/api/balance.py:11-18` |
| `PlaybookOut`, `PlaybookEditIn` | `web/api/playbook.py:12-20`, `:53-54` |

### 5. Tests `web/tests/`

| Archivo | Cubre |
|---------|--------|
| `conftest.py` | SQLite in-memory adaptado desde metadata Postgres |
| `test_health.py` | `/api/ping`, `/api/health` |
| `test_config_api.py` | listado, PUT, key inválida |
| `test_control_api.py` | kill switch, mode confirmation |
| `test_decisions_api.py` | listado, filtros |
| `test_trades_api.py` | listado, filtro status |

**Sin tests** para: positions, balance, playbook, stats, suggestions, WS, supervisor/run.

---

## Parte B — Frontend (React 19)

### 1. Stack

- React 19 + Vite + TypeScript strict + Tailwind v4 + react-router-dom 7.
- Sin state manager (sólo `useState`/`useEffect` por página).
- HTTP: `fetch` nativo wrappeado en `frontend/src/api/client.ts`.
- WebSocket: API nativa via `frontend/src/hooks/useWebSocket.ts`.
- i18n: **sin librería** — hardcoded español, `lang="es-AR"` en `index.html`.
- `recharts` declarada pero **sin uso** real.

### 2. Estructura `frontend/src/`

```
src/
  api/client.ts          — Cliente REST
  hooks/useWebSocket.ts  — Hook WS
  types/index.ts         — Tipos TS
  pages/                 — 6 páginas
  App.tsx                — Router + NavBar
  main.tsx               — Mount
  index.css              — Tailwind
```

### 3. Rutas

| Path | Componente | Archivo |
|------|------------|---------|
| `/` | `Dashboard` | `pages/Dashboard.tsx` |
| `/trades` | `Trades` | `pages/Trades.tsx` |
| `/decisions` | `Decisions` | `pages/Decisions.tsx` |
| `/playbook` | `PlaybookPage` | `pages/Playbook.tsx` |
| `/config` | `Config` | `pages/Config.tsx` |
| `/health` | `Health` | `pages/Health.tsx` |

### 4. Cliente API (`frontend/src/api/client.ts`)

Wrappers `get/put/post/patch`; exporta `api.{trades, closeTrade, decisions, positions, balance, config, setConfig, killSwitch, runSupervisor, setMode, playbookActive, playbookHistory, playbookActivate, playbookEditContent, dailyStats, configSuggestions}`.

### 5. Hook `useWebSocket`

`frontend/src/hooks/useWebSocket.ts:5-28` expone `{last, connected}`; reconexión automática cada 3000ms.

### 6. Gaps frontend (D-013/D-014/D-015)

- `/health`: sin métricas LLM (latency, quota, fallback), sin recent errors panel.
- `/trades`: sin date range, result, close_reason filters; sin export CSV; sin sorting.
- `/decisions`: sin slider confidence; sin filtro action.
- `/playbook`: sin diff viewer; sin reset a v0.
- Dashboard: sin live price chart (recharts no usado).

### 7. Config Vite/nginx

- `vite.config.ts`: dev server `:3100`, proxy `/api → :8100`, `/ws → ws://:8100`.
- `nginx.conf` (prod): SPA `try_files` + proxy `/api` y `/ws` a `web:8000`.
- `frontend/Dockerfile` build con nginx.
