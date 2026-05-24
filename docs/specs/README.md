# Especificaciones — Crypto AI Trading

Carpeta de especificaciones funcionales y técnicas del proyecto **Crypto AI Trading**: bot autónomo de day trading BTC/USDT en Binance Spot impulsado por dos agentes LLM (Decisor + Supervisor) con risk gate determinístico.

## Índice

| Documento | Audiencia | Descripción |
|-----------|-----------|-------------|
| [`01-functional-spec.md`](./01-functional-spec.md) | Product / Trading / Stakeholders | Visión de negocio, usuarios, casos de uso, flujos operativos, reglas de negocio, criterios de aceptación y métricas de éxito. |
| [`02-technical-spec.md`](./02-technical-spec.md) | Tech leads / Devs / SRE | Arquitectura, servicios, modelo de datos, contratos de API, scheduler, agentes LLM, risk gate, despliegue, operaciones y *Code Ownership Map*. |
| [`03-data-model.md`](./03-data-model.md) | Devs / DBAs | Esquema relacional detallado, índices, migraciones Alembic y políticas de retención. |
| [`04-api-contracts.md`](./04-api-contracts.md) | Frontend / Integraciones | Endpoints REST, contratos JSON, WebSocket events y códigos de error. |
| [`05-risk-and-safety.md`](./05-risk-and-safety.md) | Risk / Compliance | Reglas absolutas R0–R11, circuit breakers, kill switch, gates de pasaje paper → LIVE. |
| [`06-patterns.md`](./06-patterns.md) | Devs / Tech leads | Patrones de implementación reutilizables + anti-patrones a evitar. |
| [`07-discrepancies-and-gaps.md`](./07-discrepancies-and-gaps.md) | Tech leads / SRE / Risk | Discrepancias design doc vs código + gaps + **pendientes activos**. |

## Convenciones

- Idioma: **Español (es-AR)**; términos técnicos en inglés (API, REST, OHLCV, ATR, R:R) no se traducen.
- Timestamps: **UTC** (`TIMESTAMPTZ`).
- Montos: tipo `NUMERIC` (precisión exacta), nunca `FLOAT`.
- Persistencia: **Postgres 17** como única fuente de verdad entre servicios.
- Logging: `structlog` con salida JSON.

## Estado del documento

| Campo | Valor |
|-------|-------|
| Versión | 1.5 |
| Fecha de generación | 2026-05-14 |
| Fecha de unificación | 2026-05-15 (incorpora reverse-engineering de `meli/specs/`) |
| Última revisión | 2026-05-23 (outcome attribution contrafactual, migraciones 005–010, R0/R11, health enriquecido, WS completo, Telegram, drawdown/circuit-breaker reset) |
| Alcance | Bot autónomo en paper trading (PAPER_TRADING) sobre Binance Testnet, con roadmap a LIVE (mainnet). |
| Owner | Equipo Crypto AI Trading |

## Cómo navegar la documentación

- **Si vas a entender el negocio o presentar el producto**: empezar por `01-functional-spec.md`.
- **Si vas a desarrollar una feature**: leer `02-technical-spec.md` + `06-patterns.md` (reusar patrones existentes).
- **Si vas a tocar la BD**: `03-data-model.md` antes de cualquier migración.
- **Si vas a integrar frontend o externo**: `04-api-contracts.md`.
- **Si vas a hacer un cambio que afecte riesgo financiero**: `05-risk-and-safety.md` es obligatorio.
- **Si estás haciendo onboarding o auditando estado actual**: `07-discrepancies-and-gaps.md` resume qué se prometió y qué se entregó.
- **Si vas a tomar trabajo del backlog**: ver § Pendientes activos abajo.

## Pendientes activos (2026-05-23)

Trabajo conocido que **no bloquea** paper trading. Detalle completo en [`07-discrepancies-and-gaps.md`](./07-discrepancies-and-gaps.md) §10.

### Frontend / UX

| ID | Prioridad | Item |
|----|-----------|------|
| D-026 | Media | Filtros avanzados en `/trades` (date range, win/loss, close reason, CSV) y `/decisions` (action, confidence range, date range) |
| D-014 | Media | Modal en `/trades`: click en fila → decisión LLM origen |
| D-015 | Media | Diff viewer entre versiones de playbook + word-diff + botón reset a v0 |

### Backend / infra

| ID | Prioridad | Item | Cuándo abordar |
|----|-----------|------|----------------|
| D-028 | Baja | Job cron para pre-computar `daily_stats` | Histórico > 90 días o latencia de `/api/stats/daily` degradada |
| D-029 | Baja | Telemetría engine (uptime, RSS, fallback count) | Requiere endpoint en engine; útil antes de LIVE prolongado |

### v2 (fuera de scope v1)

| ID | Item |
|----|------|
| D-005 | Auto-rollback del Supervisor ante degradación de drawdown |
| — | Auth/RBAC en frontend |
| — | Backtesting con LLM real |
| — | Prometheus/Sentry |
| — | Backup Postgres automatizado con retención |

---

- Diseño original: `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md`
- Decisor LLM-centric: `docs/superpowers/specs/2026-05-17-decisor-llm-centric-design.md`
- Outcome attribution: `docs/superpowers/specs/2026-05-18-supervisor-counterfactual-design.md`
- Convenciones del proyecto: `CLAUDE.md`
- Runbook de despliegue: `README.md` (raíz del repo)
- Migraciones de BD: `trading-engine/alembic/versions/`
