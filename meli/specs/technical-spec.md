# Technical Specification — Crypto AI Trading

**Versión**: 1.0 (reverse-engineered)
**Fecha de extracción**: 2026-05-14
**Source of truth**: código actual; cross-validation con design doc 2026-05-02

## Sistema de confianza

| Marker | Significado |
|--------|-------------|
| ✅✅✅ THREE_WAY | En código + design doc + plan/README, coherente |
| ✅✅ VERIFIED | En código + design doc, coherente |
| ✅⚠️ PARTIAL | En código + design doc, **con diferencias** |
| 🔸 CODE_ONLY | Sólo en código |
| ⚠️ DOCS_ONLY | Sólo en docs — **NO en código** |

---

## 1. Stack tecnológico

### 1.1 Backend (Python 3.12)

| Capa | Choice | Evidencia | Confidence |
|------|--------|-----------|------------|
| Lenguaje | Python 3.12 | `trading-engine/Dockerfile`, `web/Dockerfile` | ✅✅✅ |
| Web framework | FastAPI 0.115+ | `web/main.py`, `web/requirements.txt` | ✅✅✅ |
| Async runtime | asyncio | `trading-engine/main.py:run()` | ✅✅✅ |
| ORM | SQLAlchemy 2.0 async | `shared/db/base.py:10-28` | ✅✅✅ |
| DB driver | asyncpg | `shared/db/base.py:14-16` (URL `postgresql+asyncpg://`) | ✅✅✅ |
| Migraciones | Alembic | `trading-engine/alembic/env.py` | ✅✅✅ |
| Base de datos | PostgreSQL 17-alpine | `docker-compose.yml:3` | ✅✅✅ |
| Scheduler | APScheduler 3.x | `trading-engine/scheduler.py:4-6` | ✅✅✅ |
| Exchange client | CCXT (REST async) | `trading-engine/exchange.py:16-24` | ✅✅✅ |
| Exchange WS | CCXT (`watch_order_book`) | `collectors/orderbook_collector.py:59` | ✅⚠️ design doc dice ccxt.pro |
| Indicadores técnicos | **pandas puro** (no pandas-ta) | `collectors/indicators.py` | ✅⚠️ design doc dice pandas-ta |
| Data manipulation | pandas + numpy | `requirements.txt` | ✅✅✅ |
| LLM primario | google-genai (Gemini SDK) | `agents/llm_client.py:123-135` | ✅✅✅ |
| LLM fallback | groq SDK | `agents/llm_client.py:137-152` | ✅✅✅ |
| HTTP async | httpx | `requirements.txt` | ✅✅✅ |
| Validación | Pydantic 2 | `shared/schemas.py` | ✅✅✅ |
| Logging | structlog (JSON) | `trading-engine/main.py:41-46` | ✅✅✅ |
| Testing | pytest + pytest-asyncio + freezegun | `pytest.ini`, `requirements.txt` | ✅✅✅ |
| Backtesting | **pandas puro** | `backtesting/runner.py` | ✅⚠️ design doc dice vectorbt |

### 1.2 Frontend

| Capa | Choice | Evidencia |
|------|--------|-----------|
| Framework | React 19 | `frontend/package.json:12-13` |
| Build | Vite | `frontend/vite.config.ts` |
| Lenguaje | TypeScript strict | `frontend/tsconfig.json` |
| Styling | Tailwind CSS v4 + PostCSS | `frontend/postcss.config.js`, `frontend/package.json:22-26` |
| Charts | recharts (declarada, **sin uso real**) | `package.json` vs grep |
| Router | react-router-dom 7 | `frontend/package.json:14` |
| Estado global | **ninguno** — `useState`/`useEffect` por página | `frontend/src/pages/*` |
| HTTP client | `fetch` nativo wrappeado | `frontend/src/api/client.ts` |
| WebSocket | API nativa `WebSocket` | `frontend/src/hooks/useWebSocket.ts:13` |
| Server prod | nginx | `frontend/nginx.conf` |
| Locale | es-AR | `frontend/index.html:2` |

---

## 2. Arquitectura

### 2.1 Topología

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  trading-engine  │  │       web        │  │     frontend     │
│  Python asyncio  │  │ FastAPI + asyncio│  │  React 19 + nginx│
│  no HTTP server  │  │  expone :8100    │  │  expone :3100    │
└──────────────────┘  └──────────────────┘  └──────────────────┘
         │                     │                      │
         │ writes/reads        │ reads/writes         │ HTTP/WS proxy
         └─────────────────────┴──────────────────────┘
                               │
                               ▼
                   ┌──────────────────────┐
                   │  PostgreSQL 17       │
                   │  expone :5532        │
                   │  (única fuente       │
                   │   de verdad)         │
                   └──────────────────────┘
```

**Separación de responsabilidades** ✅✅✅:

| Aspecto | `trading-engine` | `web` |
|---------|------------------|-------|
| HTTP server | ❌ | ✅ FastAPI :8100 |
| Corre 24/7 | ✅ | ❌ (idle si no hay clientes) |
| Llama Binance | ✅ CCXT REST + WS | ❌ nunca |
| Llama LLM | ✅ Decisor + Supervisor | ❌ nunca |
| Coloca órdenes | ✅ Executor | ❌ nunca |
| Lee Postgres | ✅ todo lo necesario | ✅ todo para UI |
| Escribe Postgres | ✅ `decisions`, `trades`, `ohlcv`, `indicators`, `positions`, `playbook_versions`, `fee_snapshots`, `balance_snapshots`, `config` (algunas claves) | ✅ sólo `config` + `config_history` |
| Comunicación | Sólo vía Postgres (no IPC) | Sólo vía Postgres |

### 2.2 Componentes del `trading-engine`

| Componente | Archivo | Responsabilidad |
|------------|---------|-----------------|
| `EngineScheduler` | `scheduler.py` | APScheduler con jobs async (decisor, supervisor, fees, positions, order_tracker) |
| `PriceCollector` | `collectors/price_collector.py` | Fetch OHLCV multi-TF y persist en `ohlcv` + `indicators` |
| `OrderBookCollector` | `collectors/orderbook_collector.py` | WebSocket Binance (`watch_order_book`); ⚠️ `start()` no se invoca |
| `compute_indicators` | `collectors/indicators.py` | TA con pandas: RSI, MACD, EMAs, ATR, Bollinger, volumen |
| `FeeManager` | `execution/fee_manager.py` | Refresh fees CCXT cada 24h; fallback al último `FeeSnapshot` o 0.001 |
| `ContextBuilder` | `agents/context_builder.py` | Arma dict para los prompts: indicadores multi-TF, posiciones, decisiones recientes, OB, ATR 7d, fees, calibración |
| `LLMClient` | `agents/llm_client.py` | Wrapper Gemini + Groq con retry/backoff |
| `PromptManager` | `agents/prompt_manager.py` | Carga y renderiza prompts; seed playbook v0; persist nuevos playbooks |
| `Decisor` | `agents/decisor.py` | Decisión LLM + overrides + persist `Decision` |
| `Supervisor` | `agents/supervisor.py` | Job diario: métricas + LLM + nuevo playbook + auto-apply config |
| `RiskGate` | `risk/risk_gate.py` | 14 chequeos determinísticos |
| `CircuitBreaker` | `risk/circuit_breaker.py` | Contadores LLM/exchange + `engine_paused` (⚠️ `evaluate()` no usada) |
| `Executor` | `execution/executor.py` | `execute_buy` con SL `STOP_LOSS_LIMIT` + TP `LIMIT`; `execute_sell` market; `record_bracket_fill` |
| `PositionManager` | `execution/position_manager.py` | Refresca `current_price`, `unrealized_pnl` cada 30s |
| `OrderTracker` | `execution/order_tracker.py` | Detecta `close_requested`; matchea ventas inferiendo SL/TP fills |

### 2.3 Periodicidad de jobs

| Job | Frecuencia | Configurable | Fuente |
|-----|------------|--------------|--------|
| `decisor_tick` | `decisor_interval_min` minutos | sí (`config.decisor_interval_min`) | `main.py:302-304` |
| `supervisor_tick` | cron `supervisor_cron` | sí (`config.supervisor_cron`, default `"0 0 * * *"`) | `main.py:305-308` |
| `fees_refresh` | 24h | no | `main.py:309` |
| `positions_tick` | 30s | no | `main.py:310` |
| `order_tracker_tick` | 30s | no | `main.py:311` |

---

## 3. APIs externas

### 3.1 Binance Spot (via CCXT)

| SDK call | Donde | Manejo de error |
|----------|-------|------------------|
| `fetch_ohlcv` | `price_collector.py:47` | warning `engine.ohlcv_fetch_failed_using_cached_data` (`main.py:138-142`) |
| `fetch_trading_fees` | `fee_manager.py:40` | fallback último snapshot o 0.001 |
| `fetch_balance` | `main.py:149` | fallback al último `BalanceSnapshot` |
| `create_market_order` | `executor.py:23-39` | `RuntimeError` si fill cero; propaga al supervisor del loop |
| `create_order` (STOP_LOSS_LIMIT / LIMIT) | `executor.py:31-40` | idem |
| `fetch_ticker` | varios | — |
| `fetch_my_trades` | `order_tracker.py:40` | — |
| `watch_order_book` | `orderbook_collector.py:59` | log + sleep 2s en error |
| `close` | `main.py:323` | — |

**Rate limiting**: `enableRateLimit: True` en `build_binance_client()` (`exchange.py:19`).

### 3.2 Google Gemini

| SDK | Modelos usados |
|-----|-----------------|
| `google-genai` con `Client` async | `gemini-2.5-flash` (Decisor), `gemini-2.5-pro` (Supervisor) |

Configurado opcional: si la API key no está, el cliente queda en `None` y los agentes loguean warning.

### 3.3 Groq

| SDK | Modelos usados |
|-----|-----------------|
| `groq.AsyncGroq` | `groq-llama-3.3-70b` (fallback Decisor) |

Cascade implementado en `LLMClient.call` con retry/backoff.

---

## 4. Modelo de datos (canónico)

> Para detalle exhaustivo columna por columna ver `meli/extracted/raw/code-analysis/shared-db-backtesting-analysis.md`.

### 4.1 Tablas

| Tabla | Migración inicial | PK | Índices clave |
|-------|-------------------|----|----------------|
| `ohlcv` | `001` | `(time, timeframe)` compuesta | `idx_ohlcv_tf (timeframe, time)` |
| `indicators` | `001` | `time` | (GIN sólo en ORM, ⚠️ D-006) |
| `decisions` | `001` | `id UUID` | `idx_decisions_ts`; (GIN en input/output sólo en ORM) |
| `trades` | `001` + `002` | `id UUID` | `idx_trades_status`, `idx_trades_ts` |
| `positions` | `001` | `id UUID` | — |
| `playbook_versions` | `001` | `id UUID` | `version` UNIQUE; (parcial activa sólo en ORM) |
| `config` | `001` + `004` | `key VARCHAR(60)` | — |
| `config_history` | `001` | `id UUID` | — |
| `daily_stats` | `001` | `date` | — |
| `fee_snapshots` | `001` | `id UUID` | `idx_fee_snapshots_ts` |
| `balance_snapshots` | `003` | `id UUID` | `idx_balance_snapshots_ts` |

### 4.2 Convenciones

- ✅✅✅ Tiempos en `TIMESTAMPTZ` (excepto `daily_stats.date` que es `DATE`).
- ✅✅✅ Precios `NUMERIC(18,8)`; PnL `NUMERIC(18,4)` / `NUMERIC(8,4)`; volumen `NUMERIC(24,8)`.
- ✅✅✅ Payloads variables: `JSONB` (`indicators.data`, `decisions.input/output/outcome`, `playbook_versions.pnl_summary`, `daily_stats.breakdown`, `fee_snapshots.raw`).
- ✅✅✅ UUIDs con `server_default=gen_random_uuid()`.

### 4.3 Migraciones aplicadas

| # | Revision | Down | Mensaje |
|---|----------|------|---------|
| 1 | `001` | — | initial schema (10 tablas) |
| 2 | `002` | `001` | add `trades.close_requested` |
| 3 | `003` | `002` | add `balance_snapshots` |
| 4 | `004` | `003` | seed 6 claves nuevas en `config` (decisor v2) |

### 4.4 Schemas Pydantic (en `shared/schemas.py`)

| Modelo | Propósito | Campos clave |
|--------|-----------|---------------|
| `DecisorAction` | Enum BUY / SELL / HOLD | — |
| `MarketRegime` | Enum TRENDING_UP / TRENDING_DOWN / RANGE / HIGH_VOLATILITY | — |
| `DecisorOutput` | JSON de salida del Decisor | `regime`, `confluences`, `action`, `confidence_base` 🔸, `confidence_adjustment` 🔸, `confidence` (derivado), `stop_loss?`, `take_profit?`, `position_size_pct` [0, 0.25], `expected_holding_min` 🔸, `reasoning` (max 800 chars) |
| `TradeOutcome` | Outcome de un trade cerrado | `pnl_usdt`, `pnl_pct`, `close_reason`, `duration_min`, `fees_usdt?` |

---

## 5. API REST (`web`)

✅✅ Todos los endpoints bajo `/api` excepto WebSocket. Sin autenticación.

### 5.1 Health / Diagnóstico

| Método | Path | Handler | Body |
|--------|------|---------|------|
| GET | `/api/health` | `health.health` | — |
| GET | `/api/ping` | `health.ping` | — |

### 5.2 Configuración y Control

| Método | Path | Handler | Body | Notas |
|--------|------|---------|------|-------|
| GET | `/api/config` | `config.list_config` | — | Excluye `supervisor_run_now` |
| PUT | `/api/config/{key}` | `config.update_config` | `ConfigUpdate {value: str}` | 400 si key inválida |
| POST | `/api/kill-switch` | `control.toggle_kill_switch` | `KillSwitchBody {enabled: bool}` | — |
| POST | `/api/mode` | `control.set_mode` | `ModeBody {mode, confirmation}` | LIVE requiere `confirmation == "CONFIRMO TRADING REAL"` |
| POST | `/api/supervisor/run` | `control.run_supervisor_now` | — | Set flag para que el engine corra el supervisor fuera de cron |
| GET | `/api/config/suggestions` | `suggestions.get_config_suggestions` | — | Última decisión supervisor con `config_suggestions` |

### 5.3 Trading / Operación

| Método | Path | Handler | Query/Body | Notas |
|--------|------|---------|------------|-------|
| GET | `/api/decisions` | `decisions.list_decisions` | query: `agent`, `executed`, `limit` (def 100, max 500) | Orden desc por `ts` |
| GET | `/api/trades` | `trades.list_trades` | query: `status`, `limit` | Orden desc por `ts_open` |
| POST | `/api/trades/{trade_id}/close` | `trades.request_trade_close` | path | Setea `close_requested=true` |
| GET | `/api/positions` | `positions.list_positions` | — | Sólo `status="open"` |
| GET | `/api/balance` | `balance.get_balance` | — | `realized_pnl_today` siempre 0 (TODO) |
| GET | `/api/playbook/active` | `playbook.active` | — | — |
| GET | `/api/playbook/history` | `playbook.history` | — | — |
| POST | `/api/playbook/{version}/activate` | `playbook.activate` | path | Rollback / cambio de versión |
| PATCH | `/api/playbook/{version}/content` | `playbook.edit_content` | `PlaybookEditIn {content: str}` | Edición manual |
| GET | `/api/stats/daily` | `stats.daily_stats` | — | Agrega on-the-fly, no usa tabla `daily_stats` |

### 5.4 WebSocket

`GET /ws` upgrade. Mensajes server→client en formato `{"event": "<name>", "data": <payload>}`:

| Evento | Frecuencia | Payload | Status |
|--------|-----------|---------|--------|
| `ticker` | cada 5s | `{symbol, price, ts}` | ✅ |
| `decision` | cada 2s, sólo si hay nuevas | resumen `Decision` | ✅ |
| `positions` | cada 2s | lista de posiciones abiertas | ✅ |
| `trade_opened` | — | (design doc §9.2) | ⚠️ NO implementado |
| `trade_closed` | — | (design doc §9.2) | ⚠️ NO implementado |
| `playbook_updated` | — | (design doc §9.2) | ⚠️ NO implementado |
| `kill_switch_triggered` | — | (design doc §9.2) | ⚠️ NO implementado |

Cliente → servidor: no se aceptan mensajes.

### 5.5 Schemas Pydantic de la API

> Detalle completo en `meli/extracted/raw/code-analysis/web-frontend-analysis.md` §A.4.

| Modelo | Archivo |
|--------|---------|
| `ConfigEntryOut`, `ConfigUpdate` | `web/api/config.py` |
| `KillSwitchBody`, `ModeBody` | `web/api/control.py` |
| `DecisionOut` | `web/api/decisions.py` |
| `TradeOut` | `web/api/trades.py` (⚠️ no incluye `order_id_open/close`) |
| `PositionOut` | `web/api/positions.py` |
| `DailyStatsOut` | `web/api/stats.py` |
| `BalanceOut` | `web/api/balance.py` |
| `PlaybookOut`, `PlaybookEditIn` | `web/api/playbook.py` |

---

## 6. Frontend (React 19)

### 6.1 Estructura `frontend/src/`

```
src/
  api/client.ts          — Cliente REST hacia /api/*
  hooks/useWebSocket.ts  — Hook WS con last/connected/reconectar (3s)
  types/index.ts         — Tipos TS alineados a payloads API
  pages/Dashboard.tsx    — Live monitor
  pages/Trades.tsx       — Historial + cierre manual
  pages/Decisions.tsx    — Audit log LLM + detalle lateral
  pages/Playbook.tsx     — Playbook activo + edición + historial
  pages/Config.tsx       — Editor (SliderField, ConfigField, FIELD_DEFS, GROUPS)
  pages/Health.tsx       — Diagnóstico
  App.tsx                — Router + NavBar
  main.tsx               — Mount StrictMode + CSS global
  index.css              — Tailwind imports
```

No hay carpetas separadas `components/`, `services/`, `stores/`, ni `i18n/`.

### 6.2 Routing

```typescript
<Routes>
  <Route path="/"           element={<Dashboard />} />
  <Route path="/trades"     element={<Trades />} />
  <Route path="/decisions"  element={<Decisions />} />
  <Route path="/playbook"   element={<PlaybookPage />} />
  <Route path="/config"     element={<Config />} />
  <Route path="/health"     element={<Health />} />
</Routes>
```

### 6.3 Cliente API (`frontend/src/api/client.ts`)

Wrappers `get`, `put`, `post`, `patch` sobre `fetch` con manejo de errores; exporta `api.{trades, closeTrade, decisions, positions, balance, config, setConfig, killSwitch, runSupervisor, setMode, playbookActive, playbookHistory, playbookActivate, playbookEditContent, dailyStats, configSuggestions}`.

### 6.4 Hook `useWebSocket`

`frontend/src/hooks/useWebSocket.ts:5-28` expone `{last, connected}`; reconecta automáticamente cada 3000ms al `onclose`.

### 6.5 i18n

Sin librería. UI completamente en español, hardcodeado en componentes. `frontend/index.html` tiene `lang="es-AR"`. Formatos de número/fecha usan `toLocaleString("es-AR", ...)`.

---

## 7. Code Ownership Map (Fase 4.5)

Mapeo componente → archivos con scoring de propiedad.

| Componente | Rol | Primary files (0.8–1.0) | Supporting (0.5–0.79) | Shared (0.2–0.49) |
|------------|-----|--------------------------|------------------------|-------------------|
| **Engine entrypoint** | Bootstrap | `trading-engine/main.py` (1.0) | `trading-engine/config.py` (0.6), `trading-engine/exchange.py` (0.6), `trading-engine/scheduler.py` (0.7) | `shared/db/base.py` (0.3) |
| **Decisor** | Agent | `trading-engine/agents/decisor.py` (1.0), `trading-engine/agents/prompts/decisor_system.txt` (1.0), `trading-engine/agents/prompts/decisor_user.txt` (1.0) | `trading-engine/agents/context_builder.py` (0.7), `trading-engine/agents/llm_client.py` (0.6), `trading-engine/agents/prompt_manager.py` (0.6) | `shared/schemas.py` (0.4), `shared/db/models.py` (0.3) |
| **Supervisor** | Agent | `trading-engine/agents/supervisor.py` (1.0), `trading-engine/agents/prompts/supervisor_system.txt` (1.0), `trading-engine/agents/prompts/supervisor_user.txt` (1.0), `trading-engine/agents/prompts/playbook_v0.md` (1.0) | `trading-engine/agents/llm_client.py` (0.6), `trading-engine/agents/prompt_manager.py` (0.6) | `shared/config_store.py` (0.4), `shared/db/models.py` (0.3) |
| **Risk Gate** | Risk | `trading-engine/risk/risk_gate.py` (1.0) | — | `shared/schemas.py` (0.3) |
| **Circuit Breaker** | Risk | `trading-engine/risk/circuit_breaker.py` (1.0) | — | (no integrado en main.py) |
| **PriceCollector** | Data | `trading-engine/collectors/price_collector.py` (1.0), `trading-engine/collectors/indicators.py` (0.9) | — | `shared/db/models.py` (0.3) |
| **OrderBookCollector** | Data | `trading-engine/collectors/orderbook_collector.py` (1.0) | — | — |
| **FeeManager** | Execution | `trading-engine/execution/fee_manager.py` (1.0) | — | `shared/db/models.py` (0.3) |
| **Executor** | Execution | `trading-engine/execution/executor.py` (1.0) | — | `shared/db/models.py` (0.3) |
| **PositionManager** | Execution | `trading-engine/execution/position_manager.py` (1.0) | — | `shared/db/models.py` (0.3) |
| **OrderTracker** | Execution | `trading-engine/execution/order_tracker.py` (1.0) | `trading-engine/execution/executor.py` (0.5) | `shared/db/models.py` (0.3) |
| **Scheduler** | Infra | `trading-engine/scheduler.py` (1.0) | — | — |
| **Web bootstrap** | API | `web/main.py` (1.0) | — | `shared/db/base.py` (0.3), `web/ws/feeds.py` (0.6) |
| **Web API routers** | API | `web/api/*.py` (1.0 cada uno) | — | `shared/db/models.py` (0.4), `shared/config_store.py` (0.4) |
| **WebSocket feeds** | API | `web/ws/feeds.py` (1.0), `web/ws/manager.py` (1.0) | — | `shared/db/models.py` (0.3) |
| **Frontend pages** | UI | `frontend/src/pages/*.tsx` (1.0 cada una) | `frontend/src/api/client.ts` (0.5), `frontend/src/hooks/useWebSocket.ts` (0.5) | `frontend/src/types/index.ts` (0.3) |
| **Schemas compartidos** | Shared | `shared/schemas.py` (1.0) | — | (usado por engine + tests) |
| **Config store** | Shared | `shared/config_store.py` (1.0) | — | (usado por engine + web) |
| **DB layer** | Shared | `shared/db/base.py` (1.0), `shared/db/models.py` (1.0) | — | — |
| **Migraciones** | Infra | `trading-engine/alembic/versions/*.py` (1.0 cada una), `trading-engine/alembic/env.py` (1.0) | — | `shared/db/models.py` (0.4) |
| **Backtester** | Standalone | `backtesting/runner.py` (1.0) | `backtesting/tests/test_runner.py` (0.7) | — |

---

## 8. Deployment

### 8.1 Docker Compose

`docker-compose.yml` define 4 servicios:

| Servicio | Imagen / Build | Puertos host | Volúmenes | Restart |
|----------|----------------|--------------|-----------|---------|
| `postgres` | `postgres:17-alpine` (oficial) | 5532 → 5432 | `postgres_data` | `unless-stopped` |
| `trading-engine` | build `./trading-engine/Dockerfile` | — (sin puerto) | `./trading-engine:/app`, `./shared:/app/shared` | `unless-stopped` |
| `web` | build `./web/Dockerfile` | 8100 → 8000 | `./web:/app`, `./shared:/app/shared` | `unless-stopped` |
| `frontend` | build `./frontend/Dockerfile` | 3100 → 80 | — | `unless-stopped` |

### 8.2 Variables de entorno (`.env`)

| Variable | Uso | Default |
|----------|-----|---------|
| `POSTGRES_USER` | Postgres | `trader` |
| `POSTGRES_PASSWORD` | Postgres | (requerido) |
| `POSTGRES_DB` | Postgres | `crypto_ai_trading` |
| `DATABASE_URL` | Engine + Web | construido dinámicamente con `asyncpg` |
| `BINANCE_API_KEY` | Engine | (requerido) |
| `BINANCE_API_SECRET` | Engine | (requerido) |
| `BINANCE_TESTNET` | Engine | `true` por default |
| `GEMINI_API_KEY` | Engine | (opcional, sin él Decisor falla) |
| `GROQ_API_KEY` | Engine | (opcional, fallback) |
| `ALLOWED_ORIGINS` | Web (CORS) | `http://localhost:3100` |
| `TRADING_MODE` | Engine (declarado pero no consumido — D-005) | — |
| `LOG_LEVEL` | Engine (declarado pero no consumido) | — |

### 8.3 Healthchecks

Sólo `postgres` tiene healthcheck explícito:
```yaml
test: ["CMD-SHELL", "pg_isready -U trader -d crypto_ai_trading"]
interval: 5s
timeout: 3s
retries: 10
```

Engine y web dependen de `postgres: service_healthy`.

### 8.4 Migraciones

```bash
docker-compose run --rm trading-engine alembic upgrade head
```

Ejecutarse una sola vez al inicio o tras un pull con migraciones nuevas.

---

## 9. Observabilidad

### 9.1 Logging

| Servicio | Library | Formato | Configuración |
|----------|---------|---------|----------------|
| `trading-engine` | structlog | JSON | `trading-engine/main.py:41-46` |
| `web` | structlog (esperado) | JSON | (no detectado explícitamente; uvicorn loggea por default) |

### 9.2 Métricas y observabilidad externa

❓ No detectado uso de Prometheus, OpenTelemetry, Datadog, NewRelic, ni Sentry. **Sin observabilidad externa.**

### 9.3 Recursos sugeridos para producción (no implementados)

- `pg_dump` diario (cron host); no detectado en el repo.
- Alerting (telegram/discord/email): mencionado en design doc §13 future work, no en código.

---

## 10. Testing

### 10.1 Coverage por capa

| Capa | Tests presentes | Tests faltantes (relevantes) |
|------|------------------|-------------------------------|
| `trading-engine` | 19 archivos en `tests/` | `test_order_tracker.py` (gap), `test_models.py` desactualizado vs `balance_snapshots` |
| `web` | 6 archivos en `tests/` | positions, balance, playbook, stats/daily, config/suggestions, WS, supervisor/run |
| `shared` | (cubiertos vía engine + web) | — |
| `backtesting` | 1 archivo (`test_runner.py`) | `test_signal_buy_requires_min_confluences` parece roto (D-019) |
| `frontend` | 0 — sin tests | toda la SPA sin coverage |

### 10.2 Stack de testing

- ✅✅✅ `pytest` + `pytest-asyncio` (modo `auto`) + `freezegun` + `pytest-cov`.
- ✅ Web `conftest.py` adapta el ORM Postgres a SQLite para tests rápidos.

---

## 11. Decisiones técnicas registradas

### 11.1 Postgres como único bus

✅✅ **Por qué**: aislamiento de fallos (si `web` cae, el engine sigue tradeando; si el engine cae, el dashboard sigue mostrando histórico). Alternativa rechazada: Redis Streams + microservicios (over-engineering para single-pair single-user).

### 11.2 SQL puro vs ORM

Mezcla: SQLAlchemy 2 async como ORM principal; algunos `INSERT` de migraciones usan SQL crudo (`004`) por simplicidad.

### 11.3 Asincronía total

Toda la stack es async: `asyncpg`, `SQLAlchemy 2 async`, `httpx`, `ccxt async`, `APScheduler.AsyncIOScheduler`, `FastAPI`, `WebSocket nativo`.

### 11.4 Pydantic 2 como schema universal

✅ `DecisorOutput` y `TradeOutcome` en `shared/schemas.py` validan los JSONs del LLM antes de tocarlos.

### 11.5 Indicadores con pandas puro (sin pandas-ta)

✅⚠️ Discrepancia con design doc (D-018). Decisión técnica revisada post-design-doc; reduce dependencias pero pierde optimizaciones.

### 11.6 Frontend sin state manager

✅⚠️ Decisión consciente: el app es simple (6 páginas, una sola sesión, fuente de verdad en backend). `useState`/`useEffect` por página + `useWebSocket` para push.

### 11.7 No usar `vectorbt` en backtester

✅⚠️ Discrepancia con design doc (D-016). pandas puro suficiente para reglas baseline sin LLM.

---

## 12. Áreas con riesgo técnico identificado

1. 🔴 **OrderBook WS no se inicia** → decisor sin microestructura real (D-002).
2. 🔴 **PnL diario y drawdown total siempre 0** al RiskGate → 2 reglas críticas no disparan (D-001).
3. 🔴 **CircuitBreaker no integrado** → segunda línea de defensa ausente (D-003).
4. 🟠 **Índices GIN y FKs sólo en ORM** → DB productiva carece de ellos (D-006).
5. 🟠 **Auto-rollback de playbook no implementado** (D-005).
6. 🟠 **WebSocket emite 3 de 6 eventos** (D-009).
7. 🟠 **`/health` sin métricas LLM** (D-013).
8. 🟡 **`min_fees_to_tp_ratio` no pasa de config a RG** (D-004).
9. 🟡 **Sin partial fills en Executor** (asume fill total o falla).
10. 🟡 **`daily_stats` y `balance_snapshots` no se popula automáticamente** (no hay cron jobs).
