# Discrepancies Report — Deep Cross-Validation

**Phase**: 3 (Deep / Field-by-Field)
**Date**: 2026-05-14
**Comparison**: Design Doc 2026-05-02 vs. código actual (commit a 2026-05-14)

> **Regla de oro**: cuando un campo difiere entre el design doc y el código, **el código manda**. Los conflictos se documentan acá para que el equipo decida si actualizar el código a la spec o la spec al código.

## Severidad

| Nivel | Significado |
|-------|-------------|
| 🔴 CRÍTICO | Diferencia introduce bug funcional o riesgo financiero |
| 🟠 ALTO | Feature documentada no entregada o entregada parcialmente |
| 🟡 MEDIO | Discrepancia menor, no afecta operación pero confunde |
| 🟢 INFO | Diferencia esperada (decisión post-design doc, evolución natural) |

---

## D-001 — Risk Gate: reglas de daily stop y total drawdown nunca disparan

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Regla 8 design doc §8.1 | "Daily stop: if `daily_pnl_pct ≤ daily_stop_pct`, force HOLD" | Misma intención (`risk_gate.py:60-61`) |
| Regla 9 design doc §8.1 | "Total drawdown: if `total_drawdown ≤ max_drawdown_pct`, force kill switch" | Misma intención (`risk_gate.py:35-37`) |
| **Valores reales en runtime** | Computar PnL diario y drawdown total y pasarlos al RG | `main.py:213-216` pasa **`daily_pnl_pct=0.0`** y **`total_drawdown_pct=0.0`** siempre |

**Severidad**: 🔴 CRÍTICO. Las reglas existen en `validate()` pero nunca se gatillan porque los inputs son cero.

**Acción sugerida (no aplicada por reverse-eng)**: computar `daily_pnl_pct` desde `trades` cerrados del día UTC y `total_drawdown_pct` desde balance vs. high-water mark; pasarlos al `RiskGate.validate()` antes del tick decisor.

---

## D-002 — OrderBookCollector nunca se inicia

| Aspecto | Design doc | Código |
|---------|------------|--------|
| §4.2 | "OrderBookColl. • Binance WS • in-memory top 10 levels" | Clase implementada (`orderbook_collector.py:1-103`) |
| Inicialización | Se asume que el `start()` corre al boot | `main.py:76` crea instancia pero **no llama `start()`** |
| Consecuencia | Decisor recibe orderbook real | Decisor recibe `None` → spreads/imbalance en 0 |

**Severidad**: 🔴 CRÍTICO. El Decisor decide sin información de microestructura aunque el prompt sí pide orderbook.

---

## D-003 — CircuitBreaker daily/drawdown no integrado

| Aspecto | Design doc | Código |
|---------|------------|--------|
| §8.3 | "Daily stop: cuando dispara, `kill_switch_daily=true` hasta 00:00 UTC. Total drawdown: dispara `kill_switch` permanente con reset manual." | `CircuitBreaker.evaluate()` implementa la lógica (`circuit_breaker.py:23-30`) pero **no se llama desde `main.py`** |
| Operativo en runtime | sí | no — sólo cuenta fallos LLM/exchange |

**Severidad**: 🔴 CRÍTICO. Los circuit breakers documentados como "última línea de defensa financiera" no se aplican.

---

## D-004 — `MIN_FEES_TO_TP_RATIO` declarado pero no consumido

| Aspecto | Design doc | Código |
|---------|------------|--------|
| Config key | No documentado explícitamente en §6.3, pero presente en migración `004` | `ConfigKey.MIN_FEES_TO_TP_RATIO` en `config_store.py` |
| Consumo | — | `calibration` dict en `main.py:108-134` **NO incluye** `min_fees_to_tp_ratio` |
| Default fallback | — | `risk_gate.py:30` usa `min_fees_to_tp_ratio: float = 3.0` siempre |

**Severidad**: 🟠 ALTO. La regla R10 que exige `move_pct >= N × roundtrip_fee_pct` ignora el valor configurado por usuario.

---

## D-005 — Supervisor sin auto-rollback

| Aspecto | Design doc | Código |
|---------|------------|--------|
| §8.4 | "Automatic rollback: if 7 days post-update show >2× drawdown vs prior 7 days, revert to previous version + alert" | NO implementado |
| Manual rollback | UI tiene botón | `POST /api/playbook/{v}/activate` existe (`web/api/playbook.py:42`) |

**Severidad**: 🟠 ALTO. Característica de seguridad documentada que no existe en código.

---

## D-006 — Índices GIN y FK declarados sólo en ORM

Mismatch entre `shared/db/models.py` (ORM) y `trading-engine/alembic/versions/001_initial_schema.py` (DDL real).

| Objeto | Design doc § 6.1 | ORM | Migración |
|--------|--------------------|-----|-----------|
| `idx_indicators_data` GIN | ✅ | ✅ | ❌ |
| `idx_decisions_output` GIN | ✅ | ✅ | ❌ |
| `idx_decisions_input` GIN | ✅ | ✅ | ❌ |
| `idx_playbook_active` unique partial WHERE active=true | ✅ | ✅ | ❌ |
| FK `decisions.trade_id → trades.id` | ✅ ALTER TABLE | ✅ use_alter | ❌ (sólo columna sin FK) |
| FK `trades.decision_id → decisions.id` | ✅ | ✅ use_alter | ❌ |

**Severidad**: 🟠 ALTO. En la BD real no existen esos índices ni FKs. Performance y consistencia comprometidas.

**Acción sugerida**: crear migración Alembic que agregue índices GIN, índice parcial único, y FKs `use_alter`.

---

## D-007 — RiskGate: cantidad y nombre de reglas

| Fuente | Cantidad reglas | Nombres |
|--------|-----------------|---------|
| Design doc §8.1 | **10 reglas** numeradas (1: schema, 2: action validity, 3: position size, 4: concurrency, 5: slippage, 6: R:R 1.5:1, 7: SL distance 0.5× ATR, 8: daily stop, 9: total dd, 10: kill switch) | Numéricas |
| Código `risk_gate.py:validate()` | **14 chequeos** | Ninguno tiene nombre formal salvo "R10:" en el mensaje de rechazo |

**Diffs específicos:**

| Aspecto | Design doc | Código |
|---------|------------|--------|
| R:R mínimo | 1.5:1 | **1.3** (`risk_gate.py:11`) |
| SL distance mínima | ≥ 0.5 × ATR(1h) | **0.3 × ATR** (`sl_atr_multiplier=0.3`) |
| SL distance máxima | no documentada | **1.5 × ATR** (`sl_atr_max_multiplier=1.5`) |
| Slippage check | Regla 5 documentada | **NO implementada** en `validate()` aunque `MAX_SLIPPAGE_PCT` existe en config |
| Schema validation Pydantic | Regla 1 dentro del RG | Está en el **Decisor** (`decisor.py:89-98`), no en RG |
| Fee-aware TP (R10) | NO documentada | **Implementada** en código (`risk_gate.py:77-85`) |

**Severidad**: 🟡 MEDIO. Las reglas son sustancialmente las mismas pero los thresholds y la asignación de responsabilidades difieren del design doc.

---

## D-008 — Overrides determinísticos pre-RiskGate en Decisor

| Fuente | Lógica |
|--------|--------|
| Design doc §7.1 | Decisor produce JSON y el RG valida |
| Código `decisor.py:127-160` | Decisor aplica **overrides** ANTES de persistir y ANTES del RG: BUY en TRENDING_DOWN → HOLD; confidence < 0.60 → HOLD; tamaño según confianza (≥0.70 → max_position_pct, sino 0.03, piso 0.01) |

**Severidad**: 🟡 MEDIO. Los overrides protegen pero no están documentados — quien lee el design doc esperaría que esa lógica viva en el RG, no en el Decisor.

---

## D-009 — WebSocket: cantidad y nombre de eventos

| Evento design doc §9.2 | Evento código `web/ws/feeds.py` | Frecuencia |
|------------------------|----------------------------------|------------|
| `price_update` | `ticker` | ~5s ✅ (renombrado) |
| `position_update` | `positions` | ~2s ✅ (renombrado, más frecuente que en spec) |
| `decision_emitted` | `decision` | ~2s ✅ (renombrado) |
| `trade_opened` | ❌ no implementado | — |
| `trade_closed` | ❌ no implementado | — |
| `playbook_updated` | ❌ no implementado | — |
| `kill_switch_triggered` | ❌ no implementado | — |

**Severidad**: 🟠 ALTO. La UI no recibe notificación push cuando se abre/cierra un trade o cambia el playbook — debe polear.

---

## D-010 — Campos del JSON del Decisor: `DecisorOutput`

| Campo | Design doc §7.1 (ejemplo) | Código `shared/schemas.py:20-68` |
|-------|----------------------------|-----------------------------------|
| `regime` | ✅ | ✅ idéntico |
| `confluences` | ✅ list[str] | ✅ list[str] max_length=10 |
| `action` | ✅ | ✅ |
| `confidence` | ✅ float 0-1 | ✅ pero **derivado** de `confidence_base + confidence_adjustment` |
| `confidence_base` | ❌ no en design doc | ✅ float [0,1] = 0.0 |
| `confidence_adjustment` | ❌ no en design doc | ✅ float [-0.10, 0.10] = 0.0 |
| `stop_loss` | ✅ float \| null | ✅ |
| `take_profit` | ✅ float \| null | ✅ |
| `position_size_pct` | ✅ 0.01-0.10 | ✅ pero rango **[0, 0.25]** más amplio |
| `expected_holding_min` | ❌ no en design doc | ✅ int >= 1 = 1 |
| `reasoning` | ✅ max 240 chars | ✅ pero **max 800 chars** (4× más) |

**Severidad**: 🟡 MEDIO. La spec del JSON evolucionó. El plan `2026-05-12-decisor-v2-refactor.md` posiblemente justifica `confidence_base`/`confidence_adjustment` y `expected_holding_min`.

---

## D-011 — Daily stats: pre-computado vs on-the-fly

| Fuente | Comportamiento |
|--------|----------------|
| Design doc §6.1 | Tabla `daily_stats` "Pre-computed daily stats" |
| Código | `GET /api/stats/daily` (`web/api/stats.py:37`) **agrega en Python** sobre filas del día UTC; **ningún job escribe `daily_stats`** |

**Severidad**: 🟡 MEDIO. La tabla existe vacía. Performance OK con volúmenes actuales; degradará con histórico grande.

---

## D-012 — `balance_snapshots` y `close_requested` no en design doc

| Item | Status |
|------|--------|
| Tabla `balance_snapshots` (migración `003`) | NO documentada en design doc |
| Columna `trades.close_requested` (migración `002`) | NO documentada en design doc |
| Endpoint `POST /api/trades/{id}/close` que la usa | Documentado conceptualmente como "Forzar cierre" (§9.1) |

**Severidad**: 🟢 INFO. Evolución natural post-design-doc.

---

## D-013 — Frontend `/health`: cobertura

| Métrica design doc §9.1 | Implementado |
|--------------------------|--------------|
| Engine card: heartbeat ts | ✅ (vía edad última decisión) |
| Engine card: uptime | ❌ |
| Engine card: memory usage | ❌ |
| Engine card: # errors 24h | ❌ |
| Engine card: recent error log | ❌ |
| Postgres card: connection | ✅ |
| Postgres card: # rows por tabla | ❌ |
| Postgres card: DB size | ❌ |
| Binance card: REST | ✅ (vía edad ohlcv) |
| Binance card: WebSocket | ❌ |
| Binance card: rate limit usage | ❌ |
| LLM card por provider: status | ❌ |
| LLM card: latency p50/p95/p99 | ❌ |
| LLM card: quota remaining | ❌ |
| LLM card: # fallback triggers | ❌ |
| Recent errors panel | ❌ |

**Severidad**: 🟠 ALTO. Operación a ciegas en producción cuando algo falla.

---

## D-014 — Frontend `/trades` y `/decisions`: filtros faltantes

### `/trades` design doc §9.1

| Filtro design doc | Implementado |
|-------------------|--------------|
| Date range picker | ❌ |
| Status (open/closed/cancelled) | ✅ (query `?status=`) |
| Result (win/loss/all) | ❌ |
| Close reason | ❌ |
| Sortable por cualquier columna | ❌ |
| Click row → modal con decisión completa | ❌ |
| Export CSV | ❌ |
| Summary footer agregados | ❌ |

### `/decisions` design doc §9.1

| Filtro design doc | Implementado |
|-------------------|--------------|
| Agent (decisor/supervisor) | ✅ (query `?agent=`) |
| Action (BUY/SELL/HOLD) | ❌ |
| Confidence range slider | ❌ |
| Executed (yes/no/rejected) | 🟡 parcial (`?executed=`) |
| Date range | ❌ |
| Click row → side panel con input/output | ✅ |

**Severidad**: 🟡 MEDIO. Funcionalidad básica OK, UX power-user incompleta.

---

## D-015 — Frontend `/playbook`: diff viewer y reset

| Feature design doc | Implementado |
|---------------------|--------------|
| Active version renderizado markdown | ✅ |
| Version history sidebar con badge "active" | ✅ |
| **Diff viewer** entre 2 versiones | ❌ |
| Manual edit (markdown editor) | ✅ (`PATCH /api/playbook/{v}/content`) |
| **Reset to v0** botón | ❌ |
| Rollback one-click | ✅ (`POST /activate`) |

**Severidad**: 🟡 MEDIO.

---

## D-016 — Backtester: librería elegida

| Fuente | Lib |
|--------|-----|
| Design doc §5 + §10 fase 6 | "vectorbt" |
| Código `backtesting/requirements.txt` y `runner.py` | **pandas puro** (sin vectorbt instalado) |

**Severidad**: 🟢 INFO. Decisión técnica revertida post-design doc.

---

## D-017 — `recharts` instalada pero sin uso

| Item | Status |
|------|--------|
| `frontend/package.json` declara `recharts` | ✅ |
| Uso en `frontend/src/**` | ❌ ningún match `grep recharts` |
| Design doc §9.1 menciona "Live price chart (BTC/USDT, last 24h, 5-min candles)" | ✅ |

**Severidad**: 🟢 INFO. Dependencia muerta + feature no entregada.

---

## D-018 — README vs código (libs de indicadores)

| Fuente | Indicador |
|--------|-----------|
| `README.md:153` | menciona el stack a alto nivel |
| Design doc §5 | "Technical indicators: pandas-ta" |
| `requirements.txt` y `collectors/indicators.py` | **pandas puro** (sin pandas-ta) |

**Severidad**: 🟢 INFO.

---

## D-019 — Test `test_signal_buy_requires_min_confluences` parece roto

| Aspecto | Detalle |
|---------|---------|
| Archivo | `backtesting/tests/test_runner.py` |
| Intención | Confirmar que `signal_buy` falla con <3 confluencias |
| Realidad | `pd.Series` mockeada **no incluye** `open`, `close`, `ema200_slope_pct` que `signal_buy` lee al inicio → `KeyError` antes de validar confluencias |

**Severidad**: 🟡 MEDIO. Test no valida lo que dice validar.

---

## D-020 — `TradeOut` no expone `order_id_open` ni `order_id_close`

| Aspecto | Estado |
|---------|--------|
| Columnas en `Trade` ORM y migración | ✅ existen |
| `TradeOut` Pydantic (`web/api/trades.py:12-28`) | ❌ no las incluye |
| Design doc §9.1 `/trades` "Table columns" | no lista order_ids explícitamente, pero implícito |

**Severidad**: 🟢 INFO. Auditoría desde UI limitada.

---

## Resumen ejecutivo

| Severidad | Cantidad |
|-----------|----------|
| 🔴 CRÍTICO | 3 (D-001, D-002, D-003) |
| 🟠 ALTO | 5 (D-004, D-005, D-006, D-009, D-013) |
| 🟡 MEDIO | 7 (D-007, D-008, D-010, D-011, D-014, D-015, D-019) |
| 🟢 INFO | 5 (D-012, D-016, D-017, D-018, D-020) |

**Recomendación**: tratar los 3 críticos como bugs blocking antes de pasar a LIVE. Los 5 altos como roadmap inmediato.
