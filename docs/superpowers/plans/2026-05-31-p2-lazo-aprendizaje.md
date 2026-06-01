# P2 — Cerrar el Lazo de Aprendizaje Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Modelo de ejecución:** subagentes con `model: sonnet`.

**Goal:** Hacer que el sistema realmente aprenda de sus errores: (1) clasificar la calidad de cada entrada de trading por MFE/MAE con umbral neto de fees en vez de solo el signo del P&L, y (2) dar al Supervisor un lazo cerrado donde las sugerencias de configuración que degradan las métricas se revierten automáticamente al día siguiente.

**Architecture:** Dos lazos de feedback independientes. Task 1 mejora la *señal de entrada* al pipeline de aprendizaje: `outcome_attribution._classify` pasa de `pnl > 0` a `pnl > net_fee_threshold`, aislando la calidad de la decisión de entrada de los errores del exit. Task 2 cierra el lazo de configuración del Supervisor: almacena un baseline de métricas en el `output` JSONB de cada `Decision` de supervisor cuando aplica cambios, y al inicio de cada run chequea si esos cambios degradaron métricas y los revierte. Task 3 robustece el parser JSON del Supervisor (mismo patrón frágil que P1-T1 corrigió en el Decisor). No requieren migraciones de BD — usan columnas JSONB existentes.

**Tech Stack:** Python 3.11, SQLAlchemy async, Pydantic, pytest + pytest-asyncio.

**Contexto empírico:**
- `outcome_attribution.py:168-175`: BUY ejecutado → `pnl > 0` → GOOD_BUY. Un trade que ganó +0.10% con fees round-trip 0.20% queda como GOOD_BUY cuando en realidad perdió dinero neto. Clasifica mal → el postmortem aprende de señales incorrectas.
- `supervisor.py:639-712` (`_apply_config_suggestions`): aplica cambios vía `ConfigStore.set`, guarda en `config_history`. No hay comparación de métricas pre/post ni rollback automático. Es open-loop.
- `supervisor.py:80-87` (`_parse_json_strict`): mismo `split("```")[1]` frágil de P1. Si el LLM de sugerencias de config emite prosa o fence sin cerrar → `json.loads` falla → se pierde la sugerencia silenciosamente.
- `models.py:62`: `Decision.output` es `JSONB` — se puede agregar claves arbitrarias sin migración.
- `models.py:204-216`: `ConfigHistory` tiene `id, ts, key, old_value, new_value, changed_by` — tiene `old_value` para revertir.

---

## Estructura de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `trading-engine/agents/outcome_attribution.py:145-175` | Modificar | Agregar `net_fee_threshold_pct` a `_classify` y `attribute` |
| `trading-engine/agents/outcome_attribution_job.py:108-142` | Modificar | Leer `MIN_ROUNDTRIP_FEE_PCT` de config y pasarlo al tick |
| `trading-engine/tests/test_outcome_attribution.py` | Modificar | Tests del threshold de fees |
| `trading-engine/agents/supervisor.py:80-87` | Modificar | `_parse_json_strict` robusto (balanced-brace) |
| `trading-engine/agents/supervisor.py:221+` | Modificar | `run()`: llamar a `_maybe_revert_degraded_config` + guardar baseline |
| `trading-engine/agents/supervisor.py` | Modificar | Nueva `_maybe_revert_degraded_config()` |
| `trading-engine/tests/test_supervisor.py` | Modificar | Tests de revert + baseline |

**Orden:** Tasks 1, 2, 3 son independientes entre sí. Task 4 (no-regresión) va último.

---

### Task 1: Outcome attribution — threshold de fees para GOOD/BAD_BUY

**Files:**
- Modify: `trading-engine/agents/outcome_attribution.py:145,175`
- Modify: `trading-engine/agents/outcome_attribution_job.py:108-142`
- Test: `trading-engine/tests/test_outcome_attribution.py`

**Por qué:** `_classify` línea 175 hace `"GOOD_BUY" if pnl > 0`. Un trade +0.10% con fees 0.20% round-trip es una pérdida neta → BAD_BUY. El postmortem aprende que esa entrada fue buena cuando no lo fue. El fix: `pnl > net_fee_threshold_pct` donde el threshold es `MIN_ROUNDTRIP_FEE_PCT` (0.20 desde P0).

- [ ] **Step 1: Escribir los tests que fallan**

Leer las primeras 60 líneas de `trading-engine/tests/test_outcome_attribution.py` para ver los helpers existentes (`_candle`, `_dense_candles`, `_buy_decision_executed` si existe). Luego agregar al final del archivo:

```python
# ─────────────── P2-T1: threshold de fees en GOOD/BAD_BUY ───────────────

def _buy_decision_exec(ts: datetime, price: float = 100.0):
    """SimpleNamespace que simula un Decision con BUY ejecutado."""
    from uuid import uuid4
    return SimpleNamespace(
        id=uuid4(),
        ts=ts,
        output={
            "action": "BUY",
            "stop_loss": price * 0.99,
            "take_profit": price * 1.02,
        },
        executed=True,
        rejected_reason=None,
        trade_id=uuid4(),
    )


def _trade_pnl(pnl_pct: float):
    """SimpleNamespace que simula un Trade con P&L conocido."""
    return SimpleNamespace(pnl_pct=Decimal(str(pnl_pct)))


def test_classify_buy_when_pnl_above_fee_threshold_should_be_good():
    """Ganancia que cubre fees → GOOD_BUY."""
    from agents.outcome_attribution import _classify
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = _dense_candles(t0, horizon_min=10, peaks=[])
    decision = _buy_decision_exec(t0)
    trade = _trade_pnl(0.30)   # +0.30% > threshold 0.20%

    result = _classify(
        decision=decision, mfe=2.0, mae=-0.5,
        t_mfe=5, t_mae=8, sl_dist_pct=1.0, tp_target_pct=2.0,
        matured=True, associated_trade=trade, candles=candles,
        horizon_min=10, ts0=t0,
        net_fee_threshold_pct=0.20,
    )
    assert result == "GOOD_BUY"


def test_classify_buy_when_pnl_positive_but_below_fee_threshold_should_be_bad():
    """Ganancia que NO cubre fees → BAD_BUY aunque pnl > 0."""
    from agents.outcome_attribution import _classify
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = _dense_candles(t0, horizon_min=10, peaks=[])
    decision = _buy_decision_exec(t0)
    trade = _trade_pnl(0.10)   # +0.10% < threshold 0.20%

    result = _classify(
        decision=decision, mfe=2.0, mae=-0.5,
        t_mfe=5, t_mae=8, sl_dist_pct=1.0, tp_target_pct=2.0,
        matured=True, associated_trade=trade, candles=candles,
        horizon_min=10, ts0=t0,
        net_fee_threshold_pct=0.20,
    )
    assert result == "BAD_BUY"


def test_classify_buy_default_threshold_zero_preserves_backward_compat():
    """Sin threshold (default 0.0): pnl > 0 → GOOD_BUY (comportamiento anterior)."""
    from agents.outcome_attribution import _classify
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = _dense_candles(t0, horizon_min=10, peaks=[])
    decision = _buy_decision_exec(t0)
    trade = _trade_pnl(0.05)   # pequeña ganancia

    result = _classify(
        decision=decision, mfe=2.0, mae=-0.5,
        t_mfe=5, t_mae=8, sl_dist_pct=1.0, tp_target_pct=2.0,
        matured=True, associated_trade=trade, candles=candles,
        horizon_min=10, ts0=t0,
        # sin net_fee_threshold_pct → default 0.0
    )
    assert result == "GOOD_BUY"
```

- [ ] **Step 2: Verificar que fallan**

```bash
cd trading-engine && python -m pytest tests/test_outcome_attribution.py -k "fee_threshold" -v -p no:cov 2>&1 | tail -15
```
Expected: 3 FAIL — `TypeError: _classify() got an unexpected keyword argument 'net_fee_threshold_pct'`.

- [ ] **Step 3: Agregar `net_fee_threshold_pct` a `_classify` en `outcome_attribution.py`**

En `trading-engine/agents/outcome_attribution.py`, modificar la firma de `_classify` (línea 145) para agregar el parámetro al final de los keyword-only args:
```python
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
    candles: list[Any],
    horizon_min: int,
    ts0: datetime,
    net_fee_threshold_pct: float = 0.0,     # ← agregar esta línea
) -> Classification:
```

Y cambiar la línea 175 de:
```python
        return "GOOD_BUY" if pnl > 0 else "BAD_BUY"
```
a:
```python
        return "GOOD_BUY" if pnl > net_fee_threshold_pct else "BAD_BUY"
```

- [ ] **Step 4: Agregar `net_fee_threshold_pct` a `attribute()` y pasarlo a `_classify`**

En el mismo archivo, buscar la función `attribute()` (se llama desde el job, línea 135 del job). Agregar el parámetro a su firma (keyword-only, default 0.0) y pasarlo al call a `_classify`. La firma actual de `attribute()` debe ser algo como:

```python
def attribute(
    *,
    decision: Any,
    ohlcv_1m: list[Any],
    associated_trade: Any | None,
    horizon_min: int,
    now: datetime,
    coverage_threshold_pct: float = 30.0,
    net_fee_threshold_pct: float = 0.0,   # ← agregar
) -> "DecisionAttribution":
```

Y dentro del cuerpo de `attribute()`, pasar `net_fee_threshold_pct=net_fee_threshold_pct` al call a `_classify(...)`.

- [ ] **Step 5: Leer el job para configuración y pasarlo**

En `trading-engine/agents/outcome_attribution_job.py`, leer la función `outcome_attribution_tick` completa. Dentro del bloque `async with session_factory() as session:`, agregar la lectura de config y pasarla a `attribute`:

```python
# Después de crear la sesión y antes de _fetch_candidates:
from shared.config_store import ConfigStore, ConfigKey
store = ConfigStore(session)
try:
    net_fee_threshold = float(await store.get_typed(ConfigKey.MIN_ROUNDTRIP_FEE_PCT))
except KeyError:
    net_fee_threshold = 0.0

# Y en el loop donde se llama attribute():
attr = attribute(
    decision=d, ohlcv_1m=window, associated_trade=trade,
    horizon_min=horizon_min, now=now,
    coverage_threshold_pct=coverage_threshold_pct,
    net_fee_threshold_pct=net_fee_threshold,   # ← agregar
)
```

- [ ] **Step 6: Verificar que los tests pasan**

```bash
cd trading-engine && python -m pytest tests/test_outcome_attribution.py -v -p no:cov 2>&1 | tail -15
```
Expected: todos PASS incluyendo los 3 nuevos.

- [ ] **Step 7: Commit**

```bash
git add trading-engine/agents/outcome_attribution.py trading-engine/agents/outcome_attribution_job.py trading-engine/tests/test_outcome_attribution.py
git commit -m "fix(attribution): GOOD/BAD_BUY uses net-fee threshold not just pnl sign

A trade winning +0.10% with 0.20% round-trip fees was classified GOOD_BUY
when it actually lost money net. Now _classify uses pnl > net_fee_threshold_pct
(default 0.0 for backward compat; job reads MIN_ROUNDTRIP_FEE_PCT=0.20 from
config). Postmortem now receives accurate entry quality signals.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Supervisor closed-loop — baseline de métricas + auto-revert

**Files:**
- Modify: `trading-engine/agents/supervisor.py` (método `run()` + nuevo método `_maybe_revert_degraded_config`)
- Test: `trading-engine/tests/test_supervisor.py`

**Por qué:** El Supervisor aplica sugerencias de config y nunca verifica si mejoraron las métricas. Si `min_rr_ratio` sube de 1.3 a 2.5 y el win rate cae 15 puntos en 24h, nadie lo revierte. El fix: guardar un snapshot de métricas en el `output` JSONB de la Decision del Supervisor cuando aplica cambios; al inicio del siguiente run, comparar y revertir si degradaron.

**Diseño sin migración de BD:** `Decision.output` es JSONB flexible. Cuando se aplican sugerencias, se agrega `"config_applied_baseline": {"win_rate": X, "profit_factor": Y, "applied_keys": [...], "ts": "ISO"}`. Al inicio del run, se busca la última Decision de supervisor con esa clave y se comparan las métricas actuales.

**Constantes** (hardcoded, YAGNI sobre configurarlas):
```python
_REVERT_WR_DELTA    = 10.0   # pp: si WR bajó > 10 puntos → revertir
_REVERT_PF_DELTA    = 0.30   # absoluto: si PF bajó > 0.30 → revertir
_REVERT_WINDOW_HOURS = 48    # horas hacia atrás para buscar baseline
```

- [ ] **Step 1: Leer la estructura del método `run()` del supervisor**

Leer `trading-engine/agents/supervisor.py`, buscar el método `run()` (línea 221+). Identificar:
- Dónde se llama `_apply_config_suggestions()` y cómo se guarda su resultado.
- Dónde se construye el dict `output` que se pasa a `session.add(Decision(..., output=output, ...))`.
- Cómo se accede a `metrics` en ese punto del método.

Esto es necesario para saber exactamente dónde insertar el código de baseline y revert.

- [ ] **Step 2: Escribir los tests que fallan**

Agregar al final de `trading-engine/tests/test_supervisor.py`:

```python
# ─────────────── P2-T2: closed-loop revert ───────────────
from shared.db.models import Decision as DecisionModel, ConfigHistory, ConfigEntry


async def _seed_config_entry(session, key: str, value: str):
    """Helper: inserta o actualiza una ConfigEntry en la BD de test."""
    from sqlalchemy import select as _sel
    existing = (await session.execute(
        _sel(ConfigEntry).where(ConfigEntry.key == key)
    )).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        session.add(ConfigEntry(key=key, value=value, value_type="float", description="test"))
    await session.commit()


@pytest.mark.asyncio
async def test_supervisor_stores_baseline_when_config_applied(session, fake_llm):
    """Cuando el supervisor aplica una sugerencia, Decision.output debe tener
    'config_applied_baseline' con win_rate, profit_factor y applied_keys."""
    # GIVEN: config seeded, LLM que devuelve una sugerencia válida
    await _seed_config_entry(session, "min_rr_ratio", "1.5")
    await _seed_config_entry(session, "default_rr_ratio", "2.5")

    # LLM de config suggestions devuelve sugerencia válida
    config_suggestion_json = json.dumps({
        "suggestions": [
            {"key": "min_rr_ratio", "current": 1.5, "suggested": 2.0,
             "reason": "win rate bajo"},
        ],
        "summary": "subir R:R",
    })
    fake_llm.set_responses([
        '{"ratify": false, "reason": "métricas bajas"}',   # eval pass
        "# Playbook v2\n## Métricas\n- test\n## Setups\n- test\n## Patrones\n- test\n## Contexto\n- RANGE\n## Bias\nNEUTRAL\n## Reglas\n1. test\n## Cambios\n- test",  # playbook
        config_suggestion_json,
    ])

    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=0)
    await sup.run()

    # THEN: la Decision del supervisor tiene el baseline
    decisions = (await session.execute(
        select(DecisionModel).where(DecisionModel.agent == "supervisor")
    )).scalars().all()
    assert len(decisions) >= 1
    sup_decision = decisions[-1]
    baseline = sup_decision.output.get("config_applied_baseline")
    assert baseline is not None, "Falta config_applied_baseline en output del Supervisor"
    assert "win_rate" in baseline
    assert "profit_factor" in baseline
    assert "min_rr_ratio" in baseline.get("applied_keys", [])


@pytest.mark.asyncio
async def test_supervisor_reverts_config_when_metrics_degraded(session, fake_llm):
    """Si las métricas actuales son peores que el baseline guardado, el supervisor
    revierte las claves aplicadas anteriormente."""
    # GIVEN: configuración con un cambio previo que degradó las métricas
    await _seed_config_entry(session, "min_rr_ratio", "2.0")  # valor ACTUAL en DB
    await _seed_config_entry(session, "default_rr_ratio", "2.5")

    # Simular una Decision previa del supervisor con baseline high win_rate
    prev_decision = DecisionModel(
        ts=datetime.now(tz=timezone.utc) - timedelta(hours=24),
        agent="supervisor",
        model="test-model",
        tokens_in=100, tokens_out=50, latency_ms=500,
        input={},
        output={
            "ratified": False,
            "config_applied_baseline": {
                "win_rate": 60.0,           # antes: 60%
                "profit_factor": 1.8,       # antes: 1.8
                "applied_keys": ["min_rr_ratio"],
                "ts": (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat(),
            },
        },
        executed=False,
    )
    session.add(prev_decision)

    # Simular entrada en config_history con el old_value
    session.add(ConfigHistory(
        id=uuid.uuid4(),
        ts=datetime.now(tz=timezone.utc) - timedelta(hours=24),
        key="min_rr_ratio",
        old_value="1.5",
        new_value="2.0",
        changed_by="supervisor",
    ))
    await session.commit()

    # LLM de ratificación — win rate actual es 40% (degradó 20pp > threshold 10pp)
    fake_llm.set_responses([
        '{"ratify": true, "reason": "mantener"}',  # eval: ratificar
        json.dumps({"suggestions": [], "summary": "sin cambios"}),  # config suggestions
    ])

    sup = Supervisor(session=session, llm=fake_llm, symbol="BTC/USDT", min_trades=0)
    # Inyectar métricas actuales degradadas directamente en el supervisor
    # NOTA: el implementador debe adaptar este test al API real de Supervisor.run().
    # Si run() calcula métricas internamente (no se pueden inyectar directamente),
    # mockear _compute_metrics o la query de trades para devolver win_rate=40.
    await sup.run()

    # THEN: min_rr_ratio fue revertido al valor anterior (1.5)
    reverted_entries = (await session.execute(
        select(ConfigHistory)
        .where(ConfigHistory.key == "min_rr_ratio",
               ConfigHistory.changed_by == "supervisor:revert")
    )).scalars().all()
    assert len(reverted_entries) >= 1
    assert reverted_entries[-1].new_value == "1.5"
```

> **Nota:** El segundo test puede requerir mockear `_compute_metrics` o los trades de la BD para forzar win_rate=40. Si el supervisor lee métricas directamente de la BD (no hay trades en la BD de test), el win_rate será 0.0, que también es < 60.0 - 10.0 = 50.0 → el revert debería dispararse. Si el supervisor tiene lógica especial para `closed_trades == 0` (modo diagnostic), ajustar el test para crear algún trade de prueba.

- [ ] **Step 3: Verificar que los tests fallan**

```bash
cd trading-engine && python -m pytest tests/test_supervisor.py -k "baseline or reverts" -v -p no:cov 2>&1 | tail -15
```
Expected: 2 FAIL — el primero porque `config_applied_baseline` no existe en el output, el segundo porque no hay revert.

- [ ] **Step 4: Agregar las constantes y el método `_maybe_revert_degraded_config`**

En `trading-engine/agents/supervisor.py`, agregar después de `_INVARIANTS` (línea 62) y antes de la clase `Supervisor`:

```python
# Umbrales para revertir sugerencias de config que degradaron métricas.
# Hardcoded intencionalmente (YAGNI) — son puntos de inflexión obvios.
_REVERT_WR_DELTA    = 10.0   # pp: WR cayó > 10 puntos vs baseline → revertir
_REVERT_PF_DELTA    = 0.30   # absoluto: PF cayó > 0.30 → revertir
_REVERT_WINDOW_HOURS = 48    # horas hacia atrás para buscar el baseline previo
```

Luego, dentro de la clase `Supervisor`, agregar el nuevo método. La ubicación más lógica es cerca de `_apply_config_suggestions`:

```python
async def _maybe_revert_degraded_config(self, current_metrics: dict) -> list[str]:
    """Busca el último baseline de config del Supervisor y revierte las claves
    aplicadas si las métricas actuales degradaron significativamente.

    Retorna la lista de claves revertidas (vacía si no hay degradación).
    """
    from datetime import timedelta
    from sqlalchemy import select, desc
    from shared.db.models import Decision as DecisionModel, ConfigHistory

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=_REVERT_WINDOW_HOURS)

    # Buscar las últimas Decisions del supervisor en ventana de 48h
    rows = (await self.session.execute(
        select(DecisionModel)
        .where(
            DecisionModel.agent == "supervisor",
            DecisionModel.ts >= cutoff,
        )
        .order_by(desc(DecisionModel.ts))
        .limit(10)
    )).scalars().all()

    # Encontrar la más reciente con baseline guardado
    prev = next(
        (r for r in rows if r.output and "config_applied_baseline" in r.output),
        None,
    )
    if prev is None:
        return []

    baseline = prev.output["config_applied_baseline"]
    baseline_wr  = baseline.get("win_rate")
    baseline_pf  = baseline.get("profit_factor")
    applied_keys = baseline.get("applied_keys", [])

    if baseline_wr is None or baseline_pf is None or not applied_keys:
        return []

    current_wr = float(current_metrics.get("win_rate", 0.0))
    current_pf = float(current_metrics.get("profit_factor", 0.0))

    wr_degraded = current_wr < float(baseline_wr) - _REVERT_WR_DELTA
    pf_degraded = current_pf < float(baseline_pf) - _REVERT_PF_DELTA

    if not (wr_degraded or pf_degraded):
        return []

    logger.warning(
        "supervisor.config_degradation_detected",
        baseline_wr=baseline_wr, current_wr=current_wr,
        baseline_pf=baseline_pf, current_pf=current_pf,
        applied_keys=applied_keys,
    )

    store = ConfigStore(self.session)
    reverted: list[str] = []

    for key in applied_keys:
        try:
            # Buscar el old_value en config_history (el primer registro del supervisor)
            history_entry = (await self.session.execute(
                select(ConfigHistory)
                .where(
                    ConfigHistory.key == key,
                    ConfigHistory.changed_by == "supervisor",
                    ConfigHistory.ts >= cutoff,
                )
                .order_by(ConfigHistory.ts)  # el más antiguo = el que se aplicó
                .limit(1)
            )).scalar_one_or_none()

            if history_entry and history_entry.old_value:
                await store.set(
                    ConfigKey(key), history_entry.old_value,
                    changed_by="supervisor:revert",
                )
                reverted.append(key)
                logger.warning(
                    "supervisor.config_reverted",
                    key=key,
                    reverted_to=history_entry.old_value,
                    baseline_wr=baseline_wr,
                    current_wr=current_wr,
                )
        except Exception as e:
            logger.error("supervisor.config_revert_failed", key=key, error=str(e))

    return reverted
```

- [ ] **Step 5: Modificar `run()` para llamar al revert y guardar el baseline**

En `run()` (línea ~221), agregar el call a `_maybe_revert_degraded_config` **antes** del call a `_apply_config_suggestions`. Buscar la línea donde se llaman:
```python
# Antes del call a _apply_config_suggestions:
reverted = await self._maybe_revert_degraded_config(metrics)
if reverted:
    logger.info("supervisor.reverted_degraded_config", keys=reverted)
```

Y después del call a `_apply_config_suggestions`, si hay `applied`, agregar el baseline al dict de output que se va a persistir en la Decision. Buscar la línea donde se construye ese dict (probablemente algo como `output = {"ratified": False, "playbook": ..., "config_suggestions": ...}`):

```python
# Después de: applied, rejected = await self._apply_config_suggestions(...)
# Y ANTES de construir el dict final de la Decision:
config_baseline: dict | None = None
if applied:
    config_baseline = {
        "win_rate": metrics.get("win_rate", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "applied_keys": [s["key"] for s in applied],
        "ts": datetime.now(tz=timezone.utc).isoformat(),
    }
```

Luego, al construir el dict `output` de la Decision, agregar:
```python
if config_baseline:
    output["config_applied_baseline"] = config_baseline
```

> **Nota:** El implementador debe leer el método `run()` completo para identificar exactamente dónde está el dict `output` y cómo se construye. El patrón exacto depende de si `run()` construye el dict en un solo lugar o lo ensambla por partes. El objetivo es que cuando la Decision se persiste al final de `run()`, su `output` contenga `config_applied_baseline` si se aplicaron sugerencias.

- [ ] **Step 6: Verificar que los tests pasan**

```bash
cd trading-engine && python -m pytest tests/test_supervisor.py -k "baseline or reverts" -v -p no:cov 2>&1 | tail -15
```
Expected: 2 PASS.

```bash
cd trading-engine && python -m pytest tests/test_supervisor.py -v -p no:cov 2>&1 | tail -10
```
Expected: todos PASS (no-regresión).

- [ ] **Step 7: Commit**

```bash
git add trading-engine/agents/supervisor.py trading-engine/tests/test_supervisor.py
git commit -m "feat(supervisor): closed-loop config revert on metric degradation

Supervisor now stores win_rate/profit_factor baseline in Decision.output
when it applies config suggestions (changed_by='supervisor'). On the next
run, if current WR dropped >10pp or PF dropped >0.30 vs that baseline,
the applied keys are reverted to their previous values
(changed_by='supervisor:revert') before generating new suggestions.
Fully auditable via config_history; no DB migration required.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Fix JSON parser en `_parse_json_strict` del Supervisor

**Files:**
- Modify: `trading-engine/agents/supervisor.py:80-87`
- Test: `trading-engine/tests/test_supervisor.py`

**Por qué:** `_parse_json_strict` (línea 80) tiene el mismo patrón frágil que corregimos en P1-T1 para el Decisor: `raw.split("```")[1]`. Si el LLM de sugerencias de config o del eval emite prosa o un fence sin cerrar, `json.loads` falla silenciosamente y el Supervisor pierde la sugerencia o el eval. Es el mismo root cause de P1-T1, misma solución.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `trading-engine/tests/test_supervisor.py`:

```python
# ─────────────── P2-T3: parser JSON robusto en supervisor ───────────────
from agents.supervisor import _parse_json_strict


def test_parse_json_strict_when_prose_before_json_should_parse():
    json_str = '{"ratify": true, "reason": "métricas OK"}'
    result = _parse_json_strict(f"Aquí mi análisis.\n\n{json_str}")
    assert result["ratify"] is True


def test_parse_json_strict_when_uppercase_JSON_fence_should_parse():
    json_str = '{"suggestions": [], "summary": "nada"}'
    result = _parse_json_strict(f"```JSON\n{json_str}\n```")
    assert result["suggestions"] == []


def test_parse_json_strict_when_think_tags_before_json_should_parse():
    json_str = '{"ratify": false, "reason": "mal performance"}'
    result = _parse_json_strict(f"<think>Pensando...</think>\n{json_str}")
    assert result["ratify"] is False
```

- [ ] **Step 2: Verificar que fallan**

```bash
cd trading-engine && python -m pytest tests/test_supervisor.py -k "parse_json_strict" -v -p no:cov 2>&1 | tail -10
```
Expected: 3 FAIL — el test de prosa y think-tags fallan con `JSONDecodeError`. El de uppercase fence puede fallar con el split.

- [ ] **Step 3: Reemplazar `_parse_json_strict` en `supervisor.py`**

Reemplazar el bloque de líneas 80-87 (la función `_parse_json_strict`) con:

```python
def _parse_json_strict(text: str) -> dict:
    """Extrae el primer objeto JSON balanceado del texto del LLM.

    Tolera prosa antes/después, fences markdown (```json/```JSON),
    y tags de razonamiento <think>...</think>.
    """
    import re as _re
    # 1. Eliminar tags <think>
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    # 2. Desempaquetar fence si hay
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 3:
            block = parts[1]
            block = _re.sub(r"^[a-zA-Z]+\s*\n?", "", block)
            stripped = block.strip()
    # 3. Extraer objeto JSON balanceado
    start = stripped.find("{")
    if start == -1:
        return json.loads(stripped)
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(stripped[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start : i + 1])
    return json.loads(stripped)
```

> `import re as _re` en el cuerpo de la función porque `re` ya puede estar importado a nivel de módulo en `supervisor.py`. Si ya lo está, usar `re` directamente.

- [ ] **Step 4: Verificar que los tests pasan**

```bash
cd trading-engine && python -m pytest tests/test_supervisor.py -k "parse_json_strict" -v -p no:cov 2>&1 | tail -10
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/supervisor.py trading-engine/tests/test_supervisor.py
git commit -m "fix(supervisor): robust JSON parser tolerating prose and think-tags

_parse_json_strict used the same fragile split('```')[1] pattern fixed in
P1-T1 for the Decisor. Now uses balanced-brace extraction tolerating prose,
uppercase JSON fences, and <think> tags in config suggestions and eval output.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Suite completa no-regresión

**Files:** ninguno (verificación)

- [ ] **Step 1: Correr la suite completa del engine**

```bash
cd trading-engine && python -m pytest -q 2>&1 | tail -20
```
Expected: todos PASS, coverage ≥ 70 %.

**Causas probables de regresión:**
- Tests del supervisor que mockeaban `run()` y ahora esperan `_maybe_revert_degraded_config` → puede fallar si `run()` intenta queries adicionales. Solución: agregar datos mínimos de Decision previas al fixture de test.
- Tests de `test_outcome_attribution.py` que llaman a `attribute()` sin el nuevo parámetro → compatibles por default=0.0, no deberían fallar.
- Tests del job que no seedean `MIN_ROUNDTRIP_FEE_PCT` en config → agregar `KeyError` como fallback en el job (Step 5 del Task 1).

- [ ] **Step 2: Commit si hubo cambios**

```bash
git add -A
git commit -m "test: fix regressions after P2 learning loop changes

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Cobertura de objetivos P2:**
- Clasificación GOOD/BAD_BUY con threshold de fees → Task 1. ✅
- Supervisor closed-loop (baseline + auto-revert) → Task 2. ✅
- Parser JSON del supervisor (riesgo de que el loop falle silenciosamente) → Task 3. ✅

**Scan de placeholders:**
- Task 2, Step 5: menciona "el implementador debe leer `run()`" — necesario porque el Explore agent no leyó el método completo. No es un placeholder de implementación: el código exacto está en Step 4 y el implementador sabe qué buscar y qué insertar.
- Task 2, Step 2: nota sobre mockear `_compute_metrics` — el test depende de cómo el supervisor lee las métricas. Si win_rate=0.0 por no tener trades en la BD de test, eso ya dispara el revert (0.0 < 60.0 - 10.0 = 50.0). Comportamiento correcto y documentado.

**Consistencia de tipos:**
- `net_fee_threshold_pct: float = 0.0` definido en Task 1 para `_classify` y `attribute`. ✅
- `config_applied_baseline` dict con claves `win_rate, profit_factor, applied_keys, ts` — definido en Task 2 Step 5 y leído en Task 2 Step 4 (`_maybe_revert_degraded_config`). ✅
- `_REVERT_WR_DELTA`, `_REVERT_PF_DELTA`, `_REVERT_WINDOW_HOURS` definidas en Task 2 Step 4 antes de la clase y usadas en `_maybe_revert_degraded_config`. ✅
- `changed_by="supervisor:revert"` en Task 2 Step 4 y verificado en el test de Task 2 Step 2 (`ConfigHistory.changed_by == "supervisor:revert"`). ✅

**Riesgos para el implementador:**
- Task 2 es el más complejo por la integración con `run()`. El método `run()` de Supervisor es largo y el implementador debe leerlo completo antes de modificarlo. Los tests son la guía de qué debe pasar, no el detalle de implementación.
- `_REVERT_WINDOW_HOURS = 48` asume que el Supervisor corre al menos cada 48h. Si se corre manualmente con más frecuencia y hay múltiples baselines en ventana, siempre se usa el más reciente. Comportamiento correcto.
