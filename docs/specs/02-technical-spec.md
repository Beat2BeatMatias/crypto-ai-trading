# Especificación Técnica — Crypto AI Trading

> Audiencia: Tech leads, devs, SRE.
> Versión: 1.0 — 2026-05-14.

---

## 1. Vista de alto nivel

Tres servicios en contenedores Docker que comparten una única base de datos Postgres como **fuente de verdad**. Los servicios **no se comunican entre sí por IPC**; sincronizan estado leyendo y escribiendo en la BD.

```
                  ┌────────────────────────────────────────────────┐
                  │                  Postgres 17                  │
                  │ (decisions, trades, positions, ohlcv,         │
                  │  indicators, playbook_versions, config,       │
                  │  config_history, fee_snapshots, balance_*…)   │
                  └─────────▲──────────────────────────▲──────────┘
                            │                          │
                 RW (todo)  │                          │ R (todo) + W (config*)
                            │                          │
        ┌───────────────────┴──────┐         ┌─────────┴─────────────┐
        │      trading-engine      │         │          web          │
        │ (Python 3.12, asyncio)   │         │ (FastAPI + uvicorn)   │
        │                          │         │                       │
        │ • PriceCollector (CCXT)  │         │ • REST API (/api/*)   │
        │ • OrderBookCollector (WS)│         │ • WebSocket /ws       │
        │ • Decisor LLM            │         │ • Ticker broadcaster  │
        │ • RiskGate + Override    │         │                       │
        │ • Executor (CCXT)        │         └───────────▲───────────┘
        │ • OrderTracker           │                     │
        │ • Supervisor LLM         │                     │ HTTP/WS
        │ • CircuitBreaker         │                     │
        │ • APScheduler            │              ┌──────┴──────┐
        └─────────▲──────────────┬─┘              │  frontend   │
                  │              │                │ React + Vite│
       CCXT REST/WS           Gemini/Groq         │ Tailwind v4 │
                  │              │                └─────────────┘
            ┌─────┴───────┐ ┌────┴──────┐
            │   Binance   │ │ LLM Provs │
            │ (testnet/   │ │ Gemini    │
            │  mainnet)   │ │ Groq      │
            └─────────────┘ └───────────┘
```

### 1.1 Stack tecnológico

| Capa | Tecnología |
|------|------------|
| Lenguaje backend | Python 3.12 (asyncio en todo el engine y la web) |
| Web framework | FastAPI + uvicorn |
| ORM / DB driver | SQLAlchemy 2.0 (async) + asyncpg |
| Migraciones | Alembic |
| Scheduler engine | APScheduler `AsyncIOScheduler` (timezone UTC) |
| Exchange SDK | `ccxt.async_support` + `ccxt.pro` (WS order book) |
| LLM SDKs | `google-genai` (Gemini), `groq` (Groq async) |
| Validación | Pydantic v2 + pydantic-settings |
| Logging | `structlog` con `JSONRenderer` |
| Indicadores técnicos | pandas (sin libs externas de TA) |
| Frontend | React 19, React Router 7, Vite 6, Tailwind 4, recharts, react-markdown |
| HTTP frontend → API | fetch (proxy nginx en Docker) |
| Realtime | WebSocket nativo |
| Contenedores | Docker Compose v2 (Postgres 17-alpine + 3 servicios) |
| Testing | pytest + pytest-asyncio + freezegun |

### 1.2 Servicios y puertos

| Servicio | Puerto host | Puerto interno | Notas |
|----------|-------------|----------------|-------|
| `postgres` | 5532 | 5432 | Volumen `postgres_data` persistente |
| `trading-engine` | — | — | Daemon sin HTTP |
| `web` | 8100 | 8000 | API + WS |
| `frontend` | 3100 | 80 | nginx sirviendo bundle Vite |

> Estos puertos están escogidos para convivir con el proyecto hermano `crypto-arbitrage` (3000/8000/5432).

---

## 2. Servicio `trading-engine`

Daemon `asyncio` orquestado por APScheduler. Sin servidor HTTP.

### 2.1 Entrypoint (`trading-engine/main.py`)

```text
run():
  • EngineSettings (env-derived) + structlog config
  • Crea SQLAlchemy async engine + session_factory
  • Inicializa Gemini / Groq clients (degraded si faltan keys)
  • Construye CCXT Binance client (set_sandbox_mode si testnet)
  • Bootstrap (1 sola vez):
      - ConfigStore.seed_defaults()
      - PromptManager.seed_playbook_v0()
      - FeeManager.refresh()
  • OrderBookCollector (WS) — se mantiene en memoria
  • CircuitBreaker (umbrales se actualizan cada tick desde config)
  • EngineScheduler:
      - add_decisor       interval = decisor_interval_min
      - add_supervisor    cron     = supervisor_cron (UTC)
      - add_fee_refresh   24 h
      - add_position_refresh   30 s
      - add_order_tracker      30 s
  • signal handler SIGINT/SIGTERM → shutdown ordenado
```

### 2.2 Componentes principales

| Módulo | Responsabilidad |
|--------|-----------------|
| `config.py` | `EngineSettings` (Pydantic Settings). Solo settings env-derivados (URLs, keys, símbolo). El resto vive en DB. |
| `exchange.py` | Factory `ccxt_async.binance` con sandbox según `BINANCE_TESTNET`. |
| `scheduler.py` | `EngineScheduler` envoltura tipada de APScheduler. |
| `collectors/price_collector.py` | Fetch OHLCV vía CCXT, upsert por (`time`,`timeframe`), recomputa indicadores. Soporta sqlite + postgres. |
| `collectors/indicators.py` | RSI/MACD/EMA/BB/ATR; TR **winsorizado** a 3×mediana móvil para neutralizar velas anómalas. |
| `collectors/orderbook_collector.py` | `OrderBookCollector` con WS CCXT pro; expone `snapshot(levels=10)` derivando spread, imbalance, walls. |
| `execution/fee_manager.py` | Fetch trading fees; cachea + refresca 24 h; fallback a último `FeeSnapshot` si Binance falla. |
| `execution/executor.py` | `execute_buy` (market + bracket SL/TP), `execute_sell`, `record_bracket_fill` (no emite orden, solo reconcilia BD). |
| `execution/order_tracker.py` | Cada 30 s lista trades abiertos; si `close_requested=true` → SELL; recorre `fetch_my_trades` y matchea fills de venta dentro de ±2% qty. |
| `execution/position_manager.py` | Count/list de open positions; recálculo P&L no realizado con precio actual. |
| `risk/risk_gate.py` | `RiskGate.validate` aplica R1–R10 (ver `05-risk-and-safety.md`). |
| `risk/circuit_breaker.py` | Cuenta fallas consecutivas (LLM, exchange) y daily/max drawdown; setea `engine_paused`. |
| `agents/llm_client.py` | `LLMClient` con cascade de providers (Gemini Flash/Pro + 8 modelos Groq). Retry con backoff exponencial salvo rate-limit (salta al siguiente provider). |
| `agents/prompt_manager.py` | Carga `prompts/*.txt`, sustitución de placeholders, persiste `PlaybookVersion`. |
| `agents/context_builder.py` | Construye el dict de contexto unificado para el Decisor a partir de BD + orderbook. |
| `agents/decisor.py` | Renderiza system+user prompts, llama LLM, valida `DecisorOutput`, aplica override determinístico, persiste Decision. |
| `agents/supervisor.py` | Compute métricas 24h, llamada LLM al playbook, llamada LLM #2 para config suggestions, persistencia. |

### 2.3 Diagrama de secuencia — Decisor tick

```
APScheduler ──▶ decisor_tick()
   │
   ├─ check cb.engine_paused → return si pausado
   │
   ├─ check supervisor_run_now flag → trigger supervisor_tick()
   │
   ├─ Open AsyncSession
   │    ├─ Read ~30 keys de ConfigStore
   │    ├─ PriceCollector.fetch_and_persist (5 timeframes)
   │    ├─ PriceCollector.compute_and_persist_indicators
   │    ├─ FeeManager.get_or_refresh
   │    ├─ fetch_balance → BalanceSnapshot
   │    │     (si falla: usdt=0, btc=Σ posiciones abiertas)
   │    ├─ orderbook.snapshot(levels=10)
   │    ├─ Decisor.decide(...)
   │    │     ├─ ContextBuilder.build(...)
   │    │     ├─ LLM.call(provider + fallbacks)
   │    │     ├─ json.loads + DecisorOutput.model_validate
   │    │     ├─ _apply_deterministic_overrides
   │    │     └─ INSERT Decision (input + output + metrics)
   │    ├─ RiskGate.validate(...)
   │    │     └─ Si rechazado: UPDATE Decision.rejected_reason → return
   │    └─ Executor.execute_buy / execute_sell (si action ≠ HOLD)
   │
   └─ Commit + close session
```

### 2.4 Cascade de LLM providers

| Slot | Default Decisor | Default Supervisor |
|------|----------------|--------------------|
| primary | `groq-llama-3.3-70b` | `gemini-2.5-pro` |
| fallback CSV | `gemini-2.5-flash,groq-llama-4-scout,groq-gpt-oss-120b,groq-qwen3-32b,groq-llama-3.1-8b` | `groq-llama-3.3-70b,groq-llama-4-scout,groq-gpt-oss-120b,gemini-2.5-flash` |

`LLMClient._is_rate_limit` reconoce 429 / `ResourceExhausted` / "rate limit" en el mensaje y **salta sin retries** al siguiente provider.

Retries normales: `max_retries=3` con backoff `0.5 * 2^attempt`.

### 2.5 Errores recurrentes y comportamiento

| Falla | Comportamiento |
|-------|---------------|
| `json.JSONDecodeError` o `pydantic.ValidationError` en respuesta del LLM | `_hold_decision("parse_error")`, persistido con `rejected_reason="parse_error: …"`. |
| Excepción genérica del LLM | `_hold_decision("llm_error")`, `cb.record_llm_failure()`. |
| `fetch_balance` falla | `engine.balance_unavailable_using_db_fallback`, usdt=0. |
| `fetch_ohlcv` falla | log warning, continúa con datos cacheados. |
| `fetch_trading_fees` falla | usa último `FeeSnapshot` de BD o (0.001, 0.001). |
| Orden buy con `filled=0` o `avg_price=0` | `RuntimeError`, `cb.record_exchange_failure()`. |

---

## 3. Servicio `web`

FastAPI + uvicorn. Solo escribe en `config`, `config_history`, `trades.close_requested` y `playbook_versions.active|content`.

### 3.1 Entrypoint (`web/main.py`)

- Lifespan: crea engine/session_factory, lanza `ticker_broadcaster` async task.
- CORS configurable (`ALLOWED_ORIGINS`, default `http://localhost:3100`).
- Si la URL es SQLite → `Base.metadata.create_all` (modo dev/test).
- Routers en prefijo `/api`: health, trades, decisions, positions, balance, playbook, config, control, stats, suggestions.
- WebSocket `/ws` (sin prefijo).

### 3.2 Endpoints REST

| Método | Path | Resp | Descripción |
|--------|------|------|-------------|
| GET | `/api/health` | `{ok, db, engine{ok,detail,last_decision_age_min}, binance{ok,detail}}` | Health check + frescura del engine (último decision <15 min) y de Binance (último OHLCV 1m <15 min). |
| GET | `/api/ping` | `{pong:true}` | Liveness. |
| GET | `/api/trades?status=&limit=` | `TradeOut[]` | Listado paginable (default 100, max 500). |
| POST | `/api/trades/{trade_id}/close` | `TradeOut` | Solicita cierre (`close_requested=true`). 409 si no está abierto. |
| GET | `/api/decisions?agent=&executed=&limit=` | `DecisionOut[]` | Historial. |
| GET | `/api/positions` | `PositionOut[]` | Open positions. |
| GET | `/api/balance` | `BalanceOut` | Última `BalanceSnapshot` + qty BTC bloqueada en posiciones. |
| GET | `/api/playbook/active` | `PlaybookOut \| null` | Versión activa. |
| GET | `/api/playbook/history` | `PlaybookOut[]` | Todas las versiones, descendente. |
| POST | `/api/playbook/{v}/activate` | `{ok, version}` | Rollback / activación. |
| PATCH | `/api/playbook/{v}/content` | `{ok, version}` | Editar contenido en caliente. |
| GET | `/api/config` | `ConfigEntryOut[]` | Excluye keys internas (`supervisor_run_now`). |
| PUT | `/api/config/{key}` | `{ok, key, value}` | Valida `ConfigKey`; 404 si seed pendiente. |
| POST | `/api/kill-switch` | `{ok, kill_switch}` | Toggle kill switch. |
| POST | `/api/mode` | `{ok, mode}` | Cambia entre PAPER_TRADING/LIVE. Para LIVE requiere `confirmation == "CONFIRMO TRADING REAL"`. |
| POST | `/api/supervisor/run` | `{ok, queued:true}` | Setea `supervisor_run_now=true`; el engine lo consume en el próximo tick. |
| GET | `/api/stats/daily` | `DailyStatsOut` | Métricas del día desde 00:00 UTC. |
| GET | `/api/config/suggestions` | `{ generated_at, suggestions, summary, ... } \| null` | Última sugerencia del Supervisor (`Decision.output.config_suggestions`). |

### 3.3 WebSocket `/ws`

- Loop cada 2 s consulta:
  - `Decision` con `ts > last_decision_ts` → emite evento `decision`.
  - `Position WHERE status='open'` → emite snapshot `positions`.
- Tarea de fondo `ticker_broadcaster` cada 5 s consulta REST público `https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT` (o testnet) y emite `ticker`.
- Formato de mensaje:
  ```json
  { "event": "decision"|"ticker"|"positions", "data": { ... } }
  ```

### 3.4 Modelo de errores

| HTTP | Causa típica |
|------|-------------|
| 400 | Body inválido (Pydantic), `mode=LIVE` sin frase de confirmación. |
| 404 | Trade no encontrado, config key desconocido, playbook version inexistente, seed faltante. |
| 409 | Trade no está abierto. |
| 422 | Validación Pydantic estándar. |

---

## 4. Frontend (`frontend/`)

### 4.1 Estructura

```
frontend/src/
├── App.tsx              ← Router + NavBar
├── main.tsx             ← bootstrap React 19
├── api/client.ts        ← wrapper fetch (BASE = "/api")
├── hooks/useWebSocket.ts
├── pages/
│   ├── Dashboard.tsx    ← balance, posiciones, última decisión, P&L día
│   ├── Trades.tsx       ← listado + cerrar
│   ├── Decisions.tsx    ← historial con input/output JSON
│   ├── Playbook.tsx     ← markdown + rollback + edit
│   ├── Config.tsx       ← 60+ parámetros tipados + kill switch + modo
│   └── Health.tsx       ← estado motor/DB/Binance
└── types/index.ts       ← interfaces TS espejo de los Pydantic
```

### 4.2 Comunicación

- API: fetch al mismo origen, ruta `/api/*` (proxy nginx).
- WebSocket: `${ws://|wss://}${host}/ws` (mismo host del frontend).
- Polling de respaldo: health 15 s, balance 30 s.

### 4.3 Build / serving

- Vite build → bundle estático.
- nginx (`frontend/nginx.conf`) sirve el SPA y proxea `/api` y `/ws` al servicio `web` interno.

---

## 5. Modelo de datos (resumen)

Ver `03-data-model.md` para detalle de columnas, índices y migraciones.

| Tabla | Owner write | Propósito |
|-------|------------|-----------|
| `ohlcv` | engine | Velas (time, timeframe) PK compuesto, upsert idempotente. |
| `indicators` | engine | JSONB con indicadores por timeframe + GIN. |
| `decisions` | engine | Log de cada decisión LLM. Input+output JSONB con índice GIN. |
| `trades` | engine | Operaciones (BUY/SELL). Estados open/closed. |
| `positions` | engine | Estado en tiempo real, con P&L no realizado. |
| `playbook_versions` | engine (Supervisor) y web (rollback) | Versionado con índice único parcial sobre `active=true`. |
| `config` | engine (seed/auto-apply) + web | Clave/valor tipado con descripciones. |
| `config_history` | engine + web | Auditoría inmutable. |
| `daily_stats` | engine | Métricas agregadas (poco usado por ahora). |
| `fee_snapshots` | engine | Snapshots de fees Binance. |
| `balance_snapshots` | engine | Snapshots de balance USDT/BTC. |

### 5.1 Convenciones de tipos

- `TIMESTAMPTZ` para todos los tiempos.
- `NUMERIC(18,8)` para precios/BTC; `NUMERIC(18,4)` para USDT/PnL; `NUMERIC(8,4)` para %; `NUMERIC(8,6)` para fees.
- `JSONB` para payloads de LLM y datos heterogéneos.
- UUIDs `gen_random_uuid()` server-side.

---

## 6. Configuración runtime (`config` table)

Singleton `ConfigStore` (`shared/config_store.py`). Enum `ConfigKey` define **60+** parámetros, agrupados en:

| Grupo | Keys |
|-------|------|
| Operación | `mode`, `decisor_interval_min`, `supervisor_cron`, `kill_switch`, `supervisor_run_now`. |
| Riesgo absoluto | `max_position_pct`, `max_simultaneous_trades`, `daily_stop_pct`, `max_drawdown_pct`, `max_slippage_pct`, `default_rr_ratio`. |
| LLM providers | `decisor_provider`, `supervisor_provider`, `fallback_providers`, `supervisor_fallback_providers`, `llm_max_retries`, `llm_timeout_sec`. |
| Order book | `orderbook_levels`. |
| ATR / R:R | `atr_timeframe`, `min_rr_ratio`, `sl_atr_multiplier`, `sl_atr_max_multiplier`. |
| Umbrales confidence por régimen | `conf_threshold_trending_up`, `conf_threshold_range`, `conf_threshold_high_vol`. |
| RSI filter | `rsi_overbought_1h`. |
| Fórmula de confianza | `conf_base_*`, `peso_timeframe_*`, `peso_regime_*`, `adj_*`, `factor_conf_*`, `factor_regime_non_trending`. |
| Decisor v2 | `min_fees_to_tp_ratio`, `min_confluences_buy`, `cooldown_after_sell_min`, `subjective_adj_max`, `expected_holding_max_min`, `confluence_weak_factor`. |

Comportamiento:

- `seed_defaults()` inserta sólo claves faltantes (idempotente).
- `get_typed()` castea según `value_type` (`int|float|bool|json|string`).
- `set()` actualiza valor + inserta `ConfigHistory` con `changed_by`.

### 6.1 Auto-apply Supervisor (`_SAFE_BOUNDS`)

```python
_SAFE_BOUNDS = {
  "sl_atr_multiplier":          (0.1, 0.8),
  "min_rr_ratio":               (1.0, 3.0),
  "decisor_interval_min":       (5, 60),
  "max_position_pct":           (0.01, 0.20),
  "conf_threshold_trending_up": (0.40, 0.85),
  "conf_threshold_range":       (0.50, 0.90),
  "conf_threshold_high_vol":    (0.60, 0.95),
}
_VALID_ATR_TIMEFRAMES = {"5m", "15m", "1h"}
```

`daily_stop_pct` y `max_drawdown_pct` están **excluidos** intencionalmente — el operador debe cambiarlos manualmente.

---

## 7. Calidad y testing

- `pytest` + `pytest-asyncio` + `freezegun`.
- Coverage actual: `trading-engine/.coverage` y `.coverage` en raíz.
- Tests por módulo en `trading-engine/tests/` y `web/tests/` espejo de la estructura productiva.
- BD de tests: sqlite in-memory (modo dev). El collector y el playbook manager tienen ramas específicas para sqlite vs postgres en los `INSERT … ON CONFLICT`.

Comandos:

```bash
docker-compose run --rm trading-engine pytest
docker-compose run --rm web pytest
```

---

## 8. Operación

### 8.1 Variables de entorno (`.env`)

| Key | Default | Uso |
|-----|---------|-----|
| `POSTGRES_USER`/`PASSWORD`/`DB` | `trader` / *(secret)* / `crypto_ai_trading` | Postgres init. |
| `DATABASE_URL` | `postgresql+asyncpg://...:5432/...` | SQLAlchemy. |
| `BINANCE_API_KEY` / `SECRET` | — | CCXT. |
| `BINANCE_TESTNET` | `true` | Sandbox CCXT. |
| `GEMINI_API_KEY` | — | LLM Decisor / Supervisor. |
| `GROQ_API_KEY` | — | Fallbacks. |
| `TRADING_MODE` | `PAPER_TRADING` | Sólo informativo en .env; la verdad es `config.mode`. |
| `LOG_LEVEL` | `INFO` | structlog. |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8000` | uvicorn. |
| `ALLOWED_ORIGINS` | `http://localhost:3100` | CORS. |

### 8.2 Despliegue (Docker Compose)

```bash
cp .env.example .env
docker-compose build
docker-compose run --rm trading-engine alembic upgrade head
docker-compose up -d
docker-compose logs -f trading-engine
```

### 8.3 Migraciones

| Rev | Cambio |
|-----|--------|
| 001 | Schema inicial (ohlcv, indicators, trades, decisions, positions, playbook_versions, config, config_history, daily_stats, fee_snapshots). |
| 002 | `trades.close_requested boolean default false` |
| 003 | Tabla `balance_snapshots`. |
| 004 | Seed de 6 keys Decisor v2 (`min_fees_to_tp_ratio`, `min_confluences_buy`, …). |

### 8.4 Operaciones rutinarias

- **Logs**: `docker-compose logs -f trading-engine` (eventos clave: `scheduler.started`, `ohlcv.persisted`, `indicators.persisted`, `decisor.decided`, `decision.rejected`, `executor.buy_executed`, `supervisor.playbook_saved`, `order_tracker.bracket_detected`).
- **Backup DB**: `docker-compose exec postgres pg_dump -U trader crypto_ai_trading > backup_$(date +%Y%m%d).sql`.
- **Kill switch**: dashboard `/` botón rojo o `POST /api/kill-switch {enabled:true}`.
- **Rollback de playbook**: dashboard `/playbook` → "Activar" sobre versión anterior.

### 8.5 Health & observabilidad

- `/api/health`:
  - `engine.ok = (última decisión <15 min)`.
  - `binance.ok = (último OHLCV 1m <15 min)`.
- `/api/ping` para liveness.
- Logs JSON pueden ingerirse en cualquier stack (no hay APM acoplado).

---

## 9. Decisiones de diseño y trade-offs

| Decisión | Justificación |
|----------|---------------|
| **Postgres como único canal entre engine y web** | Elimina necesidad de bus de mensajes; permite restart independiente; trazabilidad total. |
| **Risk Gate determinístico post-LLM** | El LLM puede alucinar; las reglas R1–R10 son no-negociables y verificadas en código. |
| **Override determinístico de sizing / threshold** | Aunque el LLM aplica las mismas reglas, se reaplican en `_apply_deterministic_overrides` para evitar divergencia silenciosa. |
| **TR winsorizado en ATR** | Velas anómalas del testnet inflaban ATR 4–5× durante horas. |
| **Playbook como markdown versionado en BD** | El Supervisor escribe lenguaje natural que el Decisor lee como prompt; permite auditoría y rollback. |
| **Configuración 100% en BD** | Cambios en caliente sin redeploy; auditados en `config_history`. |
| **Frase literal `CONFIRMO TRADING REAL`** | Imposibilita scripts accidentales que toggleen LIVE. |
| **Cascade de providers LLM con skip en 429** | Free tiers de Gemini/Groq tienen rate limits agresivos; saltar evita degradación de calidad. |
| **OrderTracker pasivo basado en `fetch_my_trades`** | Binance ejecuta el bracket; el bot solo reconcilia, evita doble cierre. |
| **Sin shorts / sin margin / sin retiros** | Spot puro, llave API restringida → superficie de ataque mínima. |

---

## 10. Roadmap técnico (extracto)

- [ ] Tests de integración end-to-end con un fake exchange determinístico.
- [ ] Persistir `daily_pnl_pct` y `total_drawdown_pct` para que el Risk Gate los reciba con datos reales (actualmente se pasan 0.0 desde `main.py`).
- [ ] Telemetría/metrics (OpenTelemetry) — hoy solo logs JSON.
- [ ] Backtesting con LLM real (hoy es indicator-only baseline).
- [ ] Notificaciones (telegram/email) ante eventos críticos (kill switch, daily stop, supervisor rollback).
- [ ] Hardening del frontend (auth, RBAC) — actualmente sin login.
