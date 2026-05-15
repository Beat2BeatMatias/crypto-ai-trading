# Documentation Gaps — Cross-Validation Report

**Phase**: 2 (Basic Cross-Validation)
**Date**: 2026-05-14
**Repository**: crypto-ai-trading

## Source Priority

| Priority | Source |
|----------|--------|
| 1 (truth) | Código real (`trading-engine/`, `web/`, `frontend/`, `shared/`, `backtesting/`) |
| 2 (validated) | Design doc 2026-05-02 |
| 3 (hints) | Plans / README |
| 4 (N/A) | FuryMCP (no aplica) |

## Coverage por capa

| Capa | % docs vs código | Comentario |
|------|-------------------|------------|
| Arquitectura | 95% | Topología 3 contenedores y Postgres como bus coinciden con design doc |
| Modelo de datos | 90% | Gaps: índices GIN no en migración, FK trades.decision_id, balance_snapshots/close_requested no en design doc |
| Componentes runtime | 80% | 3 críticos: OrderBook WS no inicia, RG con inputs 0.0, CircuitBreaker no integrado |
| REST API | 85% | API existe; filtros documentados (date range, result, etc.) faltan |
| WebSocket | 50% | Sólo 3 de 6 eventos del design doc |
| Frontend páginas | 70% | Features avanzadas (diff viewer, charts, export CSV, métricas LLM) no entregadas |
| Prompts LLM | 100% | Todos los archivos `prompts/*` presentes y coherentes |

## Gaps prioritarios

### 🔴 CRÍTICO (blocking para LIVE)
1. `OrderBookCollector.start()` no se invoca en `main.py` → decisor sin microestructura real.
2. `RiskGate` recibe siempre `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0` → reglas RG-daily-stop y RG-drawdown no disparan.
3. `CircuitBreaker.evaluate()` no integrado al loop → daily stop/drawdown del breaker ausentes.

### 🟠 ALTO
4. `MIN_FEES_TO_TP_RATIO` declarado pero no consumido (default hardcoded 3.0).
5. Auto-rollback del Supervisor no implementado (design doc §8.4).
6. Índices GIN (`indicators.data`, `decisions.input/output`) y FK `trades.decision_id` sólo en ORM, no en Alembic.
7. WebSocket: faltan `trade_opened`, `trade_closed`, `playbook_updated`, `kill_switch_triggered`.
8. `/health` carece de métricas LLM (latency, quota, fallbacks) y panel de errores.

### 🟡 MEDIO
9. Tablas `daily_stats` y `balance_snapshots` no se popula por ningún job.
10. Filtros frontend incompletos (`/trades` sin date range, result, CSV; `/decisions` sin slider confidence).
11. Reglas del RG con thresholds diferentes (R:R 1.3 vs design doc 1.5; SL min 0.3× vs 0.5× ATR).
12. Overrides determinísticos del Decisor no documentados en design doc.

### 🟢 INFO
13. `recharts` instalada pero no usada.
14. Backtester con pandas puro (design doc decía `vectorbt`).
15. Indicadores con pandas puro (design doc decía `pandas-ta`).
16. `balance_snapshots` y `close_requested` agregadas post-design-doc.

## Documentación faltante (categorías sin docs)

| Tópico | Estado |
|--------|--------|
| Plan de backups Postgres en producción | mencionado en README, no automatizado |
| Telemetría / observabilidad externa (Prometheus, Sentry) | no aplica para v1 |
| Onboarding para nuevo developer | parcialmente cubierto por CLAUDE.md y design doc |
| Runbook operativo (qué hacer en cada error) | parcialmente cubierto en README "Controles de emergencia" |

Ver `DISCREPANCIES_REPORT.md` para el detalle field-by-field de cada gap.
