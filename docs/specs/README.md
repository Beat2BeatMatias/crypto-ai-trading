# Especificaciones — Crypto AI Trading

Carpeta de especificaciones funcionales y técnicas del proyecto **Crypto AI Trading**: bot autónomo de day trading BTC/USDT en Binance Spot impulsado por dos agentes LLM (Decisor + Supervisor) con risk gate determinístico.

## Índice

| Documento | Audiencia | Descripción |
|-----------|-----------|-------------|
| [`01-functional-spec.md`](./01-functional-spec.md) | Product / Trading / Stakeholders | Visión de negocio, usuarios, casos de uso, flujos operativos, reglas de negocio, criterios de aceptación y métricas de éxito. |
| [`02-technical-spec.md`](./02-technical-spec.md) | Tech leads / Devs / SRE | Arquitectura, servicios, modelo de datos, contratos de API, scheduler, agentes LLM, risk gate, despliegue y operaciones. |
| [`03-data-model.md`](./03-data-model.md) | Devs / DBAs | Esquema relacional detallado, índices, migraciones Alembic y políticas de retención. |
| [`04-api-contracts.md`](./04-api-contracts.md) | Frontend / Integraciones | Endpoints REST, contratos JSON, WebSocket events y códigos de error. |
| [`05-risk-and-safety.md`](./05-risk-and-safety.md) | Risk / Compliance | Reglas absolutas R1–R10, circuit breakers, kill switch, gates de pasaje paper → LIVE. |

## Convenciones

- Idioma: **Español (es-AR)**; términos técnicos en inglés (API, REST, OHLCV, ATR, R:R) no se traducen.
- Timestamps: **UTC** (`TIMESTAMPTZ`).
- Montos: tipo `NUMERIC` (precisión exacta), nunca `FLOAT`.
- Persistencia: **Postgres 17** como única fuente de verdad entre servicios.
- Logging: `structlog` con salida JSON.

## Estado del documento

| Campo | Valor |
|-------|-------|
| Versión | 1.0 |
| Fecha de generación | 2026-05-14 |
| Alcance | Bot autónomo en paper trading (PAPER_TRADING) sobre Binance Testnet, con roadmap a LIVE (mainnet). |
| Owner | Equipo Crypto AI Trading |

## Referencias rápidas

- Diseño original: `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md`
- Convenciones del proyecto: `CLAUDE.md`
- Runbook de despliegue: `README.md` (raíz del repo)
- Migraciones de BD: `trading-engine/alembic/versions/`
