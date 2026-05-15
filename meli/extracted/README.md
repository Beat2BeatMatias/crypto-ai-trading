# Extracted — Index and Metadata

**Reverse-engineering completado**: 2026-05-14
**Comando**: `/meli.reverse-eng` (Meli SDD Kit)
**Modo**: FULL EXTRACTION
**Estrategia**: ASSISTED (design doc 2026-05-02 como hints, código como fuente de verdad)

## Archivos generados

| Archivo | Propósito |
|---------|-----------|
| `functional-spec.md` | Spec funcional con casos de uso, actores, reglas de negocio |
| `technical-spec.md` | Spec técnica con arquitectura, APIs, modelo de datos, code ownership |
| `PATTERNS.md` | 16 patrones reutilizables descubiertos en el código |
| `DOCUMENTATION_GAPS.md` | Cobertura código vs docs por categoría |
| `DISCREPANCIES_REPORT.md` | 20 discrepancias detalladas con severidad |
| `raw/` | Material crudo de extracción (análisis por capa) |
| `raw/existing-specs/` | Copias de specs/plans/README pre-existentes |
| `raw/existing-specs/DETECTION_REPORT.md` | Frameworks detectados |
| `raw/code-analysis/trading-engine-analysis.md` | Análisis exhaustivo del bot autónomo |
| `raw/code-analysis/web-frontend-analysis.md` | Análisis de FastAPI + React |
| `raw/code-analysis/shared-db-backtesting-analysis.md` | Shared, Alembic, backtester |

## Resumen de hallazgos

### 🔴 Bugs CRÍTICOS (blocking para LIVE trading)
1. **D-001**: `RiskGate` recibe `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0` siempre — reglas de daily stop y drawdown total nunca disparan.
2. **D-002**: `OrderBookCollector.start()` no se invoca — decisor opera sin información de microestructura.
3. **D-003**: `CircuitBreaker.evaluate()` no integrado en el loop — segunda línea de defensa ausente.

### 🟠 Issues ALTOS (roadmap inmediato)
- **D-004**: `min_fees_to_tp_ratio` no se pasa del config al `RiskGate`.
- **D-005**: Auto-rollback de playbook no implementado (documentado en design doc).
- **D-006**: Índices GIN y FKs declarados sólo en ORM, no en migraciones Alembic.
- **D-009**: WebSocket emite 3 de 6 eventos prometidos en el design doc.
- **D-013**: Página `/health` carece de métricas de LLM y panel de errores.

### 🟡 Issues MEDIOS
- Diferencias en thresholds del RG vs design doc.
- Overrides determinísticos del Decisor no documentados.
- Tablas `daily_stats` y `balance_snapshots` no se populan automáticamente.
- Filtros faltantes en frontend (`/trades`, `/decisions`, `/playbook`).
- Test `test_signal_buy_requires_min_confluences` parece roto.

### 🟢 Issues INFO
- Evolución natural post-design-doc: `balance_snapshots`, `close_requested`, `confidence_base/adjustment`, `expected_holding_min`.
- Dependencia `recharts` declarada sin uso.
- Backtester usa pandas puro (design doc decía `vectorbt`).

## Verificación de consistency (Fase 6)

| Chequeo | Resultado |
|---------|-----------|
| Cada caso de uso en `functional-spec.md` tiene path de implementación en `technical-spec.md` | ✅ |
| Cada endpoint en `technical-spec.md` trazea a un caso de uso o acción de operador | ✅ |
| Modelos de datos coinciden entre specs | ✅ |
| Glosario consistente (Decisor, Supervisor, RG, R10, etc.) | ✅ |
| Severidad de discrepancias clasificada | ✅ |
| Code Ownership Map cubre todos los componentes principales | ✅ |
| Patrones tienen 2+ evidencias en código | ✅ |
| Forbidden filenames evitados (sin FOCUSED_*, DEEP_DIVE, etc.) | ✅ |

## Próximos pasos

1. **Resolver los 3 críticos** antes de operar en LIVE.
2. **Promover specs** a `meli/specs/` (Fase 7 del `/meli.reverse-eng`).
3. **Empezar features** con `/meli.start` cuando haya un próximo desarrollo.
