# PROJECT.md

Configuración del proyecto para Meli SDD Kit.

## Identificación

- **Nombre**: crypto-ai-trading
- **Tipo**: Bot autónomo de trading de criptomonedas con agentes LLM
- **Stack**: Python (3.12) + FastAPI + PostgreSQL + React (Vite + TypeScript)
- **Tier**: Personal / Producto interno (NO es una app Fury de Mercado Libre)

## Lenguaje de los Specs

```yaml
language:
  specs: es        # Specs en español
  code: en         # Identificadores y comentarios técnicos en inglés
  ui: es-AR        # Textos visibles al usuario en español rioplatense
```

## Servicios y Puertos

| Servicio | Tecnología | Puerto host | Rol |
|----------|------------|-------------|-----|
| `postgres` | Postgres 17-alpine | 5532 → 5432 | Source of truth compartido |
| `trading-engine` | Python (asyncio) | — | Bot autónomo (sin HTTP server) |
| `web` | FastAPI + uvicorn | 8100 → 8000 | REST + WebSocket para el dashboard |
| `frontend` | React + Vite + nginx | 3100 → 80 | Dashboard SPA |

## Convenciones del proyecto

- **Tiempos**: todos en UTC (`TIMESTAMPTZ`).
- **Precios y cantidades**: `NUMERIC`, nunca `FLOAT`.
- **Payloads LLM**: `JSONB` para querying flexible.
- **Logging**: `structlog` con salida JSON.
- **Tests**: `pytest` + `pytest-asyncio` + `freezegun`.
- **Idioma de la UI**: español (es-AR).
- **Modo por defecto**: `PAPER_TRADING` (Binance Spot Testnet).
- **Ciclo a LIVE**: backtesting + 4 semanas de paper trading exitoso.

## Mercado Libre / Fury

Este proyecto **NO** corre en Fury. Las herramientas como FuryMCP, MeliSystemMCP, BigQueue, KVS, Director **no aplican**. El reverse-engineering se basa exclusivamente en análisis de código y documentación existente.

## Estructura de specs

```
meli/
├── PROJECT.md                # este archivo
├── PATTERNS.md               # patrones descubiertos (post reverse-eng)
├── specs/                    # specs globales canónicas
│   ├── functional-spec.md
│   └── technical-spec.md
├── extracted/                # working dir del reverse-eng
└── wip/                      # work-in-progress de features
```
