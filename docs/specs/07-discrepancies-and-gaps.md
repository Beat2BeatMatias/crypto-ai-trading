# Discrepancias y Gaps de Documentación — Crypto AI Trading

> Audiencia: Tech leads / SRE / Risk.
> Versión: 1.0 — 2026-05-14.
> Base de comparación: design doc 2026-05-02 vs. código en HEAD (2026-05-14).

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
| 🟡 MEDIO | 7 | D-007, D-008, D-010, D-011, D-014, D-015, D-019 |
| 🟢 INFO | 5 | D-012, D-016, D-017, D-018, D-020 |
| **Total** | **20** | |

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

| Fuente | Lógica |
|--------|--------|
| Design doc §7.1 | Decisor produce JSON y el RG valida. |
| Código `decisor.py:127-160` | Decisor aplica **overrides** ANTES de persistir y ANTES del RG: BUY en `TRENDING_DOWN` → HOLD; confidence < 0.60 → HOLD; tamaño según confianza (≥0.70 → max_position_pct, sino 0.03, piso 0.01). |

**Severidad**: 🟡 MEDIO. Los overrides protegen pero no están en el design doc original — quien lo lee esperaría que esa lógica viva en el RG, no en el Decisor.

> Documentado formalmente en `05-risk-and-safety.md` §3.

---

### D-010 — `DecisorOutput`: campos del JSON

| Campo | Design doc §7.1 (ejemplo) | Código `shared/schemas.py:20-68` |
|-------|---------------------------|-----------------------------------|
| `regime` | ✅ | ✅ idéntico. |
| `confluences` | ✅ list[str] | ✅ list[str] max_length=10. |
| `action` | ✅ | ✅ |
| `confidence` | ✅ float 0-1 | ✅ pero **derivado** de `confidence_base + confidence_adjustment`. |
| `confidence_base` | ❌ no en design doc | ✅ float [0,1] default 0.0. |
| `confidence_adjustment` | ❌ no en design doc | ✅ float [-0.10, 0.10] default 0.0. |
| `stop_loss` | ✅ float \| null | ✅ |
| `take_profit` | ✅ float \| null | ✅ |
| `position_size_pct` | ✅ 0.01-0.10 | ✅ pero rango **[0, 0.25]** más amplio. |
| `expected_holding_min` | ❌ no en design doc | ✅ int >= 1 default 1. |
| `reasoning` | ✅ max 240 chars | ✅ pero **max 800 chars** (4× más). |

**Severidad**: 🟡 MEDIO. La spec del JSON evolucionó. El refactor "Decisor v2" justifica `confidence_base`/`confidence_adjustment` y `expected_holding_min`.

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
| Agent (decisor/supervisor) | ✅ (`?agent=`). |
| Action (BUY/SELL/HOLD) | ❌ |
| Confidence range slider | ❌ |
| Executed (yes/no/rejected) | 🟡 parcial (`?executed=`). |
| Date range | ❌ |
| Click row → side panel con input/output | ✅ |

**Severidad**: 🟡 MEDIO. Funcionalidad básica OK, UX power-user incompleta.

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
| Arquitectura | 95% | Topología 3 contenedores y Postgres como bus coinciden con design doc. |
| Modelo de datos | 90% | Gaps: índices GIN no en migración, FK `trades.decision_id`, `balance_snapshots` y `close_requested` no en design doc. |
| Componentes runtime | 80% | 3 críticos: OrderBook WS no inicia, RG con inputs 0.0, CircuitBreaker no integrado. |
| REST API | 85% | API existe; filtros documentados (date range, result, CSV) faltan. |
| WebSocket | 50% | Sólo 3 de 7 eventos del design doc. |
| Frontend páginas | 70% | Features avanzadas (diff viewer, charts, export CSV, métricas LLM) no entregadas. |
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

- **Cada PR** que resuelva una discrepancia debe marcarla como `RESUELTA: <PR #>` y mover la fila a una sección "Histórico de resoluciones" (a crear cuando aparezca la primera).
- **Cada nueva discrepancia** detectada en code review o reverse-engineering futuro debe abrir un ID `D-021`, `D-022`, … con el mismo formato.
- El documento se revisa **semanalmente** durante la fase de paper trading y en cada gate del roadmap hacia LIVE (ver `05-risk-and-safety.md` §10).
