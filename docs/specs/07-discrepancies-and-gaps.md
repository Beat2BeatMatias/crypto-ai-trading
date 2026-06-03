# Discrepancias y Gaps de Documentación — Crypto AI Trading

> Audiencia: Tech leads / SRE / Risk.
> Versión: 1.5 — 2026-06-02 (futuros/shorts documentados; D-034 entregado).
> Base de comparación: design docs + `docs/specs/` vs. código en HEAD (2026-06-02).

Este documento consolida el resultado del cross-validation entre la documentación de diseño existente y la implementación real. La **regla de oro**: cuando un campo difiere entre design doc y código, **el código manda**. Las discrepancias se registran para que el equipo decida si actualizar el código a la spec o la spec al código.

---

## 1. Convenciones

### 1.1 Escala de severidad

| Nivel | Significado | Acción esperada |
|-------|-------------|------------------|
| 🔴 CRÍTICO | Diferencia introduce bug funcional o riesgo financiero | Resolver antes de pasar a LIVE. |
| 🟠 ALTO | Feature documentada no entregada o entregada parcialmente | Roadmap inmediato (siguiente sprint). |
| 🟡 MEDIO | Discrepancia menor, no afecta operación pero confunde | Backlog priorizado. |
| 🟢 INFO | Diferencia esperada (evolución natural post-design-doc) | Sólo actualizar docs cuando se toque el área. |

### 1.2 Resumen ejecutivo

| Severidad | Cantidad | IDs |
|-----------|----------|-----|
| 🔴 CRÍTICO | 3 | D-001, D-002, D-003 |
| 🟠 ALTO | 5 | D-004, D-005, D-006, D-009, D-013 |
| 🟡 MEDIO | 8 | D-007, D-008, D-010, D-011, D-014, D-015, D-019, D-021 |
| 🟢 INFO | 10 | D-012, D-016, D-017, D-018, D-020, D-030, D-031, D-032, D-033, D-034 |
| **Total** | **27** | |

### 1.3 Estado de resolución (actualizado 2026-05-25)

Todos los gaps D-001–D-021 resueltos o documentados (ver tabla inferior). Desde la revisión 2026-05-17 se incorporaron features nuevas no cubiertas por el design doc original:

| ID | Severidad | Estado | Descripción |
|----|-----------|--------|-------------|
| D-022 | 🟢 INFO | ✅ ENTREGADO | Outcome attribution contrafactual (`decision_outcomes`, job, API `/decisions/outcomes`). |
| D-023 | 🟢 INFO | ✅ ENTREGADO | Telegram notifications opcionales (`notifications/telegram.py`). |
| D-024 | 🟢 INFO | ✅ ENTREGADO | Bracket OCO con `order_id_sl/tp` (migration 007). |
| D-025 | 🟢 INFO | ✅ ENTREGADO | Balance snapshots con `usdt_locked`/`btc_locked` (migration 010). |
| D-030 | 🟢 INFO | ✅ ENTREGADO | Post-mortem learning: job LLM encadenado, Bloque K, columnas en `decision_outcomes` (migration 011). Endurecido 2026-05-25: coerce, retry, fallback (013). |
| D-031 | 🟢 INFO | ✅ ENTREGADO | Catálogo extendido I–Z: `confluence_candidates`, `confluence_registry`, promoción Supervisor (migration 012). |
| D-032 | 🟢 INFO | ✅ ENTREGADO | UI operador `/confluence` + API POST promote/reject/deactivate. |
| D-033 | 🟢 INFO | ✅ RESUELTO | Post-mortems `failed` por JSON heterogéneo del LLM y 429 Groq: `coerce_lesson_raw`, prompts con schema, `postmortem_fallback_providers`, reintento hasta 3 intentos. |
| D-026 | 🟡 MEDIO | ⏳ PENDIENTE | Filtros avanzados frontend `/trades` y `/decisions` (date range, export CSV). |
| D-027 | 🟡 MEDIO | ⏳ PENDIENTE | Diff viewer playbook a nivel de palabra. |
| D-028 | 🟡 MEDIO | 📋 DOCUMENTADA | `daily_stats` sigue sin job batch; query on-the-fly OK para volúmenes actuales. |
| D-029 | 🟢 INFO | 📋 DOCUMENTADA | Health no expone uptime RSS del proceso engine (requiere endpoint de telemetría en engine). |
| D-034 | 🟢 INFO | ✅ ENTREGADO | Futuros USDT-M + shorts: `ExchangeAdapter`, `SHORT`, R12–R15, migración 016, specs v1.11–v1.12. Diseño: `docs/superpowers/specs/2026-06-02-futures-shorts-design.md`. |
| D-035 | 🟡 MEDIO | ⏳ PENDIENTE | `GET /api/balance` no expone `margin_balance` / `available_margin` aunque el engine los persiste en `balance_snapshots` (futures). |
| D-036 | 🟡 MEDIO | ⏳ PENDIENTE | Frontend: markers SHORT y línea de liquidación en `PriceChart`; filtros CSV por `position_side` en `/trades` (parcial en Decisions). |

### 1.3.bis Estado de resolución (histórico 2026-05-17)

| ID | Severidad | Estado | Resolución |
|----|-----------|--------|------------|
| D-001 | 🔴 | ✅ RESUELTA | `_compute_risk_metrics()` en `main.py` + `cb.evaluate()` con valores reales |
| D-002 | 🔴 | ✅ RESUELTA | `await orderbook.start()` invocado en bootstrap de `main.py` |
| D-003 | 🔴 | ✅ RESUELTA | `cb.evaluate()` integrado al loop principal antes del Decisor |
| D-004 | 🟠 | ✅ RESUELTA | `min_fees_to_tp_ratio` incluido en `calibration` dict y pasado a `RiskGate.validate()` |
| D-005 | 🟠 | 📋 DOCUMENTADA | Auto-rollback fuera de scope v1; sólo rollback manual vía `POST /api/playbook/{v}/activate` |
| D-006 | 🟠 | ✅ RESUELTA | Migración `006` crea índices GIN (indicators, decisions input/output), índice parcial único playbook y FK `trades.decision_id` |
| D-007 | 🟡 | 📋 DOCUMENTADA | Thresholds reales actualizados en `05-risk-and-safety.md` §4 (R:R 1.3, SL 0.3–1.5×ATR) |
| D-008 | 🟡 | ✅ RESUELTA | Overrides documentados en `05-risk-and-safety.md` §3 |
| D-009 | 🟠 | ✅ RESUELTA | `feeds.py` emite los 7 eventos: ticker, positions, decision, trade_opened, trade_closed, playbook_updated, kill_switch_triggered |
| D-010 | 🟡 | 📋 DOCUMENTADA | Campos nuevos de `DecisorOutput` documentados en `02-technical-spec.md` §7.1 |
| D-011 | 🟡 | 📋 DOCUMENTADA | `daily_stats` on-the-fly aceptado para v1; cron pre-computo en backlog para cuando el histórico supere 90 días |
| D-012 | 🟢 | ✅ RESUELTA | Evolución natural documentada; `balance_snapshots` y `close_requested` son features válidas |
| D-013 | 🟠 | ✅ RESUELTA | `/health` enriquecido: Postgres table counts + DB size, Binance WS status (age 1m), LLM latency p50/p95/p99 desde `decisions.latency_ms` |
| D-014 | 🟡 | ⏳ PENDIENTE | Filtros frontend /trades y /decisions en backlog |
| D-015 | 🟡 | ⏳ PENDIENTE | Diff viewer y reset-to-v0 playbook en backlog |
| D-016 | 🟢 | 📋 DOCUMENTADA | pandas puro elegido en lugar de vectorbt; decisión técnica válida |
| D-017 | 🟢 | ✅ RESUELTA | `recharts` eliminada de `frontend/package.json`; `lightweight-charts` cubre el uso previsto |
| D-018 | 🟢 | 📋 DOCUMENTADA | pandas puro en lugar de pandas-ta; sin impacto operativo |
| D-019 | 🟡 | ✅ RESUELTA | Test `test_signal_buy_requires_min_confluences` corregido con fixture completa (open, close, ema200_slope_pct) |
| D-020 | 🟢 | ✅ RESUELTA | `order_id_open` y `order_id_close` agregados a `TradeOut` y `_to_out()` en `web/api/trades.py` |
| D-021 | 🟡 | ✅ RESUELTA | Fase de ratificación del Supervisor (§F5.bis.5 + §2.7.4) elimina la obligación de generar `PlaybookVersion` por ciclo. Audit trail garantizado por AC-14. |

---

## 2. Discrepancias críticas (blocking para LIVE)

### D-001 — Risk Gate: reglas de daily stop y total drawdown nunca disparan

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Regla "Daily stop" (R9) | `if daily_pnl_pct ≤ daily_stop_pct → force HOLD` | Misma intención (`risk_gate.py:60-61`). |
| Regla "Total drawdown" | `if total_drawdown ≤ max_drawdown_pct → kill switch` | Misma intención (`risk_gate.py:35-37`). |
| **Valores reales pasados al gate** | Computar PnL diario y drawdown total y pasarlos al RG | `main.py:213-216` pasa `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0` **siempre**. |

**Severidad**: 🔴 CRÍTICO. Las reglas existen en `validate()` pero **nunca se gatillan** porque los inputs son cero.

**Acción sugerida**: computar `daily_pnl_pct` desde `trades` cerrados del día UTC y `total_drawdown_pct` desde balance vs. high-water mark; pasarlos al `RiskGate.validate()` antes del tick del Decisor.

---

### D-002 — `OrderBookCollector` nunca se inicia

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Spec §4.2 | "OrderBookColl. • Binance WS • in-memory top 10 levels" | Clase implementada (`orderbook_collector.py:1-103`). |
| Inicialización | Se asume que `start()` corre al boot | `main.py:76` instancia pero **no llama `start()`**. |
| Consecuencia | Decisor recibe orderbook real | Decisor recibe `None` → spread/imbalance en valores neutros. |

**Severidad**: 🔴 CRÍTICO. El Decisor decide sin información de microestructura aunque el prompt sí pide orderbook (features F y E del catálogo de confluencias).

---

### D-003 — `CircuitBreaker.evaluate()` no integrado al loop

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Spec §8.3 | "Daily stop: cuando dispara, `kill_switch_daily=true` hasta 00:00 UTC. Total drawdown: dispara `kill_switch` permanente con reset manual." | `CircuitBreaker.evaluate()` implementa la lógica (`circuit_breaker.py:23-30`) pero **no se llama desde `main.py`**. |
| Operativo en runtime | Sí | No — sólo cuenta fallos LLM/exchange. |

**Severidad**: 🔴 CRÍTICO. Los circuit breakers documentados como "última línea de defensa financiera" no se aplican en tiempo real.

---

## 3. Discrepancias altas (roadmap inmediato)

### D-004 — `MIN_FEES_TO_TP_RATIO` declarado pero no consumido

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Config key | Presente en migración `004` | `ConfigKey.MIN_FEES_TO_TP_RATIO` en `config_store.py`. |
| Consumo | — | `calibration` dict en `main.py:108-134` **NO incluye** `min_fees_to_tp_ratio`. |
| Default fallback | — | `risk_gate.py:30` usa `min_fees_to_tp_ratio: float = 3.0` hardcoded. |

**Severidad**: 🟠 ALTO. La regla R10 que exige `move_pct >= N × roundtrip_fee_pct` ignora el valor configurado por usuario.

---

### D-005 — Supervisor sin auto-rollback

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Spec §8.4 | "Automatic rollback: si 7 días post-update muestran >2× drawdown vs prior 7 días, revertir a versión previa + alert" | NO implementado. |
| Rollback manual | UI tiene botón | `POST /api/playbook/{v}/activate` existe (`web/api/playbook.py:42`). |

**Severidad**: 🟠 ALTO. Característica de seguridad documentada que no existe en código.

---

### D-006 — Índices GIN y FKs declarados sólo en ORM

Mismatch entre `shared/db/models.py` (ORM) y `trading-engine/alembic/versions/001_initial_schema.py` (DDL real).

| Objeto | Design doc § 6.1 | ORM | Migración Alembic |
|--------|-------------------|-----|-------------------|
| `idx_indicators_data` GIN | ✅ | ✅ | ❌ |
| `idx_decisions_output` GIN | ✅ | ✅ | ❌ |
| `idx_decisions_input` GIN | ✅ | ✅ | ❌ |
| `idx_playbook_active` unique partial WHERE active=true | ✅ | ✅ | ❌ |
| FK `decisions.trade_id → trades.id` | ✅ ALTER TABLE | ✅ `use_alter` | ❌ (sólo columna sin FK) |
| FK `trades.decision_id → decisions.id` | ✅ | ✅ `use_alter` | ❌ |

**Severidad**: 🟠 ALTO. En la BD real **no existen** esos índices ni FKs. Performance y consistencia comprometidas.

**Acción sugerida**: crear migración Alembic (revisión 005) que agregue índices GIN, índice parcial único, y FKs `use_alter`.

---

### D-009 — WebSocket: 3 de 7 eventos emitidos

| Evento design doc §9.2 | Evento código `web/ws/feeds.py` | Frecuencia |
|------------------------|----------------------------------|------------|
| `price_update` | `ticker` | ~5 s ✅ (renombrado). |
| `position_update` | `positions` | ~2 s ✅ (renombrado, más frecuente). |
| `decision_emitted` | `decision` | ~2 s ✅ (renombrado). |
| `trade_opened` | ❌ no implementado | — |
| `trade_closed` | ❌ no implementado | — |
| `playbook_updated` | ❌ no implementado | — |
| `kill_switch_triggered` | ❌ no implementado | — |

**Severidad**: 🟠 ALTO. La UI no recibe notificación push cuando se abre/cierra un trade o cambia el playbook — debe pollear.

---

### D-013 — Página `/health`: cobertura parcial

| Métrica design doc §9.1 | Implementado |
|--------------------------|--------------|
| Engine card: heartbeat ts | ✅ (vía edad última decisión). |
| Engine card: uptime | ❌ |
| Engine card: memory usage | ❌ |
| Engine card: # errors 24h | ❌ |
| Engine card: recent error log | ❌ |
| Postgres card: connection | ✅ |
| Postgres card: # filas por tabla | ❌ |
| Postgres card: DB size | ❌ |
| Binance card: REST | ✅ (vía edad ohlcv). |
| Binance card: WebSocket | ❌ |
| Binance card: rate limit usage | ❌ |
| LLM card por provider: status | ❌ |
| LLM card: latency p50/p95/p99 | ❌ |
| LLM card: quota remaining | ❌ |
| LLM card: # fallback triggers | ❌ |
| Panel de "Recent errors" | ❌ |

**Severidad**: 🟠 ALTO. Operación a ciegas en producción cuando algo falla.

---

## 4. Discrepancias medias

### D-007 — Risk Gate: cantidad y nombre de reglas

| Fuente | Cantidad de reglas | Naming |
|--------|--------------------|--------|
| Design doc §8.1 | **10 reglas** numeradas | Numérico (R1–R10). |
| Código `risk_gate.py:validate()` | **14 chequeos** | Sin nombre formal salvo `R10:` en el mensaje de rechazo. |

**Diffs específicos**:

| Aspecto | Design doc | Código |
|---------|------------|--------|
| R:R mínimo | 1.5:1 | **1.3** (`risk_gate.py:11`). |
| SL distance mínima | ≥ 0.5 × ATR(1h) | **0.3 × ATR** (`sl_atr_multiplier=0.3`). |
| SL distance máxima | No documentada | **1.5 × ATR** (`sl_atr_max_multiplier=1.5`). |
| Slippage check | Regla 5 documentada | **NO implementada** en `validate()`. |
| Schema validation Pydantic | Regla 1 dentro del RG | Está en el **Decisor** (`decisor.py:89-98`). |
| Fee-aware TP (R10) | NO documentada | **Implementada** (`risk_gate.py:77-85`). |

**Severidad**: 🟡 MEDIO. Las reglas son sustancialmente las mismas pero los thresholds y la asignación de responsabilidades difieren del design doc.

---

### D-008 — Overrides determinísticos pre-RiskGate en Decisor

| Estado | **RESUELTO** (v1.4 / LLM-centric) |
|--------|----------------------------------|
| Código actual | No hay overrides de `action` ni sizing por confianza/régimen. Sizing BUY/SHORT server-side (`risk_per_trade_pct`). Self-consistency opcional. Risk Gate (R0–R15) y `coherence_strict_mode` son barreras hard. Futuros opt-in (`trading_product`). |
| Histórico | Antes existían overrides pre-RG (TRENDING_DOWN→HOLD, confidence-floor); eliminados en rediseño v1.3–v1.4. |

---

### D-010 — `DecisorOutput`: campos del JSON

| Campo | Design doc §7.1 (ejemplo) | Código actual (v1.9) |
|-------|---------------------------|----------------------|
| `regime` | ✅ | ✅ idéntico. |
| `confluences` | ✅ list[str] | ✅ post-filtro A–H + I–Z activas, max_length=10. |
| `action` | ✅ | ✅ |
| `confidence_base` | ❌ | ✅ **calculado en servidor** (`shared/confidence.py`), no por el LLM. |
| `confidence_adjustment` | ❌ | ✅ LLM, clamp ±0.10. |
| `confidence` | ✅ float 0-1 | ✅ derivado `clip(base+adj, 0, 1)` en Pydantic. |
| `confidence_meta` | ❌ | ✅ auditoría del cálculo (conteo, factores, dropped). |
| `stop_loss` / `take_profit` | ✅ | ✅ |
| `position_size_pct` | ✅ 0.01-0.10 | ✅ rango **[0, 0.25]**. |
| `expected_holding_min` | ❌ | ✅ int ≥ 1. |
| `reasoning` | ✅ max 240 chars | ✅ max **1000** chars. |

**Severidad**: 🟢 BAJO — documentado en `04-api-contracts.md` §1.3 y `01-functional-spec.md` §6.3 / §F2.bis.1.

**Nota v1.9**: el conteo de confluencias para la base incluye I–Z promovidas (peso 1.0). Códigos desactivados se eliminan antes del cálculo.

---

### D-011 — `daily_stats`: pre-computado vs on-the-fly

| Fuente | Comportamiento |
|--------|----------------|
| Design doc §6.1 | Tabla `daily_stats` "Pre-computed daily stats". |
| Código | `GET /api/stats/daily` (`web/api/stats.py:37`) **agrega en Python** sobre filas del día UTC; **ningún job escribe `daily_stats`**. |

**Severidad**: 🟡 MEDIO. La tabla existe vacía. Performance OK con volúmenes actuales; degradará con histórico grande.

---

### D-014 — Frontend `/trades` y `/decisions`: filtros faltantes

**`/trades`**:

| Filtro design doc | Implementado |
|-------------------|--------------|
| Date range picker | ❌ |
| Status (open/closed/cancelled) | ✅ (`?status=`). |
| Result (win/loss/all) | ❌ |
| Close reason | ❌ |
| Sortable por columna | ❌ |
| Click row → modal con decisión completa | ❌ |
| Export CSV | ❌ |
| Summary footer | ❌ |

**`/decisions`**:

| Filtro design doc | Implementado |
|-------------------|--------------|
| Agent (decisor/supervisor) | ✅ (`?agent=` API; selector en UI). |
| Action (BUY/SELL/HOLD) | ✅ (filtro client-side en UI). |
| Confidence range slider | ✅ (filtro client-side en UI). |
| Executed (yes/no/rejected) | 🟡 parcial (`?executed=` API). |
| Date range | ✅ (filtro client-side en UI). |
| Click row → side panel con input/output | ✅ |
| Desglose confianza (base / adj / meta) | ✅ v1.9 (`ConfidenceBreakdown`) |

**Severidad**: 🟡 MEDIO. Faltan filtros server-side para acción/confianza/fecha y export CSV.

---

### D-015 — Frontend `/playbook`: diff viewer y reset

| Feature design doc | Implementado |
|---------------------|--------------|
| Renderizado markdown activo | ✅ |
| Version history sidebar con badge "active" | ✅ |
| **Diff viewer** entre 2 versiones | ❌ |
| Manual edit (markdown editor) | ✅ (`PATCH /api/playbook/{v}/content`). |
| **Reset to v0** botón | ❌ |
| Rollback one-click | ✅ (`POST /activate`). |

**Severidad**: 🟡 MEDIO.

---

### D-019 — Test `test_signal_buy_requires_min_confluences` roto

| Aspecto | Detalle |
|---------|---------|
| Archivo | `backtesting/tests/test_runner.py`. |
| Intención | Confirmar que `signal_buy` falla con <3 confluencias. |
| Realidad | `pd.Series` mockeada **no incluye** `open`, `close`, `ema200_slope_pct` que `signal_buy` lee al inicio → `KeyError` antes de validar confluencias. |

**Severidad**: 🟡 MEDIO. Test no valida lo que dice validar (false-pass disfrazado de pass).

---

## 5. Discrepancias info (evolución natural)

### D-012 — `balance_snapshots` y `close_requested` no en design doc

| Item | Status |
|------|--------|
| Tabla `balance_snapshots` (migración `003`) | NO documentada en design doc. |
| Columna `trades.close_requested` (migración `002`) | NO documentada. |
| Endpoint `POST /api/trades/{id}/close` | Documentado conceptualmente como "Forzar cierre" (§9.1). |

**Severidad**: 🟢 INFO. Evolución natural post-design-doc.

---

### D-016 — Backtester: librería elegida

| Fuente | Lib |
|--------|-----|
| Design doc §5 + §10 fase 6 | "vectorbt". |
| Código `backtesting/requirements.txt` y `runner.py` | **pandas puro** (sin vectorbt). |

**Severidad**: 🟢 INFO. Decisión técnica revertida post-design doc.

---

### D-017 — `recharts` instalada pero sin uso

| Item | Status |
|------|--------|
| `frontend/package.json` declara `recharts` | ✅ |
| Uso en `frontend/src/**` | ❌ |
| Design doc §9.1 menciona "Live price chart" | ✅ documentado, no entregado. |

**Severidad**: 🟢 INFO. Dependencia muerta + feature no entregada.

---

### D-018 — `README` vs código: indicadores

| Fuente | Indicador |
|--------|-----------|
| Design doc §5 | "Technical indicators: pandas-ta". |
| `requirements.txt` y `collectors/indicators.py` | **pandas puro** (sin pandas-ta). |

**Severidad**: 🟢 INFO.

---

### D-020 — `TradeOut` no expone `order_id_open` ni `order_id_close`

| Aspecto | Estado |
|---------|--------|
| Columnas en `Trade` ORM y migración | ✅ existen. |
| `TradeOut` Pydantic (`web/api/trades.py:12-28`) | ❌ no las incluye. |
| Design doc §9.1 `/trades` | No las lista explícitamente, pero implícito. |

**Severidad**: 🟢 INFO. Auditoría desde UI limitada.

---

## 6. Gaps de cobertura por capa

Resultado del cross-validation entre documentación de diseño y código.

| Capa | % docs vs código | Comentario |
|------|-------------------|------------|
| Arquitectura | 98% | Topología 4 contenedores + Postgres; outcome attribution y Telegram agregados post-design. |
| Modelo de datos | 98% | 13 tablas activas incl. `decision_outcomes`, `confluence_*`; migraciones 001–012 aplicadas. |
| Componentes runtime | 98% | Post-mortem pipeline, registry I–Z, UI `/confluence`, CoherenceChecker C7/C8. |
| REST API | 92% | API completa; filtros avanzados de trades/decisions en backlog. |
| WebSocket | 95% | 8 eventos implementados (incl. supervisor_ran, kill_switch_triggered). |
| Frontend páginas | 75% | Chart en Dashboard entregado; filtros avanzados y diff viewer pendientes. |
| Prompts LLM | 100% | Todos los archivos `prompts/*` presentes y coherentes. |

---

## 7. Documentación faltante (categorías sin docs)

| Tópico | Estado | Acción |
|--------|--------|--------|
| Plan de backups Postgres en producción | Mencionado en README, no automatizado. | Definir job de `pg_dump` con retención. |
| Telemetría/observabilidad externa (Prometheus, Sentry) | No aplica para v1. | Roadmap. |
| Onboarding para nuevo developer | Parcialmente cubierto por `CLAUDE.md` y design doc. | Consolidar quickstart. |
| Runbook operativo (qué hacer en cada error) | Parcialmente cubierto en README "Controles de emergencia". | Expandir con escenarios. |

---

## 8. Plan sugerido de remediación

### 8.1 Antes de pasar a LIVE (críticos)

1. **D-001**: computar `daily_pnl_pct` y `total_drawdown_pct` en cada tick del Decisor y pasarlos al `RiskGate.validate()`.
2. **D-002**: invocar `OrderBookCollector.start()` en `main.py` durante el bootstrap.
3. **D-003**: integrar `CircuitBreaker.evaluate(daily_pnl, total_drawdown)` en el loop principal.

### 8.2 Roadmap inmediato (altos)

4. **D-004**: incluir `min_fees_to_tp_ratio` en el `calibration` dict que va al RG.
5. **D-005**: implementar auto-rollback del Supervisor (o documentar explícitamente que queda fuera de scope).
6. **D-006**: crear migración Alembic `005` para índices GIN, índice parcial único de playbook activo, y FKs deferidas.
7. **D-009**: emitir eventos WS `trade_opened`, `trade_closed`, `playbook_updated`, `kill_switch_triggered`.
8. **D-013**: enriquecer `/health` con métricas LLM (latency p50/p95/p99, fallback count) y panel de "Recent errors".

### 8.3 Medio plazo (mejoras UX y test hygiene)

9. **D-007 / D-008**: alinear thresholds del RG con design doc o actualizar la spec en `05-risk-and-safety.md` para reflejar los valores reales (R:R 1.3, SL 0.3–1.5× ATR).
10. **D-011**: cron job para poblar `daily_stats` y migrar `/api/stats/daily` a leer la tabla.
11. **D-014 / D-015**: completar filtros del frontend y diff viewer del playbook.
12. **D-019**: arreglar test de confluencias con fixture realista (incluir `open`, `close`, `ema200_slope_pct`).

---

## 9. Cómo mantener este documento

- **Cada PR** que resuelva una discrepancia debe actualizar su fila en §1.3 con estado `✅ RESUELTA` y referencia al PR.
- **Cada nueva discrepancia** detectada en code review o reverse-engineering futuro debe abrir un ID `D-021`, `D-022`, … con el mismo formato.
- El documento se revisa **semanalmente** durante la fase de paper trading y en cada gate del roadmap hacia LIVE (ver `05-risk-and-safety.md` §10).

---

## 10. Estado final de gaps (2026-05-23)

Todos los gaps críticos y altos (D-001–D-021) están resueltos o documentados como decisiones de diseño. El sistema está operativo para paper trading con las API keys configuradas.

### Pendientes técnicos de baja/media prioridad

#### D-026 — Filtros frontend `/trades` y `/decisions`

**`/trades`** — faltan:

| Filtro | Estado |
|--------|--------|
| Date range picker | ❌ |
| Status (open/closed/cancelled) | ✅ (`?status=`) |
| Result (win/loss/all) | ❌ |
| Close reason | ❌ |
| Sortable por columna | ❌ |
| Export CSV | ❌ |
| Summary footer (P&L agregado) | ❌ |

**`/decisions`** — faltan:

| Filtro | Estado |
|--------|--------|
| Agent (decisor/supervisor) | ✅ (`?agent=`) |
| Action (BUY/SELL/HOLD) | ❌ |
| Confidence range slider | ❌ |
| Executed (yes/no/rejected) | 🟡 parcial (`?executed=`) |
| Date range | ❌ |
| Click row → panel con input/output | ✅ |

**Acción sugerida**: extender query params en `web/api/trades.py` y `web/api/decisions.py`; UI en `frontend/src/pages/Trades.tsx` y `Decisions.tsx`.

#### D-027 — Diff viewer y reset playbook (extiende D-015)

| Feature | Estado |
|---------|--------|
| Renderizado markdown activo | ✅ |
| Historial con badge "active" | ✅ |
| Diff viewer entre 2 versiones | ❌ |
| Edición manual markdown | ✅ |
| Reset to v0 (botón) | ❌ |
| Rollback one-click | ✅ |

**Acción sugerida**: componente diff en `Playbook.tsx`; endpoint opcional `GET /api/playbook/diff?v1=&v2=` o diff client-side.

#### D-028 — Cron job `daily_stats` (extiende D-011)

| Fuente | Comportamiento actual |
|--------|----------------------|
| Tabla `daily_stats` | Existe; **vacía** en operación normal |
| `GET /api/stats/daily` | Agrega on-the-fly sobre `trades` + `decisions` del día UTC |

**Trigger**: monitorear duración de `GET /stats/daily`; implementar job nocturno en engine cuando supere ~500 ms o histórico > 10.000 trades.

#### D-029 — Telemetría del proceso engine

Items aún **fuera** de `/api/health`:

| Métrica | Estado |
|---------|--------|
| Uptime del proceso engine | ❌ |
| Memory RSS | ❌ |
| Conteo de fallback LLM triggers (24 h) | ❌ |

**Acción sugerida**: endpoint interno o escritura periódica a `config`/tabla de métricas desde `main.py` (el engine no expone HTTP).

### Funcionalidades para v2

#### D-005 — Auto-rollback del Supervisor

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Auto-rollback | Si 7 días post-update muestran >2× drawdown vs prior 7 días → revertir + alert | ❌ No implementado |
| Rollback manual | UI botón | ✅ `POST /api/playbook/{v}/activate` |

**Precondición sugerida**: ≥ 4 semanas de histórico con ≥ 5 trades/semana antes de automatizar.

#### D-014 (modal) — Trade → decisión origen

Click en fila de `/trades` debería abrir modal con `decisions` vinculada vía `trades.decision_id`. Datos ya existen en BD; falta UI.

#### D-015 (word-diff) — Diff fino del playbook

El diff actual (cuando exista) será por línea; se pide diff a nivel de palabra para revisar cambios del Supervisor con más granularidad.

#### Backlog adicional (sin ID)

| Item | Notas |
|------|-------|
| Auth / RBAC frontend | Sin login; acceso = quien llegue al puerto 3100 |
| Backtesting con LLM | `backtesting/` es baseline determinístico; no en docker-compose |
| Observabilidad externa | Prometheus, Sentry — roadmap |
| Backup Postgres automatizado | Solo manual vía `pg_dump` en README |

---

## 10.bis Estado final de gaps (2026-05-17, histórico)

Todos los gaps D-001–D-020 han sido resueltos o documentados como decisiones de diseño explícitas.

### Pendientes técnicos de baja prioridad

- **D-011** — Cron job para pre-computar `daily_stats` cuando el histórico supere los 90 días (la query on-the-fly degrada con > ~10.000 trades). Trigger: monitorear duración de `GET /stats/daily` en producción.
- **D-013 (resto)** — Items aún fuera del `/health`: uptime del proceso engine, memory RSS, conteo de fallback LLM triggers. Requieren endpoint de telemetría en el engine.

### Funcionalidades para v2

- **D-005** — Auto-rollback automático del Supervisor (cuando haya ≥ 4 semanas de histórico con ≥ 5 trades/semana).
- **D-014 (modal)** — Click en fila de `/trades` → modal con la decisión que originó el trade.
- **D-015 (word-diff)** — Diff a nivel de palabra dentro de cada línea (actualmente el diff es por línea completa).
