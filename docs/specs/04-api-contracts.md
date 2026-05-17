# Contratos de API — Crypto AI Trading

> Audiencia: Frontend / Integraciones.
> Versión: 1.0 — 2026-05-14.

Servicio: **`web`** (FastAPI). Base URL local: `http://localhost:8100`. Todas las rutas REST viven bajo el prefijo `/api`. WebSocket en `/ws` (sin prefijo).

CORS configurable via `ALLOWED_ORIGINS` (default `http://localhost:3100`).

---

## 1. Tipos compartidos

### 1.1 `DecisorAction` (enum)

```
"BUY" | "SELL" | "HOLD"
```

### 1.2 `MarketRegime` (enum)

```
"TRENDING_UP" | "TRENDING_DOWN" | "RANGE" | "HIGH_VOLATILITY"
```

### 1.3 `DecisorOutput` (payload del Decisor, dentro de `decisions.output`)

```jsonc
{
  "regime": "TRENDING_UP",                  // MarketRegime
  "confluences": ["B","C","G"],              // 0..10 códigos del catálogo A-H
  "action": "BUY",                            // DecisorAction
  "confidence_base": 0.85,                    // 0.0..1.0
  "confidence_adjustment": 0.05,              // -0.10..+0.10
  "confidence": 0.90,                         // = clip(base+adj, 0, 1) — recomputado server-side
  "stop_loss": 94820.00,                      // null si action != BUY (requerido si BUY)
  "take_profit": 96400.00,                    // null si action != BUY (requerido si BUY)
  "position_size_pct": 0.10,                  // 0.0..0.25
  "expected_holding_min": 90,                 // entero ≥ 1
  "reasoning": "Régimen TRENDING_UP …"        // ≤ 800 caracteres, español
}
```

Reglas:
- Si `action == "BUY"` → `stop_loss` y `take_profit` son **obligatorios**.
- `confidence` se **recomputa** server-side desde `confidence_base + confidence_adjustment` (no se confía en el LLM).
- `position_size_pct` con `null` se coerciona a `0.0`. `expected_holding_min` con `null` → `1`.

### 1.4 `TradeOutcome` (reservado en `decisions.outcome`)

```jsonc
{
  "pnl_usdt": 12.34,
  "pnl_pct": 0.45,
  "close_reason": "tp_triggered",
  "duration_min": 90,
  "fees_usdt": 0.12
}
```

---

## 2. Endpoints REST

### 2.1 Health

#### `GET /api/health`

200 OK:

```jsonc
{
  "ok": true,
  "db": "up",
  "engine": {
    "ok": true,
    "detail": "última decisión hace 3m",
    "last_decision_age_min": 3
  },
  "binance": {
    "ok": true,
    "detail": "último precio hace 0m"
  }
}
```

- `engine.ok` ⇔ última `decisions.ts` < 15 min.
- `binance.ok` ⇔ último `ohlcv` 1m < 15 min.
- En error de BD: devuelve `ok=false` y descripciones específicas.

#### `GET /api/ping`

200 OK `{ "pong": true }` — liveness simple.

---

### 2.2 Trades

#### `GET /api/trades?status=&limit=`

Parámetros query:
- `status` opcional: `open` | `closed` | `cancelled`.
- `limit` opcional: 1..500 (default 100).

200 OK: `TradeOut[]` ordenado `ts_open DESC`.

```jsonc
{
  "id": "uuid",
  "decision_id": "uuid|null",
  "ts_open": "2026-05-14T12:00:00Z",
  "ts_close": "2026-05-14T13:30:00Z",
  "side": "BUY",
  "quantity_btc": 0.0012,
  "entry_price": 80123.45,
  "exit_price": 80850.10,
  "pnl_usdt": 0.83,
  "pnl_pct": 0.91,
  "status": "closed",
  "stop_loss": 79900.00,
  "take_profit": 80900.00,
  "close_reason": "tp_triggered",
  "fees_usdt": 0.05,
  "close_requested": false
}
```

#### `POST /api/trades/{trade_id}/close`

Solicita cierre del trade (no ejecuta inmediatamente; setea `close_requested=true`).

200 OK: `TradeOut` actualizado.

Errores:
- `404`: trade no encontrado.
- `409`: el trade no está abierto.

---

### 2.3 Decisions

#### `GET /api/decisions?agent=&executed=&limit=`

Parámetros query:
- `agent` opcional: `decisor` | `supervisor`.
- `executed` opcional: `true` | `false`.
- `limit`: 1..500 (default 100).

200 OK: `DecisionOut[]`, `ts DESC`.

```jsonc
{
  "id": "uuid",
  "ts": "2026-05-14T12:00:00Z",
  "agent": "decisor",
  "model": "groq-llama-3.3-70b",
  "tokens_in": 1842,
  "tokens_out": 312,
  "latency_ms": 715,
  "input":  { "...contexto unificado...": "..." },
  "output": { "...DecisorOutput o supervisor output...": "..." },
  "outcome": null,
  "trade_id": "uuid|null",
  "executed": true,
  "rejected_reason": null
}
```

> Para decisiones del Supervisor, `output` puede contener `{ "playbook": "markdown", "mode": "normal|diagnostic", "config_suggestions": {...}, "config_applied": [...], "config_rejected": [...] }`.

---

### 2.4 Positions

#### `GET /api/positions`

200 OK: `PositionOut[]` (solo `status="open"`).

```jsonc
{
  "id": "uuid",
  "trade_id": "uuid|null",
  "symbol": "BTC/USDT",
  "quantity_btc": 0.0012,
  "entry_price": 80123.45,
  "current_price": 80450.10,
  "unrealized_pnl": 0.39,
  "unrealized_pct": 0.41,
  "status": "open",
  "opened_at": "2026-05-14T12:00:00Z",
  "updated_at": "2026-05-14T12:05:30Z"
}
```

---

### 2.5 Balance

#### `GET /api/balance`

200 OK:

```jsonc
{
  "usdt": 4321.50,
  "btc_exchange": 0.0035,
  "btc_in_positions": 0.0012,
  "open_positions": 1,
  "balance_ts": "2026-05-14T12:05:00Z",
  "balance_source": "binance",
  "realized_pnl_today": 0.0
}
```

- `usdt` / `btc_exchange` toman el último `balance_snapshots`.
- `btc_in_positions` = Σ `quantity_btc` de `positions WHERE status='open'`.
- `realized_pnl_today` actualmente fijo `0.0` (TODO: cómputo real, ver `stats/daily`).

---

### 2.6 Playbook

#### `GET /api/playbook/active`

200 OK: `PlaybookOut | null`.

```jsonc
{
  "id": "uuid",
  "version": 12,
  "ts_generated": "2026-05-14T00:00:01Z",
  "content": "# Playbook v12 — …",
  "model": "gemini-2.5-pro",
  "trades_analyzed": 23,
  "win_rate": 56.52,
  "active": true
}
```

#### `GET /api/playbook/history`

200 OK: `PlaybookOut[]` (todas las versiones, `version DESC`).

#### `POST /api/playbook/{version}/activate`

Activa una versión (desactiva todas las demás).

200 OK: `{ "ok": true, "version": 11 }`.

Errores:
- `404`: version no encontrada.

#### `PATCH /api/playbook/{version}/content`

Edita en caliente el contenido (markdown) de una versión.

Body:

```jsonc
{ "content": "# Playbook editado…" }
```

200 OK: `{ "ok": true, "version": 12 }`.

Errores:
- `400`: `content` vacío.
- `404`: versión no existe.

---

### 2.7 Configuración

#### `GET /api/config`

200 OK: `ConfigEntryOut[]` (ordenado por `key`; excluye `supervisor_run_now`).

```jsonc
{
  "key": "max_position_pct",
  "value": "0.10",
  "value_type": "float",
  "description": "Max % capital per trade"
}
```

#### `PUT /api/config/{key}`

Body:

```jsonc
{ "value": "0.08" }
```

200 OK: `{ "ok": true, "key": "max_position_pct", "value": "0.08" }`.

Errores:
- `400`: clave no pertenece a `ConfigKey`.
- `404`: clave no seedada (apply migrations + seed defaults).

> Toda actualización inserta una fila en `config_history` con `changed_by="user"`.

#### `GET /api/config/suggestions`

200 OK: última sugerencia del Supervisor (o `null`).

```jsonc
{
  "generated_at": "2026-05-14T00:00:01Z",
  "summary": "Win rate sobre target; sugerimos…",
  "suggestions": [
    { "key": "sl_atr_multiplier", "current": "0.30",
      "suggested": 0.35, "reason": "…" }
  ]
}
```

---

### 2.8 Control

#### `POST /api/kill-switch`

Body:

```jsonc
{ "enabled": true }
```

200 OK: `{ "ok": true, "kill_switch": true }`.

Efecto: setea `config.kill_switch`. El engine lee el flag cada tick; con `kill_switch=true` el Risk Gate sólo permite SELL-to-close.

#### `POST /api/mode`

Body:

```jsonc
{ "mode": "LIVE", "confirmation": "CONFIRMO TRADING REAL" }
```

200 OK: `{ "ok": true, "mode": "LIVE" }`.

Errores:
- `400`: `mode=LIVE` sin la frase exacta `CONFIRMO TRADING REAL`.

#### `POST /api/supervisor/run`

Body: vacío `{}`.

200 OK: `{ "ok": true, "queued": true }`.

Efecto: setea `supervisor_run_now=true`. El engine consume el flag en el próximo ciclo del Decisor.

---

### 2.9 Stats

#### `GET /api/stats/daily`

200 OK:

```jsonc
{
  "trades_open": 1,
  "trades_closed": 4,
  "trades_won": 3,
  "trades_lost": 1,
  "pnl_realized": 2.45,
  "pnl_unrealized": 0.39,
  "fees_total": 0.18,
  "decisions_total": 48,
  "decisions_buy": 5,
  "decisions_sell": 4,
  "decisions_hold": 39,
  "decisions_executed": 9,
  "decisions_blocked": 1
}
```

Ventana: desde 00:00 UTC del día actual.

---

## 3. WebSocket `/ws`

Endpoint: `ws://localhost:8100/ws` (o `wss://` en producción).

El servidor empuja mensajes; no espera mensajes del cliente. Formato:

```jsonc
{ "event": "ticker" | "decision" | "positions", "data": ... }
```

### 3.1 Event `ticker` (cada 5 s)

```jsonc
{
  "event": "ticker",
  "data": {
    "symbol": "BTC/USDT",
    "price": 80123.45,
    "ts": "2026-05-14T12:00:00.123Z"
  }
}
```

Fuente: Binance REST `/api/v3/ticker/price` (testnet o mainnet según `BINANCE_TESTNET`).

### 3.2 Event `decision` (cada nueva `decisions.ts > last_decision_ts`)

```jsonc
{
  "event": "decision",
  "data": {
    "id": "uuid",
    "ts": "2026-05-14T12:00:00Z",
    "agent": "decisor",
    "action": "HOLD",
    "confidence": 0.55,
    "reasoning": "…"
  }
}
```

### 3.3 Event `positions` (snapshot cada 2 s)

```jsonc
{
  "event": "positions",
  "data": [
    {
      "id": "uuid",
      "trade_id": "uuid",
      "symbol": "BTC/USDT",
      "quantity_btc": 0.0012,
      "entry_price": 80123.45,
      "current_price": 80450.10,
      "unrealized_pnl": 0.39,
      "unrealized_pct": 0.41,
      "status": "open",
      "opened_at": "2026-05-14T12:00:00Z",
      "updated_at": "2026-05-14T12:05:30Z"
    }
  ]
}
```

> El array puede estar vacío.

### 3.4 Manejo de cliente recomendado

- Reconectar con backoff exponencial si el socket cierra.
- Mantener última señal `connected` para mostrar status en UI.
- Mezclar con polling REST para `health` y `balance` (no se emiten por WS).

---

### 2.10 OHLCV (gráfico de precios)

#### `GET /api/ohlcv?timeframe=&limit=`

Devuelve velas OHLCV de la tabla `ohlcv` en **orden cronológico ascendente** (la más antigua primero).

Parámetros query:

| Param | Tipo | Default | Restricciones |
|-------|------|---------|---------------|
| `timeframe` | `"1m" \| "5m" \| "15m" \| "1h" \| "4h"` | `"5m"` | Obligatorio que sea uno de los 5 valores. Otro valor → 422. |
| `limit` | `int` | `300` | `1 ≤ limit ≤ 1000`. Fuera de rango → 422. Devuelve las últimas `limit` velas (orden DESC en BD, luego reversed antes de responder). |

200 OK: `CandleOut[]`

```jsonc
[
  {
    "time": "2026-05-14T12:00:00Z",   // ISO 8601 UTC
    "open":   95200.00,
    "high":   95450.00,
    "low":    95100.00,
    "close":  95380.00,
    "volume": 1.23456789
  }
]
```

- El array puede estar vacío (`[]`) si no hay velas para ese timeframe en la BD.
- Campos `open/high/low/close/volume` pueden ser `null` si la vela fue persistida con datos parciales (raro; el engine siempre los completa).
- La fuente de los datos es la tabla `ohlcv` escrita por el `PriceCollector` del engine, no Binance directamente. Esto garantiza coherencia con los indicadores que usa el Decisor.

Errores:

| HTTP | Cuándo |
|------|--------|
| 422  | `timeframe` no es uno de los 5 valores permitidos o `limit` fuera de rango. |

---

## 4. Códigos de error globales

| HTTP | Cuándo | Notas |
|------|--------|-------|
| 200 | Éxito (GET/POST/PUT/PATCH). | |
| 400 | Validación fallida (frase de confirmación, body inválido, content vacío). | Body usualmente `{"detail":"…"}`. |
| 404 | Recurso no encontrado (trade, version, config key). | |
| 409 | Conflicto de estado (cerrar trade no abierto). | |
| 422 | Errores Pydantic de body parsing. | Estructura estándar FastAPI. |
| 500 | Error inesperado. | El handler default de FastAPI; debe loguearse en el server. |

---

## 5. Versionado

Esta API **no está versionada vía URL** (todo bajo `/api`). Cambios incompatibles deben coordinarse con el frontend (`frontend/src/types/index.ts` debe espejar los Pydantic). Para evoluciones futuras se sugiere convención `/api/v2/...` si se llegan a romper contratos.
