# Especificaciones — Crypto AI Trading

Carpeta de especificaciones funcionales y técnicas del proyecto **Crypto AI Trading**: bot autónomo de day trading BTC/USDT en Binance Spot impulsado por dos agentes LLM (Decisor + Supervisor) con risk gate determinístico.

## Índice

| Documento | Audiencia | Descripción |
|-----------|-----------|-------------|
| [`01-functional-spec.md`](./01-functional-spec.md) | Product / Trading / Stakeholders | Visión de negocio, usuarios, casos de uso, flujos operativos, reglas de negocio, criterios de aceptación y métricas de éxito. |
| [`02-technical-spec.md`](./02-technical-spec.md) | Tech leads / Devs / SRE | Arquitectura, servicios, modelo de datos, contratos de API, scheduler, agentes LLM, risk gate, despliegue, operaciones y *Code Ownership Map*. |
| [`03-data-model.md`](./03-data-model.md) | Devs / DBAs | Esquema relacional detallado, índices, migraciones Alembic y políticas de retención. |
| [`04-api-contracts.md`](./04-api-contracts.md) | Frontend / Integraciones | Endpoints REST, contratos JSON, WebSocket events y códigos de error. |
| [`05-risk-and-safety.md`](./05-risk-and-safety.md) | Risk / Compliance | Reglas absolutas R1–R10, circuit breakers, kill switch, gates de pasaje paper → LIVE. |
| [`06-patterns.md`](./06-patterns.md) | Devs / Tech leads | 16 patrones de implementación reutilizables descubiertos en el código + anti-patrones a evitar. |
| [`07-discrepancies-and-gaps.md`](./07-discrepancies-and-gaps.md) | Tech leads / SRE / Risk | 20 discrepancias entre design doc y código (clasificadas por severidad) + gaps de cobertura + plan de remediación. |

## Convenciones

- Idioma: **Español (es-AR)**; términos técnicos en inglés (API, REST, OHLCV, ATR, R:R) no se traducen.
- Timestamps: **UTC** (`TIMESTAMPTZ`).
- Montos: tipo `NUMERIC` (precisión exacta), nunca `FLOAT`.
- Persistencia: **Postgres 17** como única fuente de verdad entre servicios.
- Logging: `structlog` con salida JSON.

## Estado del documento

| Campo | Valor |
|-------|-------|
| Versión | 1.2 |
| Fecha de generación | 2026-05-14 |
| Fecha de unificación | 2026-05-15 (incorpora reverse-engineering de `meli/specs/`) |
| Última revisión | 2026-05-17 (agrega §F2.bis y §F5.bis en funcional, §2.6.bis y §2.7 en técnica, §1.bis en riesgo: modelo explícito de autonomía y aprendizaje del Decisor) |
| Alcance | Bot autónomo en paper trading (PAPER_TRADING) sobre Binance Testnet, con roadmap a LIVE (mainnet). |
| Owner | Equipo Crypto AI Trading |

## Cómo navegar la documentación

- **Si vas a entender el negocio o presentar el producto**: empezar por `01-functional-spec.md`.
- **Si vas a desarrollar una feature**: leer `02-technical-spec.md` + `06-patterns.md` (reusar patrones existentes).
- **Si vas a tocar la BD**: `03-data-model.md` antes de cualquier migración.
- **Si vas a integrar frontend o externo**: `04-api-contracts.md`.
- **Si vas a hacer un cambio que afecte riesgo financiero**: `05-risk-and-safety.md` es obligatorio.
- **Si estás haciendo onboarding o auditando estado actual**: `07-discrepancies-and-gaps.md` resume qué se prometió y qué se entregó.

## Referencias rápidas

- Diseño original: `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md`
- Convenciones del proyecto: `CLAUDE.md`
- Runbook de despliegue: `README.md` (raíz del repo)
- Migraciones de BD: `trading-engine/alembic/versions/`
