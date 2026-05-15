# Shared + DB + Backtesting — Analysis

Análisis de `shared/`, migraciones Alembic (`trading-engine/alembic/versions/*`), y `backtesting/`. Cada hallazgo respaldado por `archivo:línea`.

> **Nota**: material crudo. La síntesis final está en `meli/specs/technical-spec.md` §4 (modelo de datos).

## Parte A — Shared

### 1. `shared/schemas.py`

| Modelo | Tipo | Línea | Notas |
|--------|------|-------|-------|
| `DecisorAction` | Enum | `:6-10` | `BUY`, `SELL`, `HOLD` |
| `MarketRegime` | Enum | `:13-17` | `TRENDING_UP`, `TRENDING_DOWN`, `RANGE`, `HIGH_VOLATILITY` |
| `DecisorOutput` | Pydantic | `:20-68` | Validadores: SL+TP obligatorios en BUY; reasoning trunca 800; confidence = clamp(base+adj) |
| `TradeOutcome` | Pydantic | `:71-76` | `pnl_usdt`, `pnl_pct`, `close_reason`, `duration_min`, `fees_usdt?` |

### 2. `shared/config_store.py`

| Símbolo | Línea | Propósito |
|---------|-------|-----------|
| `ConfigKey` Enum | `:17-80` | Claves tipadas persistidas en `config` |
| `DEFAULTS` dict | `:89-175` | Valores por defecto |
| `ConfigStore.seed_defaults` | `:184-201` | Inserta filas faltantes, idempotente |
| `ConfigStore.get_typed` | `:209-214` | Lee + convierte via `_cast` |
| `ConfigStore.set` | `:216-233` | Update + audit en `config_history` + commit |
| `_cast` | `:236-245` | int/float/bool/json/string |

Sin caché ni TTL. Escritura desde web: `web/api/config.py:38-50`, `web/api/control.py:24-31`, `:35-44`, `:47-54`.

### 3. `shared/db/base.py`

- `Base = DeclarativeBase` (`:6-7`).
- `create_engine_from_url(url, *, echo=False)`: `create_async_engine(url, echo=echo, pool_pre_ping=True)` (`:10-18`).
- `create_session_factory(engine)`: `async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)` (`:21-28`).

### 4. `shared/db/models.py` — modelos ORM

11 modelos. Detalle columna-por-columna en `meli/specs/technical-spec.md` §4 y en la sección B de este documento.

**`shared/__init__.py` NO existe.**

---

## Parte B — Esquema de base de datos (vía Alembic)

### 1. Línea de tiempo migraciones

| Orden | Revision | Down rev | Mensaje | Archivo |
|-------|----------|----------|---------|---------|
| 1 | `001` | `None` | initial schema | `001_initial_schema.py` |
| 2 | `002` | `001` | add close_requested to trades | `002_add_trade_close_requested.py` |
| 3 | `003` | `002` | add balance_snapshots | `003_add_balance_snapshots.py` |
| 4 | `004` | `003` | seed decisor_v2 config entries | `004_add_decisor_v2_config.py` |

Cadena `down_revision` coherente.

### 2. Tablas finales

11 tablas: `ohlcv`, `indicators`, `decisions`, `trades`, `positions`, `playbook_versions`, `config`, `config_history`, `daily_stats`, `fee_snapshots`, `balance_snapshots`.

#### Columnas relevantes (truncado a campos clave; detalle completo en `technical-spec.md`)

**`ohlcv`** PK `(time, timeframe)` — índice `idx_ohlcv_tf`.
**`indicators`** PK `time`, `data JSONB` (índice GIN sólo en ORM ⚠️ D-006).
**`decisions`** UUID PK, `agent`, `model`, `input/output/outcome JSONB`, `trade_id` FK use_alter, `executed bool`, `rejected_reason`.
**`trades`** UUID PK, `side`, `quantity_btc`, `entry/exit_price`, `pnl_usdt/pct`, `status`, `stop_loss/take_profit`, `close_reason`, `order_id_open/close`, `fees_usdt`, `close_requested` (mig `002`).
**`positions`** UUID PK, `symbol="BTC/USDT"`, `quantity_btc`, `entry/current_price`, `unrealized_pnl/pct`, `status="open"`.
**`playbook_versions`** UUID PK, `version` UNIQUE, `content TEXT`, `active bool` (índice parcial único sólo en ORM ⚠️).
**`config`** PK `key VARCHAR(60)`, `value TEXT`, `value_type`, `description`, `updated_at`.
**`config_history`** UUID PK, `key`, `old_value`, `new_value`, `changed_by`.
**`daily_stats`** PK `date`, counters Integer, `pnl_*`, `breakdown JSONB`.
**`fee_snapshots`** UUID PK, `maker_fee/taker_fee NUMERIC(8,6)`, `raw JSONB`.
**`balance_snapshots`** UUID PK, `usdt`, `btc`, `source` (mig `003`).

### 3. Convenciones transversales

- `TIMESTAMPTZ` para todos los tiempos (`daily_stats.date` es DATE).
- `NUMERIC(p,s)` para precios/cantidades/PnL.
- `JSONB` para payloads variables.

### 4. Discrepancias ORM vs migraciones (D-006)

| Objeto | ORM | Migración |
|--------|-----|-----------|
| `idx_indicators_data` GIN | ✅ | ❌ |
| `idx_decisions_output` GIN | ✅ | ❌ |
| `idx_decisions_input` GIN | ✅ | ❌ |
| `idx_playbook_active` partial unique | ✅ | ❌ |
| FK `trades.decision_id → decisions.id` | ✅ use_alter | ❌ |
| FK `decisions.trade_id → trades.id` | ✅ use_alter | ❌ (sólo columna) |

**En BD real, esos objetos NO existen** tras aplicar sólo Alembic.

### 5. Migración 004 — seeds nuevos

Inserta condicionalmente en `config`:
- `min_fees_to_tp_ratio`
- `min_confluences_buy`
- `cooldown_after_sell_min`
- `subjective_adj_max`
- `expected_holding_max_min`
- `confluence_weak_factor`

---

## Parte C — Backtesting

### `backtesting/runner.py`

- CLI argparse: `--symbol BTC/USDT`, `--timeframe 1h`, `--days 365`, `--sl-atr-mult 1.0`, `--rr 2.5`, `--diagnose` (`:223-230`).
- Algoritmo: fetch Binance via ccxt → `add_indicators` → `run_baseline` (1 posición a la vez) → print metrics → heurística gate.
- **Sin LLM**, sólo reglas inline en `signal_buy`.
- **pandas puro** (sin pandas-ta, sin vectorbt).

### Métricas

| Métrica | Cálculo |
|---------|---------|
| `win_rate` | `wins / n_trades * 100` |
| `pnl` por trade | `(exit - entry)/entry - 2*fee` |
| `sharpe` | `mean / (std + 1e-9) * sqrt(252)` |
| `max_drawdown_pct` | `((cum / cum.cummax() - 1).min()) * 100` |
| `profit_factor` | `gross_profit / max(gross_loss, 1e-9)` |

### Tests

| Archivo | Cubre |
|---------|-------|
| `backtesting/tests/test_runner.py` | imports, columnas requeridas, flat market, **`test_signal_buy_requires_min_confluences` parece roto** por columnas ausentes en pd.Series (D-019) |

---

## Hallazgos consolidados

1. 🟠 Índices GIN y FK declarados sólo en ORM no en Alembic (D-006).
2. 🟠 `DecisorOutput`/`TradeOutcome` sólo en `shared/schemas.py`; DTOs HTTP son distintos.
3. 🟡 `TradeOut` no expone `order_id_open/close` que sí existen en ORM (D-020).
4. 🟡 `decisions.executed` nullable en migración vs `default=False` en ORM.
5. 🟡 `positions.status` nullable en migración vs default `"open"` en ORM.
6. 🟢 Cadena Alembic coherente.
7. 🟢 `shared/__init__.py` ausente.
8. 🟢 Sin TODO/FIXME en shared/backtesting/alembic.
9. 🟡 `test_signal_buy_requires_min_confluences` con bug oculto (D-019).
