# Supervisor Counterfactual — PR 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el cómputo, persistencia, job de attribution y endpoint REST para clasificar cada decisión del Decisor contra el OHLCV posterior. Sin integración al Supervisor ni al Frontend — esos vienen en PR 2 y PR 3 después de validar PR 1 en paper trading 24 h.

**Architecture:** Función pura `outcome_attribution.attribute()` clasifica una decisión usando velas OHLCV 1m, el trade asociado (si existe) y los inputs de riesgo persistidos en `decisions.input`. Una nueva tabla `decision_outcomes` con UPSERT idempotente persiste el resultado. Un job APScheduler corre cada 1 h sobre las decisiones de las últimas 25 h, reprocesando `PENDING` cuando madura su ventana. Un endpoint REST expone los outcomes con filtros.

**Tech Stack:** Python 3.12 asyncio, SQLAlchemy 2.0 (async) + asyncpg, Alembic, APScheduler, FastAPI, Pydantic v2, pytest + pytest-asyncio + freezegun.

**Spec source:** [`docs/superpowers/specs/2026-05-18-supervisor-counterfactual-design.md`](../specs/2026-05-18-supervisor-counterfactual-design.md).

**Decisiones por defecto (spec §11)** asumidas en este plan:
- Horizonte H = `expected_holding_max_min` (default 240 min) — reusa la clave existente.
- Frecuencia del job = 1 h.
- Umbral de cobertura OHLCV faltante para `UNKNOWN` = 30 %.

---

## File Structure

| Archivo | Tipo | Responsabilidad |
|---|---|---|
| `trading-engine/alembic/versions/008_add_decision_outcomes.py` | Nuevo | Migración: crea tabla `decision_outcomes` + 2 índices. |
| `shared/db/models.py` | Modificar | Agregar clase `DecisionOutcome` con relación inversa desde `Decision`. |
| `trading-engine/agents/outcome_attribution.py` | Nuevo | Función pura `attribute()` + dataclass `DecisionAttribution` + helpers `_extract_decision_inputs`, `_compute_mfe_mae`, `_classify`. Cero I/O. |
| `trading-engine/agents/outcome_attribution_job.py` | Nuevo | Job `outcome_attribution_tick()`: query candidates → fetch OHLCV → attribute → UPSERT. |
| `trading-engine/scheduler.py` | Modificar | Método `add_outcome_attribution(fn, interval_min=60)`. |
| `trading-engine/main.py` | Modificar | Bootstrap del job en `run()`. |
| `web/api/decisions.py` | Modificar | Endpoint `GET /decisions/outcomes` + DTO `DecisionOutcomeOut`. |
| `trading-engine/tests/test_outcome_attribution.py` | Nuevo | Tests de la función pura. Sin DB. |
| `trading-engine/tests/test_outcome_attribution_job.py` | Nuevo | Tests del job: idempotencia, UPSERT, ventana acotada, PENDING que madura. |
| `web/tests/test_decisions_outcomes_api.py` | Nuevo | Tests del endpoint: filtros, JOIN, paginación. |

**Convenciones de código (extraídas de `06-patterns.md` y CLAUDE.md):**
- Imports `from __future__ import annotations` cuando aplique.
- Decimal/Numeric en BD (nunca `FLOAT`); `float` en función pura.
- `structlog` con `dominio.evento` (P-03). `outcome_attribution.job.completed`, `outcome_attribution.upsert`, etc.
- Tests con `freezegun` para tiempo determinístico (P-11).
- SQLite in-memory para tests rápidos (P-11); ver fixture `session` en `test_supervisor.py:173-221` para el patrón con `_sqlite_metadata` que strippea tipos PG-específicos.

---

## Task 1.1: Migración Alembic + modelo ORM `DecisionOutcome`

**Files:**
- Create: `trading-engine/alembic/versions/008_add_decision_outcomes.py`
- Modify: `shared/db/models.py` (agregar al final, antes de la última línea)

- [ ] **Step 1: Escribir la migración Alembic**

```python
# trading-engine/alembic/versions/008_add_decision_outcomes.py
"""Add decision_outcomes table for counterfactual attribution.

Stores forward returns (MFE/MAE) and classification per decisor decision,
populated by outcome_attribution_job. 1-to-1 with decisions via PK FK.

Revision ID: 008
Revises: 007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decisions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("horizon_min", sa.Integer, nullable=False),
        sa.Column("matured", sa.Boolean, nullable=False),
        sa.Column("forward_return_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("mfe_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("mae_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("time_to_mfe_min", sa.Integer, nullable=True),
        sa.Column("time_to_mae_min", sa.Integer, nullable=True),
        sa.Column("sl_dist_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("tp_target_pct", sa.Numeric(10, 5), nullable=True),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_decision_outcomes_classification",
        "decision_outcomes",
        ["classification", "computed_at"],
    )
    op.create_index(
        "idx_decision_outcomes_pending",
        "decision_outcomes",
        ["computed_at"],
        postgresql_where=sa.text("classification = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("idx_decision_outcomes_pending", table_name="decision_outcomes")
    op.drop_index("idx_decision_outcomes_classification", table_name="decision_outcomes")
    op.drop_table("decision_outcomes")
```

- [ ] **Step 2: Agregar el modelo ORM `DecisionOutcome`**

En `shared/db/models.py`, después de la clase `Decision`:

```python
class DecisionOutcome(Base):
    __tablename__ = "decision_outcomes"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    horizon_min: Mapped[int] = mapped_column(Integer, nullable=False)
    matured: Mapped[bool] = mapped_column(Boolean, nullable=False)
    forward_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    mfe_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    mae_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    time_to_mfe_min: Mapped[int | None] = mapped_column(Integer)
    time_to_mae_min: Mapped[int | None] = mapped_column(Integer)
    sl_dist_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    tp_target_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )

    decision: Mapped["Decision"] = relationship("Decision", foreign_keys=[decision_id])

    __table_args__ = (
        Index("idx_decision_outcomes_classification", "classification", "computed_at"),
    )
```

- [ ] **Step 3: Aplicar la migración en local**

```bash
cd trading-engine && alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade 007 -> 008`.

- [ ] **Step 4: Verificar que el modelo carga sin errores**

```bash
cd trading-engine && python -c "from shared.db.models import DecisionOutcome; print(DecisionOutcome.__tablename__)"
```

Expected stdout: `decision_outcomes`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/alembic/versions/008_add_decision_outcomes.py shared/db/models.py
git commit -m "feat(db): add decision_outcomes table for counterfactual attribution"
```

---

## Task 1.2: Dataclass `DecisionAttribution` + skeleton de `attribute()`

**Files:**
- Create: `trading-engine/agents/outcome_attribution.py`
- Create: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir el test del shape del dataclass + retorno UNKNOWN**

```python
# trading-engine/tests/test_outcome_attribution.py
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from agents.outcome_attribution import (
    DecisionAttribution,
    attribute,
)


def test_decision_attribution_dataclass_is_frozen():
    attr = DecisionAttribution(
        decision_id=uuid4(),
        horizon_min=240,
        matured=False,
        forward_return_pct=None,
        mfe_pct=None,
        mae_pct=None,
        time_to_mfe_min=None,
        time_to_mae_min=None,
        sl_dist_pct=None,
        tp_target_pct=None,
        classification="UNKNOWN",
        computed_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        attr.classification = "GOOD_HOLD"  # type: ignore[misc]


def test_attribute_returns_unknown_when_decision_missing_inputs():
    """Una decisión sin price/atr_pct en su input se clasifica UNKNOWN."""
    decision = _make_decision(input={}, output={"action": "HOLD"})
    result = attribute(
        decision=decision,
        ohlcv_1m=[],
        associated_trade=None,
        horizon_min=240,
        now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert result.classification == "UNKNOWN"
    assert result.decision_id == decision.id


def _make_decision(*, input: dict, output: dict, ts=None, executed=False):
    """Helper for tests — minimal Decision-like object without DB."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=uuid4(),
        ts=ts or datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        input=input,
        output=output,
        executed=executed,
        trade_id=None,
    )
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: `ImportError: cannot import name 'DecisionAttribution' from 'agents.outcome_attribution'`.

- [ ] **Step 3: Implementar el dataclass + función skeleton**

```python
# trading-engine/agents/outcome_attribution.py
"""Counterfactual outcome attribution for decisor decisions.

Pure module: no DB queries, no commits, no clocks outside the `now` parameter.
Tested in trading-engine/tests/test_outcome_attribution.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


Classification = Literal[
    "PENDING", "UNKNOWN",
    "GOOD_BUY", "BAD_BUY",
    "BLOCKED_GOOD_TRADE", "CORRECTLY_BLOCKED",
    "MISSED_OPPORTUNITY", "GOOD_HOLD",
    "GOOD_SELL", "BAD_SELL",
]


@dataclass(frozen=True)
class DecisionAttribution:
    decision_id: UUID
    horizon_min: int
    matured: bool
    forward_return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    time_to_mfe_min: int | None
    time_to_mae_min: int | None
    sl_dist_pct: float | None
    tp_target_pct: float | None
    classification: Classification
    computed_at: datetime


def attribute(
    *,
    decision: Any,
    ohlcv_1m: list[Any],
    associated_trade: Any | None,
    horizon_min: int,
    now: datetime,
) -> DecisionAttribution:
    """Classify a decisor decision against the OHLCV evolution after `decision.ts`.

    Pure: deterministic given the same inputs. Caller provides `now` (no `datetime.now()`).
    Returns `UNKNOWN` if essential inputs are missing in `decision.input`.
    """
    inputs = _extract_decision_inputs(decision)
    if inputs is None:
        return DecisionAttribution(
            decision_id=decision.id,
            horizon_min=horizon_min,
            matured=False,
            forward_return_pct=None,
            mfe_pct=None,
            mae_pct=None,
            time_to_mfe_min=None,
            time_to_mae_min=None,
            sl_dist_pct=None,
            tp_target_pct=None,
            classification="UNKNOWN",
            computed_at=now,
        )
    # Real classification lives in subsequent tasks.
    raise NotImplementedError("classification not yet implemented")


def _extract_decision_inputs(decision: Any) -> dict[str, float] | None:
    """Return {price_t, atr_pct_t, sl_atr_mult, min_rr} or None if any required key is missing."""
    inp = decision.input or {}
    try:
        price = float(inp["price"])
        atr_pct = float(inp["atr_ref_pct"])
        sl_mult = float(inp["sl_atr_multiplier"])
        rr = float(inp["min_rr_ratio"])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or atr_pct <= 0:
        return None
    return {
        "price_t": price,
        "atr_pct_t": atr_pct,
        "sl_atr_mult": sl_mult,
        "min_rr_ratio": rr,
    }
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): add outcome_attribution dataclass and skeleton (UNKNOWN path)"
```

---

## Task 1.3: Helper `_compute_mfe_mae`

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir el test con velas sintéticas**

Agregar al test file:

```python
from datetime import timedelta
from types import SimpleNamespace

from agents.outcome_attribution import _compute_mfe_mae


def _candle(t, *, high, low, close=None, open_=None):
    return SimpleNamespace(
        time=t,
        open=Decimal(str(open_ if open_ is not None else low)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close if close is not None else high)),
    )


def test_compute_mfe_mae_simple_rally():
    """Precio sube de 100 a 102, sin caer abajo de 99.95 — mfe +2 %, mae -0.05 %."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.5, low=99.95),
        _candle(t0 + timedelta(minutes=2), high=101.0, low=99.98),
        _candle(t0 + timedelta(minutes=3), high=102.0, low=100.5),
    ]
    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(price_t=100.0, candles=candles, ts0=t0)
    assert mfe == pytest.approx(2.0, abs=1e-6)
    assert mae == pytest.approx(-0.05, abs=1e-6)
    assert t_mfe == 3  # tercera vela
    assert t_mae == 1  # primera vela


def test_compute_mfe_mae_empty_candles():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(price_t=100.0, candles=[], ts0=t0)
    assert (mfe, mae, t_mfe, t_mae) == (None, None, None, None)


def test_compute_mfe_mae_drop_then_recover():
    """Cae primero a 99, sube a 101 — t_mae < t_mfe."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.0),
        _candle(t0 + timedelta(minutes=2), high=101.0, low=100.0),
    ]
    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(price_t=100.0, candles=candles, ts0=t0)
    assert mfe == pytest.approx(1.0, abs=1e-6)
    assert mae == pytest.approx(-1.0, abs=1e-6)
    assert t_mae == 1
    assert t_mfe == 2
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py::test_compute_mfe_mae_simple_rally -v`
Expected: `ImportError: cannot import name '_compute_mfe_mae'`.

- [ ] **Step 3: Implementar el helper**

Agregar a `outcome_attribution.py`:

```python
def _compute_mfe_mae(
    *,
    price_t: float,
    candles: list[Any],
    ts0: datetime,
) -> tuple[float | None, float | None, int | None, int | None]:
    """Iterate candles in order; track MFE/MAE and minute-offset when each was reached.

    Returns (mfe_pct, mae_pct, time_to_mfe_min, time_to_mae_min).
    All None if `candles` is empty.

    MFE is the maximum (high - price_t) / price_t across all candles.
    MAE is the minimum (low - price_t) / price_t (negative number for drawdown).
    """
    if not candles or price_t <= 0:
        return None, None, None, None

    mfe = float("-inf")
    mae = float("inf")
    t_mfe: int | None = None
    t_mae: int | None = None

    for c in candles:
        high_pct = (float(c.high) - price_t) / price_t * 100
        low_pct = (float(c.low) - price_t) / price_t * 100
        minute_offset = int((c.time - ts0).total_seconds() // 60)
        if high_pct > mfe:
            mfe = high_pct
            t_mfe = minute_offset
        if low_pct < mae:
            mae = low_pct
            t_mae = minute_offset

    return mfe, mae, t_mfe, t_mae
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): add _compute_mfe_mae helper with time-tracking"
```

---

## Task 1.4: `_classify` — MISSED_OPPORTUNITY (AC OA-02)

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir el test (AC OA-02)**

```python
def test_classify_hold_as_missed_when_mfe_exceeds_target_and_no_sl_hit():
    """AC OA-02: price_t=100, atr_pct=1.0, sl_mult=0.3, rr=1.3 → SL_dist=0.3%, TP_target=0.39%.
    Vela posterior: high=100.5 (mfe +0.5%), low=99.95 (mae -0.05%).
    time_to_mfe (1) < time_to_mae (1) — same minute, MFE strictly greater than threshold,
    MAE not crossing SL → MISSED_OPPORTUNITY.
    """
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={
            "price": 100.0, "atr_ref_pct": 1.0,
            "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3,
        },
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.05, low=99.95, close=100.05),
        _candle(t0 + timedelta(minutes=10), high=100.5, low=100.0, close=100.5),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "MISSED_OPPORTUNITY"
    assert result.mfe_pct == pytest.approx(0.5, abs=1e-3)
    assert result.tp_target_pct == pytest.approx(0.39, abs=1e-3)
    assert result.matured is True
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py::test_classify_hold_as_missed_when_mfe_exceeds_target_and_no_sl_hit -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implementar `_classify` con el caso MISSED_OPPORTUNITY**

Reemplazar el `raise NotImplementedError` de `attribute()` y agregar `_classify`:

```python
def attribute(
    *,
    decision: Any,
    ohlcv_1m: list[Any],
    associated_trade: Any | None,
    horizon_min: int,
    now: datetime,
) -> DecisionAttribution:
    inputs = _extract_decision_inputs(decision)
    if inputs is None:
        return _unknown(decision, horizon_min, now)

    sl_dist_pct = inputs["sl_atr_mult"] * inputs["atr_pct_t"]
    tp_target_pct = inputs["min_rr_ratio"] * sl_dist_pct

    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(
        price_t=inputs["price_t"], candles=ohlcv_1m, ts0=decision.ts,
    )

    matured = now >= decision.ts + _minutes(horizon_min)
    forward_return_pct = _forward_return(inputs["price_t"], ohlcv_1m, decision.ts, horizon_min)

    classification = _classify(
        decision=decision, mfe=mfe, mae=mae, t_mfe=t_mfe, t_mae=t_mae,
        sl_dist_pct=sl_dist_pct, tp_target_pct=tp_target_pct,
        matured=matured, associated_trade=associated_trade,
    )

    return DecisionAttribution(
        decision_id=decision.id,
        horizon_min=horizon_min,
        matured=matured,
        forward_return_pct=forward_return_pct,
        mfe_pct=mfe,
        mae_pct=mae,
        time_to_mfe_min=t_mfe,
        time_to_mae_min=t_mae,
        sl_dist_pct=sl_dist_pct,
        tp_target_pct=tp_target_pct,
        classification=classification,
        computed_at=now,
    )


def _unknown(decision: Any, horizon_min: int, now: datetime) -> DecisionAttribution:
    return DecisionAttribution(
        decision_id=decision.id,
        horizon_min=horizon_min, matured=False,
        forward_return_pct=None, mfe_pct=None, mae_pct=None,
        time_to_mfe_min=None, time_to_mae_min=None,
        sl_dist_pct=None, tp_target_pct=None,
        classification="UNKNOWN", computed_at=now,
    )


def _minutes(n: int):
    from datetime import timedelta
    return timedelta(minutes=n)


def _forward_return(price_t: float, candles: list[Any], ts0: datetime, horizon_min: int) -> float | None:
    if not candles:
        return None
    target_ts = ts0 + _minutes(horizon_min)
    # Last candle whose time is <= target_ts (clipped to last available)
    last = candles[-1]
    for c in reversed(candles):
        if c.time <= target_ts:
            last = c
            break
    return (float(last.close) - price_t) / price_t * 100


def _classify(
    *,
    decision: Any,
    mfe: float | None,
    mae: float | None,
    t_mfe: int | None,
    t_mae: int | None,
    sl_dist_pct: float,
    tp_target_pct: float,
    matured: bool,
    associated_trade: Any | None,
) -> Classification:
    action = (decision.output or {}).get("action")
    if mfe is None or mae is None:
        return "UNKNOWN"
    mfe_hits_first = (t_mfe is not None and t_mae is not None and t_mfe < t_mae) \
                      or (t_mfe is not None and t_mae is None)
    if action == "HOLD":
        if mfe >= tp_target_pct and mae > -sl_dist_pct and mfe_hits_first:
            return "MISSED_OPPORTUNITY"
    return "PENDING"  # placeholder — subsequent tasks add other branches
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): classify HOLD with MFE > tp_target as MISSED_OPPORTUNITY"
```

---

## Task 1.5: `_classify` — GOOD_HOLD (AC OA-03 — MAE primero)

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir el test (AC OA-03 — SL hubiera pegado primero)**

```python
def test_classify_hold_as_good_when_mae_exceeds_sl_before_mfe():
    """AC OA-03: el precio cae a -0.4% (mae < -SL_dist) ANTES de subir a +0.5%.
    El SL hubiera pegado primero → GOOD_HOLD, no MISSED.
    """
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={
            "price": 100.0, "atr_ref_pct": 1.0,
            "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3,
        },
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.6, close=99.7),  # mae -0.4% acá
        _candle(t0 + timedelta(minutes=5), high=100.5, low=99.9, close=100.4),  # mfe +0.5% después
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_HOLD"


def test_classify_hold_as_good_when_mfe_below_tp_target():
    """Subió pero sin alcanzar el TP_target → GOOD_HOLD (no era oportunidad real)."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.3, low=99.95, close=100.2),  # mfe +0.3% < tp_target 0.39%
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_HOLD"
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v -k "good_when"`
Expected: 2 FAILED (devuelven `"PENDING"` placeholder).

- [ ] **Step 3: Extender `_classify` con la rama GOOD_HOLD**

Reemplazar la rama de HOLD en `_classify`:

```python
    if action == "HOLD":
        if not matured and mfe < tp_target_pct and mae > -sl_dist_pct:
            return "PENDING"
        if mfe >= tp_target_pct and mae > -sl_dist_pct and mfe_hits_first:
            return "MISSED_OPPORTUNITY"
        return "GOOD_HOLD"
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): classify HOLD as GOOD_HOLD when SL would have triggered first"
```

---

## Task 1.6: `_classify` — BLOCKED_GOOD_TRADE y CORRECTLY_BLOCKED (AC OA-04)

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir los tests**

```python
def test_classify_buy_rejected_as_blocked_good_when_mfe_hits_first():
    """AC OA-04: BUY rechazado por R5, pero MFE llega al TP_target sin que MAE cruce SL → BLOCKED_GOOD_TRADE."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=False,
    )
    candles = [
        _candle(t0 + timedelta(minutes=2), high=100.5, low=99.95, close=100.4),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "BLOCKED_GOOD_TRADE"


def test_classify_buy_rejected_as_correctly_blocked_when_mae_hits_first():
    """BUY rechazado y precio cae a -SL_dist antes de tocar TP_target → CORRECTLY_BLOCKED."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=False,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.5, close=99.6),  # mae -0.5% > SL 0.3%
        _candle(t0 + timedelta(minutes=10), high=100.5, low=99.7, close=100.4),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "CORRECTLY_BLOCKED"
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v -k "rejected"`
Expected: 2 FAILED.

- [ ] **Step 3: Extender `_classify` con BUY rechazado**

Después de la rama HOLD en `_classify`, antes del `return "PENDING"`:

```python
    if action == "BUY" and not decision.executed:
        if not matured and mfe < tp_target_pct and mae > -sl_dist_pct:
            return "PENDING"
        if mfe >= tp_target_pct and mae > -sl_dist_pct and mfe_hits_first:
            return "BLOCKED_GOOD_TRADE"
        return "CORRECTLY_BLOCKED"
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): classify rejected BUYs as BLOCKED_GOOD_TRADE or CORRECTLY_BLOCKED"
```

---

## Task 1.7: `_classify` — GOOD_BUY / BAD_BUY (ejecutados, con trade)

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir los tests**

```python
def test_classify_executed_buy_with_positive_pnl_as_good_buy():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(pnl_pct=Decimal("1.2"))
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.95, close=100.05),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_BUY"


def test_classify_executed_buy_with_negative_pnl_as_bad_buy():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(pnl_pct=Decimal("-0.5"))
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.5, close=99.6),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "BAD_BUY"


def test_classify_executed_buy_without_associated_trade_is_unknown():
    """Decisión ejecutada pero sin trade asociado todavía (race) → UNKNOWN."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=True,
    )
    candles = [_candle(t0 + timedelta(minutes=1), high=100.1, low=99.95, close=100.05)]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "UNKNOWN"
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v -k "executed_buy"`
Expected: 3 FAILED.

- [ ] **Step 3: Extender `_classify`**

Antes de las ramas HOLD/BUY rechazado:

```python
    if action == "BUY" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return "GOOD_BUY" if pnl > 0 else "BAD_BUY"
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): classify executed BUYs as GOOD_BUY/BAD_BUY by trade pnl_pct"
```

---

## Task 1.8: `_classify` — GOOD_SELL / BAD_SELL + PENDING + UNKNOWN por gaps

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py`
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir los tests**

```python
def test_classify_executed_sell_with_positive_trade_pnl_as_good_sell():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "SELL"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(pnl_pct=Decimal("0.8"))
    candles = [_candle(t0 + timedelta(minutes=1), high=100.1, low=99.9, close=100)]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_SELL"


def test_classify_pending_when_window_not_matured_and_no_resolution():
    """AC OA-05: ventana no madurada y todavía sin MFE >= TP ni MAE <= -SL."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.98, close=100.05),  # mfe +0.1% < 0.39%
    ]
    # now = ts + 30 min, horizon = 240 min → no madurado
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(minutes=30),
    )
    assert result.classification == "PENDING"
    assert result.matured is False


def test_classify_unknown_when_ohlcv_coverage_below_threshold():
    """AC OA-06: si > 30% de la ventana no tiene velas, clasificamos UNKNOWN."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    # ventana = 240 min, sólo 100 velas (40% coverage) → faltante 60% > 30%
    candles = [
        _candle(t0 + timedelta(minutes=i), high=100.05, low=99.98, close=100.0)
        for i in range(1, 101)
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "UNKNOWN"
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v -k "sell or pending or coverage"`
Expected: 3 FAILED.

- [ ] **Step 3: Extender `_classify` + agregar `_coverage_ok`**

```python
_OHLCV_MISSING_THRESHOLD_PCT = 30.0


def _coverage_ok(candles: list[Any], horizon_min: int, now: datetime, ts0: datetime) -> bool:
    """True if at least (100 - threshold)% of the expected 1m slots are present."""
    expected = min(horizon_min, int((now - ts0).total_seconds() // 60))
    if expected <= 0:
        return True
    missing_pct = (expected - len(candles)) / expected * 100
    return missing_pct <= _OHLCV_MISSING_THRESHOLD_PCT
```

Y al inicio de `attribute()`, después de `inputs is None`:

```python
    if not _coverage_ok(ohlcv_1m, horizon_min, now, decision.ts):
        return _unknown(decision, horizon_min, now)
```

Y en `_classify`, agregar la rama SELL (después de la rama BUY ejecutado):

```python
    if action == "SELL" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return "GOOD_SELL" if pnl > 0 else "BAD_SELL"
```

Reemplazar el placeholder final `return "PENDING"` por:

```python
    if not matured:
        return "PENDING"
    return "UNKNOWN"  # action desconocida o sin datos suficientes
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/tests/test_outcome_attribution.py
git commit -m "feat(agents): classify SELLs, PENDING immature window, UNKNOWN on OHLCV gaps"
```

---

## Task 1.9: Idempotencia — test contractual de `attribute()`

**Files:**
- Modify: `trading-engine/tests/test_outcome_attribution.py`

- [ ] **Step 1: Escribir el test contractual (AC OA-01)**

```python
def test_attribute_is_deterministic_for_same_inputs():
    """AC OA-01: misma entrada produce mismo output (función pura)."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [_candle(t0 + timedelta(minutes=i), high=100.5, low=99.95, close=100.4)
               for i in range(1, 241)]
    now_fixed = t0 + timedelta(hours=5)
    r1 = attribute(decision=decision, ohlcv_1m=candles, associated_trade=None,
                   horizon_min=240, now=now_fixed)
    r2 = attribute(decision=decision, ohlcv_1m=candles, associated_trade=None,
                   horizon_min=240, now=now_fixed)
    assert r1 == r2
```

- [ ] **Step 2: Correr y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_outcome_attribution.py::test_attribute_is_deterministic_for_same_inputs -v`
Expected: PASS (la función ya es determinística por construcción).

- [ ] **Step 3: Commit**

```bash
git add trading-engine/tests/test_outcome_attribution.py
git commit -m "test(agents): contract test for outcome_attribution determinism"
```

---

## Task 1.10: Helpers async para el job — fetch candidates + fetch OHLCV

**Files:**
- Create: `trading-engine/agents/outcome_attribution_job.py`
- Create: `trading-engine/tests/test_outcome_attribution_job.py`

- [ ] **Step 1: Escribir el test de `_fetch_candidates`**

```python
# trading-engine/tests/test_outcome_attribution_job.py
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

# Reuse the SQLite metadata + before_insert event listeners from test_supervisor.
from trading_engine.tests.test_supervisor import (
    _sqlite_metadata,
    _before_insert_decision, _before_insert_trade,
)
from shared.db.models import Decision, DecisionOutcome, Trade
from agents.outcome_attribution_job import _fetch_candidates


@pytest_asyncio.fixture
async def session():
    event.listen(Decision, "before_insert", _before_insert_decision)
    event.listen(Trade, "before_insert", _before_insert_trade)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    event.remove(Decision, "before_insert", _before_insert_decision)
    event.remove(Trade, "before_insert", _before_insert_trade)
    await engine.dispose()


async def test_fetch_candidates_returns_decisions_in_window(session):
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    # Antigua (fuera de ventana 25h)
    session.add(Decision(
        ts=now - timedelta(hours=26), agent="decisor", model="m",
        input={}, output={"action": "HOLD"}, executed=False,
    ))
    # Reciente sin outcome
    fresh = Decision(
        ts=now - timedelta(hours=2), agent="decisor", model="m",
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"}, executed=False,
    )
    session.add(fresh)
    # Reciente con outcome ya GOOD_HOLD (no se reprocesa)
    done = Decision(
        ts=now - timedelta(hours=3), agent="decisor", model="m",
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"}, executed=False,
    )
    session.add(done)
    await session.commit()
    session.add(DecisionOutcome(
        decision_id=done.id, horizon_min=240, matured=True,
        classification="GOOD_HOLD",
    ))
    await session.commit()

    candidates = await _fetch_candidates(session, now=now)
    ids = {c.id for c in candidates}
    assert fresh.id in ids
    assert done.id not in ids
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_outcome_attribution_job.py -v`
Expected: `ImportError: cannot import name '_fetch_candidates'`.

- [ ] **Step 3: Implementar `_fetch_candidates` y `_fetch_ohlcv`**

```python
# trading-engine/agents/outcome_attribution_job.py
"""Background job that attributes outcomes to recent decisor decisions.

Runs hourly (configurable). Idempotent: upserts on `decision_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import structlog
from sqlalchemy import select, and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Decision, DecisionOutcome, Ohlcv, Trade
from agents.outcome_attribution import attribute

logger = structlog.get_logger()

_WINDOW_HOURS = 25
_BUFFER_MIN = 15


async def _fetch_candidates(session: AsyncSession, *, now: datetime) -> list[Decision]:
    """Decisor decisions in (now-25h, now-15min) with no outcome or with PENDING."""
    since = now - timedelta(hours=_WINDOW_HOURS)
    upto = now - timedelta(minutes=_BUFFER_MIN)
    stmt = (
        select(Decision)
        .outerjoin(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(
            Decision.agent == "decisor",
            Decision.ts >= since,
            Decision.ts <= upto,
            or_(
                DecisionOutcome.decision_id.is_(None),
                DecisionOutcome.classification == "PENDING",
            ),
        )
        .order_by(Decision.ts.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _fetch_ohlcv_1m(
    session: AsyncSession, *, ts_from: datetime, ts_to: datetime,
) -> list[Ohlcv]:
    """Velas 1m en bloque para cubrir todas las ventanas de las decisions del tick."""
    stmt = (
        select(Ohlcv)
        .where(Ohlcv.timeframe == "1m", Ohlcv.time >= ts_from, Ohlcv.time <= ts_to)
        .order_by(Ohlcv.time.asc())
    )
    return list((await session.execute(stmt)).scalars().all())
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_outcome_attribution_job.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution_job.py trading-engine/tests/test_outcome_attribution_job.py
git commit -m "feat(agents): fetch candidates and OHLCV for outcome attribution job"
```

---

## Task 1.11: UPSERT helper + dialect-agnostic implementation

**Files:**
- Modify: `trading-engine/agents/outcome_attribution_job.py`
- Modify: `trading-engine/tests/test_outcome_attribution_job.py`

- [ ] **Step 1: Escribir test de idempotencia y UPSERT (AC OA-07, OA-08)**

```python
from agents.outcome_attribution_job import _upsert_outcome
from agents.outcome_attribution import DecisionAttribution


async def test_upsert_outcome_inserts_then_updates(session):
    decision = Decision(
        ts=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        agent="decisor", model="m",
        input={}, output={"action": "HOLD"}, executed=False,
    )
    session.add(decision)
    await session.commit()

    attr1 = DecisionAttribution(
        decision_id=decision.id, horizon_min=240, matured=False,
        forward_return_pct=None, mfe_pct=0.1, mae_pct=-0.05,
        time_to_mfe_min=5, time_to_mae_min=2,
        sl_dist_pct=0.3, tp_target_pct=0.39,
        classification="PENDING",
        computed_at=datetime(2026, 5, 18, 13, 0, tzinfo=timezone.utc),
    )
    await _upsert_outcome(session, attr1)
    await session.commit()

    attr2 = DecisionAttribution(
        decision_id=decision.id, horizon_min=240, matured=True,
        forward_return_pct=0.5, mfe_pct=0.5, mae_pct=-0.05,
        time_to_mfe_min=15, time_to_mae_min=2,
        sl_dist_pct=0.3, tp_target_pct=0.39,
        classification="MISSED_OPPORTUNITY",
        computed_at=datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc),
    )
    await _upsert_outcome(session, attr2)
    await session.commit()

    rows = (await session.execute(select(DecisionOutcome))).scalars().all()
    assert len(rows) == 1
    assert rows[0].classification == "MISSED_OPPORTUNITY"
    assert rows[0].matured is True
```

- [ ] **Step 2: Correr y verificar que falla**

Expected: `ImportError: cannot import name '_upsert_outcome'`.

- [ ] **Step 3: Implementar `_upsert_outcome` (dialect-agnostic)**

```python
async def _upsert_outcome(session: AsyncSession, attr) -> None:
    """Dialect-agnostic UPSERT on (decision_id) PK.

    Postgres uses ON CONFLICT; SQLite (tests) uses delete + insert (simpler, transactional).
    """
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "postgresql"
    payload = dict(
        decision_id=attr.decision_id,
        horizon_min=attr.horizon_min,
        matured=attr.matured,
        forward_return_pct=attr.forward_return_pct,
        mfe_pct=attr.mfe_pct,
        mae_pct=attr.mae_pct,
        time_to_mfe_min=attr.time_to_mfe_min,
        time_to_mae_min=attr.time_to_mae_min,
        sl_dist_pct=attr.sl_dist_pct,
        tp_target_pct=attr.tp_target_pct,
        classification=attr.classification,
        computed_at=attr.computed_at,
    )
    if dialect == "postgresql":
        stmt = pg_insert(DecisionOutcome).values(**payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["decision_id"],
            set_={k: v for k, v in payload.items() if k != "decision_id"},
        )
        await session.execute(stmt)
    else:
        from sqlalchemy import delete
        await session.execute(
            delete(DecisionOutcome).where(DecisionOutcome.decision_id == attr.decision_id)
        )
        session.add(DecisionOutcome(**payload))
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `cd trading-engine && pytest tests/test_outcome_attribution_job.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution_job.py trading-engine/tests/test_outcome_attribution_job.py
git commit -m "feat(agents): idempotent UPSERT for decision_outcomes (Postgres+SQLite)"
```

---

## Task 1.12: Job entrypoint `outcome_attribution_tick()` end-to-end

**Files:**
- Modify: `trading-engine/agents/outcome_attribution_job.py`
- Modify: `trading-engine/tests/test_outcome_attribution_job.py`

- [ ] **Step 1: Escribir test end-to-end**

```python
async def test_outcome_attribution_tick_classifies_pending_and_finalized_decisions(session):
    """End-to-end: una decisión madura con MFE > tp_target → MISSED_OPPORTUNITY persistido."""
    from agents.outcome_attribution_job import outcome_attribution_tick

    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    now = t0 + timedelta(hours=5)
    decision = Decision(
        ts=t0, agent="decisor", model="m",
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"}, executed=False,
    )
    session.add(decision)
    # Seed OHLCV 1m con rally (mfe +0.5%) sin caer (mae -0.05%)
    for i in range(1, 241):
        session.add(Ohlcv(
            time=t0 + timedelta(minutes=i), timeframe="1m",
            open=Decimal("100.0"), high=Decimal("100.5"),
            low=Decimal("99.95"), close=Decimal("100.4"),
            volume=Decimal("1.0"),
        ))
    await session.commit()

    factory = lambda: _SessionContext(session)
    await outcome_attribution_tick(
        session_factory=factory, horizon_min=240, now_fn=lambda: now,
    )

    outcome = (await session.execute(
        select(DecisionOutcome).where(DecisionOutcome.decision_id == decision.id)
    )).scalar_one()
    assert outcome.classification == "MISSED_OPPORTUNITY"


class _SessionContext:
    """Async context manager wrapping an existing session for tests."""
    def __init__(self, session): self._s = session
    async def __aenter__(self): return self._s
    async def __aexit__(self, *_): return None
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `cd trading-engine && pytest tests/test_outcome_attribution_job.py::test_outcome_attribution_tick_classifies_pending_and_finalized_decisions -v`
Expected: `ImportError: cannot import name 'outcome_attribution_tick'`.

- [ ] **Step 3: Implementar el entrypoint**

```python
from typing import Callable, ContextManager


async def outcome_attribution_tick(
    *,
    session_factory: Callable[[], "ContextManager[AsyncSession]"],
    horizon_min: int = 240,
    now_fn: Callable[[], datetime] | None = None,
) -> None:
    """Tick called by the scheduler. Idempotent."""
    now = (now_fn or _utcnow)()
    async with session_factory() as session:
        candidates = await _fetch_candidates(session, now=now)
        if not candidates:
            logger.info("outcome_attribution.job.no_candidates")
            return
        ohlcv = await _fetch_ohlcv_1m(
            session,
            ts_from=min(c.ts for c in candidates),
            ts_to=now,
        )
        ohlcv_by_minute = _index_ohlcv_by_minute(ohlcv)
        processed = 0
        for d in candidates:
            window = _slice_window(
                ohlcv_by_minute, ts_from=d.ts, ts_to=d.ts + timedelta(minutes=horizon_min),
            )
            trade = (await _load_trade(session, d.trade_id)) if d.trade_id else None
            attr = attribute(
                decision=d, ohlcv_1m=window, associated_trade=trade,
                horizon_min=horizon_min, now=now,
            )
            await _upsert_outcome(session, attr)
            processed += 1
        await session.commit()
        logger.info("outcome_attribution.job.completed", processed=processed)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _index_ohlcv_by_minute(rows: Iterable[Ohlcv]) -> dict[datetime, Ohlcv]:
    return {_truncate_to_minute(r.time): r for r in rows}


def _truncate_to_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _slice_window(
    index_by_min: dict[datetime, Ohlcv], *, ts_from: datetime, ts_to: datetime,
) -> list[Ohlcv]:
    start = _truncate_to_minute(ts_from)
    end = _truncate_to_minute(ts_to)
    out = []
    cursor = start + timedelta(minutes=1)
    while cursor <= end:
        row = index_by_min.get(cursor)
        if row is not None:
            out.append(row)
        cursor += timedelta(minutes=1)
    return out


async def _load_trade(session: AsyncSession, trade_id) -> Trade | None:
    return (await session.execute(
        select(Trade).where(Trade.id == trade_id)
    )).scalar_one_or_none()
```

- [ ] **Step 4: Correr todos los tests del módulo y verificar que pasan**

Run: `cd trading-engine && pytest tests/test_outcome_attribution_job.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/outcome_attribution_job.py trading-engine/tests/test_outcome_attribution_job.py
git commit -m "feat(agents): outcome_attribution_tick entrypoint (idempotent, batched OHLCV fetch)"
```

---

## Task 1.13: Wire del job al `EngineScheduler` y `main.py`

**Files:**
- Modify: `trading-engine/scheduler.py`
- Modify: `trading-engine/main.py`

- [ ] **Step 1: Inspeccionar el patrón actual del scheduler**

Run: `cd trading-engine && grep -n "add_" scheduler.py`
Expected: ver métodos `add_decisor`, `add_supervisor`, `add_fee_refresh`, `add_position_refresh`, `add_order_tracker`.

- [ ] **Step 2: Agregar método `add_outcome_attribution`**

En `trading-engine/scheduler.py`, sumar después del último `add_*`:

```python
    def add_outcome_attribution(self, fn, *, interval_min: int = 60) -> None:
        self._sched.add_job(
            fn,
            trigger=IntervalTrigger(minutes=interval_min),
            id="outcome_attribution",
            max_instances=1,
            coalesce=True,
        )
```

- [ ] **Step 3: Wire en `main.py`**

En `trading-engine/main.py`, dentro de `run()`, después del `add_position_refresh` (o similar):

```python
    async def outcome_attribution_job_wrapper():
        from agents.outcome_attribution_job import outcome_attribution_tick
        # horizon_min lee de config; default 240
        horizon_min = 240
        try:
            async with session_factory() as cfg_session:
                store = ConfigStore(cfg_session)
                horizon_min = int(await store.get_typed(ConfigKey.EXPECTED_HOLDING_MAX_MIN))
        except Exception as e:
            logger.warning("outcome_attribution.config_read_failed", error=str(e))
        await outcome_attribution_tick(
            session_factory=session_factory,
            horizon_min=horizon_min,
        )

    scheduler.add_outcome_attribution(outcome_attribution_job_wrapper, interval_min=60)
    logger.info("scheduler.outcome_attribution_registered", interval_min=60)
```

- [ ] **Step 4: Verificar que el módulo `main` importa sin errores**

```bash
cd trading-engine && python -c "import main"
```

Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/scheduler.py trading-engine/main.py
git commit -m "feat(engine): register outcome_attribution job (interval=60min)"
```

---

## Task 1.14: Endpoint REST `GET /api/decisions/outcomes`

**Files:**
- Modify: `web/api/decisions.py`
- Create: `web/tests/test_decisions_outcomes_api.py`

- [ ] **Step 1: Inspeccionar el patrón del router existente**

Run: `cd web && grep -n "router\." api/decisions.py | head -20`
Expected: ver el patrón actual de endpoints + DTOs.

- [ ] **Step 2: Escribir el test del endpoint (AC OA-10)**

```python
# web/tests/test_decisions_outcomes_api.py
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


async def test_get_outcomes_returns_recent_classifications(client, app_with_db):
    from shared.db.models import Decision, DecisionOutcome

    factory = app_with_db.state.session_factory
    async with factory() as s:
        now = datetime.now(tz=timezone.utc)
        decision = Decision(
            ts=now - timedelta(hours=2), agent="decisor", model="m",
            input={"price": 100.0}, output={"action": "HOLD", "confidence": 0.55},
            executed=False,
        )
        s.add(decision)
        await s.commit()
        s.add(DecisionOutcome(
            decision_id=decision.id, horizon_min=240, matured=True,
            forward_return_pct=Decimal("0.5"), mfe_pct=Decimal("0.5"),
            mae_pct=Decimal("-0.05"), time_to_mfe_min=15, time_to_mae_min=1,
            sl_dist_pct=Decimal("0.3"), tp_target_pct=Decimal("0.39"),
            classification="MISSED_OPPORTUNITY",
        ))
        await s.commit()

    resp = await client.get("/api/decisions/outcomes?since_hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    assert item["classification"] == "MISSED_OPPORTUNITY"
    assert item["mfe_pct"] == pytest.approx(0.5)
    assert item["action"] == "HOLD"


async def test_get_outcomes_filter_by_classification(client, app_with_db):
    from shared.db.models import Decision, DecisionOutcome

    factory = app_with_db.state.session_factory
    async with factory() as s:
        now = datetime.now(tz=timezone.utc)
        d1 = Decision(ts=now - timedelta(hours=2), agent="decisor", model="m",
                      input={}, output={"action": "HOLD"}, executed=False)
        d2 = Decision(ts=now - timedelta(hours=3), agent="decisor", model="m",
                      input={}, output={"action": "HOLD"}, executed=False)
        s.add_all([d1, d2])
        await s.commit()
        s.add(DecisionOutcome(decision_id=d1.id, horizon_min=240, matured=True,
                              classification="MISSED_OPPORTUNITY"))
        s.add(DecisionOutcome(decision_id=d2.id, horizon_min=240, matured=True,
                              classification="GOOD_HOLD"))
        await s.commit()

    resp = await client.get(
        "/api/decisions/outcomes?since_hours=24&classification=MISSED_OPPORTUNITY"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["classification"] == "MISSED_OPPORTUNITY"
```

- [ ] **Step 3: Correr y verificar que falla**

Run: `cd web && pytest tests/test_decisions_outcomes_api.py -v`
Expected: `404 Not Found` (endpoint no existe).

- [ ] **Step 4: Implementar el DTO y el endpoint**

En `web/api/decisions.py`, agregar al final (antes del último `}`):

```python
class DecisionOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision_id: UUID
    ts: datetime
    action: str | None
    confidence: float | None
    regime: str | None
    executed: bool
    rejected_reason: str | None

    horizon_min: int
    matured: bool
    forward_return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    time_to_mfe_min: int | None
    time_to_mae_min: int | None
    sl_dist_pct: float | None
    tp_target_pct: float | None
    classification: str
    computed_at: datetime


@router.get("/outcomes", response_model=list[DecisionOutcomeOut])
async def list_outcomes(
    since_hours: int = Query(24, ge=1, le=168),
    classification: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionOutcomeOut]:
    since = datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(Decision, DecisionOutcome)
        .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(Decision.ts >= since)
        .order_by(Decision.ts.desc())
        .limit(limit)
    )
    if classification:
        stmt = stmt.where(DecisionOutcome.classification == classification)
    rows = (await session.execute(stmt)).all()
    out = []
    for d, o in rows:
        out.append(DecisionOutcomeOut(
            decision_id=d.id,
            ts=d.ts,
            action=(d.output or {}).get("action"),
            confidence=(d.output or {}).get("confidence"),
            regime=(d.output or {}).get("regime"),
            executed=d.executed,
            rejected_reason=d.rejected_reason,
            horizon_min=o.horizon_min,
            matured=o.matured,
            forward_return_pct=float(o.forward_return_pct) if o.forward_return_pct is not None else None,
            mfe_pct=float(o.mfe_pct) if o.mfe_pct is not None else None,
            mae_pct=float(o.mae_pct) if o.mae_pct is not None else None,
            time_to_mfe_min=o.time_to_mfe_min,
            time_to_mae_min=o.time_to_mae_min,
            sl_dist_pct=float(o.sl_dist_pct) if o.sl_dist_pct is not None else None,
            tp_target_pct=float(o.tp_target_pct) if o.tp_target_pct is not None else None,
            classification=o.classification,
            computed_at=o.computed_at,
        ))
    return out
```

Asegurarse de que los imports del top del archivo incluyan: `from shared.db.models import DecisionOutcome`, `from fastapi import Query`.

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `cd web && pytest tests/test_decisions_outcomes_api.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add web/api/decisions.py web/tests/test_decisions_outcomes_api.py
git commit -m "feat(api): GET /api/decisions/outcomes with classification filter"
```

---

## Task 1.15: Validación end-to-end + smoke check del job en local

**Files:**
- Sólo verificación, sin cambios de código.

- [ ] **Step 1: Correr toda la suite del trading-engine**

Run: `cd trading-engine && pytest -v 2>&1 | tail -30`
Expected: todos pasan (incluye los nuevos + los que ya existían).

- [ ] **Step 2: Correr toda la suite del web**

Run: `cd web && pytest -v 2>&1 | tail -20`
Expected: todos pasan.

- [ ] **Step 3: Levantar el stack con Docker y verificar logs del scheduler**

Run: `docker-compose up -d --build && sleep 30 && docker-compose logs trading-engine | grep -E "(scheduler|outcome_attribution)"`
Expected: presencia de `scheduler.outcome_attribution_registered` con `interval_min=60`.

- [ ] **Step 4: Forzar una corrida temprana del job vía Python REPL (opcional)**

```bash
docker-compose exec trading-engine python -c "
import asyncio
from agents.outcome_attribution_job import outcome_attribution_tick
from main import session_factory
asyncio.run(outcome_attribution_tick(session_factory=session_factory))
"
```

Expected: log `outcome_attribution.job.no_candidates` (BD nueva) o `outcome_attribution.job.completed processed=N`.

- [ ] **Step 5: Verificar la tabla**

```bash
docker-compose exec postgres psql -U trading -d trading_db -c "SELECT classification, COUNT(*) FROM decision_outcomes GROUP BY 1;"
```

Expected: tabla existe (vacía si no hay decisiones, o con filas si el bot ya operó).

- [ ] **Step 6: Abrir PR contra `main` (o la rama base apropiada)**

```bash
git push origin feature/decisor-v2
gh pr create --title "[CB-XXX] feat: contrafactual outcome attribution (PR 1 — foundation)" \
  --body-file - <<'EOF'
# Requerimiento

## Descripción
PR 1 del spec `docs/superpowers/specs/2026-05-18-supervisor-counterfactual-design.md`.
Implementa el cómputo, persistencia (tabla `decision_outcomes`), job APScheduler (1h) y endpoint REST `GET /api/decisions/outcomes` para clasificar cada decisión del Decisor contra el OHLCV posterior.

Sin integración al Supervisor ni al Frontend — esos vienen en PR 2 y PR 3 después de validar este PR en paper trading 24 h.

## Tasks / Issues resueltos
- Implementa [CB-XXX](url)

## Dependencias
Ninguna.

## Testing

### Pruebas manuales
- `docker-compose up -d --build` → log `scheduler.outcome_attribution_registered`.
- Después de 1 hora (o forzando con `docker-compose exec`): log `outcome_attribution.job.completed processed=N`.
- `curl localhost:8100/api/decisions/outcomes?since_hours=24` devuelve la lista.
- `psql ... SELECT classification, COUNT(*) FROM decision_outcomes GROUP BY 1;` muestra la distribución.

### Test automáticos agregados
- [x] Unitarios (`test_outcome_attribution.py` — 14 tests)
- [x] Integración (`test_outcome_attribution_job.py` — 3 tests, `test_decisions_outcomes_api.py` — 2 tests)
- [ ] Funcionales

## Cambios en DB

- [x] **Este PR incluye cambios en la BD**

### Tablas afectadas
- `decision_outcomes` (nueva)

### Migraciones
- `008_add_decision_outcomes`

### Scripts
```sql
-- Ver trading-engine/alembic/versions/008_add_decision_outcomes.py
```

## Notas

- Función `attribute()` es **pura**: cero queries, cero clocks no inyectados.
- UPSERT idempotente con fallback dialect-agnostic (Postgres ON CONFLICT, SQLite delete+insert).
- Job filtra `ts <= now - 15min` para evitar race con escrituras del Decisor.
- Umbral de cobertura OHLCV faltante: 30% (configurable como constante).
- Validación esperada en paper trading antes de habilitar PR 2: revisar `SELECT classification, COUNT(*) FROM decision_outcomes WHERE computed_at >= now() - interval '24 hours' GROUP BY 1;` y comprobar que la distribución es razonable (no todo PENDING/UNKNOWN).
EOF
```

- [ ] **Step 7: Commit final del plan completado (sólo si hay cambios sin commitear)**

```bash
git status  # debe ser limpio
```

---

## Self-Review

**Spec coverage:**
- ✅ AC OA-01 (determinismo): Task 1.9.
- ✅ AC OA-02 (MISSED): Task 1.4.
- ✅ AC OA-03 (GOOD_HOLD por orden temporal): Task 1.5.
- ✅ AC OA-04 (BLOCKED_GOOD): Task 1.6.
- ✅ AC OA-05 (PENDING): Task 1.8.
- ✅ AC OA-06 (UNKNOWN por gaps): Task 1.8.
- ✅ AC OA-07 (idempotencia del job): Task 1.11.
- ✅ AC OA-08 (UPSERT, no inserta duplicados): Task 1.11.
- ✅ AC OA-10 (endpoint con filtros): Task 1.14.
- ⏭ AC OA-09 / OA-09b: dependen del Supervisor, son **PR 2**.
- ⏭ AC OA-11 / OA-12: dependen del Frontend, son **PR 3**.
- ⏭ AC OA-13: las specs se actualizan en los 3 PRs sucesivos.

**Placeholder scan:** ningún "TBD" / "fill in details" / "implement later". Cada step tiene código completo o comando exacto con output esperado.

**Type consistency:**
- `DecisionAttribution.classification` es `Classification = Literal[...]` consistente entre Task 1.2 y Task 1.8.
- `attribute()` signature es la misma en todas las invocaciones (`decision`, `ohlcv_1m`, `associated_trade`, `horizon_min`, `now` keyword-only).
- `_upsert_outcome` recibe `DecisionAttribution`, no `dict` — consistente con Task 1.11 y Task 1.12.
- `outcome_attribution_tick(session_factory, horizon_min, now_fn)` consistente en Task 1.12 y Task 1.13.

**Cosas que dejo fuera del scope de PR 1 (por diseño):**
- Backfill de decisiones > 25 h (fuera de scope §10 del spec).
- Optimización con materialized view (YAGNI; lo evaluamos si el endpoint se vuelve lento).
- Métricas Prometheus / OTel (sin infra de métricas en el repo).
- Actualización de specs (`docs/specs/*`): la documentación oficial se actualiza al final del PR cuando los tests estén verdes, pero el cambio es trivial (agregar entry de la tabla y del endpoint a `03-data-model.md` y `04-api-contracts.md`). Se puede dejar como subtask al cierre del PR o como PR de docs separado.
