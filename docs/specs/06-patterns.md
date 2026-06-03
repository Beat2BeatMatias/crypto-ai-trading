# Patrones de Implementación — Crypto AI Trading

> Audiencia: Devs / Tech leads.
> Versión: 1.4 — 2026-06-02.

Catálogo de **19 patrones reutilizables** descubiertos en el código y **4 anti-patrones** a evitar. Cada patrón tiene 2+ evidencias en el repositorio y se documenta con un ejemplo mínimo y la regla de cuándo aplicarlo.

Este documento complementa la [Especificación Técnica](./02-technical-spec.md) describiendo *cómo* se construye el sistema, no *qué* hace. Sirve para nuevas features que deben mantener consistencia con la arquitectura existente.

---

## P-01 — Async SQLAlchemy session por scope corto

**Categoría**: Database

**Evidencia**: `trading-engine/main.py:68-209`, `web/api/decisions.py:31-42`, `web/api/config.py:31-50`, `shared/config_store.py:184-233`.

```python
async def decisor_tick() -> None:
    async with session_factory() as session:
        config_store = ConfigStore(session)
        cfg = await _load_calibration(config_store)
        decision = await Decisor(session, llm, ...).run(...)
        await session.commit()
```

**Cuándo usar**: cada operación de negocio que toca DB abre su propia sesión async con `session_factory()` y la cierra al salir del `async with`. Nunca se comparte sesión entre coroutines independientes para evitar inconsistencias del estado de unit-of-work.

---

## P-02 — `ConfigStore` con seed idempotente + get tipado

**Categoría**: Config

**Evidencia**: `shared/config_store.py:184-214`, `trading-engine/main.py:71-134`, `web/api/config.py:38-50`.

```python
class ConfigStore:
    async def seed_defaults(self) -> None:
        existing = {row.key for row in (await self.session.execute(select(ConfigEntry))).scalars()}
        for key, default in DEFAULTS.items():
            if key.value not in existing:
                self.session.add(ConfigEntry(key=key.value, value=default.value, ...))
        await self.session.commit()

    async def get_typed(self, key: ConfigKey) -> Any:
        entry = await self.session.get(ConfigEntry, key.value)
        return _cast(entry.value, entry.value_type)
```

**Cuándo usar**: para cualquier configuración persistente editable en runtime. El seed idempotente garantiza que claves nuevas se agregan sin sobrescribir las modificadas; `get_typed` convierte `int`/`float`/`bool`/`json` automáticamente sin que el caller deba castear.

---

## P-03 — `structlog` JSON con contexto bound

**Categoría**: Logging / Observabilidad

**Evidencia**: `trading-engine/main.py:41-209`, `trading-engine/agents/decisor.py`, `trading-engine/agents/supervisor.py`.

```python
log = structlog.get_logger(__name__)

log.info(
    "decision.persisted",
    action=decision.action.value,
    confidence=decision.confidence,
    executed=executed,
    rejected_reason=verdict.reason if not verdict.approved else None,
)
```

**Cuándo usar**: nombrar **todos** los eventos con `dominio.evento` (p.ej. `decision.persisted`, `engine.ohlcv_fetch_failed`) para queryability fácil; pasar kwargs estructurados (nunca interpolar strings). Eventos en pasado (`persisted`, `failed`, `started`) describen lo que sucedió.

---

## P-04 — Fallback en cascada con persistencia auditada (FeeManager)

**Categoría**: Resilience / External APIs

**Evidencia**: `trading-engine/execution/fee_manager.py:38-75`, `trading-engine/main.py:138-150` (`fetch_balance` con fallback a `BalanceSnapshot`).

```python
async def refresh(self) -> None:
    try:
        fees = await self._exchange.fetch_trading_fees()
        parsed = self._parse(fees)
        await self._persist(parsed)
        self._maker, self._taker = parsed["maker"], parsed["taker"]
    except Exception as exc:
        log.warning("fees.refresh_failed", error=str(exc))
        snap = await self._load_last_snapshot()
        if snap:
            self._maker, self._taker = snap.maker_fee, snap.taker_fee
        else:
            self._maker, self._taker = 0.001, 0.001
```

**Cuándo usar**: para datos externos críticos donde una respuesta antigua > ningún dato. Regla en tres niveles: **(1)** persistir cada éxito; **(2)** caer al último persistido en error; **(3)** sólo como último recurso usar un valor conservador.

---

## P-05 — APScheduler async con shutdown idempotente

**Categoría**: Scheduling

**Evidencia**: `trading-engine/scheduler.py:10-39`, `trading-engine/main.py:80-324`.

```python
class EngineScheduler:
    def __init__(self) -> None:
        self._sched = AsyncIOScheduler(timezone="UTC")

    def add_decisor(self, fn, *, interval_min: int) -> None:
        self._sched.add_job(fn, trigger=IntervalTrigger(minutes=interval_min), id="decisor")

    def add_supervisor(self, fn, *, cron: str) -> None:
        self._sched.add_job(fn, trigger=CronTrigger.from_crontab(cron, timezone="UTC"), id="supervisor")

    def start(self) -> None:
        self._sched.start()
        log.info("scheduler.started")

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)
        log.info("scheduler.stopped")
```

**Cuándo usar**: para registrar coroutines como jobs con cron o intervalos. Cada job con `id` único para poder cancelarlo. `shutdown(wait=False)` para no bloquear el `finally` del entrypoint ante señales `SIGINT/SIGTERM`.

---

## P-06 — Strip markdown fences + JSON parse + Pydantic validate

**Categoría**: LLM integration

**Evidencia**: `trading-engine/agents/decisor.py:75-98`, `trading-engine/agents/supervisor.py:160-185`.

```python
raw = await llm.call(provider, system=system, user=user_msg)

cleaned = raw.strip()
if cleaned.startswith("```"):
    cleaned = "\n".join(cleaned.splitlines()[1:-1])
try:
    parsed = json.loads(cleaned)
    output = DecisorOutput.model_validate(parsed)
except (json.JSONDecodeError, ValidationError) as exc:
    log.warning("decision.parse_error", error=str(exc), raw=raw[:200])
    output = _fallback_hold("parse_error")
```

**Cuándo usar**: siempre que un LLM emita JSON. Los modelos suelen envolver en triple backticks o agregar texto antes/después. Pydantic 2 valida estructura **y** tipos; fallback explícito a un valor "seguro" (`HOLD`) en error.

---

## P-07 — CCXT con `enableRateLimit=True` y manejo de excepciones por endpoint

**Categoría**: External APIs

**Evidencia**: `trading-engine/exchange.py:16-24`, `collectors/price_collector.py:40-50`, `execution/executor.py:23-39`, `collectors/orderbook_collector.py:55-65`.

```python
def build_binance_client() -> ccxt.async_support.binance:
    return ccxt.async_support.binance({
        "apiKey": settings.binance_api_key,
        "secret": settings.binance_api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })

try:
    ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
except Exception as exc:
    log.warning("engine.ohlcv_fetch_failed_using_cached_data", error=str(exc))
    return None
```

**Cuándo usar**: para Binance Spot vía CCXT. `enableRateLimit` deja que ccxt maneje el throttling. Las excepciones se loguean con contexto del endpoint y se traducen a un valor "ningún dato disponible" cuando es seguro continuar.

---

## P-08 — Migración Alembic con `op.create_table` + `op.create_index`

**Categoría**: Database / Migrations

**Evidencia**: `trading-engine/alembic/versions/001_initial_schema.py:17-159`, `003_add_balance_snapshots.py:17-26`, `002_add_trade_close_requested.py:16-20`.

```python
def upgrade() -> None:
    op.create_table(
        "balance_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("usdt", sa.Numeric(18, 4), nullable=False),
        sa.Column("btc", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(20), server_default="binance", nullable=False),
    )
    op.create_index("idx_balance_snapshots_ts", "balance_snapshots", ["ts"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_balance_snapshots_ts", table_name="balance_snapshots")
    op.drop_table("balance_snapshots")
```

**Cuándo usar**: cada cambio de esquema. Siempre con `downgrade()` correspondiente. `server_default` para que Postgres lo respete sin que el ORM tenga que enviarlo. UUIDs con `gen_random_uuid()` server-side (requiere extensión `pgcrypto`).

---

## P-09 — WebSocket broadcast `{"event": ..., "data": ...}` con polling DB

**Categoría**: Realtime / Frontend

**Evidencia**: `web/ws/manager.py:23-32`, `web/ws/feeds.py:27-84`, `frontend/src/hooks/useWebSocket.ts:13-28`.

```python
async def broadcast(self, event: str, data: dict) -> None:
    payload = json.dumps({"event": event, "data": data})
    dead = []
    for ws in self._clients:
        try:
            await ws.send_text(payload)
        except WebSocketDisconnect:
            dead.append(ws)
    for ws in dead:
        self._clients.discard(ws)
```

```typescript
const { last, connected } = useWebSocket("/ws");

useEffect(() => {
  if (last?.event === "decision") {
    setDecisions(prev => [last.data, ...prev].slice(0, 100));
  }
}, [last]);
```

**Cuándo usar**: cuando hay un evento polled desde DB que se necesita propagar a N clientes. El servidor pollea Postgres a un intervalo razonable (2–5 s) y empuja sólo cuando hay novedades. El cliente filtra por `event` y actualiza estado local.

---

## P-10 — DTO Pydantic 2 con `from_attributes` para ORM → API

**Categoría**: API / Serialización

**Evidencia**: `web/api/decisions.py:12-25`, `web/api/trades.py:12-28`, `web/api/positions.py:12-23`, `web/api/config.py:12-16`.

```python
class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ts: datetime
    agent: str
    model: str
    input: dict
    output: dict
    executed: bool
    rejected_reason: str | None = None


@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(...):
    rows = await session.execute(select(Decision).order_by(Decision.ts.desc()).limit(limit))
    return rows.scalars().all()
```

**Cuándo usar**: cada endpoint que devuelve filas del ORM. Pydantic 2 + `from_attributes=True` lee atributos directamente (no hace falta `.dict()`). FastAPI valida el contrato HTTP y serializa.

---

## P-11 — Tests con `freezegun` + fixtures SQLite ligero

**Categoría**: Testing

**Evidencia**: `web/tests/conftest.py:70-92`, `trading-engine/tests/test_decisor.py`, `trading-engine/tests/test_supervisor.py`, `trading-engine/pytest.ini`.

```python
@pytest.fixture
async def app_with_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.session_factory = session_factory
    yield app
    await engine.dispose()


@freeze_time("2026-05-14T12:00:00Z")
async def test_decision_persists_correct_ts(session):
    decisor = Decisor(session, ...)
    decision = await decisor.run(...)
    assert decision.ts.isoformat().startswith("2026-05-14T12:00")
```

**Cuándo usar**: para tests determinísticos de lógica temporal (`ts`, retención 24h, decisor windowing). SQLite en memoria es ~10× más rápido que un Postgres testcontainer para tests unitarios; usar Postgres real sólo cuando se prueban GIN/JSONB.

---

## P-12 — Texto UI en español + `toLocaleString("es-AR")` para números

**Categoría**: Frontend / i18n

**Evidencia**: `frontend/src/types/decisorOutput.ts:fmtConfidencePct`, `components/ConfidenceBreakdown.tsx`, `pages/Decisions.tsx`, `pages/Dashboard.tsx`.

```tsx
export function fmtConfidencePct(value: number | null | undefined): string {
  const n = typeof value === "number" ? value : 0;
  return `${(n * 100).toLocaleString("es-AR", { maximumFractionDigits: 0 })}%`;
}
```

**Cuándo usar**: cuando el target es es-AR exclusivo y agregar `i18next` sería overkill. `toLocaleString` cubre números, moneda, fechas y respeta el separador decimal con coma.

---

## P-14 — Desglose de confianza del Decisor (`ConfidenceBreakdown`)

**Categoría**: Frontend / observabilidad

**Evidencia**: `frontend/src/components/ConfidenceBreakdown.tsx`, `frontend/src/types/decisorOutput.ts`, `shared/confidence.py`, `decisions.output.confidence_meta`.

```tsx
<ConfidenceBreakdown
  confidence={out.confidence}
  confidenceBase={out.confidence_base}
  confidenceAdjustment={out.confidence_adjustment}
  meta={out.confidence_meta}
/>
```

**Cuándo usar**: en cualquier vista que muestre decisiones del agente `decisor`. La confianza final **no** debe leerse solo del campo `confidence` histórico sin contexto: desde v1.9 la base es server-side y las decisiones antiguas pueden carecer de `confidence_meta`.

**Campos UI relevantes**:
- `confidence_meta.confluences_counted` — post-filtro (A–H + I–Z activas).
- `confidence_meta.confluences_dropped` — códigos que el LLM citó pero el servidor eliminó (p. ej. letra desactivada).

---

## P-13 — Cliente API REST minimalista en `frontend/src/api/client.ts`

**Categoría**: Frontend / HTTP

**Evidencia**: `frontend/src/api/client.ts:5-62`, usado en todas las páginas (`Dashboard`, `Trades`, `Decisions`, `Playbook`, `Config`, `Health`).

```typescript
async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export const api = {
  trades: (status?: string) => get<Trade[]>(`/api/trades${status ? `?status=${status}` : ""}`),
  setConfig: (key: string, value: string) => put(`/api/config/${key}`, { value }),
};
```

**Cuándo usar**: SPAs simples sin librería de fetching. Un único `api` object con un método por endpoint mantiene el tipado, evita strings dispersas y facilita el "encontrar todos los lugares que llaman X".

---

## P-14 — Configuración multi-fuente con Pydantic Settings

**Categoría**: Config

**Evidencia**: `trading-engine/config.py:1-25`. Defaults declarados con `Field(default=..., env="VAR_NAME")` y lectura cacheada vía `get_settings()`.

```python
class EngineSettings(BaseSettings):
    database_url: str = Field(..., env="DATABASE_URL")
    binance_api_key: str = Field(..., env="BINANCE_API_KEY")
    binance_api_secret: str = Field(..., env="BINANCE_API_SECRET")
    binance_testnet: bool = Field(True, env="BINANCE_TESTNET")
    gemini_api_key: str | None = Field(None, env="GEMINI_API_KEY")
    groq_api_key: str | None = Field(None, env="GROQ_API_KEY")

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)


@lru_cache
def get_settings() -> EngineSettings:
    return EngineSettings()
```

**Cuándo usar**: para configuración que viene del entorno (Docker, `.env`, K8s secrets). `lru_cache` evita re-parseo en cada llamada. Para config **editable en runtime**, usar `ConfigStore` (ver P-02).

---

## P-15 — CoherenceChecker post-LLM (reemplaza overrides deterministas)

**Categoría**: LLM safety

**Evidencia**: `trading-engine/risk/coherence_checker.py`, `trading-engine/agents/decisor.py` (two-pass).

```python
warnings = coherence_checker.evaluate(output, indicators_ctx)
if warnings and strict_mode and any(w.rule_id in ("C1", "C2", "C3") for w in warnings):
    return _hold_decision("coherence_strict")
output = output.model_copy(update={"coherence_warnings": [w.__dict__ for w in warnings]})
```

**Cuándo usar**: auditar inconsistencias lógicas del LLM (declaración vs datos) **sin reescribir silenciosamente** la decisión. Los warnings se persisten y se inyectan al ciclo siguiente (Bloque G). En `strict_mode`, C1/C2/C3 fuerzan HOLD. Reemplaza el patrón legacy de overrides deterministas (eliminado en v1.3).

---

## P-16 — Tablas con índice GIN sobre JSONB

**Categoría**: Database

> Índices GIN materializados en Postgres vía migración **006** (`006_add_gin_indexes_and_missing_fk.py`).

```python
class Indicators(Base):
    __tablename__ = "indicators"
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (
        Index("idx_indicators_data", "data", postgresql_using="gin"),
    )
```

**Cuándo aplicarlo**: para querying sobre payloads JSONB (`data->>'rsi_5m'`, `output @> '{"action":"BUY"}'`). Crear como migración Alembic separada para que el schema productivo lo refleje y no sólo el ORM en `Base.metadata.create_all`.

---

## P-17 — Cortocircuito determinístico + LLM corto para decisiones binarias

**Categoría**: LLM / Cost & Safety

**Evidencia**: `trading-engine/agents/supervisor.py::_evaluate_ratification` (fase de ratificación del playbook, `01-functional-spec.md §F5.bis.5`); `trading-engine/agents/supervisor.py::_apply_config_suggestions` (rechazo previo de claves fuera de `_SAFE_BOUNDS` antes de aplicar).

```python
async def _evaluate_ratification(self, metrics, active_playbook, cfg) -> dict:
    forced = self._force_regenerate_reason(metrics, active_playbook, cfg)
    if forced:
        return {"ratify": False, "ratify_reason": None, "force_regen_reason": forced}

    resp = await self.llm.call(
        provider=self.provider,
        system_prompt=self.prompt_manager.load_system_prompt("supervisor_eval"),
        user_prompt=self.prompt_manager.render_user_prompt("supervisor_eval", ctx, strict=False),
        fallbacks=self.fallbacks,
        json_mode=True,
    )
    parsed = _parse_json_strict(resp.text)
    return {
        "ratify": bool(parsed.get("ratify", False)),
        "ratify_reason": parsed.get("reason", ""),
        "force_regen_reason": None,
    }
```

**Cuándo usar**: para cualquier decisión binaria del LLM que pueda resolverse offline en muchos casos (mantener vs. cambiar, aceptar vs. rechazar, ratificar vs. regenerar). El cortocircuito determinístico **acota el riesgo** (forzar acción ante señales objetivas) y **reduce costo de tokens** cuando no hay nada nuevo que analizar. Requisitos:

1. Reglas explícitas, configurables y testeables (`max_playbook_age_days`, `playbook_force_regen_wr_delta_pct`).
2. Llamada LLM corta con JSON estructurado (`json_mode=True`) y parseo defensivo.
3. Persistencia del veredicto **con motivo**: distinguir si fue por cortocircuito determinístico (`force_regen_reason`) o por opinión del LLM (`ratify_reason`).
4. Audit trail asegurado aunque no haya cambios materiales (1 fila en `decisions` por ejecución).

---

## P-18 — Pipeline post-mortem encadenado (atribución → lección → acción)

**Categoría**: LLM / Learning

**Evidencia**: `trading-engine/main.py` (post-mortem encadenado tras `outcome_attribution_tick`); `agents/postmortem_job.py`; `agents/postmortem_schemas.py` (`coerce_lesson_raw`); `agents/postmortem_agent.py`; `agents/lesson_normalizer.py`; `agents/context_builder.py` (Bloque K); `frontend/src/pages/Config.tsx` (sección Post-mortem).

```python
async def outcome_attribution_tick(...):
    await run_outcome_attribution(...)
    if postmortem_enabled:
        await outcome_postmortem_tick(
            session, llm,
            provider=postmortem_provider,
            fallback_providers=postmortem_fallback_providers,
            max_per_tick=postmortem_max_per_tick,
        )
```

Flujo interno del tick post-mortem:

1. Query outcomes elegibles (`BAD_BUY`, `BAD_SELL`, `MISSED_OPPORTUNITY`, `BLOCKED_GOOD_TRADE`) con `postmortem_status IS NULL` **o** `failed` con `< 3` intentos (`lesson_raw._meta.attempts`).
2. Orden por `severity_score` DESC; tomar hasta `postmortem_max_per_tick` (**1 llamada LLM por decisión**).
3. `PostMortemAgent` con cascade primary + `postmortem_fallback_providers` → `coerce_lesson_raw()` → `LessonRaw`.
4. `lesson_normalizer.normalize()` → ruta `remap` | `candidate` | `guidance`.
5. Persistir en `decision_outcomes.lesson_normalized`; status `completed` o reintento/`failed`.
6. Si `candidate` → upsert `confluence_candidates` por `pattern_tag`.
7. Bloque K lee lecciones `remap`/`guidance` en ventana configurable (`block_k_window_hours`).

**Cuándo usar**: cuando el sistema debe aprender de errores **sin** mutar el playbook ni el catálogo A–H directamente. Requisitos:

1. Job encadenado (no scheduler separado) para garantizar orden attribution → post-mortem.
2. Límite por tick (`postmortem_max_per_tick`) = límite de **decisiones**, no de llamadas batch.
3. Parseo defensivo (`coerce_lesson_raw`) antes de Pydantic — tolera arrays heterogéneos del LLM.
4. Fallback CSV configurable independiente del Decisor/Supervisor (evita agotar cuota de un solo modelo).
5. Normalizador determinístico que clasifica la salida en rutas con efectos distintos (prompt vs. candidato vs. remap).
6. Promoción a producción (I–Z) separada con criterios P1–P6 y aprobación Supervisor u operador.

---

## P-19 — `ExchangeAdapter` para Spot vs Futures

**Categoría**: Execution / Exchange

**Evidencia**: `trading-engine/execution/exchange_adapter.py` (`SpotAdapter`, `FuturesAdapter`, `build_adapter`); `trading-engine/main.py` (bootstrap, `validate_futures_sizing`); `trading-engine/execution/executor.py` (`execute_open`, `execute_close`).

```python
adapter = build_adapter(trading_product)  # "spot" | "futures"
await adapter.setup_symbol(symbol, leverage=1, margin_mode="isolated")
result = await adapter.open_position(symbol=symbol, direction=Direction.SHORT, notional_usdt=n, price=p)
await adapter.place_brackets(symbol=symbol, direction=Direction.SHORT, qty=result.qty, stop_loss=sl, take_profit=tp)
```

**Cuándo usar**: cuando la lógica de órdenes difiere por producto (OCO Spot vs reduceOnly en Futures). El `Executor` y `OrderTracker` reciben el adapter inyectado; **no** esparcir `if futures:` por todo el módulo. Rollback operativo: `trading_product=spot` sin cambiar código.

---

## Anti-patrones identificados (a evitar)

Lista corta de patrones presentes en el código que **no deben replicarse** y conviene corregir cuando se toque el área:

| Anti-pattern | Evidencia | Por qué evitarlo |
|--------------|-----------|------------------|
| ~~Pasar `0.0` constante al Risk Gate~~ | ✅ Resuelto (D-001): `_compute_risk_metrics()` en `main.py` | — |
| ~~Declarar índice/FK sólo en ORM~~ | ✅ Resuelto (D-006): migración 006 | — |
| ~~Dependencia npm no usada (`recharts`)~~ | ✅ Resuelto (D-017): eliminada; chart usa `lightweight-charts` | — |
| Tests con `pd.Series` que omiten columnas esperadas | `backtesting/tests/test_runner.py` (D-019 resuelto) | Verificar fixtures completas al mockear indicadores. |
| Reescribir silenciosamente decisiones del LLM | Overrides deterministas (eliminados v1.3) | Oculta la intención real del LLM; usar CoherenceChecker + Risk Gate con `rejected_reason` explícito. |
| Asumir `btc_held > 0` para cerrar posiciones | Pre-futures en Risk Gate R6 | En short no hay BTC spot; usar `has_open_position` + `position_side`. |
| PnL / SL siempre long | `Dashboard`, `Trades` legacy | Usar `position_side` y helpers en `shared/pnl.py` / `frontend/src/lib/pnl.ts`. |

---

## Cómo usar este catálogo

1. **Al desarrollar una feature nueva**: revisar si ya existe un patrón aplicable y reusarlo (consistencia > novedad).
2. **Al hacer code review**: chequear que las nuevas adiciones no introduzcan anti-patrones de la tabla anterior.
3. **Al agregar un patrón**: requiere **2+ evidencias** en el código + ejemplo mínimo + regla clara de cuándo aplicarlo. Si el patrón es único o experimental, mantenerlo fuera del catálogo hasta que tenga adopción real.
