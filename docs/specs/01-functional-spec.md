# Especificación Funcional — Crypto AI Trading

> Audiencia: Product, Risk, Trading, Stakeholders.
> Versión: 1.0 — 2026-05-14.

---

## 1. Visión del producto

**Crypto AI Trading** es un bot autónomo de *day trading* sobre el par **BTC/USDT** en **Binance Spot** que combina:

- **Inteligencia técnica determinística**: cálculo de indicadores (RSI, MACD, EMAs, Bollinger, ATR) y profundidad de order book en múltiples timeframes (1m, 5m, 15m, 1h, 4h).
- **Dos agentes LLM** que cooperan:
  - **Decisor**: cada N minutos decide `BUY` / `SELL` / `HOLD` con un JSON estructurado.
  - **Supervisor**: diariamente (00:00 UTC) reescribe el *playbook* y sugiere ajustes finos de configuración.
- **Risk Gate determinístico**: verifica reglas absolutas (R1–R10) antes de ejecutar cualquier orden; las decisiones del LLM nunca eluden los límites de riesgo configurados.
- **Dashboard web** en español (es-AR) con métricas en vivo, historial de trades, decisiones, playbooks y configuración editable.

El bot opera en dos modos: **`PAPER_TRADING`** (default, Binance Testnet, sin dinero real) y **`LIVE`** (mainnet, requiere confirmación explícita).

---

## 2. Objetivos de negocio

| Objetivo | Métrica | Umbral mínimo |
|----------|---------|---------------|
| Generar P&L positivo ajustado por riesgo | Sharpe ratio anualizado | > 1.0 |
| Limitar pérdidas catastróficas | Max drawdown | < 5% (LIVE), < 10% (backtest gate) |
| Tasa de acierto sostenible | Win rate | > 52% (LIVE), > 48% (backtest gate) |
| Eficiencia operativa | Profit factor | > 1.5 (LIVE), > 1.3 (backtest gate) |
| Confiabilidad técnica | Decisiones LLM sin errores de parseo | > 99% en 48 h continuas |
| Costo operativo | LLM tokens/día (Gemini Flash + Groq) | Dentro del free tier permanente |

---

## 3. Roles y usuarios

### 3.1 Operador

Persona responsable del bot. Sus tareas diarias son:

1. Revisar dashboard (`/`) — 5 min/día.
2. Ver log de decisiones (`/decisions`) para detectar anomalías.
3. Revisar el playbook activo (`/playbook`).
4. Intervenir solo si:
   - El bot se pausa por *circuit breaker*.
   - El Supervisor produce un playbook que empeora las métricas → rollback.
   - Alguna métrica de la semana queda fuera de umbrales.
5. Ejecutar **Kill Switch** en emergencias.

### 3.2 Sistema (agentes automáticos)

| Agente | Frecuencia | Responsabilidad |
|--------|-----------|----------------|
| Decisor | cada `decisor_interval_min` (default 5 min) | Producir 1 decisión estructurada por ciclo. |
| Supervisor | 1×/día (`0 0 * * *` UTC) o disparo manual | Reescribir el playbook y sugerir ajustes de configuración dentro de guardrails. |
| Order Tracker | cada 30 s | Detectar fills de SL/TP en Binance y cerrar trades en BD. |
| Position Refresher | cada 30 s | Actualizar P&L no realizado de posiciones abiertas. |
| Price/OHLCV Collector | en cada tick del Decisor | Persistir velas y recomputar indicadores. |
| Fee Manager | cada 24 h | Refrescar maker/taker fees desde Binance. |

---

## 4. Mapa de capacidades funcionales

### F1. Recolección de datos de mercado

- Captura OHLCV de Binance vía CCXT para los timeframes **1m, 5m, 15m, 1h, 4h** (~250 velas cada uno).
- Mantiene en memoria un snapshot de **order book** (top 20 niveles vía CCXT WS) y deriva spread, imbalance, walls.
- Calcula indicadores técnicos por timeframe: RSI (Wilder), MACD(12/26/9), EMA 20/50/200, Bollinger Bands (20, 2σ), ATR con *winsorización* de TR (cap a 3× mediana móvil) para neutralizar velas anómalas del testnet.

### F2. Toma de decisiones (Decisor)

- Cada `decisor_interval_min` minutos arma un **contexto** unificado con:
  - Modo, capital, balance USDT/BTC.
  - Fees actuales (maker/taker/round-trip).
  - Indicadores de los 5 timeframes.
  - Snapshot de order book (top 10).
  - Últimas 3 decisiones del Decisor.
  - Posiciones abiertas.
  - **Playbook activo**.
- Llama al LLM Decisor con *system prompt* + *user prompt* renderizados con el contexto.
- Espera **JSON estricto** validado por Pydantic (`DecisorOutput`).
- Aplica **overrides deterministas** post-LLM:
  - `TRENDING_DOWN` o `confidence < 0.60` → forzar `HOLD`.
  - Sizing por umbral: `confidence ≥ 0.70` → `max_position_pct`; `0.60–0.69` → 0.03.
- Persiste **toda** la decisión (input + output + métricas LLM) en la tabla `decisions`.

### F3. Validación de riesgo (Risk Gate)

Antes de ejecutar la decisión, el Risk Gate verifica reglas absolutas (ver `05-risk-and-safety.md`). Si cualquiera falla → `decision.executed = false` y `rejected_reason` con el motivo. `HOLD` siempre pasa.

### F4. Ejecución de órdenes

- **BUY**: orden market con `quoteOrderQty = usdt_balance × position_size_pct`. Crea un par **bracket** SL/TP en Binance (STOP_LOSS_LIMIT + LIMIT).
- **SELL**: orden market que cierra una posición abierta. Registra `pnl_usdt`, `pnl_pct`, `fees_usdt`, `close_reason`.
- **Bracket fill por Binance**: si Binance ejecutó el SL o TP sin participación del bot, el `OrderTracker` detecta el fill, calcula `close_reason` (`sl_triggered` / `tp_triggered` / `bracket_fill`) y actualiza la BD sin emitir órdenes adicionales.
- **Cierre manual**: el operador puede solicitar cerrar un trade desde la UI (`POST /api/trades/{id}/close`); el `OrderTracker` lo procesa en su próximo ciclo.

### F5. Aprendizaje (Supervisor)

- Diariamente analiza últimas 24 h:
  - Trades cerrados, win rate, profit factor, P&L, drawdown.
  - Distribución de acciones (BUY/SELL/HOLD).
  - Cantidad de rechazos del Risk Gate.
  - Resumen de mercado (precio open/close, low/high, ATR, label de volatilidad).
- Modo **`diagnostic`** si hay menos trades cerrados que `min_trades` (5): genera diagnóstico (mercado lateral/bajista, playbook restrictivo, etc.).
- Modo **`normal`** con suficientes trades: optimiza el playbook.
- Guarda nueva `PlaybookVersion` (active=true, anteriores active=false).
- **Segunda llamada LLM** para sugerencias de configuración: propone valores nuevos para `atr_timeframe`, `sl_atr_multiplier`, `min_rr_ratio`, `decisor_interval_min`, `max_position_pct`, `conf_threshold_*`. Solo aplica las que caen dentro de **guardrails** (`_SAFE_BOUNDS`); el resto se persiste como sugerencia rechazada con motivo.

### F6. Control operativo (Web)

| Capacidad | Endpoint | Quién puede |
|-----------|----------|-------------|
| Ver estado del bot | `GET /api/health` | Cualquiera |
| Ver balance del exchange | `GET /api/balance` | Operador |
| Listar/cerrar trades | `GET /api/trades`, `POST /api/trades/{id}/close` | Operador |
| Listar decisiones | `GET /api/decisions` | Operador |
| Ver posiciones abiertas | `GET /api/positions` | Operador |
| Stats del día | `GET /api/stats/daily` | Operador |
| Playbook activo / historial / activar versión / editar contenido | `GET /api/playbook/...`, `POST /api/playbook/{v}/activate`, `PATCH /api/playbook/{v}/content` | Operador |
| Configuración (60+ parámetros tipados) | `GET /api/config`, `PUT /api/config/{key}` | Operador |
| Kill Switch | `POST /api/kill-switch` | Operador |
| Cambiar modo PAPER ↔ LIVE | `POST /api/mode` (requiere frase `CONFIRMO TRADING REAL`) | Operador |
| Disparar Supervisor manualmente | `POST /api/supervisor/run` | Operador |
| Sugerencias de configuración pendientes | `GET /api/config/suggestions` | Operador |

### F7. Dashboard en vivo

Páginas (React + Tailwind):

- **`/` Dashboard** — balance Binance, posiciones abiertas, última decisión, estado del día (P&L realizado/no realizado, trades, decisiones).
- **`/trades`** — listado, filtros por status, botón cerrar.
- **`/decisions`** — historial detallado con input/output JSON.
- **`/playbook`** — markdown del playbook activo + historial con rollback y edición inline.
- **`/config`** — formulario de los ~60 parámetros tipados, con descripciones, validación, kill switch y switch de modo.
- **`/health`** — estado de motor, DB, Binance.

WebSocket `/ws` empuja:
- `ticker` (precio BTC/USDT cada 5 s vía REST público).
- `decision` (nueva decisión persistida).
- `positions` (snapshot de posiciones abiertas cada 2 s).

---

## 5. Flujos operativos clave

### 5.1 Ciclo del Decisor (cada `decisor_interval_min`)

```
1. ¿engine_paused? → log "engine.paused" y salir.
2. ¿supervisor_run_now=true? → ejecutar Supervisor y resetear flag.
3. Leer config runtime (mode, max_position_pct, daily_stop_pct,
   provider/fallbacks, calibración completa).
4. PriceCollector: fetch_ohlcv para 1m/5m/15m/1h/4h + compute_indicators.
5. FeeManager: get_or_refresh (cada 24 h).
6. fetch_balance → BalanceSnapshot (fallback DB si exchange caído).
7. ContextBuilder.build(orderbook, balances, playbook, calibración, …).
8. Decisor.decide → LLM → parseo JSON → DecisorOutput validado.
9. Override determinístico (TRENDING_DOWN/conf<0.60/sizing).
10. Persistir Decision (input + output + tokens + latencia + rejected_reason).
11. RiskGate.validate(decision, current_price, atr_ref, balances, kill_switch, …).
12. Si rechazado → update rejected_reason y return.
13. Si BUY → Executor.execute_buy (market + bracket SL/TP).
    Si SELL → Executor.execute_sell sobre la primera posición abierta.
    Si HOLD → nada.
```

### 5.2 Ciclo del Supervisor (00:00 UTC o `supervisor_run_now=true`)

```
1. Compute metrics 24h (trades, decisiones, P&L, regime, ATR, vol_label).
2. Si closed_trades < min_trades → mode = "diagnostic", inyectar header.
3. Llamada LLM Supervisor → playbook markdown nuevo.
4. Guardar PlaybookVersion (version = prev+1, active=true, anteriores false).
5. Llamada LLM #2 → sugerencias de configuración estructuradas (JSON).
6. Aplicar sólo claves dentro de _SAFE_BOUNDS; persistir rejected con motivo.
7. Registrar Decision (agent="supervisor", output con playbook + suggestions).
```

### 5.3 Cierre de un trade

Tres rutas, todas convergen en `Trade.status = "closed"`:

1. **Decisor emite SELL** → `Executor.execute_sell` (market order) → reason `decisor_sell`.
2. **Binance ejecutó SL/TP** → `OrderTracker.poll_once` detecta fill → `record_bracket_fill` → reason `sl_triggered` / `tp_triggered` / `bracket_fill`.
3. **Operador solicita cierre** desde UI → `close_requested=true` → en el próximo tick del OrderTracker, `execute_sell` con reason `manual_close`.

### 5.4 Onboarding paper → LIVE (roadmap de 6 pasos)

| Paso | Gate |
|------|------|
| 1. Binance Testnet keys configuradas | Sin errores de auth en logs. |
| 2. LLM keys (Gemini + Groq) configuradas | Decisiones LLM logueadas 48 h sin errores. |
| 3. Backtest 90 d | Sharpe > 1.0, DD < 10%, WR > 48%, PF > 1.3. |
| 4. Paper trading 4 semanas | Cada semana: Sharpe > 1.0, DD < 5%, WR > 52%, PF > 1.5, errores LLM < 1%, sin semana con DD > 3%. Si falla, reinicia el contador. |
| 5. API keys Mainnet | Permisos mínimos (sin retiros, sin margin), IP restringida. |
| 6. Switch LIVE | `POST /api/mode {mode:"LIVE", confirmation:"CONFIRMO TRADING REAL"}`. Capital inicial $200–500 USDT. |

---

## 6. Reglas de negocio

### 6.1 Mercado y producto

- **Único par**: `BTC/USDT`. Spot. No futuros, no margin, **nunca shorts** (R7).
- **Capital inicial recomendado LIVE**: $200–500 USDT. Hasta 8 semanas LIVE no incrementar.

### 6.2 Riesgo (resumen — detalle en `05-risk-and-safety.md`)

| Parámetro | Default | Función |
|-----------|---------|---------|
| `max_position_pct` | 0.10 | Máximo % del capital por trade. |
| `max_simultaneous_trades` | 2 | Posiciones abiertas en paralelo. |
| `daily_stop_pct` | -0.03 | Si P&L del día ≤ −3% → HOLD forzado. |
| `max_drawdown_pct` | -0.10 | Si drawdown total ≤ −10% → kill switch. |
| `min_rr_ratio` | 1.3 | Reward/risk mínimo para aprobar BUY. |
| `sl_atr_multiplier` | 0.3 | Distancia SL mínima como múltiplo de ATR. |
| `sl_atr_max_multiplier` | 1.5 | Distancia SL máxima como múltiplo de ATR. |
| `min_fees_to_tp_ratio` | 3.0 | El movimiento al TP debe cubrir ≥3× los fees round-trip (R10). |

### 6.3 Confianza y sizing

- Umbrales por régimen para permitir BUY:
  - `conf_threshold_trending_up` = 0.60
  - `conf_threshold_range` = 0.70
  - `conf_threshold_high_vol` = 0.80
- Sizing determinístico (post-override del Decisor):
  - `confidence ≥ 0.70` → `position_size_pct = max_position_pct`.
  - `confidence 0.60–0.69` → `position_size_pct = 0.03` (reducido).
  - `confidence < 0.60` → `HOLD` forzado.

### 6.4 Confluencias técnicas (catálogo cerrado A–H)

| Código | Confluencia | Resumen |
|--------|-------------|---------|
| A | RSI_OVERSOLD_BOUNCE | RSI 15m/1h saliendo de <30 con vela alcista. |
| B | MACD_BULLISH_CROSS | Cruce MACD>Signal 15m/1h con hist creciente. |
| C | EMA_SUPPORT_HOLD | Rebote en EMA20/50/200 (1h o 4h) con mecha. |
| D | BB_LOWER_REVERSAL | BB% 5m <5 con vela de reversión. |
| E | ORDERBOOK_BID_PRESSURE | Imbalance > 0.6 + bid wall < 0.3% del precio. |
| F | BREAKOUT_VOL_CONFIRMED | Ruptura con volumen > 1.5× media 20. |
| G | HIGHER_TF_ALIGNMENT | RSI 4h >50 + EMA20_4h > EMA50_4h + precio > EMA20_1h. |
| H | RANGE_SUPPORT_TOUCH | Precio en banda inferior de rango definido. |

- Mínimo de confluencias para BUY: **2** (`min_confluences_buy`).
- Cooldown post-SELL: **15 min** (`cooldown_after_sell_min`).

### 6.5 Régimen de mercado

| Régimen | Acción esperada | Factor en fórmula de confidence |
|---------|----------------|--------------------------------|
| TRENDING_UP | BUY permitido con 2+ confluencias. | 1.0 |
| RANGE | BUY solo cerca de soporte. | 0.85 |
| HIGH_VOLATILITY | SL amplio cerca de `sl_atr_max_multiplier`×ATR. | 0.75 |
| TRENDING_DOWN | **BUY bloqueado**. Solo HOLD o SELL para cerrar. | 0.0 → HOLD |

### 6.6 Reglas de absoluta seguridad

1. **Kill switch** activo → solo se permiten SELL para cerrar posiciones. Cualquier BUY se rechaza.
2. **Cambio de modo a LIVE** exige confirmación exacta `CONFIRMO TRADING REAL` en el body del request.
3. **Operación spot-only**: nunca short, nunca margin, nunca retiros desde API key.
4. **Configuración del Supervisor**: solo claves dentro de `_SAFE_BOUNDS` se auto-aplican; las críticas (`daily_stop_pct`, `max_drawdown_pct`) están explícitamente excluidas y requieren intervención manual.

---

## 7. Modos de operación

| Modo | Exchange | Riesgo financiero | Activación |
|------|----------|------------------|------------|
| `PAPER_TRADING` (default) | Binance Spot **Testnet** (`testnet.binance.vision`) | Ninguno | `BINANCE_TESTNET=true` |
| `LIVE` | Binance Spot **Mainnet** | Real | `BINANCE_TESTNET=false` + `POST /api/mode {mode:"LIVE", confirmation:"CONFIRMO TRADING REAL"}` |

En testnet, los fees suelen ser 0, por lo que la regla R10 (movimiento TP cubre fees) no aplica automáticamente.

---

## 8. Criterios de aceptación

### 8.1 Funcionales

| Id | Criterio |
|----|----------|
| AC-01 | El Decisor produce 1 decisión por ciclo; siempre persistida en `decisions` (incluso con error). |
| AC-02 | Una decisión BUY con `confidence < 0.60` o régimen `TRENDING_DOWN` es **siempre** sobreescrita a HOLD por el override determinístico. |
| AC-03 | Toda decisión BUY que cruce el Risk Gate ejecuta una market order + bracket SL/TP. |
| AC-04 | Toda decisión rechazada queda con `executed=false` y `rejected_reason` poblado. |
| AC-05 | El Supervisor genera una nueva `PlaybookVersion` cada 24 h con `active=true` y desactiva las anteriores. |
| AC-06 | Las sugerencias del Supervisor **fuera de `_SAFE_BOUNDS`** se persisten como rechazadas con `reject_reason`, sin aplicarse. |
| AC-07 | El WebSocket emite eventos `ticker`, `decision` y `positions` continuamente sin requerir polling adicional desde la UI. |
| AC-08 | Cambiar de modo a `LIVE` sin la frase exacta de confirmación responde HTTP 400. |
| AC-09 | Toda escritura en `config` queda registrada en `config_history` con `changed_by`. |
| AC-10 | El operador puede activar una versión anterior del playbook con un click (rollback). |

### 8.2 No funcionales

| Id | Criterio |
|----|----------|
| AC-N1 | Tiempos UTC en BD (`TIMESTAMPTZ`) y en logs JSON. |
| AC-N2 | Montos en `NUMERIC` (precisión exacta). |
| AC-N3 | El engine sigue operativo aunque Binance esté caído: cae a `usdt=0` y `btc` derivado de posiciones abiertas, evitando nuevos BUYs (R1: balance insuficiente). |
| AC-N4 | Si el LLM falla, el cascade de fallbacks intenta hasta 5 providers; tras 5 fallas consecutivas el `CircuitBreaker` pausa el engine. |
| AC-N5 | Si Binance falla 5 ciclos consecutivos para órdenes/balance, el engine se pausa. |
| AC-N6 | UI en español (`es-AR`); formato de números/fechas con locale local. |

---

## 9. Riesgos funcionales y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| LLM "alucina" un valor numérico fuera de R1–R10 | Pérdida potencial > límite | Risk Gate determinístico bloquea; override fuerza HOLD si `confidence < 0.60`. |
| Supervisor cambia parámetros críticos | Estrategia degradada | `_SAFE_BOUNDS` excluye `daily_stop_pct` y `max_drawdown_pct`; rollback de playbook a un click. |
| Velas anómalas en testnet (flash low) inflan ATR | SL/TP irreales | TR winsorizado a 3× mediana móvil. |
| Provider LLM saturado / rate limit | Sin decisión por ciclo | Cascade de fallbacks (Gemini + 5 Groq); `CircuitBreaker` corta tras 5 fallas. |
| Pérdida de conexión a Binance | Trades zombi | `OrderTracker` cada 30 s reconcilia fills; engine pausa tras 5 fallas. |
| Operador olvida un kill switch | Riesgo sostenido | `daily_stop_pct` y `max_drawdown_pct` automáticos detienen actividad sin intervención. |
| Drift entre playbook y parámetros del sistema | Decisiones inconsistentes | Regla obligatoria en system prompt del Decisor: parámetros del sistema prevalecen; el LLM debe loguear `[DRIFT CONFIG]` en `reasoning`. |
| Cambio accidental a LIVE | Pérdida real | Frase de confirmación literal obligatoria en el endpoint `/api/mode`. |

---

## 10. Glosario rápido

- **OHLCV**: Open/High/Low/Close/Volume (candlestick).
- **ATR**: Average True Range, mide volatilidad.
- **R:R**: Reward/Risk ratio = (TP − entry) / (entry − SL).
- **Bracket**: par SL + TP que protege la entrada.
- **Confluencia**: condición técnica del catálogo A–H que justifica un BUY.
- **Playbook**: documento markdown versionado que guía al Decisor; reescrito por el Supervisor.
- **Risk Gate**: capa determinística entre LLM y exchange.
- **Circuit Breaker**: pausa global del engine ante fallas en cadena.
- **Kill Switch**: detención de emergencia que sólo permite SELL.
