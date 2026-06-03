# Futuros (shorts) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Habilitar ventas en corto (shorts) migrando el motor de Binance Spot a USDT-M Futures perpetuos (`BTC/USDT:USDT`), de forma simétrica por dirección (LONG/SHORT), con apalancamiento 1x al inicio y un perfil de riesgo controlado.

**Architecture:** Se introduce una abstracción `ExchangeAdapter` (`SpotAdapter`/`FuturesAdapter`) seleccionada por el flag `TRADING_PRODUCT`. El Decisor pasa a emitir `BUY/SHORT/SELL/HOLD`; toda la lógica downstream (Risk Gate, ejecución, PnL, atribución, frontend) se vuelve direccional. Spot se mantiene operativo para rollback.

**Tech Stack:** Python 3.12, CCXT 4.4.40 (`ccxt.async_support`), SQLAlchemy 2.0 + Alembic, Postgres 17, pytest + pytest-asyncio + freezegun, React 19 + Vite 6 + TypeScript 5.7 + Tailwind 4.

**Spec de referencia:** `docs/superpowers/specs/2026-06-02-futures-shorts-design.md`.

---

## Convenciones de ejecución

- **TDD estricto:** test que falla → mínima implementación → test pasa → commit. Tests con `# GIVEN / # WHEN / # THEN` (única excepción de comentarios permitida en este repo).
- **No comentar el código productivo** salvo intención no obvia.
- **Sin cambios de comportamiento por defecto:** `TRADING_PRODUCT=spot` inicial; todo el camino spot debe seguir verde.
- Correr la suite afectada tras cada tarea: `cd trading-engine && pytest tests/ -q`.
- Commits frecuentes, uno por tarea (o por step de commit indicado).

## Mapa de fases

| Fase | Entrega | Subsistema |
|------|---------|------------|
| **0** | Contratos compartidos: `Direction`, `DecisorAction.SHORT`, validación presencia | `shared/schemas.py` |
| **1** | Sizing y PnL direccional | `shared/position_sizing.py`, `shared/pnl.py` |
| **2** | `ExchangeAdapter` + `SpotAdapter` (refactor sin cambio de comportamiento) | `trading-engine/exchange.py`, nuevo `execution/exchange_adapter.py` |
| **3** | `FuturesAdapter` (open/close/brackets reduceOnly, leverage, funding) | `execution/exchange_adapter.py` |
| **4** | Migración 016 + modelos direccionales | `shared/db/models.py`, `alembic/versions/016_*` |
| **5** | Executor direccional (`execute_open`/`execute_close`) | `execution/executor.py` |
| **6** | OrderTracker direccional (guardians invertidos) | `execution/order_tracker.py`, `execution/position_manager.py` |
| **7** | Risk Gate direccional + R12–R15 + guard de sizing | `risk/risk_gate.py`, `risk/coherence_checker.py`, `main.py` |
| **8** | Decisor + agregación + prompt (acción SHORT, confluencias bajistas) | `agents/decisor.py`, `agents/decisor_aggregate.py`, `agents/context_builder.py`, `agents/prompts/decisor_system.txt`, `shared/confidence.py` |
| **9** | Atribución de outcomes direccional | `agents/outcome_attribution.py`, `shared/confidence_calibration.py` |
| **10** | Config + wiring + guard de arranque | `shared/config_store.py`, `config.py`, `main.py` |
| **11** | Web API (campos direccionales en contratos) | `web/api/*` |
| **12** | Frontend (tipos, PnL, badges, chart, config, health) | `frontend/src/**` |
| **13** | Specs + README | `docs/specs/*`, `README.md` |

Cada fase deja la suite verde. Las fases 0–2 no cambian comportamiento productivo (spot sigue igual). Futures recién opera tras fase 10.

---

## FASE 0 — Contratos compartidos

### Task 0.1: Enum `Direction` y acción `SHORT`

**Files:**
- Modify: `shared/schemas.py`
- Test: `trading-engine/tests/test_schemas.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# trading-engine/tests/test_schemas.py (añadir)
from shared.schemas import Direction, DecisorAction, direction_for_action


def test_direction_enum_values():
    # GIVEN/WHEN/THEN
    assert Direction.LONG.value == "LONG"
    assert Direction.SHORT.value == "SHORT"


def test_decisor_action_includes_short():
    assert DecisorAction.SHORT.value == "SHORT"


def test_direction_for_action_maps_entries():
    # GIVEN entradas, THEN dirección; cierres/hold => None
    assert direction_for_action(DecisorAction.BUY) == Direction.LONG
    assert direction_for_action(DecisorAction.SHORT) == Direction.SHORT
    assert direction_for_action(DecisorAction.SELL) is None
    assert direction_for_action(DecisorAction.HOLD) is None
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_schemas.py -k "direction or short" -v`
Expected: FAIL (`ImportError: cannot import name 'Direction'`).

- [ ] **Step 3: Implementación mínima**

```python
# shared/schemas.py
class DecisorAction(str, Enum):
    BUY = "BUY"
    SHORT = "SHORT"
    SELL = "SELL"
    HOLD = "HOLD"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


def direction_for_action(action: "DecisorAction") -> "Direction | None":
    if action == DecisorAction.BUY:
        return Direction.LONG
    if action == DecisorAction.SHORT:
        return Direction.SHORT
    return None
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/schemas.py trading-engine/tests/test_schemas.py
git commit -m "feat(schemas): add Direction enum and SHORT action"
```

### Task 0.2: Validación de presencia SL/TP para SHORT (E1)

**Files:**
- Modify: `shared/schemas.py` (`DecisorOutput`)
- Test: `trading-engine/tests/test_schemas.py`

- [ ] **Step 1: Test que falla**

```python
import pytest
from pydantic import ValidationError
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime


def _base(**kw):
    d = dict(regime=MarketRegime.TRENDING_DOWN, action=DecisorAction.SHORT,
             confidence_base=0.5, confidence=0.5, stop_loss=None,
             take_profit=None, position_size_pct=0.05, reasoning="x")
    d.update(kw)
    return d


def test_short_requires_sl_and_tp():
    # GIVEN SHORT sin SL/TP, THEN error
    with pytest.raises(ValidationError):
        DecisorOutput(**_base(stop_loss=None, take_profit=None))


def test_short_with_sl_tp_ok():
    out = DecisorOutput(**_base(stop_loss=101000.0, take_profit=99000.0))
    assert out.action == DecisorAction.SHORT
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_schemas.py -k short_requires -v`
Expected: FAIL (no se levanta ValidationError; hoy solo BUY exige SL/TP).

- [ ] **Step 3: Implementación**

```python
# shared/schemas.py — reemplazar _buy_requires_sl_and_tp
    @model_validator(mode="after")
    def _entry_requires_sl_and_tp(self) -> "DecisorOutput":
        if self.action in (DecisorAction.BUY, DecisorAction.SHORT):
            if self.stop_loss is None:
                raise ValueError(f"stop_loss is required when action={self.action.value}")
            if self.take_profit is None:
                raise ValueError(f"take_profit is required when action={self.action.value}")
        return self
```

> Nota: la geometría relativa al precio (SHORT: TP<precio<SL) NO se valida aquí porque `DecisorOutput` no tiene el precio; se valida en Risk Gate (Fase 7).

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/schemas.py trading-engine/tests/test_schemas.py
git commit -m "feat(schemas): require SL/TP presence for SHORT entries"
```

---

## FASE 1 — Sizing y PnL direccional

### Task 1.1: `apply_risk_based_sizing` direccional

**Files:**
- Modify: `shared/position_sizing.py`
- Test: `trading-engine/tests/test_position_sizing.py`

- [ ] **Step 1: Test que falla**

```python
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from shared.position_sizing import apply_risk_based_sizing


def _short(stop_loss, take_profit):
    return DecisorOutput(regime=MarketRegime.TRENDING_DOWN, action=DecisorAction.SHORT,
                         confidence_base=0.6, confidence=0.6, stop_loss=stop_loss,
                         take_profit=take_profit, position_size_pct=0.05, reasoning="x")


def test_sizing_short_uses_directional_sl_distance():
    # GIVEN short con SL 2% arriba del precio
    price = 100_000.0
    decision = _short(stop_loss=102_000.0, take_profit=96_000.0)
    # WHEN
    updated, meta = apply_risk_based_sizing(
        decision, price=price, capital_total=1000.0, usdt_available=1000.0,
        risk_per_trade_pct=0.005, max_position_pct=0.10,
        min_position_size=0.0, min_position_size_pct_notional=0.0)
    # THEN sl_distance_pct ≈ 0.02 y size = 0.005/0.02 = 0.25 -> cap 0.10
    assert meta is not None
    assert abs(meta["sl_distance_pct"] - 0.02) < 1e-6
    assert abs(updated.position_size_pct - 0.10) < 1e-9
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_position_sizing.py -k short -v`
Expected: FAIL (hoy `apply_risk_based_sizing` retorna sin tocar si `action != BUY`).

- [ ] **Step 3: Implementación**

```python
# shared/position_sizing.py
from shared.schemas import DecisorAction, DecisorOutput, Direction, direction_for_action

# ... dentro de apply_risk_based_sizing, reemplazar el early-return y el cálculo:
    direction = direction_for_action(decision.action)
    if direction is None:               # SELL / HOLD no dimensionan
        return decision, None
    if decision.stop_loss is None or price <= 0 or capital_total <= 0:
        return decision, None

    if direction == Direction.LONG:
        sl_distance_pct = (price - decision.stop_loss) / price
    else:
        sl_distance_pct = (decision.stop_loss - price) / price
    if sl_distance_pct <= 1e-9:
        return decision, None
```

(El resto de la función queda igual.)

- [ ] **Step 4: Correr y verificar que pasa (y que LONG sigue verde)**

Run: `cd trading-engine && pytest tests/test_position_sizing.py -v`
Expected: PASS (tests long existentes + nuevo short).

- [ ] **Step 5: Commit**

```bash
git add shared/position_sizing.py trading-engine/tests/test_position_sizing.py
git commit -m "feat(sizing): directional SL distance for SHORT entries"
```

### Task 1.2: PnL helper por dirección (alias `position_side`)

**Files:**
- Modify: `shared/pnl.py`
- Test: `trading-engine/tests/test_pnl.py` (crear si no existe)

> `shared/pnl.py` ya invierte cuando `side != "BUY"`. Se agrega una API explícita por dirección para que executor/position_manager no dependan del string de orden.

- [ ] **Step 1: Test que falla**

```python
from shared.pnl import compute_pnl_usdt_directional


def test_pnl_short_profit_when_price_drops():
    # GIVEN short entry 100k, exit 96k, qty 0.01 -> +40
    pnl = compute_pnl_usdt_directional(entry=100_000.0, quantity=0.01,
                                       exit_price=96_000.0, direction="SHORT")
    assert pnl == 40.0


def test_pnl_long_profit_when_price_rises():
    pnl = compute_pnl_usdt_directional(entry=100_000.0, quantity=0.01,
                                       exit_price=104_000.0, direction="LONG")
    assert pnl == 40.0
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_pnl.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implementación**

```python
# shared/pnl.py (añadir)
def compute_pnl_usdt_directional(*, entry: float, quantity: float,
                                 exit_price: float | None, direction: str) -> float | None:
    side = "BUY" if direction.upper() == "LONG" else "SELL"
    return compute_pnl_usdt(entry=entry, quantity=quantity, exit_price=exit_price, side=side)


def compute_pnl_pct_directional(*, entry: float, exit_price: float | None, direction: str) -> float | None:
    side = "BUY" if direction.upper() == "LONG" else "SELL"
    return compute_pnl_pct(entry=entry, exit_price=exit_price, side=side)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_pnl.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/pnl.py trading-engine/tests/test_pnl.py
git commit -m "feat(pnl): add directional PnL helpers"
```

---

## FASE 2 — `ExchangeAdapter` + `SpotAdapter` (refactor sin cambio de comportamiento)

### Task 2.1: Definir contrato `ExchangeAdapter` y dataclasses de resultado

**Files:**
- Create: `trading-engine/execution/exchange_adapter.py`
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla**

```python
from execution.exchange_adapter import (
    OpenResult, CloseResult, BracketResult, BalanceView, PositionView, ExchangeAdapter,
)


def test_dataclasses_exist():
    o = OpenResult(filled_qty=0.01, avg_price=100.0, order_id="1")
    assert o.filled_qty == 0.01
    b = BalanceView(available=100.0, total=100.0, position_qty=0.0)
    assert b.available == 100.0


def test_adapter_is_protocol():
    assert hasattr(ExchangeAdapter, "open_position")
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementación**

```python
# trading-engine/execution/exchange_adapter.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from shared.schemas import Direction


@dataclass(frozen=True)
class OpenResult:
    filled_qty: float
    avg_price: float
    order_id: str


@dataclass(frozen=True)
class CloseResult:
    filled_qty: float
    avg_price: float
    order_id: str


@dataclass(frozen=True)
class BracketResult:
    order_id_sl: str | None
    order_id_tp: str | None


@dataclass(frozen=True)
class BalanceView:
    available: float          # USDT libre (spot) o margen disponible (futures)
    total: float
    position_qty: float       # BTC en cartera (spot) o |qty| de la posición (futures)


@dataclass(frozen=True)
class PositionView:
    symbol: str
    direction: Direction | None
    qty: float
    entry_price: float
    liquidation_price: float | None
    leverage: float


@runtime_checkable
class ExchangeAdapter(Protocol):
    product: str

    def build_client(self) -> Any: ...
    async def setup_symbol(self, symbol: str, *, leverage: int, margin_mode: str) -> None: ...
    async def open_position(self, *, symbol: str, direction: Direction,
                            notional_usdt: float, price: float) -> OpenResult: ...
    async def close_position(self, *, symbol: str, direction: Direction,
                             qty: float, close_reason: str) -> CloseResult: ...
    async def place_brackets(self, *, symbol: str, direction: Direction, qty: float,
                             stop_loss: float | None, take_profit: float | None) -> BracketResult: ...
    async def fetch_balance(self) -> BalanceView: ...
    async def fetch_positions(self) -> list[PositionView]: ...
    async def fetch_funding_rate(self, symbol: str) -> float: ...
    def min_notional(self, symbol: str) -> float: ...
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): define ExchangeAdapter protocol and result types"
```

### Task 2.2: `SpotAdapter` envolviendo el comportamiento actual

**Files:**
- Modify: `trading-engine/execution/exchange_adapter.py`
- Modify: `trading-engine/exchange.py` (reusar `build_binance_client`)
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla** (con cliente CCXT fake)

```python
import pytest
from execution.exchange_adapter import SpotAdapter, BalanceView


class _FakeSpot:
    options = {"defaultType": "spot"}
    markets = {"BTC/USDT": {"limits": {"cost": {"min": 5.0}}}}
    async def create_market_order(self, symbol, side, amount, params=None):
        return {"id": "o1", "filled": amount or 0.001, "average": 100000.0}
    async def fetch_balance(self):
        return {"USDT": {"free": 500.0, "total": 500.0}, "BTC": {"free": 0.0}}


@pytest.mark.asyncio
async def test_spot_adapter_open_long_uses_quote_order_qty():
    # GIVEN
    a = SpotAdapter(client=_FakeSpot())
    from shared.schemas import Direction
    # WHEN
    res = await a.open_position(symbol="BTC/USDT", direction=Direction.LONG,
                                notional_usdt=50.0, price=100000.0)
    # THEN
    assert res.order_id == "o1"
    assert res.avg_price == 100000.0


@pytest.mark.asyncio
async def test_spot_min_notional_from_markets():
    a = SpotAdapter(client=_FakeSpot())
    assert a.min_notional("BTC/USDT") == 5.0
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -k spot -v`
Expected: FAIL (`ImportError: SpotAdapter`).

- [ ] **Step 3: Implementación**

```python
# trading-engine/execution/exchange_adapter.py (añadir)
from exchange import build_binance_client


class SpotAdapter:
    product = "spot"

    def __init__(self, client: Any | None = None):
        self._client = client

    def build_client(self) -> Any:
        if self._client is None:
            self._client = build_binance_client()
        return self._client

    async def setup_symbol(self, symbol: str, *, leverage: int, margin_mode: str) -> None:
        return None  # spot no usa leverage/margin

    async def open_position(self, *, symbol, direction, notional_usdt, price) -> OpenResult:
        # LONG = BUY por quoteOrderQty (gasta USDT). Spot no soporta SHORT.
        if direction != Direction.LONG:
            raise ValueError("SpotAdapter only supports LONG (BUY)")
        order = await self._client.create_market_order(
            symbol, "buy", None, params={"quoteOrderQty": notional_usdt})
        return OpenResult(filled_qty=float(order.get("filled") or 0.0),
                          avg_price=float(order.get("average") or price),
                          order_id=str(order["id"]))

    async def close_position(self, *, symbol, direction, qty, close_reason) -> CloseResult:
        order = await self._client.create_market_order(symbol, "sell", qty)
        return CloseResult(filled_qty=float(order.get("filled") or qty),
                           avg_price=float(order.get("average") or 0.0),
                           order_id=str(order["id"]))

    async def place_brackets(self, *, symbol, direction, qty, stop_loss, take_profit) -> BracketResult:
        # Mantener la lógica OCO/SL/TP actual del Executor; ver Fase 5.
        raise NotImplementedError("brackets se delegan al Executor en Fase 5")

    async def fetch_balance(self) -> BalanceView:
        bal = await self._client.fetch_balance()
        usdt = bal.get("USDT", {})
        btc = bal.get("BTC", {})
        return BalanceView(available=float(usdt.get("free") or 0.0),
                           total=float(usdt.get("total") or 0.0),
                           position_qty=float(btc.get("free") or 0.0))

    async def fetch_positions(self) -> list[PositionView]:
        return []  # spot: las posiciones viven en la tabla `positions`

    async def fetch_funding_rate(self, symbol: str) -> float:
        return 0.0

    def min_notional(self, symbol: str) -> float:
        try:
            return float(self._client.markets[symbol]["limits"]["cost"]["min"])
        except (KeyError, TypeError, AttributeError):
            return 5.0
```

> Decisión: en Fase 2 los brackets siguen en el Executor (no se mueven todavía) para no cambiar comportamiento. En Fase 5 el Executor pasa a usar `adapter.place_brackets`.

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): SpotAdapter wrapping current spot behavior"
```

---

## FASE 3 — `FuturesAdapter`

### Task 3.1: `FuturesAdapter.build_client` (defaultType future + sandbox)

**Files:**
- Modify: `trading-engine/execution/exchange_adapter.py`
- Modify: `trading-engine/exchange.py` (factory parametrizable por producto)
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla**

```python
def test_futures_client_uses_future_default_type(monkeypatch):
    from execution.exchange_adapter import FuturesAdapter
    a = FuturesAdapter()
    client = a.build_client()
    assert client.options.get("defaultType") == "future"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -k futures_client -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# trading-engine/exchange.py — generalizar la factory
def build_binance_client(*, default_type: str = "spot"):
    s = get_settings()
    client = ccxt_async.binance({
        "apiKey": s.binance_api_key,
        "secret": s.binance_api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": default_type, "fetchCurrencies": False},
    })
    if s.binance_testnet:
        client.set_sandbox_mode(True)
    return client
```

```python
# trading-engine/execution/exchange_adapter.py (añadir)
class FuturesAdapter:
    product = "futures"

    def __init__(self, client: Any | None = None):
        self._client = client

    def build_client(self) -> Any:
        if self._client is None:
            self._client = build_binance_client(default_type="future")
        return self._client
```

> Verificar que `test_exchange.py` siga verde: el default `spot` no cambia.

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py tests/test_exchange.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/exchange.py trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): FuturesAdapter client with future defaultType"
```

### Task 3.2: `FuturesAdapter.open_position` (market buy/sell + precisión qty)

**Files:**
- Modify: `trading-engine/execution/exchange_adapter.py`
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla**

```python
import pytest


class _FakeFut:
    options = {"defaultType": "future"}
    markets = {"BTC/USDT:USDT": {"limits": {"cost": {"min": 100.0}}}}
    def amount_to_precision(self, symbol, amount):
        return round(amount, 3)
    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.last = dict(symbol=symbol, type=type_, side=side, amount=amount, params=params or {})
        return {"id": "f1", "filled": amount, "average": 100000.0}


@pytest.mark.asyncio
async def test_futures_open_short_is_market_sell_rounded():
    from execution.exchange_adapter import FuturesAdapter
    from shared.schemas import Direction
    fake = _FakeFut()
    a = FuturesAdapter(client=fake)
    # GIVEN notional 200 a precio 100k -> qty 0.002
    res = await a.open_position(symbol="BTC/USDT:USDT", direction=Direction.SHORT,
                                notional_usdt=200.0, price=100000.0)
    # THEN orden market SELL, qty redondeada a precisión
    assert fake.last["side"] == "sell"
    assert fake.last["type"] == "market"
    assert res.filled_qty == 0.002


@pytest.mark.asyncio
async def test_futures_min_notional_from_markets():
    from execution.exchange_adapter import FuturesAdapter
    a = FuturesAdapter(client=_FakeFut())
    assert a.min_notional("BTC/USDT:USDT") == 100.0
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -k "futures_open or futures_min" -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# trading-engine/execution/exchange_adapter.py — métodos de FuturesAdapter
    async def open_position(self, *, symbol, direction, notional_usdt, price) -> OpenResult:
        side = "buy" if direction == Direction.LONG else "sell"
        qty = self._client.amount_to_precision(symbol, notional_usdt / price)
        qty = float(qty)
        order = await self._client.create_order(symbol, "market", side, qty)
        return OpenResult(filled_qty=float(order.get("filled") or qty),
                          avg_price=float(order.get("average") or price),
                          order_id=str(order["id"]))

    def min_notional(self, symbol: str) -> float:
        try:
            return float(self._client.markets[symbol]["limits"]["cost"]["min"])
        except (KeyError, TypeError, AttributeError):
            return 100.0
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): futures open_position market order with qty precision"
```

### Task 3.3: `FuturesAdapter.close_position` (reduceOnly lado contrario)

**Files:**
- Modify: `trading-engine/execution/exchange_adapter.py`
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla**

```python
@pytest.mark.asyncio
async def test_futures_close_short_is_reduceonly_buy():
    from execution.exchange_adapter import FuturesAdapter
    from shared.schemas import Direction
    fake = _FakeFut()
    a = FuturesAdapter(client=fake)
    await a.close_position(symbol="BTC/USDT:USDT", direction=Direction.SHORT,
                           qty=0.002, close_reason="decisor_sell")
    assert fake.last["side"] == "buy"
    assert fake.last["params"].get("reduceOnly") is True
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -k close_short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# FuturesAdapter
    async def close_position(self, *, symbol, direction, qty, close_reason) -> CloseResult:
        side = "sell" if direction == Direction.LONG else "buy"
        qty = float(self._client.amount_to_precision(symbol, qty))
        order = await self._client.create_order(
            symbol, "market", side, qty, None, {"reduceOnly": True})
        return CloseResult(filled_qty=float(order.get("filled") or qty),
                           avg_price=float(order.get("average") or 0.0),
                           order_id=str(order["id"]))
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): futures close_position reduceOnly opposite side"
```

### Task 3.4: `place_brackets` futures (STOP_MARKET + TAKE_PROFIT_MARKET reduceOnly)

**Files:**
- Modify: `trading-engine/execution/exchange_adapter.py`
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla**

```python
class _FakeFutBrackets(_FakeFut):
    def __init__(self):
        self.orders = []
    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.orders.append(dict(type=type_, side=side, params=params or {}))
        return {"id": f"id{len(self.orders)}"}


@pytest.mark.asyncio
async def test_futures_brackets_for_short_are_buy_reduceonly():
    from execution.exchange_adapter import FuturesAdapter
    from shared.schemas import Direction
    fake = _FakeFutBrackets()
    a = FuturesAdapter(client=fake)
    res = await a.place_brackets(symbol="BTC/USDT:USDT", direction=Direction.SHORT,
                                 qty=0.002, stop_loss=102000.0, take_profit=96000.0)
    types = {o["type"] for o in fake.orders}
    assert types == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert all(o["side"] == "buy" for o in fake.orders)
    assert all(o["params"].get("reduceOnly") is True for o in fake.orders)
    assert all(o["params"].get("workingType") == "MARK_PRICE" for o in fake.orders)
    assert res.order_id_sl and res.order_id_tp
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -k brackets_for_short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# FuturesAdapter
    async def place_brackets(self, *, symbol, direction, qty, stop_loss, take_profit) -> BracketResult:
        close_side = "sell" if direction == Direction.LONG else "buy"
        qty = float(self._client.amount_to_precision(symbol, qty))
        sl_id = tp_id = None
        if stop_loss is not None:
            o = await self._client.create_order(
                symbol, "STOP_MARKET", close_side, qty, None,
                {"stopPrice": stop_loss, "reduceOnly": True, "workingType": "MARK_PRICE"})
            sl_id = str(o["id"])
        if take_profit is not None:
            o = await self._client.create_order(
                symbol, "TAKE_PROFIT_MARKET", close_side, qty, None,
                {"stopPrice": take_profit, "reduceOnly": True, "workingType": "MARK_PRICE"})
            tp_id = str(o["id"])
        return BracketResult(order_id_sl=sl_id, order_id_tp=tp_id)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): futures brackets STOP_MARKET/TAKE_PROFIT_MARKET reduceOnly"
```

### Task 3.5: `setup_symbol`, `fetch_balance`, `fetch_positions`, `fetch_funding_rate`

**Files:**
- Modify: `trading-engine/execution/exchange_adapter.py`
- Test: `trading-engine/tests/test_exchange_adapter.py`

- [ ] **Step 1: Test que falla**

```python
class _FakeFutFull(_FakeFut):
    def __init__(self):
        self.calls = []
    async def set_leverage(self, lev, symbol): self.calls.append(("lev", lev, symbol))
    async def set_margin_mode(self, mode, symbol): self.calls.append(("margin", mode, symbol))
    async def fetch_balance(self):
        return {"USDT": {"free": 300.0, "total": 320.0}}
    async def fetch_positions(self, symbols=None):
        return [{"symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.002,
                 "entryPrice": 100000.0, "liquidationPrice": 150000.0, "leverage": 1}]
    async def fetch_funding_rate(self, symbol):
        return {"fundingRate": 0.0001}


@pytest.mark.asyncio
async def test_futures_setup_and_views():
    from execution.exchange_adapter import FuturesAdapter
    from shared.schemas import Direction
    fake = _FakeFutFull()
    a = FuturesAdapter(client=fake)
    await a.setup_symbol("BTC/USDT:USDT", leverage=1, margin_mode="isolated")
    assert ("lev", 1, "BTC/USDT:USDT") in fake.calls
    assert ("margin", "isolated", "BTC/USDT:USDT") in fake.calls
    bal = await a.fetch_balance()
    assert bal.available == 300.0
    pos = await a.fetch_positions()
    assert pos[0].direction == Direction.SHORT
    assert pos[0].liquidation_price == 150000.0
    assert await a.fetch_funding_rate("BTC/USDT:USDT") == 0.0001
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -k setup_and_views -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# FuturesAdapter
    async def setup_symbol(self, symbol, *, leverage, margin_mode) -> None:
        try:
            await self._client.set_margin_mode(margin_mode, symbol)
        except Exception:
            pass  # idempotente: Binance lanza si ya está en ese modo
        await self._client.set_leverage(leverage, symbol)

    async def fetch_balance(self) -> BalanceView:
        bal = await self._client.fetch_balance()
        usdt = bal.get("USDT", {})
        return BalanceView(available=float(usdt.get("free") or 0.0),
                           total=float(usdt.get("total") or 0.0),
                           position_qty=0.0)

    async def fetch_positions(self) -> list[PositionView]:
        raw = await self._client.fetch_positions([])
        out: list[PositionView] = []
        for p in raw:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0.0:
                continue
            side = (p.get("side") or "").lower()
            direction = Direction.LONG if side == "long" else Direction.SHORT
            out.append(PositionView(
                symbol=p.get("symbol", ""), direction=direction, qty=contracts,
                entry_price=float(p.get("entryPrice") or 0.0),
                liquidation_price=(float(p["liquidationPrice"]) if p.get("liquidationPrice") else None),
                leverage=float(p.get("leverage") or 1.0)))
        return out

    async def fetch_funding_rate(self, symbol: str) -> float:
        fr = await self._client.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate") or 0.0)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_exchange_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/exchange_adapter.py trading-engine/tests/test_exchange_adapter.py
git commit -m "feat(adapter): futures setup_symbol, balance, positions, funding views"
```

---

## FASE 4 — Migración 016 + modelos direccionales

### Task 4.1: Campos direccionales en modelos SQLAlchemy

**Files:**
- Modify: `shared/db/models.py` (`Trade`, `Position`, `BalanceSnapshot`)
- Test: `trading-engine/tests/test_models.py`

- [ ] **Step 1: Test que falla**

```python
def test_trade_has_directional_fields():
    from shared.db.models import Trade
    cols = Trade.__table__.columns.keys()
    assert "position_side" in cols
    assert "leverage" in cols
    assert "liquidation_price" in cols
    assert "margin_mode" in cols


def test_position_has_directional_fields():
    from shared.db.models import Position
    cols = Position.__table__.columns.keys()
    assert "position_side" in cols
    assert "liquidation_price" in cols
    assert "leverage" in cols
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_models.py -k directional -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# shared/db/models.py — en Trade
    position_side = Column(String(5), nullable=False, server_default="LONG")
    leverage = Column(Numeric(5, 2), nullable=True, server_default="1")
    liquidation_price = Column(Numeric(18, 8), nullable=True)
    margin_mode = Column(String(10), nullable=True, server_default="isolated")
    funding_paid_usdt = Column(Numeric(18, 4), nullable=True)

# en Position
    position_side = Column(String(5), nullable=True, server_default="LONG")
    leverage = Column(Numeric(5, 2), nullable=True, server_default="1")
    liquidation_price = Column(Numeric(18, 8), nullable=True)

# en BalanceSnapshot
    margin_balance = Column(Numeric(18, 4), nullable=True)
    available_margin = Column(Numeric(18, 4), nullable=True)
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_models.py -v`
Expected: PASS (SQLite `create_all` toma los nuevos campos).

- [ ] **Step 5: Commit**

```bash
git add shared/db/models.py trading-engine/tests/test_models.py
git commit -m "feat(models): directional fields on trades/positions/balance_snapshots"
```

### Task 4.2: Migración Alembic 016

**Files:**
- Create: `trading-engine/alembic/versions/016_add_futures_fields.py`
- Test: manual (`alembic upgrade head` en Postgres dev)

- [ ] **Step 1: Escribir la migración**

```python
# trading-engine/alembic/versions/016_add_futures_fields.py
"""add futures directional fields"""
from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("position_side", sa.String(5), nullable=False, server_default="LONG"))
    op.add_column("trades", sa.Column("leverage", sa.Numeric(5, 2), nullable=True, server_default="1"))
    op.add_column("trades", sa.Column("liquidation_price", sa.Numeric(18, 8), nullable=True))
    op.add_column("trades", sa.Column("margin_mode", sa.String(10), nullable=True, server_default="isolated"))
    op.add_column("trades", sa.Column("funding_paid_usdt", sa.Numeric(18, 4), nullable=True))
    op.add_column("positions", sa.Column("position_side", sa.String(5), nullable=True, server_default="LONG"))
    op.add_column("positions", sa.Column("leverage", sa.Numeric(5, 2), nullable=True, server_default="1"))
    op.add_column("positions", sa.Column("liquidation_price", sa.Numeric(18, 8), nullable=True))
    op.add_column("balance_snapshots", sa.Column("margin_balance", sa.Numeric(18, 4), nullable=True))
    op.add_column("balance_snapshots", sa.Column("available_margin", sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    for col in ("funding_paid_usdt", "margin_mode", "liquidation_price", "leverage", "position_side"):
        op.drop_column("trades", col)
    for col in ("liquidation_price", "leverage", "position_side"):
        op.drop_column("positions", col)
    for col in ("available_margin", "margin_balance"):
        op.drop_column("balance_snapshots", col)
```

- [ ] **Step 2: Verificar upgrade/downgrade en dev**

Run: `cd trading-engine && alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: sin errores; `alembic current` muestra `016`.

- [ ] **Step 3: Commit**

```bash
git add trading-engine/alembic/versions/016_add_futures_fields.py
git commit -m "feat(db): migration 016 adds futures directional fields"
```

---

## FASE 5 — Executor direccional

### Task 5.1: `execute_open(direction)` usando el adapter

**Files:**
- Modify: `trading-engine/execution/executor.py`
- Test: `trading-engine/tests/test_executor.py`

- [ ] **Step 1: Test que falla** (short con adapter fake)

```python
import pytest
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime, Direction


def _short_decision():
    return DecisorOutput(regime=MarketRegime.TRENDING_DOWN, action=DecisorAction.SHORT,
                         confidence_base=0.6, confidence=0.6, stop_loss=102000.0,
                         take_profit=96000.0, position_size_pct=0.10, reasoning="x")


@pytest.mark.asyncio
async def test_execute_open_short_persists_short_trade(db_session, fake_futures_adapter):
    # GIVEN executor con adapter futures fake y margen 1000
    from execution.executor import Executor
    ex = Executor(fake_futures_adapter, db_session, symbol="BTC/USDT:USDT")
    # WHEN
    trade = await ex.execute_open(direction=Direction.SHORT, decision=_short_decision(),
                                  decision_id=__import__("uuid").uuid4(), available_margin=1000.0,
                                  price=100000.0)
    # THEN
    assert trade.position_side == "SHORT"
    assert trade.side == "SELL"
    assert trade.stop_loss == 102000.0
```

> `fake_futures_adapter` es una fixture en `conftest.py` que implementa `ExchangeAdapter` con `open_position`/`place_brackets` registrando llamadas.

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_executor.py -k execute_open_short -v`
Expected: FAIL (`AttributeError: execute_open`).

- [ ] **Step 3: Implementación**

```python
# execution/executor.py — nuevo método (mantener execute_buy como wrapper LONG por compat)
from datetime import datetime, timezone
from decimal import Decimal
from shared.schemas import Direction, direction_for_action

    async def execute_open(self, *, direction, decision, decision_id, available_margin, price) -> Trade:
        notional = available_margin * decision.position_size_pct
        res = await self._adapter.open_position(
            symbol=self.symbol, direction=direction, notional_usdt=notional, price=price)
        if res.avg_price == 0 or res.filled_qty == 0:
            raise RuntimeError(f"open zero fill: {res}")
        order_side = "BUY" if direction == Direction.LONG else "SELL"
        # Persistir Trade + Position ANTES de los brackets (igual que execute_buy actual).
        trade = Trade(
            decision_id=decision_id, ts_open=datetime.now(tz=timezone.utc),
            side=order_side, position_side=direction.value,
            quantity_btc=Decimal(str(res.filled_qty)), entry_price=Decimal(str(res.avg_price)),
            status="open",
            stop_loss=Decimal(str(decision.stop_loss)) if decision.stop_loss else None,
            take_profit=Decimal(str(decision.take_profit)) if decision.take_profit else None,
            order_id_open=res.order_id, leverage=Decimal("1"), margin_mode="isolated")
        self.session.add(trade)
        await self.session.flush()
        # Position espejo — SOLO columnas que existen en el modelo (sin stop_loss/take_profit).
        self.session.add(Position(
            trade_id=trade.id, symbol=self.symbol,
            quantity_btc=trade.quantity_btc, entry_price=trade.entry_price,
            position_side=direction.value, status="open", opened_at=trade.ts_open))
        d = await self.session.get(Decision, decision_id)
        if d is not None:
            d.executed = True
            d.trade_id = trade.id
        await self.session.commit()
        # Brackets vía adapter (SpotAdapter=OCO; FuturesAdapter=STOP_MARKET/TAKE_PROFIT_MARKET)
        brackets = await self._adapter.place_brackets(
            symbol=self.symbol, direction=direction, qty=res.filled_qty,
            stop_loss=decision.stop_loss, take_profit=decision.take_profit)
        if brackets.order_id_sl or brackets.order_id_tp:
            await self.session.refresh(trade)
            trade.order_id_sl = brackets.order_id_sl
            trade.order_id_tp = brackets.order_id_tp
            await self.session.commit()
        await self.session.refresh(trade)
        return trade
```

> **Refactor del constructor:** `def __init__(self, adapter, session, *, symbol)` y reemplazar **todas** las referencias internas `self.exchange` por `self._adapter` (en `execute_sell`, `_place_oco_bracket`, `_place_sl_bracket`, `_place_tp_bracket`, `force_close_trade`, `record_bracket_fill`). La lógica OCO actual (`_place_oco_bracket` y sus helpers) **se mueve a `SpotAdapter.place_brackets`** para encapsular el detalle de exchange; el `FuturesAdapter.place_brackets` ya existe (Task 3.4). Es un refactor mecánico pero amplio: validar con `tests/test_executor.py` completo. Para no romper Spot, los tests legacy se adaptan a pasar un adapter (Task 5.3).

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_executor.py -k execute_open_short -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/executor.py trading-engine/tests/test_executor.py trading-engine/tests/conftest.py
git commit -m "feat(executor): execute_open with directional adapter and brackets"
```

### Task 5.2: `execute_close` direccional con PnL por dirección

**Files:**
- Modify: `trading-engine/execution/executor.py`
- Test: `trading-engine/tests/test_executor.py`

- [ ] **Step 1: Test que falla**

```python
@pytest.mark.asyncio
async def test_execute_close_short_pnl_positive_when_price_drops(db_session, fake_futures_adapter):
    from execution.executor import Executor
    from shared.schemas import Direction
    ex = Executor(fake_futures_adapter, db_session, symbol="BTC/USDT:USDT")
    trade = await ex.execute_open(direction=Direction.SHORT, decision=_short_decision(),
                                  decision_id=__import__("uuid").uuid4(),
                                  available_margin=1000.0, price=100000.0)
    fake_futures_adapter.next_close_price = 96000.0
    closed = await ex.execute_close(trade_id=trade.id, decision_id=None, close_reason="decisor_sell")
    assert closed.status == "closed"
    assert float(closed.pnl_usdt) > 0  # short gana si el precio baja
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_executor.py -k execute_close_short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# execution/executor.py
from shared.pnl import compute_pnl_usdt_directional, compute_pnl_pct_directional

    async def execute_close(self, *, trade_id, decision_id, close_reason) -> Trade:
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise RuntimeError("trade not open")
        direction = Direction(trade.position_side)
        res = await self._adapter.close_position(
            symbol=self.symbol, direction=direction,
            qty=float(trade.quantity_btc), close_reason=close_reason)
        exit_price = res.avg_price
        trade.exit_price = exit_price
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.order_id_close = res.order_id
        trade.pnl_usdt = compute_pnl_usdt_directional(
            entry=float(trade.entry_price), quantity=float(trade.quantity_btc),
            exit_price=exit_price, direction=trade.position_side)
        trade.pnl_pct = compute_pnl_pct_directional(
            entry=float(trade.entry_price), exit_price=exit_price, direction=trade.position_side)
        # marcar Position espejo como closed (igual que hoy)
        await self.session.commit()
        return trade
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_executor.py -k execute_close_short -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/executor.py trading-engine/tests/test_executor.py
git commit -m "feat(executor): execute_close with directional PnL"
```

### Task 5.3: Compatibilidad Spot (`execute_buy`/`execute_sell` como wrappers) y tests legacy

**Files:**
- Modify: `trading-engine/execution/executor.py`
- Modify: `trading-engine/tests/test_executor.py` (adaptar a adapter)
- Test: `trading-engine/tests/test_executor.py`

- [ ] **Step 1: Test que falla** (los tests legacy de spot deben pasar vía adapter)

Adaptar los tests existentes de `execute_buy` para construir `Executor(SpotAdapter(client=fake_spot), ...)` y verificar que abren LONG con OCO. Mantener un test:

```python
@pytest.mark.asyncio
async def test_execute_buy_long_still_works_via_spot_adapter(db_session, fake_spot_adapter):
    from execution.executor import Executor
    ex = Executor(fake_spot_adapter, db_session, symbol="BTC/USDT")
    trade = await ex.execute_buy(decision=_long_decision(), decision_id=__import__("uuid").uuid4(),
                                 usdt_balance=1000.0)
    assert trade.position_side == "LONG"
    assert trade.side == "BUY"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_executor.py -k long_still_works -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (wrappers de compat)

```python
# execution/executor.py
    async def execute_buy(self, *, decision, decision_id, usdt_balance) -> Trade:
        return await self.execute_open(direction=Direction.LONG, decision=decision,
                                       decision_id=decision_id, available_margin=usdt_balance,
                                       price=decision.stop_loss and 0.0 or 0.0) \
            if False else await self._execute_buy_spot(decision, decision_id, usdt_balance)
```

> Para Spot el precio de entrada lo determina el market BUY por `quoteOrderQty`; se mantiene `_execute_buy_spot` con la lógica OCO actual (mover el cuerpo de `execute_buy` actual a `_execute_buy_spot`, usando `self._adapter` para la orden y los brackets OCO del `SpotAdapter.place_brackets`). `execute_sell` delega en `execute_close`.

- [ ] **Step 4: Correr y verificar que pasa (toda la suite de executor + order_tracker)**

Run: `cd trading-engine && pytest tests/test_executor.py tests/test_order_tracker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/executor.py trading-engine/tests/test_executor.py
git commit -m "refactor(executor): spot paths via adapter, keep behavior"
```

---

## FASE 6 — OrderTracker + PositionManager direccionales

### Task 6.1: `PositionManager.refresh_unrealized` direccional

**Files:**
- Modify: `trading-engine/execution/position_manager.py`
- Test: `trading-engine/tests/test_position_manager.py`

- [ ] **Step 1: Test que falla**

```python
@pytest.mark.asyncio
async def test_refresh_unrealized_short(db_session):
    # GIVEN posición SHORT entry 100k, precio actual 98k -> unrealized > 0
    ...  # crear Position(position_side="SHORT", entry_price=100000, quantity_btc=0.01)
    from execution.position_manager import PositionManager
    pm = PositionManager(db_session)
    await pm.refresh_unrealized(current_price=98000.0)
    pos = (await pm.list_open())[0]
    assert float(pos.unrealized_pnl) > 0
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_position_manager.py -k short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# execution/position_manager.py — dentro del for de refresh_unrealized (mantener el patrón Decimal/updated_at)
        p.current_price = Decimal(str(current_price))
        entry = float(p.entry_price)
        qty = float(p.quantity_btc)
        side = getattr(p, "position_side", "LONG") or "LONG"
        if side == "LONG":
            pnl = (current_price - entry) * qty
            pct = (current_price - entry) / entry * 100 if entry > 0 else 0
        else:  # SHORT
            pnl = (entry - current_price) * qty
            pct = (entry - current_price) / entry * 100 if entry > 0 else 0
        p.unrealized_pnl = Decimal(str(round(pnl, 4)))
        p.unrealized_pct = Decimal(str(round(pct, 4)))
        p.updated_at = now
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_position_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/position_manager.py trading-engine/tests/test_position_manager.py
git commit -m "feat(positions): directional unrealized PnL"
```

### Task 6.2: Guardians invertidos en OrderTracker

**Files:**
- Modify: `trading-engine/execution/order_tracker.py`
- Test: `trading-engine/tests/test_order_tracker.py`

- [ ] **Step 1: Test que falla**

```python
@pytest.mark.asyncio
async def test_short_sl_guardian_triggers_when_price_rises(db_session, fake_futures_adapter):
    # GIVEN short SL=102k; precio sube a 102.5k -> guardian cierra
    ...  # crear trade SHORT abierto con stop_loss=102000
    from execution.order_tracker import OrderTracker
    from execution.executor import Executor
    ex = Executor(fake_futures_adapter, db_session, symbol="BTC/USDT:USDT")
    ot = OrderTracker(fake_futures_adapter, db_session, ex, symbol="BTC/USDT:USDT")
    fake_futures_adapter.ticker_price = 102500.0
    await ot.poll_once()
    # THEN trade cerrado por sl_triggered
    ...
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_order_tracker.py -k short_sl_guardian -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (ramas direccionales en los guardians)

```python
# execution/order_tracker.py — al evaluar SL/TP de cada trade abierto
        side = getattr(trade, "position_side", "LONG") or "LONG"
        if side == "LONG":
            sl_hit = (price <= sl) or (last_low is not None and last_low <= sl)
            tp_hit = price >= tp
        else:  # SHORT
            sl_hit = (price >= sl) or (last_high is not None and last_high >= sl)
            tp_hit = price <= tp
```

> Para SHORT se necesita el `high` de la última vela 1m: agregar `_fetch_last_candle_high` simétrico a `_fetch_last_candle_low`. La detección de fills de cierre filtra `side=="buy"` cuando `position_side=="SHORT"` (hoy filtra solo `"sell"`).

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_order_tracker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/execution/order_tracker.py trading-engine/tests/test_order_tracker.py
git commit -m "feat(order_tracker): directional SL/TP guardians and close-fill detection"
```

---

## FASE 7 — Risk Gate direccional + R12–R15

### Task 7.1: Generalizar firma (`has_open_position`, `available_margin`) y R6/kill switch (E2)

**Files:**
- Modify: `trading-engine/risk/risk_gate.py`
- Modify: `trading-engine/main.py` (call site)
- Test: `trading-engine/tests/test_risk_gate.py`

- [ ] **Step 1: Test que falla**

```python
def test_sell_closes_short_position():
    # GIVEN SELL con posición SHORT abierta (sin btc_held)
    gate = _gate()
    v = gate.validate(decision=_sell(), current_price=100000.0, atr_ref=500.0,
                      open_positions_count=1, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
                      kill_switch=False, available_margin=500.0, has_open_position=True,
                      open_position_side="SHORT")
    assert v.passed


def test_kill_switch_allows_closing_short():
    gate = _gate()
    v = gate.validate(decision=_sell(), current_price=100000.0, atr_ref=500.0,
                      open_positions_count=1, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
                      kill_switch=True, available_margin=500.0, has_open_position=True,
                      open_position_side="SHORT")
    assert v.passed
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_risk_gate.py -k "closes_short or closing_short" -v`
Expected: FAIL (firma usa `btc_held`).

- [ ] **Step 3: Implementación**

```python
# risk/risk_gate.py — nueva firma
    def validate(self, *, decision, current_price, atr_ref, open_positions_count,
                 daily_pnl_pct, total_drawdown_pct, kill_switch,
                 available_margin: float, has_open_position: bool,
                 open_position_side: str | None = None,
                 leverage: float = 1.0, liquidation_price: float | None = None,
                 funding_rate: float = 0.0, funding_rate_max_pct: float = 0.05,
                 liquidation_buffer_atr: float = 2.0,
                 roundtrip_fee_pct: float = 0.0, min_fees_to_tp_ratio: float = 3.0) -> RiskVerdict:
        if decision.action == DecisorAction.HOLD:
            return RiskVerdict(passed=True)
        if total_drawdown_pct <= self.max_drawdown_pct:
            return self._reject("R0_drawdown", f"max_drawdown breached: {total_drawdown_pct:.4f}")
        if kill_switch:
            if decision.action == DecisorAction.SELL and has_open_position:
                return RiskVerdict(passed=True)
            return self._reject("R0_kill_switch", "kill_switch active — only close allowed")
        if decision.action == DecisorAction.SELL:
            if not has_open_position or open_positions_count == 0:
                return self._reject("R6", "SELL requested but no open position to close")
            return RiskVerdict(passed=True)
        # ENTRADAS (BUY/SHORT) -> geometría direccional en Task 7.2
        ...
```

> `usdt_balance`/`btc_held` se reemplazan por `available_margin`/`has_open_position`. El sizing/notional (R1/R11) usa `available_margin`.

- [ ] **Step 4: Actualizar call site en `main.py`** (pasar `has_open_position`, `available_margin`, side). Correr y verificar que pasa.

Run: `cd trading-engine && pytest tests/test_risk_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/risk_gate.py trading-engine/main.py trading-engine/tests/test_risk_gate.py
git commit -m "feat(risk): position-aware signature; close any direction (E2)"
```

### Task 7.2: Geometría direccional R2–R5/R10 para SHORT

**Files:**
- Modify: `trading-engine/risk/risk_gate.py`
- Test: `trading-engine/tests/test_risk_gate.py`

- [ ] **Step 1: Test que falla**

```python
def test_short_requires_sl_above_and_tp_below():
    gate = _gate()
    # SL debajo del precio en SHORT -> R2 reject
    bad = _short(stop_loss=99000.0, take_profit=96000.0)
    v = gate.validate(decision=bad, current_price=100000.0, atr_ref=1000.0,
                      open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
                      kill_switch=False, available_margin=2000.0, has_open_position=False)
    assert not v.passed and v.rule_id == "R2"


def test_short_valid_geometry_passes():
    gate = _gate(min_notional=5.0)
    good = _short(stop_loss=101000.0, take_profit=98000.0, position_size_pct=0.10)
    v = gate.validate(decision=good, current_price=100000.0, atr_ref=2000.0,
                      open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
                      kill_switch=False, available_margin=2000.0, has_open_position=False)
    assert v.passed
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_risk_gate.py -k short_requires -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (rama de entrada direccional)

```python
# risk/risk_gate.py — tras los checks de SELL, dentro de la rama de ENTRADA
        from shared.schemas import Direction, direction_for_action
        direction = direction_for_action(decision.action)  # LONG | SHORT

        if decision.stop_loss is None:
            return self._reject("R2", "entry requires stop_loss")
        if decision.take_profit is None:
            return self._reject("R3", "entry requires take_profit")

        if direction == Direction.LONG:
            if decision.stop_loss >= current_price:
                return self._reject("R2", f"LONG stop_loss {decision.stop_loss} >= price")
            if decision.take_profit <= current_price:
                return self._reject("R3", f"LONG take_profit {decision.take_profit} <= price")
            sl_distance = current_price - decision.stop_loss
            reward = decision.take_profit - current_price
        else:  # SHORT
            if decision.stop_loss <= current_price:
                return self._reject("R2", f"SHORT stop_loss {decision.stop_loss} <= price")
            if decision.take_profit >= current_price:
                return self._reject("R3", f"SHORT take_profit {decision.take_profit} >= price")
            sl_distance = decision.stop_loss - current_price
            reward = current_price - decision.take_profit

        # R1
        if decision.position_size_pct > self.max_position_pct + 1e-9:
            return self._reject("R1", f"position_size_pct {decision.position_size_pct:.4f} > max")
        # R11 notional (sobre available_margin)
        notional_usdt = available_margin * decision.position_size_pct
        if notional_usdt < self.min_notional_usdt:
            return self._reject("R11", f"notional {notional_usdt:.4f} < min {self.min_notional_usdt:.2f}")
        # R8
        if open_positions_count >= self.max_simultaneous_trades:
            return self._reject("R8", f"max_simultaneous_trades: {open_positions_count}")
        # R9
        if daily_pnl_pct <= self.daily_stop_pct:
            return self._reject("R9", f"daily P&L breach: {daily_pnl_pct:.4f}")
        # R4 banda ATR (|distancia|)
        if sl_distance < self.sl_atr_multiplier * atr_ref:
            return self._reject("R4", f"SL distance {sl_distance:.2f} < min")
        if sl_distance > self.sl_atr_max_multiplier * atr_ref:
            return self._reject("R4", f"SL distance {sl_distance:.2f} > max")
        # R5 R:R
        if sl_distance > 0 and reward / sl_distance < self.min_rr_ratio:
            return self._reject("R5", f"R:R {reward/sl_distance:.2f} < {self.min_rr_ratio}")
        # R10 fees (|move|)
        if roundtrip_fee_pct > 0:
            move_pct = abs(reward) / current_price * 100
            min_move = min_fees_to_tp_ratio * roundtrip_fee_pct + self.max_slippage_pct * 2 * 100
            if move_pct < min_move:
                return self._reject("R10", f"TP move {move_pct:.3f}% < {min_move:.3f}%")
```

> El bloque de R11 para brackets SL/TP (notional del SL/TP) se mantiene para Spot; en futures el notional del bracket usa el mismo `qty × stopPrice`. Adaptar el cálculo a `direction` (en SHORT el bracket más restrictivo es el TP, que es el precio más bajo).

- [ ] **Step 4: Correr y verificar que pasa (LONG existente + SHORT nuevo)**

Run: `cd trading-engine && pytest tests/test_risk_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/risk_gate.py trading-engine/tests/test_risk_gate.py
git commit -m "feat(risk): directional R2-R5/R10 geometry for SHORT"
```

### Task 7.3: Reglas nuevas R12 (leverage), R13 (liq buffer), R14 (margin), R15 (funding)

**Files:**
- Modify: `trading-engine/risk/risk_gate.py`
- Test: `trading-engine/tests/test_risk_gate.py`

- [ ] **Step 1: Test que falla**

```python
def test_r12_leverage_cap():
    gate = _gate(max_leverage=1)
    v = gate.validate(decision=_short_ok(), current_price=100000.0, atr_ref=2000.0,
                      open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
                      kill_switch=False, available_margin=2000.0, has_open_position=False,
                      leverage=3.0)
    assert not v.passed and v.rule_id == "R12"


def test_r15_funding_block():
    gate = _gate()
    v = gate.validate(decision=_short_ok(), current_price=100000.0, atr_ref=2000.0,
                      open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
                      kill_switch=False, available_margin=2000.0, has_open_position=False,
                      funding_rate=0.10, funding_rate_max_pct=0.05)
    assert not v.passed and v.rule_id == "R15"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_risk_gate.py -k "r12 or r15" -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (constructor `max_leverage`; checks antes de aprobar)

```python
# __init__: agregar self.max_leverage = max_leverage (default 1)
# Al inicio de la rama de ENTRADA, tras calcular direction:
        if leverage > self.max_leverage + 1e-9:
            return self._reject("R12", f"leverage {leverage} > max {self.max_leverage}")
        if funding_rate is not None and abs(funding_rate) > funding_rate_max_pct:
            return self._reject("R15", f"funding {funding_rate} > max {funding_rate_max_pct}")
# Tras calcular sl_distance (R13/R14):
        if liquidation_price is not None and atr_ref > 0:
            buffer = liquidation_buffer_atr * atr_ref
            if direction == Direction.LONG and liquidation_price > decision.stop_loss - buffer:
                return self._reject("R13", "liquidation too close to SL (LONG)")
            if direction == Direction.SHORT and liquidation_price < decision.stop_loss + buffer:
                return self._reject("R13", "liquidation too close to SL (SHORT)")
        required_margin = (available_margin * decision.position_size_pct)  # 1x: margen == notional
        if required_margin > available_margin + 1e-9:
            return self._reject("R14", "insufficient available margin")
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_risk_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/risk_gate.py trading-engine/tests/test_risk_gate.py
git commit -m "feat(risk): R12 leverage, R13 liq-buffer, R14 margin, R15 funding"
```

### Task 7.4: CoherenceChecker C7 direccional

**Files:**
- Modify: `trading-engine/risk/coherence_checker.py`
- Test: `trading-engine/tests/test_coherence_checker.py`

- [ ] **Step 1: Test que falla**

```python
def test_c7_short_rr_directional():
    # GIVEN short con R:R bajo -> C7 critical
    from risk.coherence_checker import CoherenceChecker
    checker = CoherenceChecker(strict_mode=False)
    short_low_rr = _short(stop_loss=101000.0, take_profit=99900.0)  # R:R ~0.1
    ctx = {"price": 100000.0, ...}  # ctx con price del Bloque B
    # WHEN
    warnings = checker.evaluate(short_low_rr, ctx)
    # THEN — los warnings tienen .rule_id y .severity
    assert any(w.rule_id == "C7" and w.severity == "critical" for w in warnings)
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_coherence_checker.py -k c7_short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (en `_c7_rr_ratio_verification`, `sl_distance`/`reward` por dirección, espejo de Task 7.2).

```python
# coherence_checker.py — dentro de _c7_rr_ratio_verification
        from shared.schemas import Direction, direction_for_action
        direction = direction_for_action(decision.action)
        if direction is None:   # SELL/HOLD: no aplica C7
            return []
        if direction == Direction.LONG:
            sl_distance = price - decision.stop_loss
            reward = decision.take_profit - price
        else:
            sl_distance = decision.stop_loss - price
            reward = price - decision.take_profit
        if sl_distance <= 0:
            return []
        rr_real = reward / sl_distance
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_coherence_checker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/coherence_checker.py trading-engine/tests/test_coherence_checker.py
git commit -m "feat(coherence): directional C7 R:R"
```

---

## FASE 8 — Decisor, agregación, confianza y prompt

### Task 8.1: `regime_factor` direccional

**Files:**
- Modify: `shared/confidence.py`
- Test: `trading-engine/tests/test_confidence.py`

> **Importante:** la firma real es `regime_factor(regime, calibration)` y se invoca desde `compute_confidence_base(...)` → `apply_server_confidence(...)`. NO eliminar `calibration`. Se agrega `direction` como kwarg opcional (default LONG = comportamiento actual) y se enhebra por los callers, derivándola de `decision.action` en `apply_server_confidence`.

- [ ] **Step 1: Test que falla**

```python
from shared.confidence import regime_factor
from shared.schemas import Direction

_CAL = {}  # usa defaults


def test_regime_factor_short_favors_trending_down():
    # GIVEN calibración por defecto
    assert regime_factor("TRENDING_DOWN", _CAL, direction=Direction.SHORT) > 0.5
    assert regime_factor("TRENDING_UP", _CAL, direction=Direction.SHORT) == 0.0
    # LONG conserva comportamiento actual (calibration posicional intacta)
    assert regime_factor("TRENDING_DOWN", _CAL, direction=Direction.LONG) == 0.0
    assert regime_factor("TRENDING_UP", _CAL) == 1.0  # default LONG sin kwarg
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_confidence.py -k regime_factor_short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (mantener `calibration` posicional; `direction` kwarg)

```python
# shared/confidence.py
from shared.schemas import Direction

def regime_factor(regime, calibration: dict, *, direction: "Direction | None" = None) -> float:
    cal = {**_DEFAULT_CALIBRATION, **{k: float(v) for k, v in calibration.items() if k in _DEFAULT_CALIBRATION}}
    key = regime.value if hasattr(regime, "value") else str(regime)
    direction = direction or Direction.LONG
    if direction == Direction.LONG:
        if key == "TRENDING_UP": return 1.0
        if key == "TRENDING_DOWN": return 0.0
    else:  # SHORT — espejo
        if key == "TRENDING_DOWN": return 1.0
        if key == "TRENDING_UP": return 0.0
    if key == "RANGE": return cal["peso_regime_range"]
    if key == "HIGH_VOLATILITY": return cal["peso_regime_high_vol"]
    return cal["peso_regime_range"]  # NEUTRAL y default
```

- [ ] **Step 4: Enhebrar `direction` por los callers**

```python
# shared/confidence.py — compute_confidence_base recibe direction y lo pasa a regime_factor
def compute_confidence_base(confluences, regime, calibration=None, *, direction=None):
    cal = calibration or {}
    count = effective_confluence_count(confluences)
    qf = quality_factor(confluences)
    rf = regime_factor(regime, cal, direction=direction)
    base_n = _conf_base_table_value(count, cal)
    base = max(0.0, min(1.0, base_n * qf * rf))
    # meta igual que hoy ...

# apply_server_confidence deriva direction de la acción
def apply_server_confidence(decision, *, calibration=None, confluences_dropped=None):
    from shared.schemas import direction_for_action
    direction = direction_for_action(decision.action)  # None en SELL/HOLD -> regime_factor usa LONG (irrelevante, no se ejecuta)
    base, meta = compute_confidence_base(
        decision.confluences, decision.regime, calibration, direction=direction)
    # resto igual ...
```

- [ ] **Step 5: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_confidence.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add shared/confidence.py trading-engine/tests/test_confidence.py
git commit -m "feat(confidence): directional regime factor threaded through callers"
```

### Task 8.2: Agregación self-consistency para SHORT

**Files:**
- Modify: `trading-engine/agents/decisor_aggregate.py`
- Test: `trading-engine/tests/test_decisor_aggregate.py`

- [ ] **Step 1: Test que falla**

```python
def test_aggregate_short_majority_uses_median_sl_tp():
    # GIVEN 3 muestras SHORT con SL/TP distintos
    samples = [_short(101000, 98000), _short(102000, 97000), _short(101500, 97500)]
    agg = aggregate_decisor_outputs(samples)
    assert agg.action.value == "SHORT"
    assert agg.stop_loss == 101500  # mediana
    assert agg.take_profit == 97500
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_decisor_aggregate.py -k short_majority -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (rama SHORT espejo de la rama BUY: mediana de SL/TP/size/holding, unión de confluencias).

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_decisor_aggregate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/decisor_aggregate.py trading-engine/tests/test_decisor_aggregate.py
git commit -m "feat(decisor): aggregate SHORT samples"
```

### Task 8.3: Decisor — sizing/confianza direccional y routing

**Files:**
- Modify: `trading-engine/agents/decisor.py`
- Test: `trading-engine/tests/test_decisor.py`

- [ ] **Step 1: Test que falla**

```python
@pytest.mark.asyncio
async def test_decisor_accepts_short_and_sizes_it(...):
    # GIVEN LLM devuelve action=SHORT con SL>precio>TP
    # THEN DecisorOutput válido y position_size_pct recalculado server-side
    ...
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_decisor.py -k short -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**: `_apply_sizing_from_ctx` y `_apply_server_confidence` usan `direction_for_action`; two-pass C7 direccional; fallbacks HOLD intactos.

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_decisor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/decisor.py trading-engine/tests/test_decisor.py
git commit -m "feat(decisor): directional sizing/confidence and SHORT routing"
```

### Task 8.4: Prompt `decisor_system.txt` (acción SHORT, confluencias bajistas, eliminar R7)

**Files:**
- Modify: `trading-engine/agents/prompts/decisor_system.txt`
- Modify: `trading-engine/agents/context_builder.py` (perfil futures, funding, liquidation, position_side)
- Test: `trading-engine/tests/test_prompt_manager.py`, `tests/test_context_builder.py`

- [ ] **Step 1: Test que falla** (el prompt incluye `SHORT` y ya no la prohibición)

```python
def test_prompt_mentions_short_and_no_r7_ban():
    text = open("agents/prompts/decisor_system.txt").read()
    assert '"SHORT"' in text
    assert "NUNCA shortear" not in text
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_prompt_manager.py -k short -v`
Expected: FAIL.

- [ ] **Step 3: Editar el prompt**: perfil "USDT-M Futures perpetuos"; acciones `BUY|SHORT|SELL|HOLD`; reglas SL/TP invertidas para SHORT; catálogo de confluencias bajistas (espejo de A–H); régimen TRENDING_DOWN → SHORT; nota `SELL` cierra cualquier posición (long→sell, short→buy reduceOnly); leverage 1x informativo.

- [ ] **Step 4: Correr y verificar que pasa** (incluye `test_context_builder.py` con funding/liquidation/position_side en el contexto).

Run: `cd trading-engine && pytest tests/test_prompt_manager.py tests/test_context_builder.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/prompts/decisor_system.txt trading-engine/agents/context_builder.py trading-engine/tests/test_prompt_manager.py trading-engine/tests/test_context_builder.py
git commit -m "feat(prompt): SHORT action, bearish confluences, futures profile"
```

---

## FASE 9 — Atribución de outcomes direccional (G4)

### Task 9.1: `attribute()` recibe `position_side`; MFE/MAE favorable/adverso por dirección

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Test: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Test que falla**

```python
def test_short_good_when_price_drops(make_decision, candles_dropping):
    # GIVEN decisión SHORT ejecutada que ganó (precio bajó)
    att = attribute(decision=make_decision(action="SHORT", executed=True),
                    ohlcv_1m=candles_dropping, associated_trade=trade_with_positive_pnl,
                    horizon_min=240, now=...)
    assert att.classification == "GOOD_SHORT"
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -k short_good -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**:
- Agregar `GOOD_SHORT`/`BAD_SHORT` al `Classification` Literal.
- Derivar `direction` de `decision.output.action` (BUY→LONG, SHORT→SHORT; SELL usa `position_side` del trade asociado).
- `_compute_mfe_mae` calcula favorable/adverso: para SHORT, favorable = `(price_t - low)/price_t`, adverso = `(price_t - high)/price_t`.
- `_resolve_risk_thresholds` y `_absolute_bracket_levels` aceptan geometría short (`tp < price < sl`).
- `_classify`: rama `action == "SHORT" and executed` → `GOOD_SHORT/BAD_SHORT` según `pnl_pct`.
- `_first_bracket_outcome` direccional (en SHORT: SL si `high >= sl`, TP si `low <= tp`).

- [ ] **Step 4: Correr y verificar que pasa (LONG existente intacto)**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py tests/test_outcome_attribution_job.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(attribution): directional MFE/MAE and GOOD/BAD_SHORT"
```

### Task 9.2: Calibración por dirección

**Files:**
- Modify: `shared/confidence_calibration.py`
- Test: `trading-engine/tests/test_confidence_calibration.py`

- [ ] **Step 1: Test que falla**: métricas Brier/ECE segmentables por dirección (`GOOD_SHORT/BAD_SHORT` cuentan como outcomes positivos/negativos de short).
- [ ] **Step 2:** correr → FAIL.
- [ ] **Step 3:** incluir las clasificaciones short en el cómputo (mapear GOOD_*→1, BAD_*→0).
- [ ] **Step 4:** correr → PASS.
- [ ] **Step 5: Commit**

```bash
git add shared/confidence_calibration.py trading-engine/tests/test_confidence_calibration.py
git commit -m "feat(calibration): include SHORT outcomes"
```

---

## FASE 10 — Config + wiring + guard de arranque

### Task 10.1: Nuevas `ConfigKey` y defaults

**Files:**
- Modify: `shared/config_store.py` (enum `ConfigKey` + `DEFAULTS`)
- Test: `trading-engine/tests/test_config_store.py`

- [ ] **Step 1: Test que falla**

```python
def test_futures_config_defaults():
    from shared.config_store import ConfigKey, DEFAULTS
    assert DEFAULTS[ConfigKey.TRADING_PRODUCT].value == "spot"
    assert DEFAULTS[ConfigKey.MAX_LEVERAGE].value == "1"
    assert DEFAULTS[ConfigKey.MARGIN_MODE].value == "isolated"
    assert ConfigKey.FUNDING_RATE_MAX_PCT in DEFAULTS
    assert ConfigKey.LIQUIDATION_BUFFER_ATR in DEFAULTS
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_config_store.py -k futures_config -v`
Expected: FAIL.

- [ ] **Step 3: Implementación** (agregar al enum y a `DEFAULTS`):

```python
# shared/config_store.py — ConfigKey
    TRADING_PRODUCT = "trading_product"
    MAX_LEVERAGE = "max_leverage"
    MARGIN_MODE = "margin_mode"
    FUNDING_RATE_MAX_PCT = "funding_rate_max_pct"
    LIQUIDATION_BUFFER_ATR = "liquidation_buffer_atr"

# DEFAULTS
    ConfigKey.TRADING_PRODUCT: _Default("spot", "string", "spot | futures (default seguro)"),
    ConfigKey.MAX_LEVERAGE: _Default("1", "int", "Apalancamiento máximo (operator-only)"),
    ConfigKey.MARGIN_MODE: _Default("isolated", "string", "isolated | cross"),
    ConfigKey.FUNDING_RATE_MAX_PCT: _Default("0.05", "float", "Funding máx para abrir"),
    ConfigKey.LIQUIDATION_BUFFER_ATR: _Default("2.0", "float", "Buffer liquidación en ATR"),
```

> Excluir `MAX_LEVERAGE`, `MARGIN_MODE`, `TRADING_PRODUCT`, `LIQUIDATION_BUFFER_ATR` del `_SAFE_BOUNDS` del Supervisor (Task 10.2).

- [ ] **Step 4: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_config_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/config_store.py trading-engine/tests/test_config_store.py
git commit -m "feat(config): futures keys with safe defaults"
```

### Task 10.2: Guard de arranque + selección de adapter en `main.py`

**Files:**
- Modify: `trading-engine/main.py`
- Modify: `trading-engine/config.py` (`TRADING_PRODUCT` legible; símbolo futures)
- Modify: `trading-engine/agents/supervisor.py` (excluir claves de `_SAFE_BOUNDS`)
- Test: `trading-engine/tests/test_main_futures_guard.py` (crear)

- [ ] **Step 1: Test que falla**

```python
def test_validate_futures_sizing_unfeasible():
    from main import validate_futures_sizing
    # GIVEN margen 300, max_position 10%, leverage 1, min_notional 100 -> 30 < 100
    ok, reason = validate_futures_sizing(available_margin=300.0, max_position_pct=0.10,
                                         leverage=1, min_notional=100.0)
    assert ok is False
    assert "min_notional" in reason


def test_validate_futures_sizing_feasible():
    from main import validate_futures_sizing
    ok, _ = validate_futures_sizing(available_margin=1500.0, max_position_pct=0.10,
                                    leverage=1, min_notional=100.0)
    assert ok is True
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_main_futures_guard.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementación**

```python
# trading-engine/main.py
def validate_futures_sizing(*, available_margin: float, max_position_pct: float,
                            leverage: int, min_notional: float) -> tuple[bool, str]:
    max_trade_notional = available_margin * max_position_pct * leverage
    if max_trade_notional < min_notional:
        return False, (f"futures.sizing_unfeasible: max trade notional "
                       f"{max_trade_notional:.2f} < min_notional {min_notional:.2f}")
    return True, ""


def build_adapter(product: str):
    from execution.exchange_adapter import SpotAdapter, FuturesAdapter
    return FuturesAdapter() if product == "futures" else SpotAdapter()
```

En el arranque: leer `TRADING_PRODUCT`; si `futures`, llamar `adapter.setup_symbol(symbol, leverage, margin_mode)`, leer `min_notional = adapter.min_notional(symbol)`, y `validate_futures_sizing(...)`; si no es feasible → log `futures.sizing_unfeasible`, notificar Telegram y **no** habilitar futures (mantener `spot` o pausar). El `decisor_tick` pasa `available_margin`/`has_open_position`/`open_position_side`/`leverage`/`liquidation_price`/`funding_rate` al `RiskGate`.

- [ ] **Step 4: Correr y verificar que pasa (suite completa del engine)**

Run: `cd trading-engine && pytest tests/ -q`
Expected: PASS (toda la suite verde).

- [ ] **Step 5: Commit**

```bash
git add trading-engine/main.py trading-engine/config.py trading-engine/agents/supervisor.py trading-engine/tests/test_main_futures_guard.py
git commit -m "feat(engine): adapter selection, startup sizing guard, risk wiring"
```

---

## FASE 11 — Web API

### Task 11.1: Contratos de trades/positions/balance con campos direccionales

**Files:**
- Modify: `web/api/trades.py`, `web/api/positions.py`, `web/api/balance.py`, `web/api/decisions.py`
- Test: `web/tests/test_trades_api.py`, `web/tests/test_positions_api.py`, `web/tests/test_decisions_api.py` (harness existente con `web/tests/conftest.py`)

- [ ] **Step 1: Test que falla**: `GET /api/trades` y `/api/positions` devuelven `position_side`, `leverage`, `liquidation_price`; `/api/decisions` acepta filtro `action=SHORT`.
- [ ] **Step 2:** correr → FAIL.
- [ ] **Step 3:** agregar los campos a los serializers/responses (los modelos ya los tienen tras Fase 4).
- [ ] **Step 4:** correr → PASS.
- [ ] **Step 5: Commit**

```bash
git add web/api/ web/tests/
git commit -m "feat(api): expose directional fields and SHORT action"
```

---

## FASE 12 — Frontend

> Editar SIEMPRE los `.tsx`/`.ts`. Los `.js` son emit de `tsc -b`. Verificar con `cd frontend && npm run build` (corre `tsc -b && vite build`).

### Task 12.1: Tipos direccionales

**Files:**
- Modify: `frontend/src/types/index.ts`, `frontend/src/types/decisorOutput.ts`

- [ ] **Step 1:** `DecisorAction = "BUY" | "SHORT" | "SELL" | "HOLD"`; `Trade`/`Position` agregan `position_side?: "LONG"|"SHORT"`, `leverage?: number`, `liquidation_price?: number | null`; `OutcomeClassification` agrega `"GOOD_SHORT" | "BAD_SHORT"`.
- [ ] **Step 2:** `cd frontend && npm run build` → debe compilar.
- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/types/decisorOutput.ts
git commit -m "feat(fe-types): directional trade/position/decision types"
```

### Task 12.2: PnL y distancias SL/TP direccionales

**Files:**
- Modify: `frontend/src/lib/pnl.ts`, `frontend/src/pages/Trades.tsx`, `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1:** extraer helpers `slDistance/tpDistance/rrRatio` direccionales a `lib/pnl.ts` (LONG: SL abajo/TP arriba; SHORT: SL arriba/TP abajo); `Dashboard.tsx` pasa `position_side` a `computePnlUsdt/Pct` (hoy default BUY).
- [ ] **Step 2:** `npm run build`.
- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/pnl.ts frontend/src/pages/Trades.tsx frontend/src/pages/Dashboard.tsx
git commit -m "feat(fe): directional PnL and SL/TP distances"
```

### Task 12.3: Badges LONG/SHORT, leverage, liquidación, filtros

**Files:**
- Modify: `frontend/src/pages/Trades.tsx`, `Dashboard.tsx`, `Decisions.tsx`

- [ ] **Step 1:** badge LONG/SHORT; mostrar `leverage` y `liquidation_price` en posiciones; acción `SHORT` y panel de orden short en `Decisions.tsx`; `explainRejection()` con R12–R15; filtro por dirección; columna `position_side` en CSV.
- [ ] **Step 2:** `npm run build`.
- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Trades.tsx frontend/src/pages/Dashboard.tsx frontend/src/pages/Decisions.tsx
git commit -m "feat(fe): LONG/SHORT badges, leverage, liquidation, filters"
```

### Task 12.4: Chart markers direccionales + líneas de liquidación

**Files:**
- Modify: `frontend/src/components/chart/PriceChart.tsx`

- [ ] **Step 1:** marker SHORT → `arrowDown`/`aboveBar` y texto `SHORT $...` (hoy hardcode `BUY`); línea de liquidación por trade abierto; leyenda.
- [ ] **Step 2:** `npm run build`.
- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/chart/PriceChart.tsx
git commit -m "feat(fe-chart): directional markers and liquidation lines"
```

### Task 12.5: Config + Health

**Files:**
- Modify: `frontend/src/pages/Config.tsx`, `frontend/src/pages/Health.tsx`, `frontend/src/api/client.ts`

- [ ] **Step 1:** `Config.tsx` agrega `trading_product`, `max_leverage`, `margin_mode`, `funding_rate_max_pct`, `liquidation_buffer_atr`; `Health.tsx` muestra `GOOD_SELL/BAD_SELL` y `GOOD_SHORT/BAD_SHORT`; `api/client.ts` tipos extendidos.
- [ ] **Step 2:** `npm run build`.
- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Config.tsx frontend/src/pages/Health.tsx frontend/src/api/client.ts
git commit -m "feat(fe): futures config keys and short outcomes in health"
```

---

## FASE 13 — Specs + README

### Task 13.1: Actualizar specs

**Files:**
- Modify: `docs/specs/01-functional-spec.md`, `02-technical-spec.md`, `03-data-model.md`, `04-api-contracts.md`, `05-risk-and-safety.md`, `06-patterns.md`, `07-discrepancies-and-gaps.md`, `docs/specs/README.md`, `README.md` (raíz)

- [ ] **Step 1:** aplicar los cambios descritos en §9 del design doc (caso de uso shorts, `ExchangeAdapter`, migración 016, R12–R15, gates LIVE para perp, patrón adapter, estado de la feature). Subir versión de cada spec y la fecha de "Última revisión" en `README.md` de specs.
- [ ] **Step 2: Commit**

```bash
git add docs/specs/ README.md
git commit -m "docs(specs): document futures shorts feature across specs"
```

---

## Self-Review del plan (cobertura vs spec)

| Sección del spec | Tarea(s) que la implementan |
|------------------|------------------------------|
| §3.0 Guard de arranque | Task 10.2 |
| §3.1 ExchangeAdapter | Tasks 2.1–2.2, 3.1–3.5 |
| §3.2 Modelo de decisión (BUY/SHORT/SELL/HOLD) | Tasks 0.1–0.2, 8.2–8.4 |
| §3.3 Risk Gate direccional + firma (E2) | Tasks 7.1–7.3 |
| §3.4 Executor | Tasks 5.1–5.3 |
| §3.5 OrderTracker | Task 6.2 |
| §3.6 PositionManager/PnL | Tasks 1.2, 6.1 |
| §3.6.bis Atribución (G4) | Tasks 9.1–9.2 |
| §3.7 Migración 016 | Tasks 4.1–4.2 |
| §3.8 Frontend | Tasks 12.1–12.5 |
| §3.9 Config | Task 10.1 |
| §3.10 Web API | Task 11.1 |
| Confianza direccional | Task 8.1 |
| Sizing direccional | Task 1.1 |
| Precisión qty (G5) | Task 3.2 |
| Funding fail-soft (G6) | Tasks 7.3, 10.2 |
| Specs (§9) | Task 13.1 |

**Consistencia de tipos:** `Direction{LONG,SHORT}`, `DecisorAction{BUY,SHORT,SELL,HOLD}`, `direction_for_action()`, `ExchangeAdapter.open_position/close_position/place_brackets/fetch_balance/fetch_positions/fetch_funding_rate/min_notional`, `Executor.execute_open/execute_close` se usan consistentemente entre tareas.

**Notas de ejecución:** las fases 0–4 no cambian comportamiento productivo; el camino spot queda verde por compat (Task 5.3). Futures solo se activa con `TRADING_PRODUCT=futures` y tras pasar el guard de §3.0.
