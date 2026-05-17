# Patrones de Implementación — Crypto AI Trading

> Audiencia: Devs / Tech leads.
> Versión: 1.0 — 2026-05-14.

Catálogo de **16 patrones reutilizables** descubiertos en el código y **4 anti-patrones** a evitar. Cada patrón tiene 2+ evidencias en el repositorio y se documenta con un ejemplo mínimo y la regla de cuándo aplicarlo.

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

**Evidencia**: `frontend/src/pages/Dashboard.tsx:153-157`, `Trades.tsx`, `Decisions.tsx`, `frontend/index.html:2`.

```tsx
<div className="text-2xl font-bold">
  {balance.usdt.toLocaleString("es-AR", { minimumFractionDigits: 2 })} USDT
</div>

<span>
  {(decision.confidence * 100).toLocaleString("es-AR", { maximumFractionDigits: 0 })}%
</span>
```

**Cuándo usar**: cuando el target es es-AR exclusivo y agregar `i18next` sería overkill. `toLocaleString` cubre números, moneda, fechas y respeta el separador decimal con coma.

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

## P-15 — Override determinístico post-LLM antes de persistir

**Categoría**: LLM safety

**Evidencia**: `trading-engine/agents/decisor.py:127-160` (overrides BUY → HOLD, sizing por confidence).

```python
def _apply_deterministic_overrides(output: DecisorOutput, context: dict) -> DecisorOutput:
    if output.action == DecisorAction.BUY:
        if output.regime == MarketRegime.TRENDING_DOWN:
            return _hold(output, reason="override_TRENDING_DOWN_buy_blocked")
        if output.confidence < 0.60:
            return _hold(output, reason="override_low_confidence")

        if output.confidence >= 0.70:
            size = context["max_position_pct"]
        else:
            size = max(0.01, min(0.03, output.position_size_pct))
        return output.model_copy(update={"position_size_pct": size})
    return output
```

**Cuándo usar**: cuando el LLM puede emitir outputs estructuralmente válidos pero peligrosos. Override **después** de validar pero **antes** de persistir/ejecutar. Documentar la razón del override en `output.reasoning` o en logs estructurados para futura auditoría.

---

## P-16 — Tablas con índice GIN sobre JSONB

**Categoría**: Database

> Patrón **declarado en el ORM pero pendiente de migración Alembic** (ver `07-discrepancies-and-gaps.md` D-006). Documentado aquí como la forma recomendada para nuevos campos JSONB que se vayan a querear por contenido.

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

## Anti-patrones identificados (a evitar)

Lista corta de patrones presentes en el código que **no deben replicarse** y conviene corregir cuando se toque el área:

| Anti-pattern | Evidencia | Por qué evitarlo |
|--------------|-----------|------------------|
| Pasar `0.0` constante a un validador que se chequea contra threshold | `trading-engine/main.py:213-216` pasa `daily_pnl_pct=0.0` y `total_drawdown_pct=0.0` siempre al `RiskGate` (ver D-001) | La regla R9 (daily stop) y la de drawdown total **nunca disparan** → falsa sensación de seguridad. |
| Declarar índice/FK sólo en ORM sin migración Alembic | `shared/db/models.py` vs `001_initial_schema.py` (ver D-006) | El schema productivo no los tiene; performance y consistencia comprometidas. |
| Dependencia npm declarada y no usada (`recharts`) | `frontend/package.json` vs grep en `src/` | Bundle bloat + confusión sobre features prometidas. |
| Tests con `pd.Series` que omiten columnas esperadas | `backtesting/tests/test_runner.py::test_signal_buy_requires_min_confluences` (ver D-019) | El test falla por `KeyError`, no por la validación de negocio que intenta probar — false-pass disfrazado de pass. |

---

## Cómo usar este catálogo

1. **Al desarrollar una feature nueva**: revisar si ya existe un patrón aplicable y reusarlo (consistencia > novedad).
2. **Al hacer code review**: chequear que las nuevas adiciones no introduzcan anti-patrones de la tabla anterior.
3. **Al agregar un patrón**: requiere **2+ evidencias** en el código + ejemplo mínimo + regla clara de cuándo aplicarlo. Si el patrón es único o experimental, mantenerlo fuera del catálogo hasta que tenga adopción real.
