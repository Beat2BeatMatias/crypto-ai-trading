# Crypto AI Trading

Bot autónomo de day trading BTC/USDT en Binance Spot, impulsado por dos agentes LLM (Decisor + Supervisor) con risk gate determinístico.

Diseño completo: `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md`

## Quick start con Docker Compose

### Requisitos previos

- [Docker](https://docs.docker.com/get-docker/) >= 24
- [Docker Compose](https://docs.docker.com/compose/install/) >= 2 (incluido en Docker Desktop)

### 1. Configurar variables de entorno

```bash
cp .env.example .env
```

Editar `.env` con las API keys necesarias (ver Paso 1 y 2 del roadmap abajo):

```env
POSTGRES_PASSWORD=tu_password_seguro
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_TESTNET=true
GEMINI_API_KEY=...
GROQ_API_KEY=...
```

### 2. Construir las imágenes

```bash
docker-compose build
```

Esto construye los tres servicios: `trading-engine`, `web` y `frontend`. Postgres usa la imagen oficial y no requiere build.

### 3. Aplicar migraciones de base de datos

Solo necesario la primera vez o después de un `git pull` que incluya nuevas migraciones:

```bash
docker-compose run --rm trading-engine alembic upgrade head
```

### 4. Levantar todos los servicios

```bash
docker-compose up -d
```

Servicios que se inician:

| Servicio | Descripción | Puerto |
|----------|-------------|--------|
| `postgres` | Base de datos compartida | `5532` (host) |
| `trading-engine` | Bot autónomo de trading (sin HTTP) | — |
| `web` | API REST + WebSocket del dashboard | `8100` |
| `frontend` | Dashboard React | `3100` |

### 5. Verificar que todo está corriendo

```bash
docker-compose ps
```

Todos los servicios deben mostrar `Up`. El `trading-engine` tarda ~10 s en conectar a Postgres.

Verificar logs del engine:

```bash
docker-compose logs -f trading-engine
```

Debe aparecer `scheduler.started` y luego eventos `ohlcv.persisted` cada ciclo.

### Acceder a la app

- **Dashboard:** http://localhost:3100
- **Web API:** http://localhost:8100
- **Postgres:** `localhost:5532` (usuario: `trader`, base: `crypto_ai_trading`)

### Parar los servicios

```bash
# Parar sin borrar datos
docker-compose down

# Parar y borrar la base de datos (irreversible)
docker-compose down -v
```

---

## Roadmap hacia LIVE trading

### Estado actual

El engine está **operativo pero pausado**: colecta OHLCV y calcula indicadores en cada ciclo, pero no puede hacer decisiones porque falta configurar las API keys. Los logs muestran `engine.paused` y `API-key format invalid`.

---

### Paso 1 — Configurar API keys de Binance Testnet

El engine necesita claves de la testnet para consultar balance y ejecutar órdenes simuladas.

1. Ir a [testnet.binance.vision](https://testnet.binance.vision) y loguear con GitHub.
2. Generar un par de API Key / Secret Key.
3. En `.env`, setear:

```env
BINANCE_API_KEY=<tu-key>
BINANCE_API_SECRET=<tu-secret>
BINANCE_TESTNET=true
```

4. Reiniciar el engine:

```bash
docker-compose restart trading-engine
```

5. Verificar en los logs que desaparece el error `API-key format invalid` y aparecen eventos `indicators.persisted` seguidos de `decision.*`:

```bash
docker-compose logs -f trading-engine
```

**Gate**: ver al menos una decisión LLM logueada (BUY / SELL / HOLD) sin errores de autenticación.

---

### Paso 2 — Configurar API keys de LLM

El Decisor usa Gemini Flash y el Supervisor usa Gemini Pro, ambos en capa gratuita. Groq actúa como fallback.

| Proveedor | Obtener key | Variable en `.env` |
|-----------|-------------|---------------------|
| Gemini (primario) | [ai.google.dev](https://ai.google.dev) → API keys | `GEMINI_API_KEY` |
| Groq (fallback) | [console.groq.com](https://console.groq.com) → API keys | `GROQ_API_KEY` |

Ambas cuentas tienen tier gratuito permanente. El uso estimado de Gemini Flash es ~216k tokens/día (muy por debajo del millón gratuito/minuto).

Después de setear las keys, reiniciar el engine y confirmar en los logs que aparece `decision.action` con valores `BUY`, `SELL`, o `HOLD` junto con `decision.persisted`.

**Gate**: 48h continuas de decisiones sin errores de LLM, con tasa de parseo JSON > 99%.

---

### Paso 3 — Backtesting (validación baseline sin LLM)

Antes de dejar el paper trading correr 4 semanas, correr el backtester para validar que las reglas del playbook v0 tienen sentido sobre datos históricos.

```bash
cd backtesting
pip install -r requirements.txt

# 30 días rápido
python runner.py --days 30

# 90 días con parámetros ajustados
python runner.py --days 90 --sl-atr-mult 1.2 --rr 2.5
```

El backtester aplica las reglas del playbook v0 de forma determinística (sin llamadas LLM) sobre datos OHLCV reales de Binance.

**Criterios mínimos para continuar:**

| Métrica | Mínimo aceptable |
|---------|-----------------|
| Sharpe ratio (anualizado) | > 1.0 |
| Max drawdown | < 10% |
| Win rate | > 48% |
| Profit factor | > 1.3 |

Si el backtest falla los criterios, ajustar los parámetros de riesgo en `.env` o el playbook v0 en `trading-engine/agents/prompts/playbook_v0.md` y repetir antes de continuar.

---

### Paso 4 — Paper trading en testnet (4 semanas)

Con las API keys configuradas y el backtest aprobado, el engine ya está haciendo paper trading automáticamente en Binance Testnet. Esta fase no requiere intervención: el sistema opera solo.

**Monitoreo diario** (5 minutos/día):

1. Abrir el dashboard en http://localhost:3100
2. Revisar las métricas del día en `/` (P&L, win rate, drawdown)
3. Ver el log de decisiones en `/decisions` para detectar anomalías (sobretrading, HOLDs persistentes, rechazos del Risk Gate)
4. Revisar el playbook actualizado en `/playbook` (el Supervisor corre a las 00:00 UTC)

**Posibles intervenciones:**

- Si el engine se pausa por circuit breaker (`daily_stop_pct` alcanzado): es comportamiento correcto, se resetea a las 00:00 UTC.
- Si el engine para por `max_drawdown_pct`: requiere reset manual desde `/config` → Kill Switch → desactivar.
- Si el Supervisor genera un playbook con bias que empeora métricas: hacer rollback desde `/playbook` → Rollback.

**Criterios de éxito (4 semanas consecutivas):**

| Métrica | Umbral para ir a LIVE |
|---------|----------------------|
| Sharpe ratio (anualizado) | > 1.0 |
| Max drawdown | < 5% |
| Win rate | > 52% |
| Profit factor | > 1.5 |
| Decisiones LLM sin errores | > 99% |
| Ninguna semana con drawdown > 3% | requerido |

Si alguna semana falla un criterio, el contador de 4 semanas se reinicia.

---

### Paso 5 — Crear API keys de Binance Mainnet (preparación)

Hacer esto mientras corre el paper trading para no apurar el proceso cuando llegue el momento.

1. En la cuenta real de Binance, ir a **Gestión de API**.
2. Crear una API key con permisos:
   - ✅ Leer información de cuenta
   - ✅ Spot trading
   - ❌ Margin trading (NO habilitar)
   - ❌ Retiros (NO habilitar nunca)
3. Restringir la key a la IP del servidor donde correrá el bot.
4. Guardar las claves en un lugar seguro (no en el repo).

Capital inicial recomendado: **$200–500 USDT**. No más hasta tener al menos 8 semanas de datos LIVE.

---

### Paso 6 — Switch a LIVE

Una vez que el paper trading aprobó los 4 criterios durante 4 semanas consecutivas:

**1. Actualizar `.env` con las keys de mainnet:**

```env
BINANCE_API_KEY=<key-mainnet>
BINANCE_API_SECRET=<secret-mainnet>
BINANCE_TESTNET=false
```

**2. Reiniciar el engine:**

```bash
docker-compose restart trading-engine
```

**3. Confirmar que el fee fetch funciona** (mainnet sí tiene endpoints sapi, a diferencia del testnet):

```bash
docker-compose logs trading-engine | grep fee
# Debe aparecer "fees.refreshed" en lugar de "fees.refresh_failed"
```

**4. Activar modo LIVE desde el dashboard:**

- Ir a http://localhost:3100/config
- Sección **Timing** → cambiar `mode` de `PAPER_TRADING` a `LIVE`
- El sistema pide escribir literalmente `CONFIRMO TRADING REAL` en el modal de confirmación
- Confirmar

**5. Monitoreo intensivo la primera semana:**

- Revisar el dashboard dos veces por día.
- El Risk Gate limita el riesgo por trade al `max_position_pct` configurado (default 10%).
- Si en cualquier momento algo parece mal: **Kill Switch** en la esquina superior derecha del dashboard o desde `/config`.

---

### Controles de emergencia

| Situación | Acción |
|-----------|--------|
| Quiero parar todo ahora | Dashboard → Kill Switch (rojo, arriba derecha) |
| El engine crasheó | `docker-compose restart trading-engine` |
| Quiero volver a paper trading | `/config` → mode → `PAPER_TRADING` |
| El Supervisor generó un playbook malo | `/playbook` → Rollback a versión anterior |
| Perder acceso al dashboard | `docker-compose exec web python -c "import asyncio; ..."` o directamente en Postgres |

---

### Checklist rápido por fase

```
[ ] Paso 1: Binance Testnet keys en .env → engine sin errores de auth
[ ] Paso 2: Gemini + Groq keys en .env → decisiones LLM logueadas por 48h
[ ] Paso 3: Backtesting 90d aprueba criterios (Sharpe>1, DD<10%, WR>48%)
[ ] Paso 4: Paper trading 4 semanas consecutivas aprueba criterios (Sharpe>1, DD<5%, WR>52%)
[ ] Paso 5: API keys mainnet creadas con permisos mínimos, IP restringida
[ ] Paso 6: .env actualizado, BINANCE_TESTNET=false, LIVE activado desde dashboard
```

---

## Operaciones

```bash
# Levantar
docker-compose up -d

# Logs en vivo
docker-compose logs -f trading-engine

# Aplicar migraciones (primera vez o después de un pull)
docker-compose run --rm trading-engine alembic upgrade head

# Tests
docker-compose run --rm trading-engine pytest
docker-compose run --rm web pytest

# Backup de la DB
docker-compose exec postgres pg_dump -U trader crypto_ai_trading > backup_$(date +%Y%m%d).sql
```
