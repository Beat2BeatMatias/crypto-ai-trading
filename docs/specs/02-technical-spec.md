# Especificación Técnica — Crypto AI Trading

> Audiencia: Tech leads, devs, SRE.
> Versión: 1.11 — 2026-06-02.
>
> Cambios v1.11: **Futuros USDT-M.** `ExchangeAdapter` (`trading-engine/execution/exchange_adapter.py`: `SpotAdapter`, `FuturesAdapter`, `build_adapter()`). `Executor.execute_open` / `execute_close`. `TRADING_PRODUCT` / `engine_symbol()` (`BTC/USDT:USDT`). Risk Gate R0–R15. Migración 016. `validate_futures_sizing()` en bootstrap. Contexto Decisor con funding/margen/liquidación.
>
> Cambios v1.9: `shared/confidence.py` — `compute_confidence_base` / `apply_server_confidence` invocado en `decisor.py` tras `_filter_confluence_codes` (conteo A–H + I–Z activas, peso 1.0). Persistencia de `confidence_meta`. `_hold_datos_insuficientes` (confidence 0.95) en early-exit. Frontend: `components/ConfidenceBreakdown.tsx`, `types/decisorOutput.ts`; `/decisions` y Dashboard.
>
> Cambios v1.8: `outcome_attribution_window_hours` (config + migración 015). Ventana compartida entre outcome attribution y post-mortem (default 25 h). UI `/config` sección Outcome Attribution.
>
> Cambios v1.7: `postmortem_fallback_providers` (config + migración 013). `coerce_lesson_raw()` en `postmortem_schemas.py`. Reintento post-mortem (`failed` re-elegible, máx. 3 intentos, `_meta` en `lesson_raw`). UI `/config` sección Post-mortem + `FallbackChain`. Documentada semántica 1 LLM call / decisión.
>
> Cambios v1.10: Sizing por riesgo fijo (`shared/position_sizing.py`), temperatura Decisor (`decisor_llm_temperature`), self-consistency (`decisor_self_consistency_n`, `agents/decisor_aggregate.py`). Nuevas config keys §6.
>
> Cambios v1.6: Pipeline post-mortem encadenado a outcome attribution (`postmortem_job`, `PostMortemAgent`, `lesson_normalizer`, Bloque K). Catálogo extendido I–Z (`confluence_candidates`, `confluence_registry`, `shared/confluence_registry_ops.py`). API y UI `/confluence`. CoherenceChecker C7/C8. `_filter_confluence_codes` acepta registry dinámico. Migraciones 011–012. Nuevas config keys post-mortem y promoción.
>
> Cambios v1.5: Outcome attribution (`decision_outcomes`, job scheduler, API). Migraciones 005–010. R0/R11 en Risk Gate. Circuit breaker bifurcado (operacional vs financiero). Health enriquecido. WS 8 eventos. Telegram. Balance locked. OCO brackets (`order_id_sl/tp`). Elimina referencias obsoletas a overrides y recharts.
>
> Cambios v1.4: Rediseño LLM-centric del Decisor. Se eliminan overrides deterministas (TRENDING_DOWN, confidence-floor, sizing escalón). Se agregan CoherenceChecker (§2.6.bis.5), two-pass (§2.6.bis.6), etiquetas interpretativas (`labelers.py`, §2.6.bis.7). Se actualiza §2.6.bis (capas de defensa ahora son 5+1 sin Capa 3) y §2.6.bis.3 (garantías invariantes revisadas). Nuevas claves de config (§6): `min_position_size`, `coherence_strict_mode`, `two_pass_enabled`. Nueva API (§3): `GET /api/decisions/stats`. Indicadores extendidos (§2.2).
>
> Cambios v1.3: §2.7.2 actualiza la frecuencia de escritura en `playbook_versions`; nueva §2.7.4 documenta guardrails determinísticos del Supervisor.
>
> Cambios v1.2: se agregó §2.6.bis y §2.7.

---

## 1. Vista de alto nivel

Tres servicios en contenedores Docker que comparten una única base de datos Postgres como **fuente de verdad**. Los servicios **no se comunican entre sí por IPC**; sincronizan estado leyendo y escribiendo en la BD.

```
                  ┌────────────────────────────────────────────────┐
                  │                  Postgres 17                  │
                  │ (decisions, decision_outcomes, trades,        │
                  │  positions, ohlcv, indicators, playbook,      │
                  │  confluence_candidates, confluence_registry,  │
                  │  config, config_history, fee/balance snaps)   │
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
        │ • Decisor + Coherence    │         │ • Ticker broadcaster  │
        │ • RiskGate + CircuitBr.  │         │                       │
        │ • Executor + OrderTracker│         └───────────▲───────────┘
        │ • OutcomeAttribution     │                     │
        │ • PostMortem + lessons   │                     │
        │ • Confluence registry    │                     │
        │ • Supervisor LLM         │                     │ HTTP/WS
        │ • Telegram (opcional)    │              ┌──────┴──────┐
        │ • APScheduler            │              │  frontend   │
        └─────────▲──────────────┬─┘              │ React + Vite│
                  │              │                │ Tailwind v4 │
       CCXT REST/WS           Gemini/Groq         │ lightweight-│
                  │              │                │   charts    │
            ┌─────┴───────┐ ┌────┴──────┐         └─────────────┘
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
| Frontend | React 19, React Router 7, Vite 6, Tailwind 4, lightweight-charts, react-markdown |
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
      - add_balance_refresh    60 s
      - add_position_refresh   30 s
      - add_order_tracker      30 s
      - add_outcome_attribution  interval = outcome_attribution_interval_min
      - (encadenado) post-mortem al final del tick de outcome attribution
  • signal handler SIGINT/SIGTERM → shutdown ordenado
```

### 2.2 Componentes principales

| Módulo | Responsabilidad |
|--------|-----------------|
| `config.py` | `EngineSettings` (Pydantic Settings). Solo settings env-derivados (URLs, keys, símbolo). El resto vive en DB. |
| `exchange.py` | Factory `ccxt_async.binance` (`defaultType` spot \| future según producto). |
| `execution/exchange_adapter.py` | `SpotAdapter` / `FuturesAdapter`: open/close, brackets (OCO vs STOP/TP MARKET), balance, funding, `min_notional`. |
| `scheduler.py` | `EngineScheduler` envoltura tipada de APScheduler. |
| `collectors/price_collector.py` | Fetch OHLCV vía CCXT, upsert por (`time`,`timeframe`), recomputa indicadores. Soporta sqlite + postgres. |
| `collectors/indicators.py` | RSI/MACD/EMA/BB/ATR; TR **winsorizado** a 3×mediana móvil para neutralizar velas anómalas. |
| `collectors/orderbook_collector.py` | `OrderBookCollector` con WS CCXT pro; expone `snapshot(levels=10)` derivando spread, imbalance, walls. |
| `execution/fee_manager.py` | Fetch trading fees; cachea + refresca 24 h; fallback a último `FeeSnapshot` si Binance falla. |
| `execution/executor.py` | `execute_open` (LONG/SHORT), `execute_close`, `execute_buy`/`execute_sell` (legacy Spot), `record_bracket_fill`. Adapter opcional inyectado. |
| `execution/order_tracker.py` | Poll 30 s; `close_requested`; fills de cierre direccionales (sell long / buy reduceOnly short); guardians SL/TP invertidos en SHORT. |
| `execution/position_manager.py` | Open positions; PnL no realizado direccional por `position_side`. |
| `risk/risk_gate.py` | `RiskGate.validate` aplica R0–R15 (ver `05-risk-and-safety.md`). |
| `risk/circuit_breaker.py` | Cuenta fallas consecutivas (LLM, exchange) y daily/max drawdown; setea `engine_paused`. |
| `agents/llm_client.py` | `LLMClient` con cascade de providers (Gemini Flash/Pro + 8 modelos Groq). Retry con backoff exponencial salvo rate-limit (salta al siguiente provider). |
| `agents/prompt_manager.py` | Carga `prompts/*.txt`, sustitución de placeholders, persiste `PlaybookVersion`. |
| `agents/context_builder.py` | Construye el dict de contexto unificado para el Decisor a partir de BD + orderbook. |
| `agents/decisor.py` | Renderiza system+user prompts, llama LLM, valida `DecisorOutput`, CoherenceChecker + two-pass, persiste Decision. |
| `agents/supervisor.py` | Métricas 24h, ratificación/regeneración playbook, config suggestions, persistencia. |
| `agents/outcome_attribution.py` | Función pura `attribute()` — clasificación contrafactual MFE/MAE (sin I/O). |
| `agents/outcome_attribution_job.py` | Job scheduler: query candidates → OHLCV → UPSERT `decision_outcomes`. |
| `agents/postmortem_schemas.py` | `LessonRaw`, `coerce_lesson_raw()`, elegibilidad y `severity_score`. |
| `agents/postmortem_agent.py` | LLM 1× por decisión; primary `postmortem_provider` + fallbacks desde config. |
| `agents/postmortem_job.py` | Encadenado tras attribution; ranking por severidad; retry (máx. 3); upsert candidatos. |
| `agents/lesson_normalizer.py` | Rutas `remap` / `candidate` / `guidance`; formatea Bloque K. |
| `agents/confluence_registry.py` | Upsert candidatos, promoción Supervisor, lectura registry activo. |
| `shared/confluence_registry_ops.py` | Ops compartidas engine+web: promote/reject/deactivate, `verify_spec_testable()`. |
| `risk/coherence_checker.py` | Reglas C1–C8: auditoría consistencia declaración vs indicadores + registry I–Z. |
| `notifications/telegram.py` | Alertas push opcionales (kill switch, pausas, trades). |

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
   │    ├─ build_adapter(trading_product) + validate_futures_sizing (bootstrap)
   │    ├─ fetch_balance → BalanceSnapshot (+ margin_balance/available_margin si futures)
   │    │     (si falla: usdt=0, btc=Σ posiciones abiertas)
   │    ├─ orderbook.snapshot(levels=10)
   │    ├─ Decisor.decide(...)
   │    │     ├─ ContextBuilder.build(...)
   │    │     ├─ LLM.call(provider + fallbacks)
   │    │     ├─ json.loads + DecisorOutput.model_validate
   │    │     ├─ CoherenceChecker.evaluate + two-pass opcional
   │    │     └─ INSERT Decision (input + output + metrics)
   │    ├─ RiskGate.validate(...)
   │    │     └─ Si rechazado: UPDATE Decision.rejected_reason → return
   │    └─ execute_open(BUY|SHORT) / execute_close (SELL) según action y producto
   │
   └─ Commit + close session
```

### 2.4 Cascade de LLM providers

| Slot | Default Decisor | Default Supervisor | Default Post-mortem |
|------|----------------|--------------------|---------------------|
| primary | `groq-llama-3.3-70b` | `gemini-2.5-pro` | `gemini-2.5-flash` |
| fallback CSV | `gemini-2.5-flash,groq-llama-4-scout,...` | `groq-llama-3.3-70b,groq-llama-4-scout,...` | `groq-compound-mini,groq-llama-4-scout,groq-qwen3-32b,groq-gpt-oss-20b,groq-llama-3.1-8b` |

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

### 2.6 Formato de `DecisorOutput.reasoning`

El campo `reasoning` (max 1000 chars, truncado silenciosamente a 997 + `"..."`) usa un formato estructurado de **5 secciones etiquetadas** en español, diseñado para ser legible tanto por el operador sin experiencia técnica como por el desarrollador que revisa trazabilidad.

**Estructura:**

```
[DECISIÓN]: <acción en lenguaje simple + 1 frase explicativa del por qué>

[MERCADO]: <régimen en palabras llanas + qué lo caracteriza en el ciclo actual>

[SEÑALES]: <confluencias en lenguaje humano con código de catálogo entre paréntesis.
           Incluye [DRIFT CONFIG] o [DATOS_INSUFICIENTES] si aplican>

[CONFIANZA]: <porcentaje + resumen de cómo se calculó en términos simples>

[NIVELES]: <solo si action=BUY — SL y TP explicados con su significado funcional + R:R>
```

**Ejemplo HOLD:**
```
[DECISIÓN]: Esperar (HOLD) — no hay señales suficientes para abrir una posición con seguridad.
[MERCADO]: Lateral (RANGE) — precio oscila entre $93.800 y $95.200 sin dirección clara.
[SEÑALES]: 2 detectadas: (A) RSI 15m salió de sobreventa (28→34); (H) precio tocó soporte del rango.
[CONFIANZA]: 56% — 2 señales de calidad media en mercado lateral; insuficiente (mínimo 60%). Bot espera.
```

**Ejemplo BUY:**
```
[DECISIÓN]: Comprar (BUY) — tendencia alcista confirmada con 3 señales alineadas en múltiples marcos.
[MERCADO]: Tendencia alcista (TRENDING_UP) — precio sobre EMA20/50/200 en 1h, RSI 4h=58 sin sobrecompra.
[SEÑALES]: 3 confirmadas: (B) MACD cruzó arriba en 15m con momentum creciente; (C) rebote en EMA50 1h; (G) 1h y 4h alineados.
[CONFIANZA]: 90% — 3 señales de alta calidad (incluye G) + boost por volumen 2.1x la media. Tamaño: completo.
[NIVELES]: SL $94.820 (límite de pérdida — si baja aquí el bot vende automáticamente). TP $96.400 (meta de ganancia). R:R 2.1:1.
```

**Decisiones de diseño:**
- Los términos técnicos (TRENDING_UP, RSI, ATR, etc.) se explican inline entre paréntesis.
- Los códigos de confluencia A–H se preservan para trazabilidad técnica y coinciden con los marcadores del chart.
- La sección `[NIVELES]` se omite en HOLD/SELL para evitar ruido visual.
- El límite de 1000 chars es suficiente para todos los casos documentados; el truncado es silencioso.

### 2.6.bis Modelo de autonomía y capas de defensa (v1.4 — LLM-centric)

Esta sección complementa `01-functional-spec.md §F2.bis` con la vista técnica del nuevo modelo: sin overrides deterministas de `action` ni `sizing`; el LLM tiene autonomía total, auditada por el CoherenceChecker y protegida por el Risk Gate.

#### 2.6.bis.1 Cinco capas en línea (v1.4)

```
┌────────────────────────────────────────────────────────────┐
│ Capa 1: LLM Decisor — Pass 1                               │
│   • prompts/decisor_system.txt + prompts/decisor_user.txt  │
│   • Contexto en bloques A–K (ContextBuilder)               │
│   • Etiquetas interpretativas del labelers.py              │
│   • Output: JSON estricto                                  │
└──────────────────┬─────────────────────────────────────────┘
                   ▼ json.loads + DecisorOutput.model_validate
┌────────────────────────────────────────────────────────────┐
│ Capa 2: Pydantic (validación estructural)                  │
│   • Enums (regime, action), bounds (confidence_adjustment) │
│   • Falla → _hold_decision("parse_error")                  │
└──────────────────┬─────────────────────────────────────────┘
                   ▼ _filter_confluence_codes (A–H + I–Z activas)
┌────────────────────────────────────────────────────────────┐
│ Capa 2b: Confianza server-side (v1.9)                      │
│   • shared/confidence.py:apply_server_confidence           │
│   • Recalcula confidence_base; persiste confidence_meta    │
│   • confluence_count = len(confluences filtradas)          │
└──────────────────┬─────────────────────────────────────────┘
                   ▼ apply_risk_based_sizing (BUY, v1.10)
┌────────────────────────────────────────────────────────────┐
│ Capa 2d: Sizing por riesgo fijo                            │
│   • shared/position_sizing.py                              │
│   • position_size_pct = risk_per_trade_pct / sl_distance   │
│   • Cap R1 + piso min_position_size; position_size_meta  │
└──────────────────┬─────────────────────────────────────────┘
                   ▼
┌────────────────────────────────────────────────────────────┐
│ Capa 3: CoherenceChecker + two-pass (NUEVO, auditoría)     │
│   • risk/coherence_checker.py:CoherenceChecker.evaluate    │
│   • Reglas C1–C8: consistencia lógica declaración vs datos │
│   • Warnings → pass2 (LLM se auto-corrige): si C1/C2/C3   │
│   • strict_mode: C1/C2/C3 → _hold_decision("coherence")   │
│   • Persiste coherence_warnings en decisions.output        │
└──────────────────┬─────────────────────────────────────────┘
                   ▼  persist Decision (executed=false)
┌────────────────────────────────────────────────────────────┐
│ Capa 4: Risk Gate                                          │
│   • risk/risk_gate.py:RiskGate.validate                    │
│   • R0–R15 + drawdown + kill_switch + futures              │
│   • Cada rechazo lleva rule_id ("R0"..."R15")              │
│   • Falla → UPDATE Decision.rejected_reason → return       │
└──────────────────┬─────────────────────────────────────────┘
                   ▼
┌────────────────────────────────────────────────────────────┐
│ Capa 5: Circuit Breaker (proceso/engine)                   │
│   • risk/circuit_breaker.py                                │
│   • 5 fallas LLM consecutivas o exchange → engine_paused   │
│   • daily_stop_pct / max_drawdown_pct → pausa              │
└──────────────────┬─────────────────────────────────────────┘
                   ▼
┌────────────────────────────────────────────────────────────┐
│ Capa 6: Operador (humano)                                  │
│   • Kill switch, rollback de playbook, cambio de modo      │
│   • coherence_strict_mode (config key) — control de rigor  │
└────────────────────────────────────────────────────────────┘
```

> **Nota v1.4**: la antigua Capa 3 (`_apply_deterministic_overrides`) fue eliminada. El nuevo CoherenceChecker (Capa 3) es auditor, no reescritor — salvo en `strict_mode`. El Risk Gate (Capa 4) es la **única** barrera hard-blocking sobre la `action`.

#### 2.6.bis.2 Mapa código ↔ capa de defensa

| Capa | Archivo / función | Tipo de control | Salida observable |
|------|-------------------|-----------------|-------------------|
| 1a | `agents/prompts/decisor_system.txt` | Prompt engineering (jerarquía, guía sizing, etiquetas interpretativas). | Determina sesgo y formato del JSON; no es enforcement. |
| 1b | `agents/context_builder.py:build` | Construcción bloques A–K con indicadores enriquecidos y labelers. | Contexto inyectado en el user prompt. |
| 1c | `agents/decisor.py:_filter_confluence_codes` | Filtra códigos fuera de A–H y letras I–Z no activas en registry. | Log `decisor.invalid_confluence_codes_filtered`. |
| 2 | `shared/schemas.py:DecisorOutput` (Pydantic) | Validación estructural (tipos, enums, bounds). | Excepción → `_hold_decision("parse_error")`. |
| 2b | `shared/confidence.py` + `decisor.py:_apply_server_confidence` | Recalcula `confidence_base` y adjunta `confidence_meta`. | Base alineada a confluencias post-filtro; I–Z activas cuentan 1.0. |
| 2c | `agents/decisor.py:_hold_datos_insuficientes` | Early-exit si `critical_null_indicator`. | HOLD, `confidence=0.95`, sin tokens LLM. |
| 2d | `shared/position_sizing.py` + `decisor.py:_apply_sizing_from_ctx` | Recalcula `position_size_pct` en BUY (`risk_per_trade_pct`). | `position_size_meta` en output. |
| 1d | `agents/decisor_aggregate.py` + `_initial_llm_decision` | Self-consistency: N muestras LLM + votación si `decisor_self_consistency_n > 1`. | `self_consistency` en output; empate → HOLD. |
| 1e | `agents/llm_client.py` + config `decisor_llm_temperature` | Temperatura baja en llamadas Decisor (y two-pass). | Menor variación entre ciclos. |
| 3a | `risk/coherence_checker.py:CoherenceChecker.evaluate` | Auditoría de consistencia C1–C8. | `coherence_warnings` en output; C7 siempre critical. |
| 3b | `agents/decisor.py:_build_review_ctx` + pass 2 | Two-pass auto-revisión si hay C1/C2/C3. | Log `decisor.two_pass_triggered` + `decisor.two_pass_result`. |
| 3c | `agents/labelers.py` | Etiquetas interpretativas pre-calculadas. | Sugerencias en contexto; el LLM puede discrepar. |
| 4 | `risk/risk_gate.py:RiskGate.validate` | Bloqueo absoluto pre-exchange (R0–R15). | `Decision.rejected_reason` = `"risk_gate: R{n}"`, `rule_id` estructurado. |
| 5 | `risk/circuit_breaker.py:CircuitBreaker` | Pausa global del engine. | `engine_paused=true` + `engine_pause_reason`. |
| 6 | `web/api/control.py` + `COHERENCE_STRICT_MODE` | Intervención humana + control de rigor vía config. | `config_history`, `changed_by="user"/"operator"`. |

#### 2.6.bis.3 Garantías invariantes (v1.4)

El conjunto de capas garantiza para toda decisión en `decisions`:

1. **Sin BUY ejecutado con kill switch activo**: `kill_switch=true` ∧ `action=="BUY"` ⇒ `rejected_reason` contiene `kill_switch`.
2. **Sin BUY ejecutado violando R1**: para toda decisión BUY ejecutada, `output.position_size_pct <= max_position_pct`.
3. **Sin BUY ejecutado violando R4**: SL distance ∈ `[sl_atr_multiplier × ATR, sl_atr_max_multiplier × ATR]`.
4. **Sin BUY ejecutado violando R5**: R:R ≥ `min_rr_ratio`.
5. **Coherence warnings siempre persistidos**: `decisions.output.coherence_warnings` contiene la lista (puede ser vacía) de inconsistencias detectadas en ese ciclo.
6. **Two-pass auditado**: `decisions.output.two_pass_triggered` refleja si se realizó una segunda llamada LLM.

> **Eliminadas de v1.3**: garantías 1 (BUY en TRENDING_DOWN ejecutado) y 2 (BUY con confidence <0.60) — el LLM ahora puede ejecutar esas acciones si el Risk Gate lo aprueba.

#### 2.6.bis.4 Contexto enriquecido — Bloques A–K

El `ContextBuilder` arma el input del LLM en bloques semánticos organizados:

| Bloque | Contenido | Clave en `ctx` |
|--------|-----------|----------------|
| A | Perfil operativo (SCALPING/HÍBRIDO/DAY_TRADING), holding range, TF priority order | `block_a_*` |
| B | Market snapshot (precio, cambios%, ATR, volatility_label) | `atr_ref`, `price`, `pct_*` |
| C | Indicadores por TF ordenados por prioridad del perfil, con etiquetas labelers | `block_c_text` |
| D | Niveles clave (EMAs, VWAP, pivots, walls, highs/lows) | `block_d_text` |
| E | Order book enriquecido (depth 4 bandas, mid-impact, imbalance) | `block_e_text` |
| F | Alineación cross-timeframe | `block_f_text` |
| G | Últimas 3 decisiones **con detalle de coherence_warnings** de cada ciclo | `last_decisions_block` |
| H | Estado del portfolio (capital, P&L, drawdown, posiciones) | `capital_total`, `pnl_*` |
| I | Config de riesgo compacta (todos los parámetros numéricos) | `max_position_pct`, `min_rr_ratio`, ... |
| J | Playbook activo (markdown) | `playbook` |
| K | Lecciones post-mortem recientes (remap/guidance) | `block_k_lessons` |
| — | Catálogo dinámico I–Z (system prompt, no user block) | `{confluence_registry_block}` |

#### 2.6.bis.5 CoherenceChecker — reglas C1–C8

Ver `trading-engine/risk/coherence_checker.py`. Implementado con el dataclass `CoherenceWarning`:

```python
@dataclass
class CoherenceWarning:
    rule_id: str      # "C1"..."C8"
    message: str
    severity: str     # "warning" | "critical" (C7 siempre critical)
    evidence: dict    # valores numéricos que evidencian la inconsistencia
```

Las reglas factuales (C1/C2/C3) disparan **two-pass** cuando `TWO_PASS_ENABLED=true`. **C7** (R:R real ≤ mínimo) bloquea siempre. **C8** verifica `verify_spec` de confluencias I–Z del registry.

#### 2.6.bis.6 Two-pass — flujo técnico

```
Pass 1: LLM (system + user) → DecisorOutput → CoherenceChecker
  ↓ si hay C1/C2/C3 y TWO_PASS_ENABLED=true
Pass 2: LLM (mismo system + decisor_review_user.txt con warnings inyectados)
  → DecisorOutput (final) → CoherenceChecker (re-evaluación)
  → merged warnings + two_pass_triggered=True
```

Costo: 2× latencia y tokens en el worst case. El segundo pass usa el mismo provider y cascade de fallbacks. Si el segundo pass falla, se usa la decisión del primer pass.

#### 2.6.bis.7 Labelers — etiquetas interpretativas

`agents/labelers.py` pre-calcula etiquetas a partir de valores numéricos antes de armar el prompt. Son **sugerencias de interpretación estándar** — el LLM las ve junto al valor numérico y puede discrepar con justificación. El system prompt lo aclara explícitamente en la sección "ETIQUETAS INTERPRETATIVAS — SUGERENCIAS, NO ÓRDENES".

### 2.7 Aprendizaje del Supervisor: detalles técnicos

Complementa `01-functional-spec.md §F5.bis`. Acá se describe el contrato técnico del lazo de aprendizaje.

#### 2.7.1 Inputs del Supervisor

| Origen | Dato | Uso |
|--------|------|-----|
| `trades` (status=closed, últimas 24 h) | WR, PF, avg_win, avg_loss, avg_holding_min, sl_hits, tp_hits, max_dd, Sharpe del período. | Métricas del prompt. |
| `decisions` (agent=decisor, últimas 24 h, últimas 40 dump) | Distribución BUY/SELL/HOLD por régimen, histograma de confidence, rejection breakdown. | Análisis A2 (régimen + rechazos). |
| `playbook_versions` (last active) | Contenido previo trimmed a 1000 chars (§M3 token budget). | Continuidad evolutiva. |
| `config_history` (últimas 24 h) | Cambios de configuración, kill switch. | Contexto operacional A3. |
| `fee_snapshots` (último) | `roundtrip_fee_pct` actual. | R10 informativo. |
| `balance_snapshots` (primero de la ventana) | Capital inicial para max_dd_pct. | Gate semanal A1. |

#### 2.7.2 Outputs

| Tabla | Registro | Frecuencia |
|-------|----------|-----------|
| `playbook_versions` | Nueva versión `active=true`, anteriores `active=false`. | **Sólo cuando la fase 1 resuelve `regenerate`** (ver §2.7.4). En estado estable puede haber días sin nuevas filas. |
| `config` + `config_history` | 1 fila por cada suggestion aplicada, `changed_by="supervisor"`. | 0–14 cambios/día (independiente del veredicto del playbook). |
| `decisions` | `agent="supervisor"`. Estructura del `output` según veredicto (ver §2.7.4). | **Exactamente 1×/ejecución**, ratifique o regenere. Garantiza el audit trail (AC-14). |

#### 2.7.3 Guardrails algorítmicos (resumen)

- **`_SAFE_BOUNDS`** (14 claves) — rango admitido para auto-apply.
- **`_INVARIANTS`** — 4 relaciones cross-parámetro chequeadas incrementalmente:
  - `sl_atr_multiplier <= sl_atr_max_multiplier`
  - `min_rr_ratio <= default_rr_ratio`
  - `conf_threshold_trending_up <= conf_threshold_range <= conf_threshold_high_vol`
- **Exclusiones absolutas del auto-apply**: `daily_stop_pct`, `max_drawdown_pct`, `decisor_interval_min`, `atr_timeframe`.

> Detalle de bounds en `05-risk-and-safety.md §7`.

#### 2.7.4 Fase de ratificación (decisión `ratify | regenerate`)

Implementada en `Supervisor._evaluate_ratification()`. Cortocircuita la segunda llamada LLM cuando el playbook activo sigue siendo válido (ver `01-functional-spec.md §F5.bis.5`).

**1) Guardrails determinísticos (pre-LLM)** — fuerzan `regenerate` sin consultar al modelo:

| Disparador | Config key | Default | Causa |
|-----------|------------|---------|-------|
| Edad del playbook | `max_playbook_age_days` | `7` | Evita stale indefinido. |
| Delta WR vs. baseline | `playbook_force_regen_wr_delta_pct` | `15` | Detecta degradación o mejora material vs. WR del playbook activo. |
| Cambio de régimen | — | — | Régimen actual ≠ régimen del playbook activo. |
| Kill switch disparado | — | — | Contexto extraordinario en la ventana de 24 h. |
| Modo `diagnostic` con causa `(b)` o `(e)` | — | — | Playbook restrictivo o edge negativo (§F5.bis.4). |

**2) Llamada LLM #1 (sólo si ningún guardrail cortocircuita)** — prompt `supervisor_eval` con `json_mode=True`:

```jsonc
{
  "ratify": true,                                       // true|false
  "reason": "Mercado en RANGE estable; WR 58% vs. 60% baseline (Δ=2%); 0 cambios de régimen.",
  "suggested_change_summary": null                       // string si ratify=false; opcional
}
```

**3) Persistencia en `decisions.output`** según veredicto:

```jsonc
// Caso ratify
{
  "ratified": true,
  "ratify_reason": "...",
  "force_regen_reason": null,
  "mode": "normal",
  "config_suggestions": { ... },
  "config_applied": [ ... ],
  "config_rejected": [ ... ]
}

// Caso regenerate
{
  "ratified": false,
  "ratify_reason": null,
  "force_regen_reason": "playbook_age_days=8 >= max_playbook_age_days=7",  // null si vino del LLM
  "playbook": "# Playbook v13 — ...",
  "mode": "normal",
  "config_suggestions": { ... },
  "config_applied": [ ... ],
  "config_rejected": [ ... ]
}
```

**4) Eventos WebSocket** — diferenciados (ver `04-api-contracts.md §3.x`):
- `ratify` → `supervisor_ran` con `{ratified: true, ratify_reason, ts}`.
- `regenerate` → `playbook_updated` (preexistente) + `supervisor_ran` con `{ratified: false}`.

#### 2.7.bis Post-mortem — detalles técnicos

Complementa `01-functional-spec.md §F10`.

**Encadenamiento** (`main.py` → `outcome_attribution_tick_wrapper`):

```text
outcome_attribution_tick()
  └─ if postmortem_enabled:
       outcome_postmortem_tick(provider, fallback_providers, max_per_tick)
```

**Selección de candidatos** (`postmortem_job._fetch_candidates`):

- `Decision.ts >= now − outcome_attribution_window_hours` (misma ventana que attribution; default 25 h)
- `classification IN (BAD_BUY, BAD_SELL, MISSED_OPPORTUNITY, BLOCKED_GOOD_TRADE)`
- `matured = true`
- `postmortem_status IS NULL OR postmortem_status = 'failed'` (con `_meta.attempts < 3`)
- Orden por `severity_score` DESC; slice `[:postmortem_max_per_tick]`

**1 decisión = 1 llamada LLM** (`PostMortemAgent.analyze`):

| Slot | Config key | Default |
|------|------------|---------|
| Primary | `postmortem_provider` | `gemini-2.5-flash` |
| Fallback CSV | `postmortem_fallback_providers` | `groq-compound-mini,groq-llama-4-scout,groq-qwen3-32b,groq-gpt-oss-20b,groq-llama-3.1-8b` |

Parseo vía `_parse_providers()` (mismo helper que Decisor/Supervisor). Cascade en `LLMClient.call(fallbacks=...)`.

**Parseo defensivo**: `coerce_lesson_raw()` → `LessonRaw.model_validate()`. Errores de validación no consumen fallback adicional (la respuesta ya llegó); se reintenta en el próximo tick.

**Reintento**: `_record_failure()` incrementa `lesson_raw._meta.attempts`; status permanece `NULL` hasta 3 fallos, luego `failed`.

**Logs clave**: `postmortem.job.completed`, `postmortem.job.no_candidates`, `postmortem.job.validation_failed`, `postmortem.completed`, `postmortem.job.failed`.

---

## 3. Servicio `web`

FastAPI + uvicorn. Solo escribe en `config`, `config_history`, `trades.close_requested` y `playbook_versions.active|content`.

### 3.1 Entrypoint (`web/main.py`)

- Lifespan: crea engine/session_factory, lanza `ticker_broadcaster` async task.
- CORS configurable (`ALLOWED_ORIGINS`, default `http://localhost:3100`).
- Si la URL es SQLite → `Base.metadata.create_all` (modo dev/test).
- Routers en prefijo `/api`: health, trades, decisions, positions, balance, playbook, config, control, stats, suggestions, confluence.
- WebSocket `/ws` (sin prefijo).

### 3.2 Endpoints REST

| Método | Path | Resp | Descripción |
|--------|------|------|-------------|
| GET | `/api/health` | Health enriquecido: engine, binance (+ ws), llm (latency p50/p95/p99), risk_gate, coherence, outcome_attribution, postgres (counts + size), circuit_breaker, playbook, kill_switch. |
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
| GET | `/api/decisions/stats?window=24` | `DecisionStatsOut` | Rechazos por `rule_id` (R0–R15), warnings C1–C8, histogramas confidence/sizing, tasa two_pass. Ventana 1–168h. |
| GET | `/api/supervisor/runs` | `SupervisorRunOut[]` | Historial de ratificaciones/regeneraciones del Supervisor. |
| GET | `/api/decisions/outcomes?classification=&limit=&since_hours=&include_lessons=` | `DecisionOutcomeOut[]` | Outcomes contrafactuales; `include_lessons=true` expone post-mortem. |
| GET | `/api/confluence/candidates?status=&limit=` | `ConfluenceCandidateOut[]` | Cola de patrones aprendidos pendientes de promoción. |
| GET | `/api/confluence/registry?active_only=` | `ConfluenceRegistryOut[]` | Catálogo I–Z activo (o histórico si `active_only=false`). |
| POST | `/api/confluence/candidates/{id}/promote` | `ConfluenceRegistryOut` | Promueve candidato a registry (valida P1–P6). |
| POST | `/api/confluence/candidates/{id}/reject` | `ConfluenceCandidateOut` | Rechaza candidato con motivo opcional. |
| POST | `/api/confluence/registry/{code}/deactivate` | `ConfluenceRegistryOut` | Desactiva letra I–Z (reservada 30 días). |
| GET | `/api/ohlcv?timeframe=&limit=` | `CandleOut[]` | Velas OHLCV para chart (ascendente). |
| POST | `/api/circuit-breaker/reset` | `{ok}` | Reset pausa operacional (LLM/exchange); no aplica a daily_stop/drawdown. |
| POST | `/api/drawdown/reset` | `{ok}` | Reset ancla high-water mark (`drawdown_reset_ts`). |

| GET | `/api/config/suggestions` | `{ generated_at, suggestions, summary, ... } \| null` | Última sugerencia del Supervisor (`Decision.output.config_suggestions`). |

### 3.3 WebSocket `/ws`

- Loop cada 2 s consulta DB y emite hasta **8 tipos de evento**:
  - `decision`, `supervisor_ran`, `positions`, `trade_opened`, `trade_closed`, `playbook_updated`, `kill_switch_triggered`.
- Tarea de fondo `ticker_broadcaster` cada 5 s → evento `ticker`.
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
├── components/chart/PriceChart.tsx  ← lightweight-charts (Dashboard)
├── pages/
│   ├── Dashboard.tsx    ← balance, posiciones, chart, última decisión, P&L día
│   ├── Trades.tsx       ← listado + cerrar
│   ├── Decisions.tsx    ← historial; filtros acción/conf/fecha; ConfidenceBreakdown
│   ├── components/ConfidenceBreakdown.tsx
│   ├── types/decisorOutput.ts
│   ├── Playbook.tsx     ← markdown + rollback + edit
│   ├── Config.tsx       ← 60+ parámetros; secciones LLM (Decisor/Supervisor/Post-mortem) + FallbackChain
│   ├── Confluence.tsx   ← candidatos post-mortem, registry I–Z, promote/reject/deactivate
│   └── Health.tsx       ← estado motor/DB/Binance/LLM/outcome attribution
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
| `decision_outcomes` | engine | Atribución contrafactual 1:1 con `decisions` (MFE/MAE, clasificación, lecciones post-mortem). |
| `confluence_candidates` | engine | Patrones compuestos aprendidos pendientes de promoción (upsert por `pattern_tag`). |
| `confluence_registry` | engine + web | Letras I–Z promovidas con `verify_spec` JSONB; PK `code`. |
| `trades` | engine | Operaciones (BUY/SELL). Estados open/closed. Incluye `order_id_sl/tp`. |
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
| Fórmula de confianza | `conf_base_*`, `peso_regime_range`, `peso_regime_high_vol`, `adj_*` (guías LLM); **enforcement** de base en `shared/confidence.py`. `confluence_weak_factor` sigue en config como referencia LLM (no usado en conteo v1.9; peso I–Z = 1.0). |
| Decisor v2 | `min_fees_to_tp_ratio`, `min_confluences_buy`, `cooldown_after_sell_min`, `subjective_adj_max`, `expected_holding_max_min`, `confluence_weak_factor`. |
| Supervisor — Ratificación | `max_playbook_age_days`, `playbook_force_regen_wr_delta_pct`. |
| Coherence / two-pass / LLM Decisor | `coherence_strict_mode`, `two_pass_enabled`, `min_position_size`, `decisor_llm_temperature`, `decisor_self_consistency_n`. |
| Sizing por riesgo | `risk_per_trade_pct` (default `0.005`; usado por `apply_risk_based_sizing` en BUY). |
| Supervisor auto-config | `supervisor_config_window_hours` (168), `supervisor_config_auto_apply` (false), `supervisor_config_min_evaluated_decisions` (30). |
| Outcome attribution | `outcome_attribution_interval_min`, `outcome_attribution_horizon_min`, `outcome_attribution_window_hours`, `outcome_coverage_threshold_pct`. |
| Post-mortem / Bloque K | `postmortem_enabled`, `postmortem_max_per_tick`, `postmortem_provider`, `postmortem_fallback_providers`, `block_k_max_lines`, `block_k_window_hours`. |
| Confluencias I–Z | `confluence_promotion_min_occurrences`, `confluence_promotion_window_days`, `confluence_registry_max_active`. |
| Producto / derivados | `trading_product`, `max_leverage`, `margin_mode`, `funding_rate_max_pct`, `liquidation_buffer_atr` (operator-only salvo `funding_rate_max_pct` si se agrega a bounds). |
| Engine interno | `engine_paused`, `engine_pause_reason`, `drawdown_reset_ts`. |

Comportamiento:

- `seed_defaults()` inserta sólo claves faltantes (idempotente).
- `get_typed()` castea según `value_type` (`int|float|bool|json|string`).
- `set()` actualiza valor + inserta `ConfigHistory` con `changed_by`.

### 6.1 Auto-apply Supervisor (`_SAFE_BOUNDS`)

```python
_SAFE_BOUNDS = {
  "sl_atr_multiplier":          (0.1, 0.8),
  "sl_atr_max_multiplier":      (0.5, 20.0),
  "min_rr_ratio":               (1.0, 3.0),
  "max_position_pct":           (0.01, 0.20),
  "min_fees_to_tp_ratio":       (1.5, 6.0),
  "expected_holding_max_min":   (30, 1440),
  "cooldown_after_sell_min":    (0, 120),
  "conf_threshold_trending_up": (0.40, 0.85),
  "conf_threshold_range":       (0.50, 0.90),
  "conf_threshold_high_vol":    (0.60, 0.95),
}
```

`daily_stop_pct`, `max_drawdown_pct`, `decisor_interval_min` y `atr_timeframe` están **excluidos** — el operador debe cambiarlos manualmente desde `/config`.

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
| `TRADING_PRODUCT` | `spot` | `spot` \| `futures`; puede sobreescribirse por `config.trading_product`. |
| `LOG_LEVEL` | `INFO` | structlog. |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8000` | uvicorn. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Notificaciones opcionales. |

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
| 005 | Alineación defaults `conf_base_*`. |
| 006 | Índices GIN (`indicators`, `decisions` input/output), índice parcial único playbook, FK `trades.decision_id`. |
| 007 | `trades.order_id_sl`, `trades.order_id_tp`. |
| 008 | Tabla `decision_outcomes` + índices. |
| 009 | `trades.close_reason` ampliado a VARCHAR(30). |
| 010 | `balance_snapshots.usdt_locked`, `balance_snapshots.btc_locked`. |
| 011 | `decision_outcomes`: `postmortem_status`, `lesson_raw`, `lesson_normalized`, `postmortem_at` + índice parcial pending. |
| 012 | Tablas `confluence_candidates`, `confluence_registry`. |
| 013 | Seed config `postmortem_fallback_providers` (idempotente). |
| 015 | Seed config `outcome_attribution_window_hours` (idempotente). |
| 016 | Campos futures en `trades`, `positions`, `balance_snapshots`. |

### 8.4 Operaciones rutinarias

- **Logs**: `docker-compose logs -f trading-engine` (eventos: `scheduler.started`, `ohlcv.persisted`, `decisor.decided`, `decision.rejected`, `executor.open_executed`, `futures.sizing_unfeasible`, `order_tracker.bracket_detected`).
- **Backup DB**: `docker-compose exec postgres pg_dump -U trader crypto_ai_trading > backup_$(date +%Y%m%d).sql`.
- **Kill switch**: dashboard `/` botón rojo o `POST /api/kill-switch {enabled:true}`.
- **Rollback de playbook**: dashboard `/playbook` → "Activar" sobre versión anterior.

### 8.5 Health & observabilidad

- `/api/health`: engine freshness, binance REST + WS proxy, LLM stats (latency p50/p95/p99, parse/llm errors), risk_gate/coherence breakdown 24h, outcome_attribution stats, postgres table counts + DB size, circuit_breaker state, playbook activo.
- `/api/ping` para liveness.
- Logs JSON (`structlog`) pueden ingerirse en cualquier stack (no hay APM acoplado).

---

## 9. Decisiones de diseño y trade-offs

| Decisión | Justificación |
|----------|---------------|
| **Postgres como único canal entre engine y web** | Elimina necesidad de bus de mensajes; permite restart independiente; trazabilidad total. |
| **Risk Gate determinístico post-LLM** | El LLM puede alucinar; las reglas R0–R15 son no-negociables y verificadas en código. |
| **ExchangeAdapter + default Spot** | Futuros opt-in vía `trading_product`; rollback sin redeploy. Guard de sizing evita operar bajo `min_notional`. |
| **CoherenceChecker + two-pass** | Audita inconsistencias lógicas del LLM sin reescribir silenciosamente (salvo `strict_mode`). |
| **TR winsorizado en ATR** | Velas anómalas del testnet inflaban ATR 4–5× durante horas. |
| **Playbook como markdown versionado en BD** | El Supervisor escribe lenguaje natural que el Decisor lee como prompt; permite auditoría y rollback. |
| **Configuración 100% en BD** | Cambios en caliente sin redeploy; auditados en `config_history`. |
| **Frase literal `CONFIRMO TRADING REAL`** | Imposibilita scripts accidentales que toggleen LIVE. |
| **Cascade de providers LLM con skip en 429** | Free tiers de Gemini/Groq tienen rate limits agresivos; saltar evita degradación de calidad. |
| **OrderTracker pasivo basado en `fetch_my_trades`** | Binance ejecuta el bracket; el bot solo reconcilia, evita doble cierre. |
| **Spot por defecto; futures opt-in** | `TRADING_PRODUCT=spot` mantiene comportamiento legacy; shorts solo con futures + R12–R15. API key sin retiros. |

---

## 10. Code Ownership Map

Mapeo componente → archivos con scoring de propiedad (1.0 = owner principal, 0.5–0.79 = supporting, 0.2–0.49 = compartido / shared).

| Componente | Rol | Primary (0.8–1.0) | Supporting (0.5–0.79) | Shared (0.2–0.49) |
|------------|-----|--------------------|------------------------|-------------------|
| Engine entrypoint | Bootstrap | `trading-engine/main.py` | `trading-engine/config.py`, `trading-engine/exchange.py`, `trading-engine/scheduler.py` | `shared/db/base.py` |
| Decisor | Agent | `trading-engine/agents/decisor.py`, `trading-engine/agents/prompts/decisor_system.txt`, `trading-engine/agents/prompts/decisor_user.txt` | `agents/context_builder.py`, `agents/llm_client.py`, `agents/prompt_manager.py` | `shared/schemas.py`, `shared/confidence.py`, `shared/db/models.py` |
| Supervisor | Agent | `trading-engine/agents/supervisor.py`, `agents/prompts/supervisor_system.txt`, `agents/prompts/supervisor_user.txt`, `agents/prompts/playbook_v0.md` | `agents/llm_client.py`, `agents/prompt_manager.py` | `shared/config_store.py`, `shared/db/models.py` |
| Risk Gate | Risk | `trading-engine/risk/risk_gate.py` | — | `shared/schemas.py` |
| Circuit Breaker | Risk | `trading-engine/risk/circuit_breaker.py` | — | Integrado en `main.py`; pausas operacionales auto-reset ~10 min vs financieras manuales. |
| CoherenceChecker | Risk | `trading-engine/risk/coherence_checker.py` | `agents/decisor.py` | `shared/schemas.py` |
| Outcome Attribution | Agent | `agents/outcome_attribution.py`, `agents/outcome_attribution_job.py` | — | `shared/db/models.py` |
| Post-mortem | Agent | `agents/postmortem_agent.py`, `agents/postmortem_job.py`, `agents/lesson_normalizer.py` | `agents/prompts/postmortem_*.txt` | `shared/db/models.py` |
| Confluence registry | Agent + API | `agents/confluence_registry.py`, `shared/confluence_registry_ops.py` | `web/api/confluence.py`, `frontend/src/pages/Confluence.tsx` | `shared/db/models.py` |
| PriceCollector | Data | `collectors/price_collector.py`, `collectors/indicators.py` | — | `shared/db/models.py` |
| OrderBookCollector | Data | `collectors/orderbook_collector.py` | — | — |
| FeeManager | Execution | `execution/fee_manager.py` | — | `shared/db/models.py` |
| Executor | Execution | `execution/executor.py`, `execution/exchange_adapter.py` | `exchange.py` | `shared/db/models.py`, `shared/schemas.py` |
| PositionManager | Execution | `execution/position_manager.py` | — | `shared/db/models.py` |
| OrderTracker | Execution | `execution/order_tracker.py` | `execution/executor.py` | `shared/db/models.py` |
| Scheduler | Infra | `trading-engine/scheduler.py` | — | — |
| Web bootstrap | API | `web/main.py` | — | `shared/db/base.py`, `web/ws/feeds.py` |
| Web API routers | API | `web/api/*.py` (uno por dominio) | — | `shared/db/models.py`, `shared/config_store.py` |
| WebSocket feeds | API | `web/ws/feeds.py`, `web/ws/manager.py` | — | `shared/db/models.py` |
| Frontend pages | UI | `frontend/src/pages/*.tsx` | `frontend/src/api/client.ts`, `frontend/src/hooks/useWebSocket.ts` | `frontend/src/types/index.ts` |
| Schemas compartidos | Shared | `shared/schemas.py`, `shared/confidence.py` | — | (usado por engine + web + tests) |
| Config store | Shared | `shared/config_store.py` | — | (usado por engine + web) |
| DB layer | Shared | `shared/db/base.py`, `shared/db/models.py` | — | — |
| Migraciones | Infra | `trading-engine/alembic/versions/*.py`, `trading-engine/alembic/env.py` | — | `shared/db/models.py` |
| Backtester | Standalone | `backtesting/runner.py` | `backtesting/tests/test_runner.py` | — |

Convenciones del mapa:
- Un archivo con score 1.0 implica que el componente es la única razón de existir del archivo.
- Archivos con score < 0.5 son utilidades transversales (modelos, schemas, sesiones DB).
- Los prompts `.txt` del Decisor y Supervisor son parte intrínseca de su lógica y se versionan con su agente.

---

## 11. Roadmap técnico (extracto)

- [ ] Tests de integración end-to-end con un fake exchange determinístico.
- [ ] Telemetría/metrics (OpenTelemetry) — hoy solo logs JSON.
- [ ] Backtesting con LLM real (hoy es indicator-only baseline).
- [ ] Cron job para pre-computar `daily_stats` cuando el histórico supere ~90 días.
- [ ] Hardening del frontend (auth, RBAC) — actualmente sin login.
