# Futuros (shorts) — Spec de diseño

**Fecha:** 2026-06-02
**Estado:** Borrador v1 (pendiente de aprobación)
**Autor:** Matías + Claude (sesión de brainstorm)
**Audiencia:** Implementación (`writing-plans` skill) + revisores de spec funcional/técnica.

**Premisa:** Habilitar **ventas en corto (shorts)** para ganar en mercado bajista, migrando el motor de **Binance Spot** a **Binance USDT-M Futures perpetuos** (`BTC/USDT:USDT`), de forma simétrica por dirección (`LONG`/`SHORT`) y con un perfil de riesgo controlado (apalancamiento **1x** al inicio).

**Alcance del PR esperado:**
- Código backend: `trading-engine/exchange.py` (nuevo `ExchangeAdapter` + `SpotAdapter`/`FuturesAdapter`), `trading-engine/execution/executor.py`, `trading-engine/execution/order_tracker.py`, `trading-engine/execution/position_manager.py`, `trading-engine/risk/risk_gate.py`, `trading-engine/risk/coherence_checker.py`, `trading-engine/agents/decisor.py`, `trading-engine/agents/decisor_aggregate.py`, `trading-engine/agents/context_builder.py`, `trading-engine/agents/prompts/decisor_system.txt`, `trading-engine/main.py`, `trading-engine/config.py`.
- Código shared: `shared/schemas.py`, `shared/position_sizing.py`, `shared/confidence.py`, `shared/confidence_calibration.py`, `shared/pnl.py`, `shared/config_store.py`, `shared/db/models.py`.
- Migración: `trading-engine/alembic/versions/016_add_futures_fields.py`.
- Frontend: `frontend/src/types/index.ts`, `frontend/src/types/decisorOutput.ts`, `frontend/src/lib/pnl.ts`, `frontend/src/pages/Dashboard.tsx`, `frontend/src/pages/Trades.tsx`, `frontend/src/pages/Decisions.tsx`, `frontend/src/pages/Config.tsx`, `frontend/src/pages/Health.tsx`, `frontend/src/components/chart/PriceChart.tsx`, `frontend/src/api/client.ts`.
- Web API: `web/api/decisions.py`, contratos de trades/positions/config.
- Tests: `test_exchange.py`, `test_executor.py`, `test_order_tracker.py`, `test_position_manager.py`, `test_risk_gate.py`, `test_coherence_checker.py`, `test_decisor.py`, `test_decisor_aggregate.py`, `test_position_sizing.py`, `test_confidence.py`, `test_context_builder.py`.
- Specs: `docs/specs/01`, `02`, `03`, `04`, `05`, `06`, `07`.

---

## 1. Premisa del cambio

> "Incluir futuros para poder shortear y ganar en mercado bajista. Usamos Binance."

El sistema actual es **Spot LONG-only de punta a punta**: el Decisor solo emite `BUY`/`SELL`/`HOLD`, el prompt prohíbe explícitamente shortear (R7), y todo el stack downstream (Risk Gate, ejecución, PnL, frontend) está modelado para una única dirección de entrada (long).

Para ganar en mercado bajista hace falta **abrir posiciones cortas**, que en Binance no son posibles en Spot. La solución es operar **USDT-M Futures perpetuos**, donde un short es nativo (`side=sell` para abrir, `side=buy` `reduceOnly` para cerrar).

### 1.1 Decisiones de alcance aprobadas

| Decisión | Valor aprobado |
|----------|----------------|
| Enfoque | **Migrar todo a USDT-M Futures** (reemplaza Spot), lógica simétrica por dirección. |
| Apalancamiento inicial | **1x fijo** (riesgo ≈ Spot actual). Cap configurable solo por operador. |
| Modo de posición | **one-way** (no hedge) — una posición a la vez, coherente con el modelo actual. |
| Modo de margen | **isolated** — acota la pérdida máxima por posición. |
| Convivencia / rollback | Feature flag **`TRADING_PRODUCT`** (`spot` \| `futures`) para poder volver a Spot. |
| Rollout | **Paper-first** en Futures Testnet, gates §10 adaptados a perp. |

---

## 2. Estado actual (referencia)

### 2.1 Exchange (`trading-engine/exchange.py`)
- CCXT `4.4.40` (`ccxt.async_support.binance`), `options.defaultType = "spot"`.
- `build_binance_client()` es la única función; testnet vía `set_sandbox_mode(True)` cuando `BINANCE_TESTNET=true`.
- Sin referencias a futuros, leverage ni `positionSide`.

### 2.2 Ejecución (`trading-engine/execution/`)
- `Executor.execute_buy`: market BUY con `quoteOrderQty` (gasta USDT), persiste `Trade(side="BUY")` + `Position(status="open")`, coloca brackets **OCO SELL** (`privatePostOrderListOco`) o `STOP_LOSS_LIMIT`/`LIMIT` sueltos.
- `Executor.execute_sell`: market SELL de BTC. PnL **long**: `(exit − entry) × qty`.
- `OrderTracker.poll_once` (cada 10 s): detecta fills **sell** de brackets, guardians de SL/TP, cierre manual (`close_requested`).
- `PositionManager.refresh_unrealized`: PnL no realizado **long** (`(current − entry) × qty`).

### 2.3 Decisor (`trading-engine/agents/`)
- `DecisorAction = {BUY, SELL, HOLD}` (`shared/schemas.py`).
- `DecisorOutput`: `action`, `regime`, `confluences` (A–H + I–Z), `confidence*`, `stop_loss`/`take_profit` (obligatorios en BUY), `position_size_pct` (0–0.25).
- Prompt `decisor_system.txt`: perfil "BTC/USDT en Binance Spot"; **R7: nunca shortear**; confluencias A–H alcistas; régimen `TRENDING_DOWN` desincentiva BUY.
- `shared/confidence.py::regime_factor(TRENDING_DOWN) = 0.0` (anula confianza en bajista).
- `shared/position_sizing.py`: `sl_distance_pct = (price − stop_loss) / price` (solo BUY).
- `decisor_aggregate.py`: self-consistency con ramas BUY/SELL/HOLD.

### 2.4 Risk Gate (`trading-engine/risk/risk_gate.py`) y Coherence
Reglas R0–R11, todas con geometría **long** (R2 SL<precio, R3 TP>precio, R4 banda ATR, R5 R:R con reward hacia arriba, R6 SELL requiere `btc_held>0`, R7 estructural). `CoherenceChecker` C7 calcula R:R con fórmula long. Kill switch permite solo SELL-to-close.

### 2.5 Modelo de datos (`shared/db/models.py`, migración hasta 015)
- `trades.side` VARCHAR(4) — siempre `BUY` al abrir; `quantity_btc`, `entry_price`, `exit_price`, `stop_loss`, `take_profit`, `order_id_sl/tp`, PnL long.
- `positions` — sin campo de dirección (long implícito); `quantity_btc`, `entry_price`, `unrealized_pnl`.
- `balance_snapshots` — `usdt` + `btc` (modelo spot).

### 2.6 Frontend (`frontend/`)
- React 19 + Vite 6 + TS 5.7 + Tailwind 4. **La fuente real son los `.tsx`/`.ts`**; los `.js` son emit de `tsc -b` (no editar).
- `lib/pnl.ts` ya invierte la fórmula cuando `side != "BUY"`, pero `Dashboard.tsx`, distancias SL/TP de `Trades.tsx` y markers de `PriceChart.tsx` asumen long.
- `types/index.ts`: `DecisorAction = "BUY"|"SELL"|"HOLD"`; `Position` sin `side`.

### 2.7 API de Binance USDT-M Futures (lo relevante)
- Cliente CCXT con `options.defaultType = "future"`; símbolo `BTC/USDT:USDT` (linear swap). Testnet vía `set_sandbox_mode(True)`.
- **Abrir short:** `create_order(symbol, "market", "sell", amount=qty_btc)` (one-way, sin `positionSide`).
- **Cerrar short:** `create_order(symbol, "market", "buy", qty, params={"reduceOnly": True})`.
- **Brackets (no hay OCO spot):** `STOP_MARKET` y `TAKE_PROFIT_MARKET` con `params={"stopPrice": X, "reduceOnly": True, "workingType": "MARK_PRICE"}` (lado opuesto a la posición). Alternativa: `closePosition=true`.
- **Cantidad en BTC/contratos** (no `quoteOrderQty`): `qty = notional / price`.
- **Apalancamiento/margen:** `set_leverage(n, symbol)` y `set_margin_mode("isolated", symbol)` (idempotentes).
- Variables nuevas: **precio de liquidación**, **margen inicial/mantenimiento**, **funding rate** (`fetch_funding_rate(symbol)`).

---

## 3. Diseño propuesto

### 3.0 Guard de arranque (sizing vs minNotional)

Antes de habilitar `TRADING_PRODUCT=futures`, `main.py` ejecuta una **validación de capital** (`validate_futures_sizing()`):

```
min_notional = adapter.min_notional(symbol)        # leído de exchangeInfo, NO hardcodeado
max_trade_notional = available_margin × max_position_pct × leverage
assert max_trade_notional ≥ min_notional
```

- Si **no** se cumple: el engine **no entra en modo futures** (se mantiene en `spot` o pausa con razón clara), loguea `futures.sizing_unfeasible` y notifica por Telegram. Esto evita el escenario silencioso de "nunca opera porque todo trade cae bajo el mínimo".
- Fórmula operativa para el operador: `capital_mínimo ≈ min_notional / (max_position_pct × leverage)`.
- El `min_notional_usdt` se **obtiene del símbolo** vía `exchange.markets[symbol]["limits"]["cost"]["min"]` (ccxt) en el arranque y en cada refresh de fees; cada adapter reporta su valor real (Spot ≠ Futures).

### 3.1 Abstracción `ExchangeAdapter` (eje arquitectónico)

Para no esparcir `if futures:` por el `Executor`/`OrderTracker`, se introduce una interfaz `ExchangeAdapter` seleccionada por `TRADING_PRODUCT`:

```python
class ExchangeAdapter(Protocol):
    product: str  # "spot" | "futures"

    def build_client(self) -> ccxt_async.binance: ...
    async def setup_symbol(self, symbol: str, *, leverage: int, margin_mode: str) -> None: ...

    async def open_position(
        self, *, symbol: str, direction: Direction, notional_usdt: float, price: float
    ) -> OpenResult: ...   # market entry; devuelve filled_qty, avg_price, order_id

    async def close_position(
        self, *, symbol: str, direction: Direction, qty: float, close_reason: str
    ) -> CloseResult: ...   # market reduce-only en lado contrario

    async def place_brackets(
        self, *, symbol: str, direction: Direction, qty: float,
        stop_loss: float | None, take_profit: float | None,
    ) -> BracketResult: ...  # OCO (spot) o STOP_MARKET+TAKE_PROFIT_MARKET reduceOnly (futures)

    async def fetch_balance(self) -> BalanceView: ...
    async def fetch_positions(self) -> list[PositionView]: ...   # incluye liquidation_price (futures)
    async def fetch_funding_rate(self, symbol: str) -> float: ...
```

- `SpotAdapter`: envuelve el comportamiento actual (BUY/SELL + OCO). `funding_rate=0`, `liquidation_price=None`, `setup_symbol` no-op.
- `FuturesAdapter`: `defaultType="future"`, símbolo `BTC/USDT:USDT`, brackets reduceOnly, `set_leverage`/`set_margin_mode`, expone liquidación y funding.
- `main.py` instancia el adapter según `TRADING_PRODUCT` y lo inyecta a `Executor`, `OrderTracker`, collectors.
- `collectors/price_collector.py` y `orderbook_collector.py` usan el mismo símbolo/stream del adapter.

`Direction` = enum `{LONG, SHORT}` en `shared/schemas.py`.

### 3.2 Modelo de decisión (Decisor + schema + prompt)

**`DecisorAction`** pasa a `{BUY, SHORT, SELL, HOLD}`:

| Acción | Semántica |
|--------|-----------|
| `BUY` | Abrir **LONG** (semántica actual intacta). |
| `SHORT` | Abrir **SHORT** (nuevo). |
| `SELL` | **Cerrar** la posición abierta (long o short). Universal exit. |
| `HOLD` | No operar. |

- `DecisorOutput` gana validación de **presencia** direccional (Pydantic):
  - `BUY` **y** `SHORT`: `stop_loss` y `take_profit` no nulos (hoy `_buy_requires_sl_and_tp` solo cubre BUY → se extiende a SHORT).
  - `SELL`/`HOLD`: SL/TP nulos, `position_size_pct = 0`.
  - **Importante:** `DecisorOutput` **no** conoce el precio actual (no tiene campo `entry`), por lo que la **geometría relativa al precio** (LONG `SL<precio<TP`, SHORT `TP<precio<SL`) **no** se valida en Pydantic sino en el **Risk Gate** (R2/R3) y `CoherenceChecker` (C7), consistente con la arquitectura actual.
- `_apply_sizing_from_ctx` y `_apply_server_confidence` se generalizan para `BUY` **y** `SHORT`.
- Two-pass C7 y fallbacks se vuelven direccionales.

**Prompt `decisor_system.txt`:**
- Perfil cambia a "BTC/USDT en Binance USDT-M Futures perpetuos".
- Se **elimina R7**; se documenta que `SHORT` abre corto y `SELL` cierra.
- Catálogo de confluencias **bajistas** simétricas a A–H (p.ej. RSI overbought rejection, MACD bearish cross, ask pressure / sell wall, breakdown de soporte, alineación bajista cross-TF).
- Reglas de SL/TP invertidas para `SHORT`; régimen `TRENDING_DOWN` **incentiva** `SHORT` (y `TRENDING_UP` incentiva `BUY`).
- Nota de apalancamiento/funding: hoy `leverage=1x`; el LLM no controla leverage.

**`shared/confidence.py`:** `regime_factor` direccional — para `SHORT`, `TRENDING_DOWN` deja de ser 0.0 y pasa a factor alto; `TRENDING_UP` penaliza short (y viceversa para long). Confluencias "fuertes" reconocidas en ambas direcciones.

**`shared/position_sizing.py`:** `sl_distance_pct` según dirección:
- LONG: `(price − stop_loss) / price`.
- SHORT: `(stop_loss − price) / price`.

**`decisor_aggregate.py`:** rama de agregación para `SHORT` (mediana de `stop_loss`/`take_profit` con geometría invertida, `position_size_pct`, holding).

### 3.3 Risk Gate — reglas direccionales + futuros

Las reglas existentes se **generalizan por dirección**; se agregan reglas nuevas de futuros. El Risk Gate recibe `direction` (derivada de `action`), `leverage`, `liquidation_price`, `funding_rate`, `available_margin`.

| Regla | Comportamiento nuevo |
|------|----------------------|
| R0 | Sin cambios (drawdown / kill switch). Kill switch permite **cerrar** (SELL) long o short. |
| R1 | Sin cambios (`position_size_pct ≤ max_position_pct`). |
| R2 | Apertura requiere SL no nulo y **del lado correcto**: LONG `SL<precio`, SHORT `SL>precio`. |
| R3 | Apertura requiere TP no nulo y del lado correcto: LONG `TP>precio`, SHORT `TP<precio`. |
| R4 | Distancia SL en banda ATR con `sl_distance = |precio − SL|`. |
| R5 | R:R simétrico: `reward = |TP − precio|`, `risk = |precio − SL|`. |
| R6 | `SELL` requiere posición abierta (long **o** short). |
| R7 | **Reemplazada (R7-dir):** dirección de apertura debe ser coherente (`BUY→LONG`, `SHORT→SHORT`); valida que no haya posición opuesta abierta en one-way. |
| R8 | `open_positions < max_simultaneous_trades` (sin cambios). |
| R9 | Daily stop (sin cambios). |
| R10 | TP cubre fees round-trip (taker × 2) — fórmula con `|move|` direccional. |
| R11 | Notional mínimo Binance Futures (≥ 5 USDT / `minNotional` del símbolo). |
| **R12 (nueva)** | `leverage ≤ max_leverage` (default cap; `max_leverage` operator-only, excluido de `_SAFE_BOUNDS`). |
| **R13 (nueva)** | **Buffer de liquidación:** `liquidation_price` estimado debe estar más allá del SL por ≥ `liquidation_buffer_atr × ATR` (garantiza que el SL se toque antes que la liquidación). Con 1x el riesgo es bajo, pero la regla queda lista para cap > 1x. |
| **R14 (nueva)** | Margen disponible (initial + maintenance) suficiente para abrir el notional pedido. |
| **R15 (nueva)** | Funding: rechaza apertura si `|funding_rate| > funding_rate_max_pct` o si el funding esperado durante el holding erosiona el R:R por debajo de `min_rr_ratio`. |

**Cambio de firma crítico (`RiskGate.validate` + `main.py`):** hoy el gate recibe `usdt_balance` y `btc_held` (conceptos Spot) y el kill switch / R6 dependen de `btc_held > 0`. En futuros un short **no** tiene `btc_held` (posición negativa), por lo que esa lógica bloquearía cerrar un short. Se generaliza a una abstracción de posición:
- Reemplazar `btc_held: float` por `has_open_position: bool` + `open_position_side: Direction | None` (o reusar `open_positions_count` + side).
- Kill switch (R0): permite **cerrar** cualquier posición abierta (long → sell, short → buy reduceOnly), no solo `SELL` con `btc_held > 0`.
- R6: `SELL` válido si hay posición abierta de cualquier dirección.
- `usdt_balance` pasa a representar **margen disponible** (`available_margin`) en futures; el sizing/notional (R11) se calcula sobre esa base.
- `main.py` deja de derivar `btc` de balances spot y pasa el estado de posición del adapter.

`CoherenceChecker` C7 (y C1–C3 de confluencias) se vuelven direccionales. Confluencias bajistas tienen sus propias reglas espejo (C1'/C2'/C3').

### 3.4 Ejecución (Executor + brackets)

- `execute_buy` → **`execute_open(direction, decision, ...)`**:
  - Calcula `qty_btc = notional_usdt / price` (ya no `quoteOrderQty`) y lo **redondea a la precisión del símbolo** (`LOT_SIZE`/`stepSize`) vía `exchange.amount_to_precision(symbol, qty)`. En Spot se usaba `quoteOrderQty` y no hacía falta; en futuros la orden es por cantidad y debe respetar `stepSize` o Binance la rechaza.
  - `adapter.open_position(direction, notional, price)`:
    - LONG: market `buy`.
    - SHORT: market `sell`.
  - Persiste `Trade(side=<orden BUY|SELL>, position_side=<LONG|SHORT>, leverage, liquidation_price, margin_mode)` + `Position(position_side, liquidation_price, leverage)`.
  - `adapter.place_brackets(direction, qty, sl, tp)`:
    - SpotAdapter: OCO SELL (hoy).
    - FuturesAdapter: `STOP_MARKET` + `TAKE_PROFIT_MARKET` reduceOnly, lado opuesto, `workingType=MARK_PRICE`.
- `execute_sell` → **`execute_close(trade, close_reason)`**:
  - `adapter.close_position(direction, qty)` → reduceOnly market en lado contrario (long→sell, short→buy).
  - PnL direccional: LONG `(exit − entry) × qty`; SHORT `(entry − exit) × qty`, neto de fees + funding acumulado.
- `setup_symbol(leverage, margin_mode)` idempotente al iniciar / antes de abrir en futures.
- `force_close_trade` y `record_bracket_fill` se vuelven direccionales.

### 3.5 OrderTracker

- Detección de fills de cierre por dirección: LONG cierra con fill **sell**; SHORT con fill **buy** (reduceOnly).
- **Guardians invertidos:**
  - LONG: dispara SL si `price/low ≤ stop_loss`; TP si `price ≥ take_profit`.
  - SHORT: dispara SL si `price/high ≥ stop_loss`; TP si `price ≤ take_profit`.
- Monitoreo de `liquidation_price`: si el mark price se acerca a un umbral configurable, alerta (Telegram) y opcionalmente cierre defensivo.

### 3.6 PositionManager / PnL

- `refresh_unrealized` direccional: LONG `(current − entry) × qty`; SHORT `(entry − current) × qty`.
- `shared/pnl.py` (ya soporta short vía `side`) se alinea para consumir `position_side`.

### 3.6.bis Atribución de outcomes y calibración (alcance ampliado)

`agents/outcome_attribution.py` está modelado **íntegramente para LONG** y requiere más trabajo del estimado inicialmente:
- `_compute_mfe_mae`: MFE = máximo movimiento al alza (favorable). Para SHORT el favorable es **a la baja** → MFE/MAE deben invertirse según `position_side`.
- `_resolve_risk_thresholds` y `_absolute_bracket_levels`: asumen `sl < price < tp`; para SHORT hay que aceptar `tp < price < sl`.
- `_classify_executed_sell` / `_first_bracket_outcome`: PnL y toque de brackets con fórmula long.
- `Classification` (Literal): agregar `GOOD_SHORT`/`BAD_SHORT` (hoy solo `GOOD_BUY/BAD_BUY/GOOD_SELL/BAD_SELL`).
- `shared/confidence_calibration.py`: las métricas Brier/ECE se segmentan por dirección.
- `decision_outcomes` / `labelers.py`: clasificación direccional; el frontend `Health.tsx` muestra los nuevos labels.

Es un módulo puro con buena cobertura de tests → la inversión direccional es acotada pero **no trivial**; se trata como tarea propia en el plan.

### 3.7 Modelo de datos (migración Alembic 016)

`016_add_futures_fields.py`:
- `trades`: + `position_side` VARCHAR(5) NOT NULL DEFAULT `'LONG'`; `leverage` NUMERIC(5,2) DEFAULT 1; `liquidation_price` NUMERIC(18,8) NULL; `margin_mode` VARCHAR(10) DEFAULT `'isolated'`; `funding_paid_usdt` NUMERIC(18,4) NULL.
- `positions`: + `position_side` VARCHAR(5) DEFAULT `'LONG'`; `leverage` NUMERIC(5,2) DEFAULT 1; `liquidation_price` NUMERIC(18,8) NULL.
- `balance_snapshots`: + `margin_balance` NUMERIC(18,4) NULL; `available_margin` NUMERIC(18,4) NULL (para futures; null en spot).
- `trades.side` se mantiene como lado de la **orden** Binance (BUY/SELL); `quantity_btc` se mantiene (sigue en BTC).
- Idempotente; histórico queda `position_side='LONG'`. Compatible con SQLite (tests).

### 3.8 Frontend

- `types/index.ts`: `DecisorAction = "BUY"|"SHORT"|"SELL"|"HOLD"`; `Trade`/`Position` ganan `position_side`, `leverage`, `liquidation_price`. `OutcomeClassification` suma `GOOD_SHORT`/`BAD_SHORT`.
- `types/decisorOutput.ts`: campos de orden válidos para short.
- `lib/pnl.ts`: usar `position_side`; extraer helpers de distancia SL/TP direccionales (hoy inline en `Trades.tsx`).
- `Dashboard.tsx`: pasar dirección al PnL; badge LONG/SHORT en posiciones; mostrar leverage + precio de liquidación; balance con margen disponible.
- `Trades.tsx`: badges LONG/SHORT; distancias SL/TP y R:R direccionales; panel de orden para short; copy de cierre ("cerrar corto"); filtro por dirección; columna `position_side` en CSV.
- `Decisions.tsx`: acción `SHORT`; panel "Parámetros de orden" para short; `explainRejection()` con R12–R15.
- `PriceChart.tsx`: markers direccionales (SHORT → flecha abajo / `aboveBar`); líneas de liquidación; leyenda.
- `Config.tsx`: nuevas claves (`trading_product`, `max_leverage`, `margin_mode`, `funding_rate_max_pct`, `liquidation_buffer_atr`) en grupos de riesgo.
- `Health.tsx`: mostrar `GOOD_SELL`/`BAD_SELL` y nuevos `GOOD_SHORT`/`BAD_SHORT`.
- `api/client.ts`: tipos extendidos; endpoint de funding/positions futures si se expone.

### 3.9 Config nuevas claves (`ConfigKey` + seed migración)

| Key | Tipo | Default | Auto-apply Supervisor |
|-----|------|---------|------------------------|
| `trading_product` | string | `spot` | ❌ operator-only (default seguro: sin cambio de comportamiento hasta que el operador active `futures`) |
| `max_leverage` | int | `1` | ❌ operator-only (excluida de `_SAFE_BOUNDS`) |
| `margin_mode` | string | `isolated` | ❌ operator-only |
| `funding_rate_max_pct` | float | `0.05` | ✅ dentro de bounds |
| `liquidation_buffer_atr` | float | `2.0` | ❌ operator-only |
| `min_notional_usdt` | float | leído de `exchangeInfo` (no fijo) | ❌ derivado del símbolo; el operador no lo edita a mano |

> El guard de §3.0 valida `available_margin × max_position_pct × leverage ≥ min_notional_usdt` al activar futures.

### 3.10 Web API / contratos

- `GET /api/trades` y `/api/positions` devuelven `position_side`, `leverage`, `liquidation_price`.
- `GET /api/decisions` y stats por `rule_id` incluyen R12–R15 y action `SHORT`.
- `GET /api/balance` incluye `margin_balance`/`available_margin` cuando `trading_product=futures`.
- Outcome attribution clasifica shorts (`GOOD_SHORT`/`BAD_SHORT`).

---

## 4. Flujo end-to-end (futures, ejemplo SHORT)

```
Scheduler → main.decisor_tick
  ContextBuilder.build (incluye funding, liquidation, position_side)
  Decisor.decide → DecisorOutput(action=SHORT, SL>entry, TP<entry)
  CoherenceChecker (C7 direccional)
  position_sizing (sl_distance short)
  RiskGate.validate (R2/R3 short geometry, R12 leverage, R13 liq buffer, R14 margin, R15 funding)
  Executor.execute_open(SHORT):
     adapter.open_position → market SELL qty_btc
     persist Trade(side=SELL, position_side=SHORT, leverage=1, liquidation_price)
     adapter.place_brackets(SHORT) → STOP_MARKET(buy, reduceOnly) + TAKE_PROFIT_MARKET(buy, reduceOnly)
OrderTracker.poll_once (10s):
  guardians short (SL si price≥SL, TP si price≤TP); fill buy reduceOnly → cierre
  PnL = (entry − exit) × qty − fees − funding
```

---

## 5. Manejo de errores y resiliencia

- `setup_symbol` (set_leverage/margin) idempotente; si falla, no se abre y se cuenta como `exchange_failure` (circuit breaker).
- Funding/liquidación no disponibles (REST caído): se opera con valores neutros y R13/R15 fallan **cerradas** (rechazan apertura si no hay dato fiable), priorizando seguridad.
- Brackets reduceOnly que no se colocan → guardian del OrderTracker actúa como red de seguridad (igual que hoy).
- Cierre defensivo ante cercanía de liquidación documentado en `05-risk-and-safety.md`.
- `SpotAdapter` se mantiene funcional para rollback inmediato (`TRADING_PRODUCT=spot`).

---

## 6. Plan de rollout y gates

1. Implementar tras flag `TRADING_PRODUCT=spot` por defecto en dev (no cambia comportamiento).
2. Futures Testnet (`BINANCE_TESTNET=true`, `TRADING_PRODUCT=futures`, `max_leverage=1`): validar apertura/cierre de long y short, brackets reduceOnly, guardians, PnL, liquidación.
3. Gates §10 adaptados a perp: backtest 90 d y paper 4 semanas **en el mismo producto** (perp 1x), métricas Sharpe/DD/WR/PF.
4. Mainnet futures: keys con permiso de futures, **sin retiros**, IP restringida; capital inicial acotado.
5. Subir `max_leverage` (>1x) solo de forma deliberada por operador tras validar R13 con histórico real.

---

## 7. Testing

| Archivo | Casos nuevos |
|---------|--------------|
| `test_exchange.py` | `FuturesAdapter`: `defaultType=future`, símbolo `BTC/USDT:USDT`, sandbox, `set_leverage`/`set_margin_mode`. |
| `test_executor.py` | `execute_open(SHORT)` market sell; brackets STOP_MARKET/TAKE_PROFIT_MARKET reduceOnly; `execute_close` reduceOnly buy; PnL short; long sigue OK. |
| `test_order_tracker.py` | Guardians short (SL si sube, TP si baja); fill buy reduceOnly como cierre. |
| `test_position_manager.py` | Unrealized PnL direccional. |
| `test_risk_gate.py` | Geometría short R2/R3/R4/R5; R12 leverage; R13 liq buffer; R14 margin; R15 funding; R6 close short. |
| `test_coherence_checker.py` | C7 direccional; confluencias bajistas C1'/C2'/C3'. |
| `test_decisor.py` | `action=SHORT` válido; validación SL/TP invertidos; fallbacks. |
| `test_decisor_aggregate.py` | Agregación SHORT (mediana SL/TP invertidos). |
| `test_position_sizing.py` | `sl_distance_pct` short. |
| `test_confidence.py` | `regime_factor` direccional. |
| `test_context_builder.py` | Bloques con funding/liquidation/position_side. |

Determinismo: pytest + pytest-asyncio + freezegun. SQLite en memoria soporta migración 016 (campos nullable / con default).

---

## 8. Riesgos residuales (post-resolución)

Los gaps de la revisión de ingeniería quedaron resueltos en §11. Riesgos residuales que se aceptan o se monitorean:

- **Apalancamiento:** arrancar en 1x mantiene riesgo ≈ spot; R12 + cap operator-only evita amplificación accidental. A 1x la liquidación está muy lejos (~-100%), por lo que R13 pasa trivialmente; queda como red para cap > 1x.
- **Dependencia de `exchangeInfo`:** el sizing depende de leer `min_notional`/`stepSize` del símbolo; si `load_markets()` falla, el engine no abre (fail-safe) hasta tener los filtros.
- **Capital mínimo operativo:** a 1x el operador debe garantizar capital ≥ `min_notional / max_position_pct` (validado por el guard §3.0); por debajo, futures no se habilita.
- **Liquidación:** R13 garantiza SL antes que liquidación; el OrderTracker monitorea cercanía.
- **Funding:** R15 evita abrir en condiciones de funding adverso; el funding acumulado se descuenta del PnL.
- **Migración de datos:** histórico queda `LONG`; sin pérdida de auditoría.
- **Frontend `.js`:** editar solo `.tsx`/`.ts`; los `.js` son artefactos de `tsc -b`.
- **Compatibilidad spot:** `SpotAdapter` + flag permiten rollback sin redeploy de esquema.

---

## 9. Specs a actualizar

| Spec | Cambios |
|------|---------|
| `01-functional-spec.md` | Caso de uso "ganar en mercado bajista" (shorts); acciones `BUY/SHORT/SELL/HOLD`; producto Futures. |
| `02-technical-spec.md` | `ExchangeAdapter` (Spot/Futures); ejecución direccional; wiring `TRADING_PRODUCT`. |
| `03-data-model.md` | Migración 016: `position_side`, `leverage`, `liquidation_price`, `margin_mode`, `funding_paid_usdt`, campos de margen en `balance_snapshots`. |
| `04-api-contracts.md` | Campos nuevos en trades/positions/balance; action `SHORT`; rule_id R12–R15. |
| `05-risk-and-safety.md` | R12–R15; geometría direccional R2–R5/R10; kill switch ampliado (cerrar short); gates LIVE para perp. |
| `06-patterns.md` | Patrón `ExchangeAdapter`; brackets reduceOnly; helpers de geometría direccional. |
| `07-discrepancies-and-gaps.md` | Estado de la feature; gaps (hedge mode, multi-símbolo) marcados v2. |

---

## 10. Fuera de alcance (v2)

- Hedge mode (long y short simultáneos).
- Multi-símbolo / multi-instrumento.
- Apalancamiento dinámico decidido por el LLM.
- Arbitraje de basis perp vs spot.
- Auto-ajuste de `max_leverage` por el Supervisor.

---

## 11. Resolución de gaps (revisión de ingeniería 2026-06-02)

| # | Gap detectado | Resolución | Sección |
|---|---------------|------------|---------|
| E1 | `DecisorOutput` no puede validar geometría SL/TP en Pydantic (no tiene el precio) | Pydantic valida **solo presencia** (extender `_buy_requires_sl_and_tp` a SHORT); geometría vs precio queda en Risk Gate R2/R3 + C7 | §3.2 |
| E2 | `RiskGate.validate` usa `btc_held` (Spot); bloquearía cerrar shorts | Generalizar firma a `has_open_position` + `open_position_side`; `usdt_balance` → `available_margin`; kill switch y R6 cierran cualquier dirección | §3.3 |
| G3 | minNotional Futures vs sizing 1x: trades podrían caer siempre bajo el mínimo | (a) `min_notional` leído de `exchangeInfo` (no hardcode); (b) **guard de arranque** `available_margin × max_position_pct × leverage ≥ min_notional`, si no se cumple no se habilita futures; (c) fórmula de capital mínimo documentada | §3.0, §3.9 |
| G4 | `outcome_attribution.py` es long-biased (MFE/MAE, brackets, clasificación) | Thread `position_side` por `attribute()` y helpers; MFE/MAE como favorable/adverso según dirección; aceptar geometría short; agregar `GOOD_SHORT`/`BAD_SHORT`; calibración segmentada por dirección | §3.6.bis |
| G5 | Cantidad en futures requiere precisión `LOT_SIZE`/`stepSize` | `qty = amount_to_precision(symbol, notional/price)` en el executor antes de enviar la orden | §3.4 |
| G6 | Funding/liquidación "fail closed" podría frenar operación | Cachear último funding y degradar a neutro **solo** para R15; R13 (liquidación) se mantiene estricta | §5, §8 |
| D1 | Semántica de `SELL` para cerrar shorts (ejecuta BUY reduceOnly) | **Decisión:** mantener `SELL` como exit universal (mínima fricción, preserva analytics `GOOD_SELL/BAD_SELL`); documentar en el prompt que `SELL`=cerrar posición abierta (long→sell, short→buy reduceOnly). `CLOSE` queda descartado por churn de analytics/migración | §3.2 |

**Estado:** todos los gaps de la revisión quedaron resueltos a nivel de diseño. Pendiente único de operación: el operador fija el **capital objetivo** y `max_position_pct` para que el guard §3.0 sea satisfacible a 1x.
