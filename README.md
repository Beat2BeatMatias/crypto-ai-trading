# Crypto AI Trading

AI-driven autonomous day trading bot for BTC/USDT on Binance Spot.
See `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md` for full design.

## Quick start

```bash
cp .env.example .env
# edit .env with your API keys
docker-compose build
docker-compose up -d
```

URLs (after `up -d`):
- Frontend: http://localhost:3100
- Web API: http://localhost:8100
- Postgres: localhost:5532

## Run tests

```bash
docker-compose run --rm trading-engine pytest
docker-compose run --rm web pytest
```

## Apply migrations

```bash
docker-compose run --rm trading-engine alembic upgrade head
```

## Operations

1. Start: `docker-compose up -d`
2. Check logs: `docker-compose logs -f trading-engine`
3. Apply migrations: `docker-compose run --rm trading-engine alembic upgrade head`
4. Kill switch (emergency): use the dashboard at http://localhost:3100/config
5. Backup DB: `docker-compose exec postgres pg_dump -U trader crypto_ai_trading > backup.sql`
