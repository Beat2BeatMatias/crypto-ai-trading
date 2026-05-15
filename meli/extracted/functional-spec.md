# Functional Specification — Crypto AI Trading

**Versión**: 1.0 (reverse-engineered)
**Fecha de extracción**: 2026-05-14
**Source of truth**: código actual + cross-validation con design doc 2026-05-02

## Sistema de confianza

Cada ítem en este spec lleva un indicador del origen de la información:

| Marker | Significado |
|--------|-------------|
| ✅✅✅ THREE_WAY | En código + design doc + plan/README, coherente |
| ✅✅ VERIFIED | En código + design doc, coherente |
| ✅⚠️ PARTIAL | En código + design doc, **con diferencias** (ver `DISCREPANCIES_REPORT.md`) |
| 🔸 CODE_ONLY | Sólo en código (feature post-design-doc o no documentada) |
| ⚠️ DOCS_ONLY | Sólo en design doc — **NO en código** (gap funcional) |
| ❓ UNKNOWN | Información insuficiente |

---

## 1. Resumen ejecutivo

✅✅✅ **Crypto AI Trading** es un bot autónomo de day trading **BTC/USDT** en **Binance Spot**, operado por dos agentes LLM coordinados (Decisor + Supervisor) con un Risk Gate determinístico como última línea de defensa.

- ✅✅✅ **Decisor** (Gemini 2.5 Flash): cada N minutos observa el mercado, decide `BUY`/`SELL`/`HOLD` y emite JSON estructurado con reasoning.
- ✅✅✅ **Supervisor** (Gemini 2.5 Pro): diario a las 00:00 UTC, analiza performance reciente y genera un playbook (lecciones) que el Decisor consume en cada ciclo.
- ✅✅ **Risk Gate** (Python determinístico): valida cada propuesta antes del executor.
- ✅✅✅ El sistema corre en tres contenedores (`trading-engine`, `web`, `frontend`) + Postgres y se controla desde un dashboard React en español.

## 2. Contexto del sistema

### 2.1 Tipo y alcance

| Aspecto | Valor |
|---------|-------|
| Tipo | ✅✅✅ Bot autónomo / producto personal / no-SaaS |
| Usuarios | ✅✅✅ Un único operador propietario del bot |
| Plataforma operativa | ✅✅✅ Binance Spot (Testnet → Mainnet) |
| Par operado | ✅✅✅ BTC/USDT exclusivamente |
| Modos | ✅✅✅ `PAPER_TRADING` (testnet, default) o `LIVE` (mainnet, sólo tras 4 semanas de paper trading exitoso) |
| Capital recomendado en LIVE | ✅✅✅ $200–500 USDT inicial |

### 2.2 Actores

| Actor | Tipo | Interacción | Evidencia |
|-------|------|-------------|-----------|
| **Operador humano** | Humano externo | Configura el bot vía dashboard; activa/desactiva LIVE; gatilla kill switch; edita playbook manualmente | ✅✅✅ `frontend/src/pages/*.tsx`, `web/api/control.py` |
| **trading-engine** | Sistema interno | Loop autónomo: precios → contexto → decisor → risk gate → executor → outcome | ✅✅✅ `trading-engine/main.py` |
| **web** | Sistema interno | API REST + WebSocket para el dashboard; sólo escribe `config`/`config_history` | ✅✅✅ `web/main.py`, `web/api/*` |
| **frontend** | Sistema interno | SPA React que consume API REST y WebSocket | ✅✅✅ `frontend/src/*` |
| **Postgres** | Sistema interno | Único bus de comunicación entre engine y web | ✅✅✅ `docker-compose.yml`, `shared/db/*` |
| **Binance Spot** | Servicio externo | Datos de mercado (OHLCV, ticker, order book) + colocación de órdenes + balance + fees | ✅✅✅ vía CCXT en `trading-engine/exchange.py`, `executor.py`, `fee_manager.py` |
| **Google Gemini** | Servicio externo | LLM primario: Decisor (Flash) y Supervisor (Pro) | ✅✅✅ `trading-engine/agents/llm_client.py:123-135` |
| **Groq** | Servicio externo | LLM fallback para Decisor | ✅✅✅ `llm_client.py:137-152` |

### 2.3 Lo que NO hace el sistema (non-goals explícitos)

✅✅✅ Tomados del design doc §2 y validados contra ausencia en código:

- ❌ Margin o futures trading (sólo spot)
- ❌ Multi-pair / multi-exchange
- ❌ Shorting (imposible en spot)
- ❌ Multi-usuario / SaaS
- ❌ App móvil
- ❌ Análisis on-chain o sentiment (deferred a v2)
- ❌ Reinforcement learning end-to-end
- ❌ HFT / scalping sub-segundo

---

## 3. Casos de uso

### UC-01 — Iniciar el sistema en modo paper trading
✅✅ **Actor**: operador humano
**Precondiciones**:
- Docker corriendo
- `.env` con `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `BINANCE_TESTNET=true`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `POSTGRES_PASSWORD`

**Flujo principal**:
1. Operador ejecuta `docker-compose up -d`.
2. Postgres se levanta y pasa healthcheck.
3. `trading-engine` se conecta, ejecuta `ConfigStore.seed_defaults()` (claves con defaults), `PromptManager.seed_playbook_v0()` (carga playbook bootstrap), `FeeManager.refresh()` (fees desde Binance).
4. Scheduler arranca con jobs: decisor (cada `decisor_interval_min`), supervisor (cron `supervisor_cron`), fees (24h), positions (30s), order tracker (30s).
5. `web` arranca FastAPI con lifespan que crea engine async y lanza `ticker_broadcaster` (WS).
6. `frontend` sirve la SPA en `:3100`.

**Postcondición**: el dashboard `http://localhost:3100` muestra estado live; el bot opera en testnet.

**Referencia**: ✅✅ `README.md` Paso 1-2, `trading-engine/main.py:run()`, `web/main.py:lifespan()`.

### UC-02 — Ciclo de decisión del Decisor (cada N minutos)
✅✅✅ **Actor**: `trading-engine` (sistema)

**Flujo**:
1. Scheduler dispara `decisor_tick` cada `decisor_interval_min`.
2. `PriceCollector` hace `fetch_ohlcv` para timeframes `1m, 5m, 15m, 1h, 4h` (250 velas) → upsert en `ohlcv`.
3. Cálculo de indicadores multi-TF → fila en `indicators.data` (JSONB).
4. `ContextBuilder.build()` arma el dict de contexto: últimas posiciones, últimas 3 decisiones, snapshot de orderbook, ATR histórico 7d, fees, calibración desde `config`.
5. `Decisor.run()` carga playbook activo, sustituye placeholders en prompts `decisor_system.txt` y `decisor_user.txt`, llama LLM en cascada (Gemini Flash → Groq fallback).
6. Parser limpia markdown fences, `json.loads`, `DecisorOutput.model_validate`.
7. Validación de códigos de confluencia A-H (`_validate_confluence_codes`).
8. Overrides determinísticos (`_apply_deterministic_overrides`):
   - 🔸 Si `regime == TRENDING_DOWN` y `action == BUY` → forzar HOLD
   - 🔸 Si `confidence < 0.60` y `action == BUY` → forzar HOLD
   - 🔸 Position sizing: si `confidence >= 0.70` → `max_position_pct`; sino `0.03`; piso `0.01`
9. `RiskGate.validate()` chequea 14 reglas → `RiskVerdict(approved, reason?)`.
10. Si aprobado y `action == BUY`: `Executor.execute_buy()` con SL/TP. Si `SELL`: `execute_sell()`.
11. Persistencia: nueva fila en `decisions` con `input`/`output`/`outcome` JSONB; `executed=true/false`; `rejected_reason` si aplica.
12. Si error: `JSONDecodeError`/`ValidationError` → HOLD con `parse_error`; otra excepción → HOLD `llm_error`.

**Error paths**:
- ✅⚠️ `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0` se pasan SIEMPRE al RG (bug `D-001`) → las reglas R-daily-stop y R-drawdown no disparan.

**Referencia**: ✅✅✅ `agents/decisor.py:29-127`, `risk/risk_gate.py:31-90`.

### UC-03 — Ciclo del Supervisor (diario 00:00 UTC)
✅✅ **Actor**: `trading-engine`

**Flujo**:
1. Scheduler dispara `supervisor_tick` según `supervisor_cron` (default `"0 0 * * *"`).
2. Lee `Decision` (últimos N), `Trade` cerrados, `Ohlcv` 1h, último `Indicators` de las últimas 24h.
3. Calcula métricas: counts BUY/SELL/HOLD, rechazos, win rate, profit factor, PnL realizado, avg holding time, breakdown por `close_reason`, OHLCV 24h, ATR%, `vol_label`.
4. Sustituye placeholders en `supervisor_system.txt` y `supervisor_user.txt`.
5. Llama Gemini 2.5 Pro → recibe markdown del nuevo playbook.
6. Persiste `PlaybookVersion` (incrementa `version`, desactiva todas las anteriores, marca `active=true`).
7. Segunda llamada LLM para sugerencias JSON de config con guardrails (`_SAFE_BOUNDS`).
8. Auto-apply de cambios de config validados → `ConfigStore.set` para cada clave aprobada, audit en `config_history`.
9. Persiste una fila en `decisions` con `agent="supervisor"`.

**Error paths**:
- Si datos insuficientes (<5 trades) → mantiene playbook anterior, nota en el output.
- Si LLM falla → `rejected_reason` en la decisión supervisor.

**⚠️ DOCS_ONLY — NO implementado**: auto-rollback automático del playbook si performance degrada (design doc §8.4) — discrepancia `D-005`.

**Referencia**: ✅✅ `agents/supervisor.py:119-397`.

### UC-04 — Operador edita configuración desde el dashboard
✅✅✅ **Actor**: operador humano

**Flujo**:
1. Operador navega a `/config`.
2. Frontend pide `GET /api/config` → muestra todas las claves agrupadas (Riesgo, Timing, LLM, Datos, Prompts, Kill Switch).
3. Operador modifica un slider/dropdown/toggle.
4. Frontend llama `PUT /api/config/{key}` con `{"value": "<nuevo>"}`.
5. Backend valida `ConfigKey`; si inválida → 400. Si válida → `ConfigStore.set(key, value, changed_by="user")` → update + audit + commit.
6. Próximo tick del engine lee el nuevo valor.

**Validaciones especiales**:
- ✅✅✅ Cambiar `mode` a `LIVE` requiere POST `/api/mode` con `confirmation == "CONFIRMO TRADING REAL"` (exacto, case-sensitive). 400 si no coincide.

**Referencia**: ✅✅✅ `web/api/config.py`, `web/api/control.py`, `frontend/src/pages/Config.tsx`.

### UC-05 — Operador activa el kill switch
✅✅✅ **Flujo**:
1. Operador presiona "Kill Switch" en cualquier página (presente en todas las vistas).
2. Frontend confirma y llama `POST /api/kill-switch` con `{"enabled": true}`.
3. Backend escribe `ConfigKey.KILL_SWITCH = "true"` en `config`.
4. Próximo tick: `RiskGate.validate()` rechaza todo BUY (regla "kill switch"), permite SELL para cerrar posiciones (`risk_gate.py:39-43`).

**Referencia**: ✅✅✅ `web/api/control.py:24-32`, `risk_gate.py:39-43`.

### UC-06 — Operador fuerza cierre de un trade abierto
🔸 **CODE_ONLY** — agregado post-design-doc.

**Flujo**:
1. Operador clic en "Forzar cierre" sobre un trade abierto en `/trades`.
2. Frontend llama `POST /api/trades/{trade_id}/close`.
3. Backend valida estado del trade; setea `close_requested=true`; commit.
4. Próximo tick del `OrderTracker` (cada 30s) detecta `close_requested=true` y ejecuta `Executor.execute_sell()`.

**Referencia**: 🔸 `web/api/trades.py:56-66`, `trading-engine/execution/order_tracker.py:21-86`.

### UC-07 — Operador edita manualmente el playbook activo
✅✅✅ **Flujo**:
1. Operador navega a `/playbook`.
2. Frontend muestra el contenido markdown editable.
3. Al guardar, llama `PATCH /api/playbook/{version}/content` con `{"content": "<nuevo markdown>"}`.
4. Backend valida que no esté vacío y persiste el contenido.

**Referencia**: ✅✅✅ `web/api/playbook.py:57-76`, `frontend/src/pages/Playbook.tsx`.

### UC-08 — Operador hace rollback a un playbook anterior
✅✅✅ **Flujo**:
1. Operador navega a `/playbook` → sidebar de historial.
2. Selecciona versión X y presiona "Activar".
3. Frontend llama `POST /api/playbook/{version}/activate`.
4. Backend desactiva todos los playbooks y marca el seleccionado como `active=true`.
5. Próximo tick del Decisor lee el playbook activo.

**Referencia**: ✅✅✅ `web/api/playbook.py:42-51`.

### UC-09 — Operador dispara el Supervisor manualmente
✅✅ **Flujo**:
1. Operador presiona "Ejecutar supervisor ahora" en `/config`.
2. Frontend llama `POST /api/supervisor/run`.
3. Backend escribe `SUPERVISOR_RUN_NOW=true` en `config`.
4. El engine, al detectar el flag, dispara `Supervisor.run()` fuera del cron.

**Referencia**: ✅✅ `web/api/control.py:47-54`.

### UC-10 — Backtester valida reglas baseline sin LLM
✅✅ **Flujo**:
1. Operador entra a `backtesting/` y ejecuta `python runner.py --days 90 --sl-atr-mult 1.2 --rr 2.5`.
2. El runner descarga OHLCV histórico de Binance vía `ccxt`.
3. Calcula indicadores con pandas puro (no pandas-ta).
4. Simula una posición a la vez según reglas `signal_buy` inline.
5. Reporta a stdout: trades count, win rate, total PnL, Sharpe, max drawdown, profit factor.
6. Evalúa gate heurístico: win rate mínimo en función del R:R, Sharpe, drawdown, profit factor.

**⚠️ Discrepancia con design doc**: `D-016` la spec menciona `vectorbt`, el código usa pandas puro.

**Referencia**: ✅⚠️ `backtesting/runner.py`.

### UC-11 — Operador switch a modo LIVE (proceso de 4 semanas)
✅✅✅ Tomado de README `Roadmap hacia LIVE trading` y validado contra código.

**Precondiciones**:
1. Paper trading exitoso 4 semanas consecutivas con:
   - Sharpe > 1.0
   - Max drawdown < 5%
   - Win rate > 52%
   - Profit factor > 1.5
   - Decisiones LLM sin errores > 99%
   - Ninguna semana con drawdown > 3%
2. API keys de mainnet generadas (permisos: leer + spot trading, sin margin, sin retiros, IP restringida).

**Flujo**:
1. Actualizar `.env` con keys mainnet y `BINANCE_TESTNET=false`.
2. `docker-compose restart trading-engine`.
3. Verificar `fees.refreshed` en logs (mainnet sí tiene endpoints `sapi` para fees).
4. UI `/config` → cambiar `mode` a `LIVE` → modal pide tipear "CONFIRMO TRADING REAL" → enviar.
5. Monitorear con doble frecuencia la primera semana.

---

## 4. Reglas de negocio (Risk Gate)

El Risk Gate (`trading-engine/risk/risk_gate.py:31-90`) es la última línea de defensa antes del Executor. Aplica **14 chequeos** en orden (✅⚠️ — design doc §8.1 listaba 10):

| # | Regla | Trigger | Default |
|---|-------|---------|---------|
| 1 | HOLD pasa siempre | `action == HOLD` | — |
| 2 | Drawdown total | `total_drawdown_pct <= max_drawdown_pct` | `max_drawdown_pct = -0.10` (config seed) |
| 3 | Kill switch | `kill_switch_active == true` | sólo SELL con BTC permitido |
| 4 | SELL requiere posición | `btc_held > 0 AND open_positions_count > 0` | — |
| 5 | BUY requiere SL | `stop_loss != null` | — |
| 6 | SL vs precio | `stop_loss < current_price` | — |
| 7 | Tamaño máx | `position_size_pct <= max_position_pct` | `max_position_pct = 0.10` |
| 8 | Cupos | `open_positions_count < max_simultaneous_trades` | `max_simultaneous_trades = 2` |
| 9 | Daily PnL | `daily_pnl_pct <= daily_stop_pct` | `daily_stop_pct = -0.03` |
| 10 | SL min/max distance | `0.3 × ATR <= entry - SL <= 1.5 × ATR` | `sl_atr_multiplier=0.3`, `sl_atr_max_multiplier=1.5` |
| 11 | TP requerido | `take_profit != null` | — |
| 12 | TP vs precio | `take_profit > current_price` | — |
| 13 | R:R mínimo | `(TP - entry) / (entry - SL) >= min_rr_ratio` | `min_rr_ratio = 1.3` ✅⚠️ design doc dice 1.5 |
| 14 | **R10** fee-aware | si `roundtrip_fee_pct > 0`: `move_pct >= min_fees_to_tp_ratio × roundtrip_fee_pct` | `min_fees_to_tp_ratio = 3.0` (hardcoded; ver D-004) |

**🔴 Bugs activos** (ver `DISCREPANCIES_REPORT.md`):
- D-001: reglas 2 y 9 nunca disparan porque inputs son 0.0
- D-004: regla 14 ignora valor configurable de `min_fees_to_tp_ratio`

---

## 5. Modelo de datos (alto nivel)

✅✅✅ Postgres 17. Detalle completo en `technical-spec.md`.

| Tabla | Propósito | Escrita por | Leída por |
|-------|-----------|-------------|-----------|
| `ohlcv` | Velas multi-timeframe | engine | engine + backtester |
| `indicators` | Snapshot de indicadores por tick | engine | engine + web |
| `decisions` | Audit trail de cada llamada LLM | engine | engine + web |
| `trades` | Trades ejecutados (abiertos + cerrados) | engine | engine + web |
| `positions` | Vista en tiempo real de posiciones abiertas | engine | engine + web |
| `playbook_versions` | Historial inmutable de playbooks | engine | engine + web |
| `config` | Configuración runtime editable | engine + **web** | engine + web |
| `config_history` | Audit log de cambios de config | engine + **web** | web |
| `daily_stats` | Tabla pre-computada (✅⚠️ no se popula automáticamente) | (ninguno) | web |
| `fee_snapshots` | Histórico de comisiones | engine | engine + web |
| `balance_snapshots` | 🔸 Snapshots periódicos del balance | engine | web |

**Constraint transversal** ✅✅✅:
- Todos los tiempos son `TIMESTAMPTZ` (UTC).
- Precios y cantidades son `NUMERIC` (precisión decimal).
- Payloads LLM y agregados se guardan en `JSONB`.

---

## 6. Configuración runtime (claves editables)

✅✅ Listado completo en `shared/config_store.py:17-80`. Categorías:

| Categoría | Claves |
|-----------|--------|
| **Modo** | `mode`, `kill_switch` |
| **Riesgo** | `max_position_pct`, `max_simultaneous_trades`, `daily_stop_pct`, `max_drawdown_pct`, `max_slippage_pct`, `default_rr_ratio` |
| **Timing** | `decisor_interval_min`, `supervisor_cron`, `supervisor_run_now` |
| **LLM** | `decisor_provider`, `supervisor_provider`, `fallback_provider`, `llm_max_retries` ✅⚠️ no consumida, `llm_timeout_sec` ✅⚠️ no consumida |
| **Datos** | `orderbook_levels` ✅⚠️ no consumida |
| **Decisor v2** (migración `004`) | 🔸 `min_fees_to_tp_ratio`, `min_confluences_buy`, `cooldown_after_sell_min`, `subjective_adj_max`, `expected_holding_max_min`, `confluence_weak_factor` |

---

## 7. Vistas del dashboard

| Ruta | Página | Coverage vs design doc |
|------|--------|------------------------|
| `/` | Dashboard live | ✅⚠️ sin chart de precio (recharts declarado pero no usado) |
| `/trades` | Historial de trades | ✅⚠️ filtros básicos; sin date range, sin CSV, sin sorting |
| `/decisions` | Audit log LLM | ✅⚠️ filtros básicos; sin slider de confidence |
| `/playbook` | Playbook viewer + edición | ✅⚠️ sin diff viewer, sin reset a v0 |
| `/config` | Editor de configuración | ✅✅ excelente con SliderField y FIELD_DEFS |
| `/health` | Diagnóstico de sistema | ✅⚠️ básico; sin métricas LLM, sin recent errors |

---

## 8. Criterios de éxito (v1)

✅✅✅ Tomados del design doc §11 y validados contra implementación:

### Técnico
- ✅✅✅ Flow end-to-end: precios → decisor → risk gate → executor → outcome → supervisor → nuevo playbook
- ✅✅ JSON parse rate del LLM > 99% (Decisor con HOLD/parse_error fallback)
- ✅⚠️ Kill switch cierra todas las posiciones en < 10s — implementado vía RG block BUY y permitir SELL; verificación manual requerida
- ✅⚠️ Engine sobrevive desconexiones de Binance WS (auto-reconnect) — `OrderBookCollector` tiene reconexión pero `start()` no se invoca (`D-002`)
- ✅✅ Engine sobrevive caídas LLM (fallback Groq)
- ✅✅✅ Todas las decisiones auditadas con prompt/response

### Trading (paper, 4 semanas)
- ⚠️ Sharpe > 1.0 (objetivo)
- ⚠️ Max drawdown < 5%
- ⚠️ Win rate > 52%
- ⚠️ Profit factor > 1.5

### Operacional
- ✅✅✅ Costo LLM diario = $0 (free tier)
- ✅✅✅ Costo infra dev = $0 (Docker local)

---

## 9. Riesgos y mitigaciones

✅✅ Tomado del design doc §12, validado contra implementación:

| Riesgo | Mitigación documentada | Estado en código |
|--------|-------------------------|-------------------|
| LLM alucinaciones | Risk Gate determinístico | ✅ implementado |
| Free tier cambia | Fallback multi-provider | ✅ Gemini + Groq |
| API Binance cambia | CCXT como abstracción | ✅ `enableRateLimit: True` |
| Supervisor genera playbook tóxico | Auto-rollback en degradación | ⚠️ **NO implementado** (D-005) |
| Race condition en cierre | Postgres advisory locks | ❓ no detectado en código |
| Backtest no match live | 4 semanas paper trading obligatorio | ✅✅ doctrina del README |
| Operador deja LIVE encendido | Doble confirmación + recordatorio | ✅✅ confirmation modal; ⚠️ recordatorio diario no detectado |
| Posiciones colgadas en outage LLM | SL en exchange | ✅ Executor coloca STOP_LOSS_LIMIT |
| Pérdida de datos | `pg_dump` diario | ✅✅ documentado en README; cron no detectado |

---

## 10. Glosario

- **Decisor**: agente LLM que decide BUY/SELL/HOLD por ciclo.
- **Supervisor**: agente LLM diario que destila lecciones en un nuevo playbook.
- **Playbook**: documento markdown con lecciones del Supervisor; el Decisor lo lee en cada tick.
- **Risk Gate (RG)**: chequeo determinístico Python entre Decisor y Executor.
- **Confluencias (A–H)**: códigos de señales técnicas (RSI, MACD, EMA, Bollinger, volumen, OB imbalance) que el Decisor debe enumerar para justificar un BUY.
- **R10**: regla del RG que exige que la distancia al TP cubra N veces el roundtrip fee.
- **R:R**: risk-reward ratio = `(TP - entry) / (entry - SL)`.
- **OHLCV**: Open / High / Low / Close / Volume por timeframe.
- **ATR**: Average True Range (volatilidad).
- **TF**: timeframe (1m, 5m, 15m, 1h, 4h).
- **PAPER_TRADING**: Binance Testnet, USDT virtual.
- **LIVE**: Binance Mainnet, USDT real.

---

## 11. Próximos pasos sugeridos (post-extracción)

1. **Resolver bugs CRÍTICOS** del `DISCREPANCIES_REPORT.md`:
   - `D-001` cálculo real de `daily_pnl_pct` y `total_drawdown_pct` pre-RiskGate.
   - `D-002` invocar `OrderBookCollector.start()` en `main.py`.
   - `D-003` integrar `CircuitBreaker.evaluate()` en el loop principal.
2. **Resolver bugs ALTOS**:
   - `D-004` pasar `min_fees_to_tp_ratio` desde config al RG.
   - `D-005` implementar auto-rollback del Supervisor (o quitarlo del scope explícitamente).
   - `D-006` crear migración Alembic para índices GIN y FKs faltantes.
3. **Completar features ALTAS**:
   - `D-009` emitir eventos WS faltantes (`trade_opened`, `trade_closed`, `playbook_updated`, `kill_switch_triggered`).
   - `D-013` enriquecer `/health` con métricas LLM y panel de errores.
