# Trading Engine — Analysis

Análisis exhaustivo del directorio `trading-engine/`. Cada hallazgo respaldado por `archivo:línea`.

> **Nota**: este archivo es material crudo del reverse-engineering. La síntesis final está en `meli/specs/functional-spec.md` y `meli/specs/technical-spec.md`.

## 1. Entrypoint y bootstrap

**`trading-engine/main.py`** define `run()` y `main()`. Orden de inicialización:

1. `get_settings()` + `structlog.configure` JSON (`main.py:41-46`).
2. Engine y session async DB: `create_engine_from_url`, `create_session_factory` (`main.py:48-49`).
3. Clientes LLM (Gemini + Groq, opcionales) (`main.py:51-63`).
4. `LLMClient`, `build_binance_client()` (`main.py:65-66`).
5. Bootstrap: `ConfigStore.seed_defaults()`, `PromptManager.seed_playbook_v0()`, `FeeManager.refresh()` (`main.py:68-74`).
6. `OrderBookCollector` instanciado **pero `start()` no se llama** (`main.py:76`). 🔴 BUG D-002.
7. `CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)` (`main.py:77`).
8. `EngineScheduler` + registro de jobs (decisor, supervisor, fees, positions, order_tracker) (`main.py:78-312`).
9. `asyncio.Event` + handlers SIGINT/SIGTERM → `await stop_event.wait()` (`main.py:314-319`).
10. `finally`: shutdown ordenado (`main.py:320-324`).

## 2. Componentes

### Scheduler
- `scheduler.py:10-39` — `AsyncIOScheduler(timezone="UTC")`; jobs: `decisor` (IntervalTrigger minutes), `supervisor` (CronTrigger), `fees` (24h), `positions` (30s), `order_tracker` (30s).

### PriceCollector
- `collectors/price_collector.py:21-145` — fetch OHLCV multi-TF (`TIMEFRAMES_DEFAULT = ("1m","5m","15m","1h","4h")`, `LIMIT_DEFAULT=250`).
- Upsert `ohlcv` por vela; cálculo de indicadores con pandas puro; upsert `indicators.data` JSONB.

### OrderBookCollector
- `collectors/orderbook_collector.py:1-103` — `watch_order_book(symbol, limit=20)`; `_book` en memoria; `snapshot(levels)` calcula spread/imbalance/walls.
- 🔴 No persiste DB y no se inicia desde `main.py`.

### FeeManager
- `execution/fee_manager.py:13-76` — `REFRESH_INTERVAL = timedelta(hours=24)`.
- Fallback en cascada: error → último `FeeSnapshot` → default 0.001.

### ContextBuilder
- `agents/context_builder.py:11-170` — arma dict del Decisor con indicadores multi-TF, posiciones, últimas 3 decisiones, OB, ATR 7d, fees, calibración.
- Defaults embebidos: `sl_atr_max_multiplier=1.5`, `min_fees_to_tp_ratio=3.0`, `min_confluences_buy=2`.

### Decisor
- `agents/decisor.py:29-127` — flow: contexto → sustituir prompts → LLM cascade → strip markdown → `json.loads` → `DecisorOutput.model_validate` → `_validate_confluence_codes` → `_apply_deterministic_overrides` → persist `Decision`.
- Overrides (`decisor.py:127-160`): BUY+TRENDING_DOWN → HOLD; BUY+confidence<0.60 → HOLD; sizing: ≥0.70 → max_position_pct, sino 0.03 piso 0.01.
- Errores: parse/validation → HOLD `parse_error`; otro → HOLD `llm_error`.

### Supervisor
- `agents/supervisor.py:119-397` — métricas 24h, LLM → playbook nuevo, 2da llamada LLM → sugerencias config con `_SAFE_BOUNDS`.
- 🟠 NO implementa auto-rollback (D-005).

### RiskGate
- `risk/risk_gate.py:11-90` — constructor defaults `min_rr_ratio=1.3`, `sl_atr_multiplier=0.3`, `sl_atr_max_multiplier=1.5`.
- 14 chequeos en orden (ver `functional-spec.md` §4 para detalle).
- 🔴 Reglas drawdown y daily stop nunca disparan porque `main.py:213-216` pasa `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0`.

### Executor
- `execution/executor.py:14-130` — `execute_buy` market `quoteOrderQty` + SL `STOP_LOSS_LIMIT` (`price = stop_loss * 0.999`) + TP `LIMIT`; `execute_sell` market.
- Sin lógica de partial fills (asume fill total o `RuntimeError`).

### PositionManager
- `execution/position_manager.py:11-37` — poll 30s; actualiza `current_price`, `unrealized_pnl`, `unrealized_pct`.

### OrderTracker
- `execution/order_tracker.py:21-86` — poll 30s; si `close_requested=true` → `execute_sell`; sino `fetch_my_trades` + match ±2% para inferir SL/TP fills.

### CircuitBreaker
- `risk/circuit_breaker.py:12-50` — cuenta fallos LLM/exchange para `engine_paused`.
- 🔴 `evaluate()` con daily/drawdown thresholds no se llama desde `main.py` (D-003).

## 3. APIs externas

| Servicio | SDK calls | Archivos |
|----------|-----------|----------|
| Binance (CCXT async) | `fetch_ohlcv`, `fetch_trading_fees`, `fetch_balance`, `create_market_order`, `create_order` (STOP_LOSS_LIMIT/LIMIT), `fetch_ticker`, `fetch_my_trades`, `watch_order_book`, `close` | `price_collector.py:47`, `fee_manager.py:40`, `main.py:149`, `executor.py:23-39`, `order_tracker.py:40`, `orderbook_collector.py:59`, `exchange.py:16-24` |
| Google Gemini | `Client` + `aio.models.generate_content` | `agents/llm_client.py:123-135` |
| Groq | `chat.completions.create` | `agents/llm_client.py:137-152` |

`enableRateLimit: True` en `exchange.py:19`.

## 4. Prompts LLM

| Archivo | Propósito | Líneas |
|---------|-----------|--------|
| `agents/prompts/decisor_system.txt` | System prompt Decisor | 246 |
| `agents/prompts/decisor_user.txt` | User prompt Decisor | 36 |
| `agents/prompts/supervisor_system.txt` | System prompt Supervisor | 111 |
| `agents/prompts/supervisor_user.txt` | User prompt Supervisor | 28 |
| `agents/prompts/playbook_v0.md` | Seed playbook v0 | 38 |

## 5. Hallazgos críticos

1. 🔴 `OrderBookCollector.start()` no invocado (D-002).
2. 🔴 `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0` siempre al RG (D-001).
3. 🔴 `CircuitBreaker.evaluate()` no integrado en `main.py` (D-003).
4. 🟠 `MIN_FEES_TO_TP_RATIO` declarado pero no consumido (D-004).
5. 🟠 `TRADING_MODE` y `LOG_LEVEL` en `config.py:20-21` no referenciados.
6. 🟠 `LLM_MAX_RETRIES`/`LLM_TIMEOUT_SEC` en config_store no consumidos por `LLMClient`.
7. 🟡 `test_indicators.py` docstring miente: dice pandas-ta, código usa pandas puro.
8. 🟡 `test_models.py` no cubre `BalanceSnapshot` ni `close_requested`.
9. 🟡 No hay `test_order_tracker.py`.

## 6. Tests

19 archivos en `trading-engine/tests/`. Frameworks: pytest + pytest-asyncio (asyncio_mode=auto) + freezegun + pytest-cov.

| Archivo | Cubre |
|---------|--------|
| `test_circuit_breaker.py` | `evaluate()` y pausa por fallos |
| `test_exchange.py` | Factory CCXT |
| `test_config_store.py` | seed/get/set + historial |
| `test_llm_client.py` | Gemini/Groq + retries |
| `test_orderbook_collector.py` | Snapshot y métricas |
| `test_price_collector.py` | OHLCV fetch/persist |
| `test_config_v2_keys.py` | Defaults decisor v2 |
| `test_context_builder.py` | Dict de contexto |
| `test_position_manager.py` | Refresh PnL |
| `test_supervisor.py` | Métricas + playbook |
| `test_risk_gate.py` | 14 reglas incluyendo R10 |
| `test_indicators.py` | `compute_indicators` |
| `test_executor.py` | Buy/sell |
| `test_prompt_manager.py` | Carga prompts |
| `test_db_base.py` | Engine factory |
| `test_models.py` | Smoke ORM |
| `test_schemas.py` | Pydantic |
| `test_decisor.py` | End-to-end con mocks |
| `test_fee_manager.py` | Refresh y fallback |
