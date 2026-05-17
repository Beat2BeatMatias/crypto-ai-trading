# Decisor LLM-centric — Outline del plan de implementación

**Fecha:** 2026-05-17
**Estado:** Outline (pendiente de expansión a TDD step-by-step)
**Spec base:** `docs/superpowers/specs/2026-05-17-decisor-llm-centric-design.md`

> **Nota:** este es un outline para validar **estructura, orden y granularidad** antes de invertir en el plan TDD completo. Cada tarea acá listada se expandirá a sus 4–6 pasos TDD (test failing → run fail → impl → run pass → commit) cuando se apruebe.

---

## Estrategia general

- **TDD estricto** por tarea: test failing → impl mínima → test pass → commit.
- **Commits frecuentes** (1 commit por tarea aprobada).
- **Capa por capa, de adentro hacia afuera**: indicadores → context → coherence → decisor → prompts → integración → specs → rollout flag.
- **Sin tocar producción hasta el final**: el feature flag `DECISOR_LLM_CENTRIC` permite mergear sin activar el comportamiento nuevo.
- **Tests deterministas** con `freezegun` y fixtures de OHLCV controlados.

---

## Fases y tareas

### Fase 1 — Indicadores técnicos (capa más profunda, sin dependencias)

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 1.1 | Agregar ADX(14) a `compute_indicators` | `trading-engine/collectors/indicators.py` | `tests/test_indicators_adx.py` |
| 1.2 | Agregar Stochastic(14,3,3) | `indicators.py` | `tests/test_indicators_stochastic.py` |
| 1.3 | Agregar VWAP intradiario (reset diario UTC) | `indicators.py` | `tests/test_indicators_vwap.py` |
| 1.4 | Agregar VWAP bands (1σ, 2σ) | `indicators.py` | `tests/test_indicators_vwap_bands.py` |
| 1.5 | Agregar EMA9 | `indicators.py` | `tests/test_indicators_ema9.py` |
| 1.6 | Agregar wick/body ratios últimas 3 velas | `indicators.py` | `tests/test_indicators_wicks.py` |
| 1.7 | Agregar volume delta aprox (Lee-Ready) | `indicators.py` | `tests/test_indicators_volume_delta.py` |
| 1.8 | Agregar OBV slope 20 | `indicators.py` | `tests/test_indicators_obv.py` |
| 1.9 | Agregar estructura HH/HL últimas 20 | `indicators.py` | `tests/test_indicators_structure.py` |
| 1.10 | Agregar ATR percentile 30d | `indicators.py` | `tests/test_indicators_atr_percentile.py` |
| 1.11 | Agregar pivot points clásicos diarios | módulo nuevo `indicators_pivots.py` o función en `indicators.py` | `tests/test_indicators_pivots.py` |

**Criterio de cierre de fase 1:** `compute_indicators` retorna el dict extendido y todos los tests pasan en aislamiento.

### Fase 2 — Order book enriquecido

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 2.1 | Depth para mover ±0.1/0.25/0.5/1.0% (bid y ask) | `trading-engine/collectors/orderbook_collector.py` | `tests/test_orderbook_depth_levels.py` |
| 2.2 | Cumulative volume profile top-20 | `orderbook_collector.py` | `tests/test_orderbook_cumulative.py` |
| 2.3 | Wall clusters (paredes adyacentes) | `orderbook_collector.py` | `tests/test_orderbook_wall_clusters.py` |
| 2.4 | Mid-price impact estimate para size típico | `orderbook_collector.py` | `tests/test_orderbook_mid_impact.py` |

### Fase 3 — Etiquetado interpretativo

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 3.1 | Módulo `labelers.py` con `rsi_label`, `macd_label`, `trend_label`, `volatility_label` | `trading-engine/agents/labelers.py` (nuevo) | `tests/test_labelers.py` |
| 3.2 | Perfil operativo derivado (SCALPING / HIBRIDO / DAY_TRADING) | `labelers.py` | `tests/test_labelers_profile.py` |

### Fase 4 — ContextBuilder reorganizado (bloques A–K)

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 4.1 | Esqueleto del nuevo `build()` que retorna dict con bloques A–K como claves | `trading-engine/agents/context_builder.py` | `tests/test_context_builder_blocks.py` |
| 4.2 | Bloque A — `OPERATIONAL_PROFILE` | `context_builder.py` | `tests/test_context_block_a.py` |
| 4.3 | Bloque B — `MARKET_SNAPSHOT` | `context_builder.py` | `tests/test_context_block_b.py` |
| 4.4 | Bloque C — `TECHNICAL_TIMEFRAMES` con orden por perfil + etiquetas | `context_builder.py` | `tests/test_context_block_c.py` (3 sub-tests: scalping/híbrido/day) |
| 4.5 | Bloque D — `KEY_LEVELS` (pivots, EMAs, walls, highs/lows, VWAP) | `context_builder.py` | `tests/test_context_block_d.py` |
| 4.6 | Bloque E — `ORDER_BOOK_DEPTH` | `context_builder.py` | `tests/test_context_block_e.py` |
| 4.7 | Bloque F — `CROSS_TF_ALIGNMENT` | `context_builder.py` | `tests/test_context_block_f.py` |
| 4.8 | Bloque G — `RECENT_DECISIONS` (reusa existente, formato nuevo) | `context_builder.py` | `tests/test_context_block_g.py` |
| 4.9 | Bloque H — `PORTFOLIO_STATE` (reusa existente) | `context_builder.py` | `tests/test_context_block_h.py` |
| 4.10 | Bloque I — `RISK_CONFIG` compacto | `context_builder.py` | `tests/test_context_block_i.py` |
| 4.11 | Bloque J — `PLAYBOOK` (igual a hoy) | `context_builder.py` | `tests/test_context_block_j.py` |
| 4.12 | Bloque K — `OPERATIONAL_GUIDELINES` (estático en system prompt) | `decisor_system.txt` | cubierto en fase 6 |
| 4.13 | Tests de integración del `build()` completo | `context_builder.py` | `tests/test_context_builder_integration.py` |

### Fase 5 — Coherence Checker

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 5.1 | Schemas `CoherenceWarning`, severidad enum | `shared/schemas.py` | `tests/test_schemas_coherence.py` |
| 5.2 | Módulo `coherence_checker.py` skeleton + interfaz `evaluate(decision, ctx) -> list[CoherenceWarning]` | `trading-engine/risk/coherence_checker.py` (nuevo) | `tests/test_coherence_skeleton.py` |
| 5.3 | C1: RSI inconsistente con confluencia "A" | `coherence_checker.py` | `tests/test_coherence_c1.py` |
| 5.4 | C2: MACD inconsistente con confluencia "B" | `coherence_checker.py` | `tests/test_coherence_c2.py` |
| 5.5 | C3: régimen inconsistente con ADX/EMAs | `coherence_checker.py` | `tests/test_coherence_c3.py` |
| 5.6 | C4: confianza alta sin confluencias suficientes | `coherence_checker.py` | `tests/test_coherence_c4.py` |
| 5.7 | C5: BUY con confianza baja sin tag explicativo | `coherence_checker.py` | `tests/test_coherence_c5.py` |
| 5.8 | C6: holding fuera de rango del perfil | `coherence_checker.py` | `tests/test_coherence_c6.py` |
| 5.9 | Modo strict: C1/C2/C3 pasan a rechazo (`coherence_strict_mode`) | `coherence_checker.py` | `tests/test_coherence_strict_mode.py` |

### Fase 6 — Decisor sin overrides + integración Coherence

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 6.1 | Schema `DecisorOutput.position_size_pct: ge=0, le=1` (sin cap dinámico) + `coherence_warnings: list = []` | `shared/schemas.py` | `tests/test_schemas_decisor.py` |
| 6.2 | Eliminar `_apply_deterministic_overrides` + constantes asociadas | `trading-engine/agents/decisor.py` | `tests/test_decisor_no_overrides.py` |
| 6.3 | Integrar `CoherenceChecker.evaluate()` en `Decisor.decide()` post-LLM | `decisor.py` | `tests/test_decisor_coherence_integration.py` |
| 6.4 | Persistir `coherence_warnings` en `decisions.output` | `decisor.py` | `tests/test_decisor_persist_warnings.py` |
| 6.5 | Log nuevo `decisor.llm_decision_accepted` con metadata | `decisor.py` | cubierto por test de log |
| 6.6 | Test: BUY en TRENDING_DOWN persiste sin modificación | — | `tests/test_decisor_trending_down_passes.py` |
| 6.7 | Test: BUY con confidence=0.55 persiste sin modificación | — | `tests/test_decisor_low_confidence_passes.py` |

### Fase 7 — Prompts del Decisor

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 7.1 | Reescribir `decisor_system.txt` con marcadores de bloques A–K | `trading-engine/agents/prompts/decisor_system.txt` | `tests/test_prompt_system_blocks.py` |
| 7.2 | Reescribir "JERARQUIA DE DECISION" según §3.2.3 del spec | `decisor_system.txt` | `tests/test_prompt_jerarquia.py` |
| 7.3 | Agregar sección "GUIA DE SIZING" según §3.2.3 | `decisor_system.txt` | `tests/test_prompt_guia_sizing.py` |
| 7.4 | Reformular sección "REGIMEN DE MERCADO" (TRENDING_DOWN desincentiva, no bloquea) | `decisor_system.txt` | `tests/test_prompt_regimen.py` |
| 7.5 | Reescribir `decisor_user.txt` para inyectar bloques A–K | `trading-engine/agents/prompts/decisor_user.txt` | `tests/test_prompt_user_blocks.py` |

### Fase 8 — Risk Gate (reframing)

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 8.1 | Agregar `rule_id` ("R1"..."R10") al `RiskVerdict` y formato de `reason` | `trading-engine/risk/risk_gate.py` + `shared/schemas.py` | `tests/test_risk_gate_rule_ids.py` |
| 8.2 | Log estructurado `risk_gate.rejected` con `rule_id`, `category` | `risk_gate.py` | `tests/test_risk_gate_logs.py` |
| 8.3 | Test: BUY con `position_size_pct > max_position_pct` → rechazo R1 con `rule_id` | — | `tests/test_risk_gate_r1_size_exceed.py` |

### Fase 9 — Métricas y observabilidad

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 9.1 | Endpoint `/api/decisions/stats?window=24h` extendido con rechazos por regla y warnings por regla | `web/api/decisions.py` + posible `web/api/stats.py` | `tests/web/test_decisions_stats.py` |
| 9.2 | `/api/health` con `risk_gate.rejection_rate_24h` y `coherence.warning_rate_24h` por regla | `web/api/health.py` | `tests/web/test_health_metrics.py` |
| 9.3 | Histograma `decisor.size_chosen_distribution_24h` y `confidence_distribution_24h` | `web/api/stats.py` | `tests/web/test_stats_histograms.py` |

### Fase 10 — Config nueva + feature flag

| # | Tarea | Archivos | Test |
|---|-------|----------|------|
| 10.1 | `ConfigKey.MIN_POSITION_SIZE` (default `0.005`) | `shared/config_store.py` | `tests/test_config_store_new_keys.py` |
| 10.2 | `ConfigKey.COHERENCE_STRICT_MODE` (default `false`) | `config_store.py` | mismo test |
| 10.3 | `ConfigKey.DECISOR_LLM_CENTRIC` (default según modo) | `config_store.py` | mismo test |
| 10.4 | Lectura del flag en el engine loop y ramificación del comportamiento (legacy si `false`) | `trading-engine/main.py` o `engine.py` | `tests/test_engine_feature_flag.py` |

### Fase 11 — Specs

| # | Tarea | Archivos |
|---|-------|----------|
| 11.1 | Actualizar `docs/specs/01-functional-spec.md` §F1 (indicadores), §F2, §F2.bis, §6.3, §6.5, §8 (AC-02, AC-11) | `docs/specs/01-functional-spec.md` |
| 11.2 | Actualizar `docs/specs/02-technical-spec.md` §2.6 + nueva §2.x (bloques) + §2.y (coherence) | `docs/specs/02-technical-spec.md` |
| 11.3 | Actualizar `docs/specs/05-risk-and-safety.md` con tabla C1–C6 y reframing del Risk Gate | `docs/specs/05-risk-and-safety.md` |

### Fase 12 — Validación end-to-end

| # | Tarea | Archivos |
|---|-------|----------|
| 12.1 | Test E2E con LLM mockeado: ciclo completo desde collectors → context → decisor → coherence → risk gate → executor (mock) | `tests/e2e/test_decisor_cycle_full.py` |
| 12.2 | Test E2E: BUY en TRENDING_DOWN aprueba R1–R10 y se ejecuta (mock) | `tests/e2e/test_buy_against_regime.py` |
| 12.3 | Test E2E: BUY con sizing máximo rechazado por R1 con `rule_id` | `tests/e2e/test_buy_size_rejected.py` |
| 12.4 | Test E2E con `coherence_strict_mode=true`: warning C1 bloquea ejecución | `tests/e2e/test_strict_mode_blocks.py` |
| 12.5 | Smoke test en `docker-compose up` con engine en modo paper trading 30 min, leer logs y verificar no errores | manual + script `scripts/smoke_test.sh` |

### Fase 13 — Rollout

| # | Tarea | Descripción |
|---|-------|-------------|
| 13.1 | Merge a `master` con `DECISOR_LLM_CENTRIC=false` por default en LIVE (sin cambio funcional para LIVE) | — |
| 13.2 | Activar `DECISOR_LLM_CENTRIC=true` en paper trading. Monitorear 1 semana. | — |
| 13.3 | Métricas semana 1: `risk_gate.rejection_rate ≤ 15%`, `coherence.warning_rate ≤ 25%/regla`, WR/PF/Sharpe no peores que baseline | — |
| 13.4 | Si métricas OK → activar en LIVE. Sino, iterar prompt o activar `coherence_strict_mode`. | — |
| 13.5 | A las 4 semanas LIVE sin incidentes, eliminar el feature flag y el código legacy | — |

---

## Resumen numérico

- **Tareas totales:** ~58 tareas TDD.
- **Archivos nuevos:** ~5 (`labelers.py`, `coherence_checker.py`, `indicators_pivots.py` si aplica, módulos de tests).
- **Archivos modificados:** ~12.
- **Tests nuevos:** ~40 unitarios + 4 E2E.
- **Estimación de esfuerzo:** 3–5 días de implementación + 1 semana de validación en paper.

---

## Orden de commits sugerido (para PR review-friendly)

1. **PR 1 — Indicadores y order book (fases 1+2)**: pura extensión de collectors. Sin impacto en decisor. 15 tareas, 15 commits.
2. **PR 2 — Labelers + ContextBuilder reorganizado (fases 3+4)**: cambio de forma del `ctx`, sin tocar decisor. Decisor todavía consume claves planas via compat shim temporal. 15 tareas.
3. **PR 3 — Coherence Checker + Decisor sin overrides + Prompts (fases 5+6+7+8)**: el cambio de comportamiento. Detrás del feature flag `DECISOR_LLM_CENTRIC`. ~22 tareas.
4. **PR 4 — Métricas + Config + Specs (fases 9+10+11)**: observabilidad y docs. ~6 tareas.
5. **PR 5 — E2E + Rollout (fases 12+13)**: validación final y plan de rollout documentado. ~5 tareas.

---

## Próximo paso

Si aprobás este outline:
1. Lo expando a TDD detallado (cada tarea con `Step 1: write failing test` + código exacto, `Step 2: run + expected output`, `Step 3: impl mínima` + código exacto, `Step 4: run + expected output`, `Step 5: commit`).
2. Empezamos con la Fase 1 — Tarea 1.1 (ADX).

Si no, indicame qué ajustar (granularidad, orden, PRs).
