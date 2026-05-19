# Supervisor — Análisis contrafactual de decisiones (Enfoque B)

**Fecha:** 2026-05-18
**Estado:** Borrador (pendiente de aprobación)
**Autor:** Matías + Claude (sesión de brainstorm)
**Audiencia:** Implementación (`writing-plans` skill) + revisores de spec funcional/técnica.
**Tema disparador:** "que el Supervisor revise la fluctuación de los precios en las últimas 24 h y compare con el input/output de cada decisión, para entender qué ajustar cuando hubo oportunidad de compra que se holdeó y viceversa."

---

## 1. Premisa del cambio

> Hoy el Supervisor sólo aprende de **trades cerrados** (PnL, WR, PF). Las decisiones que terminaron en `HOLD` o en BUYs rechazados por el Risk Gate son **invisibles** para él, aunque el precio haya hecho un rally del 2 % justo después. El operador detecta estas oportunidades perdidas mirando el chart a ojo, pero el sistema no las usa para ajustar el playbook ni la config.

Solución: cruzar cada decisión persistida (`decisions.ts`, `input`, `output`) contra el OHLCV posterior, computar **forward return / MFE / MAE** dentro de un horizonte operativo, **clasificar la decisión** y persistir el resultado en una tabla nueva `decision_outcomes`. Con eso:

1. El Supervisor recibe un bloque "Análisis contrafactual" en su prompt diario, con miss_rate por régimen, top misses y BUYs bloqueados que hubieran ganado.
2. El frontend pinta en el `PriceChart` un marcador por decisión coloreado según la clasificación, de modo que el cluster de "oportunidades perdidas" se vea de un vistazo.
3. Un endpoint `/api/decisions/outcomes` expone los outcomes para queries históricas y debugging.

Tres ejes:

1. **Cómputo del contrafactual** — módulo `outcome_attribution.py` puro (sin I/O), con clasificación anclada a unidades de riesgo (no a porcentajes mágicos).
2. **Persistencia y orquestación** — tabla `decision_outcomes`, job `outcome_attribution_tick` cada hora, idempotente; reprocesa decisiones cuya ventana aún no maduró.
3. **Consumo** — Supervisor (prompt + métricas), API REST, frontend (marcadores en el chart, filtros por clasificación).

---

## 2. Estado actual (referencia)

### 2.1 Datos disponibles hoy

| Tabla | Campos relevantes | Notas |
|---|---|---|
| `decisions` | `id`, `ts`, `agent="decisor"`, `input JSONB`, `output JSONB`, `executed`, `rejected_reason`, `outcome JSONB (nullable, hoy sin uso)`, `trade_id` (FK) | El `outcome` está declarado en el modelo (`shared/db/models.py:62`) pero **no se escribe en ningún lado** hoy. |
| `ohlcv` | `time`, `timeframe ∈ {1m,5m,15m,1h,4h}`, `open/high/low/close/volume` | Granularidad suficiente para reconstruir MFE/MAE a 1 min. |
| `trades` | `id`, `decision_id`, `ts_open`, `ts_close`, `entry_price`, `exit_price`, `pnl_usdt`, `pnl_pct`, `close_reason`, `status` | Para los BUYs ejecutados ya tenemos el outcome real. |
| `indicators` | snapshots de RSI/MACD/EMA/ATR/etc. por TF | Útil para reconstruir el `SL_dist_pct` del momento de la decisión a partir del `atr_pct`. |

### 2.2 Supervisor hoy (`trading-engine/agents/supervisor.py::_compute_metrics`)

Métricas que ya computa: `win_rate`, `profit_factor`, `total_pnl`, `avg_holding_min`, distribución `BUY/SELL/HOLD`, histograma de confidence, breakdown de rechazos por regla del Risk Gate, coherence warnings por regla C1–C6, `avg_position_size_pct`, `avg_buy_confidence`, `max_dd_usdt`, `sharpe_period`, `regime_distribution`. Datos de mercado: `open_btc/close_btc/low_24h/high_24h/pct_24h/atr_avg`.

**Lo que no computa**: ningún cruce decisión-por-decisión contra el precio posterior.

### 2.3 PriceChart hoy (`frontend/src/components/chart/PriceChart.tsx`)

Marcadores existentes:

- Trade abierto: flecha azul `arrowUp` con label `"BUY $price"`.
- Trade cerrado: círculo pequeño en `ts_open`, flecha en `ts_close` con PnL si `|pnl| ≥ $5`.
- Decisión ejecutada distinta a HOLD: flecha mediana coloreada (`#26a69a` BUY / `#ef5350` SELL).
- Decisión bloqueada distinta a HOLD: círculo translúcido (color con alfa `55`).
- **HOLDs filtrados explícitamente** en `PriceChart.tsx:466` (`if (!d.action || d.action === "HOLD") return;`). Por eso las "oportunidades perdidas" son invisibles hoy.

---

## 3. Diseño propuesto

### 3.1 Concepto: clasificación contrafactual

Para cada decisión `d` del decisor, sea:

- `t = d.ts` (timestamp UTC).
- `H = expected_holding_max_min` (config; default 240 min).
- `price_t = d.input["price"]` (ya persistido por el ContextBuilder).
- `atr_pct_t = d.input["atr_ref_pct"]` (ya persistido, atado al `atr_timeframe` que vio el Decisor).
- `sl_atr_multiplier_t = d.input["sl_atr_multiplier"]` y `min_rr_ratio_t = d.input["min_rr_ratio"]` (ya persistidos).

Derivados:

```
SL_dist_pct   = sl_atr_multiplier_t × atr_pct_t          # distancia % al stop loss en el momento de la decisión
TP_target_pct = min_rr_ratio_t × SL_dist_pct             # distancia % que hubiera sido TP equivalente
```

Y sobre las velas OHLCV `1m` entre `t` y `t+H`:

```
forward_close_at_horizon       = OHLCV[1m].close donde time = min(t+H, last_known)
mfe_pct, time_to_mfe_min       = max((high − price_t) / price_t), instante en el que se alcanza
mae_pct, time_to_mae_min       = min((low  − price_t) / price_t), instante en el que se alcanza
matured                        = (last_known_ts ≥ t + H)
```

Clasificación final (en orden de prioridad — la primera que aplica gana):

| Clasificación | Condición |
|---|---|
| `PENDING` | `not matured` y `mfe_pct < TP_target_pct` y `mae_pct > -SL_dist_pct` (todavía no se resolvió) |
| `UNKNOWN` | OHLCV faltante en > 30 % de la ventana, o `atr_pct_t` ausente |
| `GOOD_BUY` | `d.action="BUY"` y `executed=true` y `trade.pnl_pct > 0` |
| `BAD_BUY` | `d.action="BUY"` y `executed=true` y `trade.pnl_pct ≤ 0` |
| `BLOCKED_GOOD_TRADE` | `d.action="BUY"` y `executed=false` y `time_to_mfe_min < time_to_mae_min` y `mfe_pct ≥ TP_target_pct` |
| `CORRECTLY_BLOCKED` | `d.action="BUY"` y `executed=false` y (`time_to_mae_min ≤ time_to_mfe_min` o `mae_pct ≤ -SL_dist_pct`) |
| `MISSED_OPPORTUNITY` | `d.action="HOLD"` y `time_to_mfe_min < time_to_mae_min` y `mfe_pct ≥ TP_target_pct` y `mae_pct > -SL_dist_pct` |
| `GOOD_HOLD` | resto de los `HOLD` maduros |
| `GOOD_SELL` / `BAD_SELL` | análogo a BUY pero con el trade que se cerró |

**Por qué este umbral**: anclado al riesgo que el propio sistema declaró en ese ciclo. Si SL es 0.3 % × ATR_pct y `min_rr=1.3`, el TP equivalente exige + 0.4 % aprox. Eso evita que micro-rallies en alta volatilidad cuenten como "missed" y mantiene la métrica coherente con lo que el Risk Gate hace en tiempo real.

**Por qué importa el orden temporal de MFE/MAE**: si el `mae_pct` se toca antes que el `mfe_pct`, el SL te hubiera sacado y la "oportunidad" no existe. Sin este chequeo, el análisis tiene *hindsight bias* sistemático que sesga al Supervisor a recortar más SL del que debería.

### 3.2 Módulo nuevo: `trading-engine/agents/outcome_attribution.py`

Función pura, sin acceso a sesión. Recibe la decisión, las velas y el trade asociado (si existe) y devuelve un `DecisionAttribution` dataclass.

```python
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
    classification: Literal[
        "PENDING", "UNKNOWN",
        "GOOD_BUY", "BAD_BUY",
        "BLOCKED_GOOD_TRADE", "CORRECTLY_BLOCKED",
        "MISSED_OPPORTUNITY", "GOOD_HOLD",
        "GOOD_SELL", "BAD_SELL",
    ]
    computed_at: datetime


def attribute(
    *,
    decision: Decision,
    ohlcv_1m: list[Ohlcv],          # velas entre decision.ts y decision.ts+H
    associated_trade: Trade | None,  # via decision.trade_id
    horizon_min: int,
    now: datetime,
) -> DecisionAttribution:
    """Función pura. Sin queries. Sin commits. Sin side effects."""
```

Helpers internos:

- `_extract_decision_inputs(decision)` → `(price_t, atr_pct_t, sl_mult, rr_ratio)` con fallbacks `None` si la decisión es vieja y no tiene esas claves.
- `_compute_mfe_mae(price_t, candles_1m)` → recorre velas en orden, devuelve `(mfe_pct, mae_pct, time_to_mfe_min, time_to_mae_min)`.
- `_classify(decision, attribution_inputs, associated_trade)` → mapa a una de las clasificaciones.

### 3.3 Tabla nueva: `decision_outcomes`

```sql
CREATE TABLE decision_outcomes (
    decision_id UUID PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
    horizon_min INTEGER NOT NULL,
    matured BOOLEAN NOT NULL,
    forward_return_pct NUMERIC(10, 5),
    mfe_pct NUMERIC(10, 5),
    mae_pct NUMERIC(10, 5),
    time_to_mfe_min INTEGER,
    time_to_mae_min INTEGER,
    sl_dist_pct NUMERIC(10, 5),
    tp_target_pct NUMERIC(10, 5),
    classification VARCHAR(32) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_decision_outcomes_classification
  ON decision_outcomes (classification, computed_at DESC);

CREATE INDEX idx_decision_outcomes_pending
  ON decision_outcomes (computed_at)
  WHERE classification = 'PENDING';
```

**Decisión deliberada**: 1-a-1 con `decisions` (PK = `decision_id`). Si una decisión todavía está `PENDING`, el job la sobrescribe (UPSERT) cuando madure. El índice parcial sobre `PENDING` acelera el escaneo de "pendientes a recomputar".

**Por qué tabla nueva en vez de usar `decisions.outcome JSONB`**: el `outcome` JSONB no permite índices eficientes por `classification`, dificulta JOINs y mezcla responsabilidades (el Decisor lo escribiría parcialmente, el Job lo completaría: race conditions). La tabla aparte es más limpia y queryable.

### 3.4 Job nuevo: `outcome_attribution_tick`

Wireado al `EngineScheduler` con `IntervalTrigger(minutes=60)`. Patrón idéntico a `add_position_refresh` (P-05).

Algoritmo:

```text
async def outcome_attribution_tick():
    async with session_factory() as session:
        # 1. Decisiones del decisor en (now - 25h, now - 1h) que no tengan outcome o
        #    cuyo outcome esté PENDING.
        candidates = (await session.execute(
            select(Decision).outerjoin(DecisionOutcome)
            .where(
                Decision.agent == "decisor",
                Decision.ts >= now - timedelta(hours=25),
                Decision.ts <= now - timedelta(minutes=15),  # buffer mínimo
                or_(DecisionOutcome.decision_id.is_(None),
                    DecisionOutcome.classification == "PENDING"),
            )
        )).scalars().all()

        # 2. Cache de OHLCV 1m en bloque.
        if not candidates: return
        ohlcv_window = await fetch_ohlcv_1m(
            session,
            ts_from=min(d.ts for d in candidates),
            ts_to=now,
        )
        index_by_ts = group_by_minute(ohlcv_window)

        # 3. Por decisión, cortar la ventana y atribuir.
        for d in candidates:
            window_end = min(d.ts + timedelta(minutes=horizon_min), now)
            window = slice_window(index_by_ts, d.ts, window_end)
            trade = await load_trade(session, d.trade_id) if d.trade_id else None
            attr = outcome_attribution.attribute(
                decision=d, ohlcv_1m=window,
                associated_trade=trade,
                horizon_min=horizon_min,
                now=now,
            )
            await upsert_outcome(session, attr)

        await session.commit()
```

Garantías:

- **Idempotente**: ejecutarlo dos veces produce el mismo estado (UPSERT). Si una decisión está `PENDING` y madura, la próxima corrida la reclasifica.
- **Acotado**: ventana fija de 25 h, máximo ~150 decisiones (intervalo 10 min × 24 h = 144) por corrida.
- **Sin acoplamiento**: no lee de `Supervisor` ni de `Decisor`. Sólo de `decisions`, `trades`, `ohlcv`.

### 3.5 Cambios en `Supervisor`

#### 3.5.1 Métricas nuevas en `_compute_metrics`

Lee de `decision_outcomes`:

```python
outcomes_24h = (await self.session.execute(
    select(Decision, DecisionOutcome)
    .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
    .where(Decision.ts >= since, Decision.agent == "decisor",
           DecisionOutcome.classification != "PENDING")
)).all()
```

Agregados nuevos en el dict de métricas (a sumar a los existentes):

- `evaluated_decisions`: total con `classification ≠ PENDING/UNKNOWN`.
- `missed_count`, `missed_rate`.
- `bad_buy_count`, `good_buy_count`, `bad_buy_rate`.
- `blocked_good_count`, `correctly_blocked_count`.
- `missed_by_regime: dict[regime, {missed, total_holds}]`.
- `missed_by_confidence_bucket: dict[bucket, {missed, total_holds}]` con buckets `<0.50`, `0.50-0.59`, `0.60-0.69`, `≥0.70`.
- `blocked_good_by_rule: dict[risk_gate_rule_id, count]` (extraído de `rejected_reason` prefix).
- `top_misses: list[dict]` con las 5 mayores oportunidades perdidas. Cada item: `{ts, regime, confidence, confluences, mfe_pct, time_to_mfe_min, reasoning_first_120_chars}`.
- `top_bad_buys: list[dict]` análogo (máximo 5).

#### 3.5.2 Bloque nuevo en `supervisor_user.txt`

Insertar antes de "DECISIONES Y OUTCOMES":

```text
ANÁLISIS CONTRAFÁCTICO (últimas 24h, horizonte H={expected_holding_max_min}min):
  Decisiones evaluadas: {evaluated_decisions} / {total_decisions} (resto: ventana no madurada).

  HOLDs:
    Missed opportunities: {missed_count} ({missed_rate:.1f}% de los HOLD maduros).
    Por régimen:
{missed_by_regime_block}
    Por confidence:
{missed_by_confidence_block}

  BUYs ejecutados:
    GOOD = {good_buy_count} | BAD = {bad_buy_count} ({bad_buy_rate:.1f}% de los BUYs).

  BUYs bloqueados por Risk Gate:
    Hubieran ganado: {blocked_good_count} | Bien bloqueados: {correctly_blocked_count}.
    Desglose por regla:
{blocked_good_by_rule_block}

  TOP MISSES:
{top_misses_block}

  TOP BAD BUYS:
{top_bad_buys_block}
```

#### 3.5.3 Guía de razonamiento en `supervisor_system.txt`

Agregar sección nueva al final del system prompt:

> **Lectura del ANÁLISIS CONTRAFÁCTICO**:
> - Si `missed_rate > 30 %` y se concentra en un régimen → el playbook está sobre-restringido para ese régimen. Considerá bajar `conf_threshold_<regime>` o `min_confluences_buy`, o relajar la regla del playbook que descarta entradas en ese contexto.
> - Si `blocked_good_count` por R5 es alto vs `correctly_blocked_count` → el `min_rr_ratio` actual puede estar cortando setups válidos. Evaluá bajarlo (siempre dentro de `_SAFE_BOUNDS` y respetando la invariante `min_rr_ratio ≤ default_rr_ratio`).
> - Si los `TOP MISSES` comparten un patrón técnico (e.g. todas con MACD bullish + RSI subiendo en TRENDING_UP), reflejá ese patrón como **regla explícita en el playbook v{new_version}**: "considerar BUY cuando ... aún con confluencias ≤ 2".
> - Si `bad_buy_rate > 50 %` con confidence ≥ 0.70 → el LLM está sobre-confiado. Subí `conf_threshold_<regime_predominante>` o reforzá los chequeos C4/C5 del CoherenceChecker.
> - Cuando los datos no apoyen un cambio claro (ej: `evaluated_decisions < 20`), NO sugieras ajustes de config sólo por sugerir.

#### 3.5.4 Inyección al prompt de config-suggestions (`_CONFIG_SUGGESTION_PROMPT`)

Agregar las nuevas métricas al template para que el LLM las use al sugerir ajustes en `conf_threshold_*`, `min_confluences_buy`, `min_rr_ratio`. Sin cambios estructurales en `_SAFE_BOUNDS` ni en `_apply_config_suggestions`.

#### 3.5.5 Inyección al prompt de ratificación (Phase 1, `supervisor_eval_user.txt`)

La Fase 1 hoy decide `ratify | regenerate` sin ver miss_rate ni bad_buy_rate. Eso es un agujero: puede ratificar un playbook que está dejando pasar oportunidades sistemáticamente.

Agregar al template `supervisor_eval_user.txt` un bloque resumido (más compacto que el de la Fase 2, sin top_misses verbatim para economizar tokens en una llamada JSON-mode):

```text
ANÁLISIS CONTRAFÁCTICO (últimas 24h, horizonte {expected_holding_max_min}min):
  Decisiones evaluadas: {evaluated_decisions} / {total_decisions}
  HOLDs missed: {missed_count} ({missed_rate:.1f}%)
  BUYs bad: {bad_buy_count} ({bad_buy_rate:.1f}%)
  Blocked good (BUYs rechazados que hubieran ganado): {blocked_good_count}
```

Y en `supervisor_eval_system.txt`, agregar una línea a la guía de ratificación:

> "Si `missed_rate > 30 %` o `bad_buy_rate > 50 %` con `evaluated_decisions ≥ 20`, **no ratifiques**: pedí regeneración para corregir la deriva. Si la muestra es chica (`evaluated_decisions < 20`), no uses estas métricas como criterio único — pueden ser ruido."

### 3.6 API REST nueva

`web/api/decisions.py` — agregar endpoint:

```python
@router.get("/decisions/outcomes", response_model=list[DecisionOutcomeOut])
async def list_outcomes(
    since_hours: int = Query(24, ge=1, le=168),
    classification: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> list[DecisionOutcomeOut]:
    ...
```

`DecisionOutcomeOut` DTO Pydantic 2 con `from_attributes=True` (P-10) — incluye todos los campos de `decision_outcomes` + `ts`, `action`, `confidence`, `regime`, `confluences`, `executed`, `rejected_reason` de la decisión asociada (JOIN).

### 3.7 Frontend — pines de outcomes en el PriceChart

#### 3.7.1 Cliente API (`frontend/src/api/client.ts`)

```typescript
outcomes: (sinceHours = 24, classification?: string) => {
  const q = new URLSearchParams();
  q.set("since_hours", String(sinceHours));
  if (classification) q.set("classification", classification);
  return get<DecisionOutcome[]>(`/decisions/outcomes?${q.toString()}`);
},
```

#### 3.7.2 Markers nuevos en `PriceChart.tsx`

Cambios en el `useMemo` de `markers` (línea 419). Tres pasos:

**Paso 1 — Markers NUEVOS sólo para HOLDs y BUYs bloqueados con outcome accionable.** Se agregan al array de markers, no reemplazan nada existente:

| Classification | Forma | Color | Tamaño | Texto |
|---|---|---|---|---|
| `MISSED_OPPORTUNITY` (HOLD) | `circle` aboveBar | `#f59e0b` (ámbar) | 0.8 | `"miss +X.X%"` si `mfe_pct ≥ 1 %`, sino `""` |
| `BLOCKED_GOOD_TRADE` (BUY rechazado) | `circle` belowBar | `#f59e0b80` (ámbar tenue) | 0.7 | `""` |
| `CORRECTLY_BLOCKED` (BUY rechazado) | sin marker | — | — | — |
| `GOOD_HOLD` (HOLD) | sin marker | — | — | — |
| `PENDING` / `UNKNOWN` | sin marker | — | — | — |

> Esto implica **quitar la línea 466** `if (!d.action || d.action === "HOLD") return;` o reemplazarla por la condición: "saltar si el decisión no tiene outcome accionable (`MISSED_OPPORTUNITY` o `BLOCKED_GOOD_TRADE`) **y** action ∈ {HOLD, BUY rechazado}".

**Paso 2 — Recolorear (NO duplicar) el marker existente de BUY ejecutado** (líneas 469-478). Hoy ese marker se colorea por `action` (`#26a69a` BUY / `#ef5350` SELL). Cuando el outcome está disponible:

| Classification | Color del arrow existente |
|---|---|
| `GOOD_BUY` | `#26a69a` (igual que hoy) |
| `BAD_BUY` | `#ef5350` (igual que hoy — el trade ya lo muestra perdedor) |
| `PENDING` / `UNKNOWN` | color actual (sin cambios) |

> Pasamos a colorear por `outcome.classification` cuando esté disponible, cayendo al comportamiento actual (por `action`) cuando no haya outcome. **Cero duplicación**: los `arrowUp` de decisión BUY ejecutada siguen siendo uno por decisión.

**Paso 3 — Tooltip enriquecido.** Aprovechar el `ReasoningBlock` ya existente para mostrar el `reasoning` del decisor + bloque "Contrafactual: mfe +X.X % en T min, mae −Y.Y % en S min, clasificación ..." cuando el outcome está disponible para la decisión bajo el cursor.

**Paso 4 — Toggle nuevo en `showOverlays`**: `outcomes: boolean` (default true). Apaga **ambos** efectos del Paso 1 y Paso 2: vuelve al comportamiento previo (colorear por action, no mostrar markers de HOLD ni de blocked-good). Reusa el patrón del checkbox actual de `showOverlays.decisions`.

#### 3.7.3 Fetch + estado

Dashboard.tsx (o el componente padre) llama `api.outcomes(24)` cada 60 s (mismo patrón que `api.decisions`). La WS broadcast no es crítica para esto — basta polling.

### 3.8 WebSocket (opcional, fuera de scope inicial)

`web/ws/feeds.py` podría emitir `{"event": "outcome_classified", "data": {...}}` cuando el job upsertea, pero **no es necesario** para el v1: el polling cada 60 s del frontend alcanza para refresh.

---

## 4. Cambios concretos por archivo

| Archivo | Cambio | Tipo |
|---|---|---|
| `trading-engine/alembic/versions/00X_add_decision_outcomes.py` | Migración nueva: `CREATE TABLE decision_outcomes` + 2 índices. Downgrade dropea ambos. | Nuevo |
| `shared/db/models.py` | Agregar `class DecisionOutcome` con relación inversa desde `Decision`. | Editar |
| `trading-engine/agents/outcome_attribution.py` | **Módulo nuevo**. Función pura `attribute(...)` + dataclass `DecisionAttribution` + helpers `_compute_mfe_mae`, `_classify`, `_extract_decision_inputs`. | Nuevo |
| `trading-engine/main.py` | Wire del job `outcome_attribution_tick` al `EngineScheduler` con `IntervalTrigger(minutes=60)`. | Editar |
| `trading-engine/scheduler.py` | Método `add_outcome_attribution(fn, interval_min=60)` análogo a `add_position_refresh`. | Editar |
| `trading-engine/agents/supervisor.py` | En `_compute_metrics`: JOIN con `decision_outcomes`, calcular agregados nuevos. Inyectarlos en ctx para los dos prompts (supervisor + config suggestions). Agregar `_format_missed_by_regime`, `_format_top_misses`, etc. | Editar |
| `trading-engine/agents/prompts/supervisor_user.txt` | Bloque "ANÁLISIS CONTRAFÁCTICO" nuevo (Fase 2 — regeneración). | Editar |
| `trading-engine/agents/prompts/supervisor_system.txt` | Sección de interpretación del nuevo bloque + tabla señal→palanca. | Editar |
| `trading-engine/agents/prompts/supervisor_eval_user.txt` | Bloque "ANÁLISIS CONTRAFÁCTICO" compacto (Fase 1 — ratificación). | Editar |
| `trading-engine/agents/prompts/supervisor_eval_system.txt` | Línea de guía: no ratificar si miss_rate > 30 % o bad_buy_rate > 50 %. | Editar |
| `web/api/decisions.py` | Endpoint `GET /api/decisions/outcomes` + DTO `DecisionOutcomeOut`. | Editar |
| `web/ws/feeds.py` | Sin cambios (WS fuera de scope). | — |
| `frontend/src/types/index.ts` | Tipo `DecisionOutcome`. | Editar |
| `frontend/src/api/client.ts` | Método `outcomes(sinceHours, classification?)`. | Editar |
| `frontend/src/components/chart/PriceChart.tsx` | Quitar filtro de HOLDs, agregar markers por `classification`, agregar toggle `showOverlays.outcomes`. | Editar |
| `frontend/src/pages/Dashboard.tsx` | Fetch + estado de outcomes; pasar al `PriceChart`. | Editar |
| `frontend/src/components/ReasoningBlock.tsx` | Aceptar prop opcional `outcome` y renderizar el contrafactual. | Editar |

### Tests

| Archivo | Cobertura |
|---|---|
| `trading-engine/tests/test_outcome_attribution.py` | **Nuevo**. Función pura, fácil de testear con fixtures sintéticas de OHLCV. |
| `trading-engine/tests/test_outcome_attribution_tick.py` | **Nuevo**. Test del job: idempotencia, manejo de PENDING que madura, ventana acotada. |
| `trading-engine/tests/test_supervisor.py` | Agregar tests del bloque nuevo en el prompt y de los agregados nuevos en `_compute_metrics`. |
| `web/tests/test_decisions_outcomes_api.py` | **Nuevo**. Endpoint REST: filtros por `since_hours`, `classification`, JOIN con decisión. |
| `frontend` | Sin tests automatizados nuevos (consistente con el patrón actual). Validación visual manual contra paper trading. |

---

## 5. Impacto en specs existentes

### 5.1 `docs/specs/01-functional-spec.md`

- **§F5 (Supervisor)**: agregar sub-sección **§F5.bis.6 — Análisis contrafactual** describiendo el flujo (job → tabla → métricas → bloque en el prompt).
- **§F2.bis (Risk Gate)**: nota informativa de que los rechazos del Risk Gate ahora generan métrica observable (`blocked_good_count` por regla) para auditoría.

### 5.2 `docs/specs/02-technical-spec.md`

- **§2.2 (componentes principales)**: agregar fila `agents/outcome_attribution.py`.
- **§2.7 (Supervisor)**: nueva **§2.7.5 — Outcome attribution job**: cron 1h, idempotente, UPSERT a `decision_outcomes`.
- **§3 (API)**: documentar `GET /api/decisions/outcomes`.
- **§6 (Config)**: nueva clave `outcome_attribution_horizon_min` (default = `expected_holding_max_min`, configurable independientemente para permitir análisis con horizonte distinto al operativo) — **decisión a confirmar**, ver §11.

### 5.3 `docs/specs/03-data-model.md`

- Agregar tabla `decision_outcomes` con todas las columnas e índices.

### 5.4 `docs/specs/04-api-contracts.md`

- Agregar contrato del endpoint `GET /api/decisions/outcomes`.

### 5.5 `docs/specs/06-patterns.md`

- Agregar P-18 (o el siguiente disponible) — **"Outcome attribution: función pura + job idempotente con UPSERT"** — con evidencia en `outcome_attribution.py` + `outcome_attribution_tick`.

---

## 6. Acceptance Criteria

| Id | Criterio |
|---|---|
| OA-01 | `outcome_attribution.attribute(...)` es función pura: dado un mismo input retorna el mismo `DecisionAttribution`. Sin queries, sin commits, sin clocks fuera del parámetro `now`. |
| OA-02 | Para una decisión `HOLD` con `price_t=100`, `atr_pct=1.0`, `sl_atr_multiplier=0.3`, `min_rr_ratio=1.3`, una vela `1m` posterior con `high=100.5` (mfe `+0.5 %` > tp_target `0.39 %`) y `low=99.95` (mae `-0.05 %` > -sl_dist `-0.3 %`), `time_to_mfe < time_to_mae` → `classification = MISSED_OPPORTUNITY`. |
| OA-03 | Para la misma decisión, si `time_to_mae < time_to_mfe` y `mae_pct ≤ -SL_dist_pct` → `classification = GOOD_HOLD` (no es missed: el SL hubiera pegado primero). |
| OA-04 | Para una decisión `BUY` con `executed=false` (rechazada R5) cuyo MFE llega a TP_target sin que el MAE cruce SL → `BLOCKED_GOOD_TRADE`. |
| OA-05 | Ventana no madurada (`now < ts + H`) y todavía sin resolución de MFE/MAE → `PENDING`; el job la reprocesa en la corrida siguiente. |
| OA-06 | OHLCV faltante en > 30 % de la ventana o `atr_pct_t` ausente → `UNKNOWN`. La métrica no se contamina. |
| OA-07 | El job `outcome_attribution_tick` es idempotente: ejecutarlo dos veces consecutivas sobre la misma BD produce el mismo set de filas. |
| OA-08 | El job upserta por `decision_id` (PK), no inserta duplicados. |
| OA-09 | El bloque "ANÁLISIS CONTRAFÁCTICO" aparece en los **dos** prompts del Supervisor (Fase 1 ratificación y Fase 2 regeneración) cuando hay al menos una decisión evaluada. |
| OA-09b | Fase 1 (ratificación) responde `ratify=false` cuando `missed_rate > 30 %` o `bad_buy_rate > 50 %` con `evaluated_decisions ≥ 20`, aún si el resto de los guardrails determinísticos pasan. |
| OA-10 | `GET /api/decisions/outcomes?since_hours=24` devuelve la lista con campos del DTO; filtra correctamente por `classification`. |
| OA-11 | El `PriceChart` ya no filtra `HOLD` con outcome ≠ `GOOD_HOLD`. Los `MISSED_OPPORTUNITY` se ven como círculos ámbar `aboveBar`. |
| OA-12 | Toggle `showOverlays.outcomes` revierte ambos cambios: oculta los markers de `MISSED_OPPORTUNITY` / `BLOCKED_GOOD_TRADE` y vuelve a colorear los BUY ejecutados por `action` (no por `classification`). No afecta los markers de trades. |
| OA-13 | Specs §F5.bis.6 (functional), §2.7.5 + §3 (technical), §3 (data-model), §4 (api-contracts), §6 (patterns) actualizadas. |

---

## 7. Métricas y observabilidad

- `outcome_attribution.job.duration_ms` (log structlog en cada tick).
- `outcome_attribution.job.candidates_processed` por tick.
- `outcome_attribution.job.classification_distribution_24h` desglosado.
- `supervisor.metric.missed_rate_24h` (ya implícito en el prompt; exponerlo también en `/api/stats/daily`).
- `supervisor.metric.bad_buy_rate_24h`.

---

## 8. Plan de rollout

1. **PR 1 — Cómputo + persistencia + job** (sin frontend, sin supervisor):
   - Migración Alembic + modelo ORM.
   - Módulo `outcome_attribution.py` con tests.
   - Job + wire al scheduler con tests.
   - Endpoint API + tests.
   - Validar en paper trading 24 h → revisar la distribución de `classification` y ajustar umbrales si es necesario.
2. **PR 2 — Supervisor integration**:
   - Cambios en `_compute_metrics`.
   - Cambios en `supervisor_user.txt` + `supervisor_system.txt`.
   - Cambios en `_CONFIG_SUGGESTION_PROMPT`.
   - Validar que el LLM razona correctamente con el bloque nuevo (revisar 2-3 corridas reales del Supervisor).
3. **PR 3 — Frontend**:
   - Tipo + cliente API.
   - Cambios en `PriceChart` + toggle.
   - Cambios en `ReasoningBlock`.
   - QA manual: que los pines coincidan con lo que el operador ve a ojo en el chart.

Cada PR independiente, deployable y reversible. No requiere feature flag porque cada capa es opcional: si rollbackeás el frontend, el supervisor y el job siguen funcionando; si rollbackeás el supervisor, el job y la API siguen disponibles.

---

## 9. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Umbral mal calibrado (demasiados o pocos `MISSED`) | Media | Medio | Anclar a `min_rr × SL_dist` (no a `%` fijo). PR 1 sin LLM integration permite revisar la distribución antes de exponerla al Supervisor. |
| Hindsight bias por ignorar orden temporal MFE/MAE | Baja | Alto | El algoritmo exige `time_to_mfe < time_to_mae` para `MISSED`/`BLOCKED_GOOD`. Test OA-03 cubre este caso. |
| OHLCV con gaps en el período | Media | Bajo | Clasificación `UNKNOWN` cuando > 30 % de la ventana falta; no contamina agregados. |
| Aumento de tokens del prompt del Supervisor | Baja | Bajo | Bloque ≈ 400-600 tokens. Mantenemos `decisions_dump` truncado a 40 (como hoy). |
| LLM "sobre-corrige" un día con muchos misses | Media | Medio | `_SAFE_BOUNDS` + invariantes + auto-apply restringido siguen vigentes. Sólo enriquecemos evidencia, no auto-applies nuevos. |
| Markers en chart saturan visualmente | Media | Bajo | Toggle `showOverlays.outcomes` para apagar. Tamaños conservadores. Sin texto en `BLOCKED_GOOD_TRADE` para reducir ruido. |
| Job tarda mucho (latencia OHLCV) | Baja | Bajo | Ventana acotada a 25 h × ~144 decisiones. Cache de OHLCV por bloque (no por decisión). |
| Race con el Decisor escribiendo en `decisions` | Baja | Bajo | El job lee `Decision.ts ≤ now - 15 min`. El Decisor escribe al tiempo `now`. No hay overlap. |

---

## 10. Fuera de alcance

- **WebSocket broadcast** de outcomes (polling 60 s alcanza).
- **Histórico > 25 h** (la tabla guarda todo, pero el job no reprocesa decisiones más viejas que 25 h por performance; un script de backfill es trivial si se necesita).
- **Aprendizaje continuo del Decisor** (alimentar outcomes al contexto del Decisor en cada tick — esto era el Enfoque C, descartado).
- **Auto-tuning de umbrales** (el umbral se queda anclado a `min_rr × SL_dist`; cambiar el modelo de umbral es out-of-scope).
- **Análisis de SELLs** más allá de su clasificación `GOOD_SELL`/`BAD_SELL`. Los SELL tienen su outcome real en el `trades` table; no agregamos métricas nuevas más allá del conteo.

---

## 11. Decisiones a confirmar antes de implementar

1. **Horizonte `H`**: ¿usamos `expected_holding_max_min` (default 240 min) directo, o creamos una clave nueva `outcome_attribution_horizon_min` configurable independiente? Recomendación: reusar `expected_holding_max_min` por ahora (YAGNI). Si en el futuro querés evaluar con un horizonte distinto al operativo, se agrega.
2. **Frecuencia del job**: 1 hora propuesto. Alternativa: cada 15 min. Tiene sentido si querés ver pines actualizados más rápido en el chart, pero suma carga sin beneficio claro hasta que valides el flujo end-to-end.
3. **Visibilidad de `CORRECTLY_BLOCKED` y `GOOD_HOLD` en el chart**: propongo no renderizarlos para evitar ruido. Si los querés visibles para auditar el R:R blocker, los activamos en un toggle separado.
4. **Cobertura mínima OHLCV**: propongo `30 %` faltante = `UNKNOWN`. ¿Querés más estricto (10 %) o más permisivo (50 %)?

---

## 12. Checklist de aprobación

- [ ] Acepto la clasificación de §3.1 (8 etiquetas) y el umbral anclado a `min_rr × SL_dist`.
- [ ] Acepto la tabla `decision_outcomes` y sus índices (§3.3).
- [ ] Acepto el job `outcome_attribution_tick` con frecuencia 1 h (§3.4).
- [ ] Acepto los cambios en el Supervisor (métricas, prompt usuario, prompt sistema, Fase 1 ratificación) (§3.5).
- [ ] Acepto el endpoint API y el DTO (§3.6).
- [ ] Acepto los markers nuevos en el `PriceChart` y los colores propuestos (§3.7).
- [ ] Acepto el rollout en 3 PRs (§8).
- [ ] Acepto el feedback loop end-to-end descrito en §13.
- [ ] Acepto los Acceptance Criteria OA-01…OA-13 (§6).
- [ ] Confirmo las decisiones de §11 (horizonte, frecuencia, visibilidad GOOD_HOLD, umbral UNKNOWN).

Una vez aprobado, paso al `writing-plans` skill para generar el plan de implementación TDD detallado (3 PRs separados, cada uno con su checkpoint review).

---

## 13. Feedback loop end-to-end — cómo el Supervisor ajusta al Decisor

Esta sección hace explícito el mecanismo por el cual el Análisis Contrafactual termina mejorando los trades del Decisor en el ciclo siguiente. Es lo que cierra el OODA loop del sistema (Observe → Orient → Decide → Act).

### 13.1 Palancas del Supervisor sobre el Decisor

El Supervisor tiene **tres palancas** efectivas; el Análisis Contrafactual provee evidencia para usar cualquiera de ellas con criterio:

| Palanca | Cómo llega al Decisor | Cuándo se aplica | Quién la decide |
|---|---|---|---|
| **Playbook** (Markdown libre) | `decisor.py:68` lo carga en cada tick y lo inyecta como Bloque J del contexto. | El tick siguiente a una regeneración (Fase 2). | LLM Supervisor — texto libre dentro de un schema markdown. |
| **Config keys numéricos** (`conf_threshold_*`, `min_confluences_buy`, `min_rr_ratio`, `sl_atr_multiplier`, `min_fees_to_tp_ratio`, `cooldown_after_sell_min`, `expected_holding_max_min`) | `ContextBuilder` los lee y los pone en Bloque I (Risk Config). Algunos (`min_rr_ratio`, `sl_atr_multiplier`, `max_position_pct`, `min_fees_to_tp_ratio`) los aplica además el Risk Gate como hard constraints. | El tick siguiente a un auto-apply. | LLM Supervisor (Fase 3 — config suggestions), filtrado por `_SAFE_BOUNDS` + invariantes. |
| **Toggles booleanos** (`coherence_strict_mode`, `two_pass_enabled`) | El Decisor los lee desde ConfigStore al construirse. | El tick siguiente. | LLM Supervisor con criterio explícito en el prompt de Fase 3. |

El **Risk Gate (R1–R10)** y el **CoherenceChecker (C1–C6)** son safety nets fijas, no palancas — limitan lo que el Decisor puede ejecutar pero no las cambia el Supervisor.

### 13.2 Mapa señal contrafactual → palanca → cambio

Esta tabla es la **operacionalización concreta** de las guías de §3.5.3. Va al `supervisor_system.txt` como referencia para el LLM Supervisor:

| Señal observada | Causa probable | Palanca recomendada | Cambio concreto |
|---|---|---|---|
| `missed_rate > 30 %` concentrado en un régimen, con confidence en banda 0.55-0.69 | Threshold del régimen demasiado alto | Config + Playbook | `conf_threshold_<regime>: 0.60 → 0.55` (auto-apply). Playbook agrega regla "considerar BUY en este régimen con confidence ≥ 0.55 si hay patrón X". |
| `blocked_good_count` por R5 alto vs `correctly_blocked_count` | `min_rr_ratio` cortando setups buenos en alta volatilidad | Config | `min_rr_ratio: 1.3 → 1.2` (dentro de `_SAFE_BOUNDS=(1.0, 3.0)`, respeta invariante `min_rr ≤ default_rr`). |
| `blocked_good_count` por R10 alto | `min_fees_to_tp_ratio` muy estricto en mercado actual de fees bajos | Config | `min_fees_to_tp_ratio: 3.0 → 2.5`. |
| `blocked_good_count` por R1 alto | `max_position_pct` cortando trades aun cuando confidence es alta | **NO tocar** (es un cap absoluto del operador). Cambiar el playbook para que el LLM no proponga sizings imposibles. |
| `bad_buy_rate > 50 %` con confidence ≥ 0.70 | LLM sobre-confiado en algún régimen | Config + Playbook | Subir `conf_threshold_<regime_predominante>: 0.70 → 0.75`. Playbook desincentiva BUY en condiciones específicas que produjeron los bad buys. |
| `top_misses` comparten patrón técnico (ej. MACD cross + volumen + RSI cruzando 50) | Playbook no captura este setup | **Playbook únicamente** | Regla nueva en el playbook v_{n+1}: "Setup A1: <condiciones específicas> → BUY con confidence ≥ X, sizing Y % de max_position_pct". |
| `coherence_warnings_total / total_decisions > 0.25` sostenido + mayoría C1/C2/C3 | Decisor está alucinando, posiblemente porque el playbook empuja a conclusiones que los datos no respaldan | Toggle + Playbook | `coherence_strict_mode: false → true` (auto-apply). Playbook simplifica las reglas que están confundiendo al LLM. |
| `two_pass_triggered_count > 30 %` sin mejora de outcomes | Two-pass se gatilla seguido sin ganancia | Toggle | `two_pass_enabled: true → false` (auto-apply). |
| `evaluated_decisions < 20` | Muestra insuficiente | **NO tocar nada**. Las métricas contrafactuales son ruido a esa escala. |

### 13.3 Timeline del ciclo cerrado

```text
t = 00:00 UTC — cron Supervisor
  │
  ├─ _compute_metrics()
  │    ├─ Lee decisions de las últimas 24 h
  │    ├─ Lee decision_outcomes (JOIN)  ← evidencia contrafactual
  │    └─ Computa missed_rate, bad_buy_rate, blocked_good_count, top_misses
  │
  ├─ Fase 1 — supervisor_eval_user.txt
  │    LLM ve bloque contrafactual compacto + métricas clásicas
  │    Decide: ratify | regenerate
  │    (Si missed_rate > 30 % o bad_buy_rate > 50 % con n ≥ 20 → no ratifica)
  │
  ├─ Fase 2 — supervisor_user.txt (sólo si regenerate)
  │    LLM ve bloque contrafactual completo (con top_misses) + tabla §13.2
  │    Escribe playbook v_{n+1} con reglas concretas:
  │      - "Setup A1: <patrón observado en top_misses> → BUY ..."
  │      - "En RANGE con ADX < 20: HOLD aunque confidence sea alta"
  │      - ...
  │    → INSERT INTO playbook_versions (version=n+1, active=true)
  │
  ├─ Fase 3 — _CONFIG_SUGGESTION_PROMPT
  │    LLM ve bloque contrafactual completo + interpretación de la tabla §13.2
  │    Sugiere ajustes JSON: [{"key": "conf_threshold_trending_up", "current": 0.60, "suggested": 0.55, "reason": "..."}]
  │    → _apply_config_suggestions filtra por _SAFE_BOUNDS + invariantes
  │    → UPDATE config + INSERT INTO config_history
  │
t = 00:10 UTC — tick del Decisor (intervalo default 10 min)
  │
  ├─ ContextBuilder.build()
  │    ├─ get_active_playbook() → carga v_{n+1} → Bloque J
  │    ├─ Lee config keys actualizadas → Bloque I (thresholds nuevos)
  │    └─ Resto de bloques A-H, K (sin cambios)
  │
  ├─ Decisor.decide()
  │    LLM razona con playbook + thresholds nuevos
  │    → tiende a tomar las decisiones que el Supervisor incentivó
  │    → respeta las nuevas restricciones donde antes había bad buys
  │
  ├─ CoherenceChecker C1-C6 — coherencia interna
  ├─ Risk Gate R1-R10 — ejecutabilidad
  └─ Persist Decision + execute si pasó gate

t = 01:00 UTC — outcome_attribution_tick
  │
  └─ Clasifica las decisiones nuevas del Decisor que ya operó bajo v_{n+1}.
     (No alcanza a clasificar las decisiones de los últimos 15 min — esperan
     a la siguiente corrida cuando madure su ventana H.)

t = 24:00 UTC — próximo cron del Supervisor
  │
  └─ Mide el efecto:
     - ¿Bajó missed_rate vs ayer? → la regla nueva del playbook está funcionando.
     - ¿Bajó bad_buy_rate? → el threshold ajustado está filtrando trades malos.
     - ¿Subió blocked_good_count? → ajustamos demasiado, hay que revertir.
     Decide: ratify v_{n+1} | regenerate v_{n+2}.
```

### 13.4 Por qué este loop converge (y no oscila)

Tres frenos previenen sobre-correcciones:

1. **`_SAFE_BOUNDS`** de `supervisor.py`. Por ejemplo `conf_threshold_trending_up ∈ [0.40, 0.85]`. Un día con muchos misses no puede tirar el threshold a 0.30.
2. **Invariantes cruzadas**: `min_rr ≤ default_rr`, `conf_threshold_trending_up ≤ conf_threshold_range ≤ conf_threshold_high_vol`, `sl_atr_multiplier ≤ sl_atr_max_multiplier`. Cualquier sugerencia que las viole se rechaza con `reject_reason`.
3. **Playbook ratification con WR delta gate**: `playbook_force_regen_wr_delta_pct = 15 %`. Una fluctuación chica de WR no fuerza regeneración; sólo cambios significativos lo hacen. Esto evita que el Supervisor "persiga" ruido diario.

A largo plazo, si el loop diverge (oscila entre dos playbooks o thresholds que se contradicen), el operador puede:
- Mirar `config_history` (cambios por `supervisor`) y los `top_misses` de cada día para detectar el ciclo.
- Activar `coherence_strict_mode=true` para endurecer las restricciones del Decisor.
- Reactivar `_apply_deterministic_overrides` (rollback al v1.2 pre-LLM-centric) vía feature flag si se considerara necesario — fuera de scope de este spec.

### 13.5 Cómo medimos que el loop funcionó

Métrica de éxito a 4 semanas en paper trading:

- **Win rate** ≥ baseline previa (no degradar) **Y** una de estas dos:
  - `missed_rate` bajó al menos 30 % vs primer ciclo con el bloque activo.
  - `bad_buy_rate` bajó al menos 20 % vs primer ciclo con el bloque activo.

Si ninguna mejora, revisar: ¿el LLM está consumiendo el bloque (mirar `decisions.input` y `decisions.output.reasoning` del Supervisor) o lo está ignorando? ¿El umbral de clasificación está mal calibrado? ¿La frecuencia del job es insuficiente?
