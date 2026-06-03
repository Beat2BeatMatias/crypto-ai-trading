# Modelo de Datos — Crypto AI Trading

> Audiencia: Devs / DBAs.
> Versión: 1.6 — 2026-06-02.
>
> Cambios v1.6: Convención `[LONG]`/`[SHORT]`/`[AMBOS]` en `definition_md` de K–Z (sin columna nueva). Migración **019** — config `min_confluences_short`. Diagrama: registry solo K–Z (I–J catálogo fijo).
>
> Cambios v1.5: Migración **016** — campos direccionales en `trades` y `positions` (`position_side`, `leverage`, `liquidation_price`, `margin_mode`, `funding_paid_usdt`); `balance_snapshots.margin_balance` / `available_margin`.
>
> Cambios v1.4: Clave `outcome_attribution_window_hours` (migration 015). Tabla claves outcome attribution.
>
> Cambios v1.3: Migración 013 — seed `postmortem_fallback_providers`. Documentadas claves post-mortem en `config` (incl. fallback CSV). Índice post-mortem pending incluye reintento de `failed`.

Base de datos: **Postgres 17** (imagen `postgres:17-alpine`).
SQLAlchemy 2.0 declarative + Alembic. Modelos en `shared/db/models.py`, migraciones en `trading-engine/alembic/versions/`.

---

## 1. Diagrama lógico

```
                       ┌────────────────────┐
                       │  playbook_versions │
                       │  (markdown + meta) │
                       └─────────┬──────────┘
                                 │ active (unique partial idx)
                                 │
                                 │   referencia conceptual (no FK)
                                 ▼
   ┌────────────────┐     ┌──────────────────┐         ┌─────────────────┐
   │     ohlcv      │     │     decisions    │ ◄─trade_id─►│     trades     │
   │ PK(time, tf)   │     │ id (uuid)        │         │ id (uuid)       │
   └────────────────┘     │ ts, agent, model │         │ ts_open/close   │
                          │ input  JSONB     │         │ order_id_sl/tp  │
   ┌────────────────┐     │ output JSONB     │         │ close_requested │
   │  indicators    │     │ outcome JSONB    │         └────────┬────────┘
   │ PK(time)       │     │ executed bool    │                  │
   │ data JSONB+GIN │     └────────┬─────────┘                  │
   └────────────────┘              │ 1:1                         │ trade_id
                                   ▼                             ▼
                          ┌──────────────────┐            ┌────────────────┐
                          │ decision_outcomes│            │   positions    │
                          │ PK decision_id   │            │ id (uuid)      │
                          │ MFE/MAE, class.  │            └────────────────┘
                          │ post-mortem cols │
                          └────────┬─────────┘
                                   │
   ┌────────────────────┐          │            ┌─────────────────────┐
   │ confluence_        │◄─────────┘ (source)   │ confluence_registry │
   │ candidates         │──promote─────────────►│ PK code (K–Z)       │
   └────────────────────┘                        └─────────────────────┘

   ┌────────────────┐      ┌─────────────┐
   │ config         │      │ config_hist │
   │ PK key         │      │ id (uuid)   │
   └────────────────┘      └─────────────┘

   ┌─────────────────┐    ┌──────────────────┐    ┌────────────────────┐
   │ fee_snapshots   │    │ balance_snapshots │    │     daily_stats    │
   │                 │    │ usdt/btc + locked │    │ date PK            │
   └─────────────────┘    └──────────────────┘    └────────────────────┘
```

Notas:
- Las FK reales son: `decisions.trade_id → trades.id` (deferred, `use_alter=True`), `trades.decision_id → decisions.id` (también deferred), `positions.trade_id → trades.id`.
- No hay FK cruzada con `playbook_versions`: la asociación a una decisión queda implícita en `decisions.input.playbook` (versión activa al momento del tick).

---

## 2. Tablas

### 2.1 `ohlcv`

Velas históricas y recientes por timeframe.

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `time` | TIMESTAMPTZ | NO | PK compuesto |
| `timeframe` | VARCHAR(4) | NO | PK compuesto. Valores: `1m`, `5m`, `15m`, `1h`, `4h` |
| `open` | NUMERIC(18,8) | YES | |
| `high` | NUMERIC(18,8) | YES | |
| `low` | NUMERIC(18,8) | YES | |
| `close` | NUMERIC(18,8) | YES | |
| `volume` | NUMERIC(24,8) | YES | |

- Índice `idx_ohlcv_tf` btree sobre `(timeframe, time)`.
- Upsert por `ON CONFLICT (time, timeframe) DO UPDATE` (Postgres) o `DO NOTHING` (SQLite tests).
- ~250 velas por timeframe ingestadas por tick del Decisor; en régimen estable el tamaño crece linealmente con el tiempo.

### 2.2 `indicators`

Snapshot de indicadores calculados, uno por tick del Decisor.

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `time` | TIMESTAMPTZ | NO | PK |
| `data` | JSONB | NO | `{ "1m": {…}, "5m": {…}, "15m": {…}, "1h": {…}, "4h": {…} }` |

- Índice GIN `idx_indicators_data` sobre `data`.
- Cada bloque de timeframe contiene: `rsi`, `macd`, `macd_signal`, `macd_hist`, `ema20`, `ema50`, `ema200`, `bb_upper`, `bb_middle`, `bb_lower`, `bb_pct`, `atr`, `volume_avg_20`, `volume_current`, `last_close`.

### 2.3 `decisions`

Log inmutable de cada llamada al LLM (Decisor o Supervisor).

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `id` | UUID | NO | PK, `gen_random_uuid()` |
| `ts` | TIMESTAMPTZ | NO | `now()` |
| `agent` | VARCHAR(20) | NO | `decisor` \| `supervisor` |
| `model` | VARCHAR(50) | NO | provider efectivo |
| `tokens_in` | INT | YES | |
| `tokens_out` | INT | YES | |
| `latency_ms` | INT | YES | |
| `input` | JSONB | NO | Contexto serializado |
| `output` | JSONB | NO | Decisión validada (DecisorOutput) o `{playbook, mode, config_suggestions, config_applied, config_rejected}` para supervisor |
| `outcome` | JSONB | YES | Reservado legacy; la atribución contrafactual vive en `decision_outcomes`. |
| `trade_id` | UUID | YES | FK (deferred) a `trades.id` |
| `executed` | BOOLEAN | YES | `true` cuando se materializó orden |
| `rejected_reason` | VARCHAR(200) | YES | Motivo del Risk Gate o `parse_error: …` |

- Índices: `idx_decisions_ts (ts)`, GIN `idx_decisions_output (output)`, GIN `idx_decisions_input (input)`.

### 2.3.bis `decision_outcomes` (migration 008)

Atribución contrafactual 1:1 con `decisions.id`. Poblada por `outcome_attribution_job`.

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `decision_id` | UUID | NO | PK, FK `decisions.id ON DELETE CASCADE` |
| `horizon_min` | INT | NO | Ventana forward evaluada |
| `matured` | BOOLEAN | NO | `false` mientras la ventana no cerró |
| `forward_return_pct` | NUMERIC(10,5) | YES | Retorno % al horizonte |
| `mfe_pct` | NUMERIC(10,5) | YES | Maximum Favorable Excursion |
| `mae_pct` | NUMERIC(10,5) | YES | Maximum Adverse Excursion |
| `time_to_mfe_min` | INT | YES | Minutos hasta MFE |
| `time_to_mae_min` | INT | YES | Minutos hasta MAE |
| `sl_dist_pct` | NUMERIC(10,5) | YES | Distancia SL declarada (%) |
| `tp_target_pct` | NUMERIC(10,5) | YES | Distancia TP declarada (%) |
| `classification` | VARCHAR(32) | NO | Incluye `GOOD_SHORT`, `BAD_SHORT` además de `GOOD_BUY`, `BAD_BUY`, `GOOD_SELL`, `BAD_SELL`, `GOOD_HOLD`, `MISSED_OPPORTUNITY`, `BLOCKED_GOOD_TRADE`, `CORRECTLY_BLOCKED`, `PENDING`, `UNKNOWN` |
| `computed_at` | TIMESTAMPTZ | NO | `now()` |
| `postmortem_status` | VARCHAR(16) | YES | `completed` \| `failed` \| `null` (migration 011) |
| `lesson_raw` | JSONB | YES | Payload validado del PostMortemAgent |
| `lesson_normalized` | JSONB | YES | Salida del normalizador (`route`, `dedupe_key`, `block_k_line`, …) |
| `postmortem_at` | TIMESTAMPTZ | YES | Timestamp del análisis post-mortem |

- Índices: `idx_decision_outcomes_classification (classification, computed_at)`, `idx_decision_outcomes_pending (matured) WHERE matured = false`, `idx_decision_outcomes_postmortem_pending` (parcial: outcomes elegibles sin post-mortem **o** `failed` con `< 3` intentos en `lesson_raw._meta.attempts`).

### 2.3.ter `confluence_candidates` (migration 012)

Patrones compuestos aprendidos vía post-mortem, pendientes de promoción a registry.

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `id` | UUID | NO | PK |
| `pattern_tag` | VARCHAR(64) | NO | Unique; clave de deduplicación |
| `proposed_code` | VARCHAR(1) | YES | Letra sugerida I–Z |
| `title` | VARCHAR(128) | NO | |
| `definition_md` | TEXT | NO | Definición operacional markdown; convención `[LONG]`/`[SHORT]`/`[AMBOS]` al inicio para K–Z promovidas |
| `verify_spec` | JSONB | NO | Spec testeable contra ctx del Decisor |
| `occurrence_count` | INT | NO | default 1 |
| `first_seen_at` | TIMESTAMPTZ | NO | |
| `last_seen_at` | TIMESTAMPTZ | NO | |
| `source_decision_ids` | JSONB | NO | Lista de UUIDs (no ARRAY — compat SQLite tests) |
| `status` | VARCHAR(16) | NO | `open` \| `promoted` \| `rejected` |
| `promoted_at` | TIMESTAMPTZ | YES | |
| `reject_reason` | TEXT | YES | |

- Índice: `idx_confluence_candidates_status (status, occurrence_count DESC)`.

### 2.3.iv `confluence_registry` (migration 012)

Catálogo dinámico de letras **K–Z** promovidas (I–J son catálogo fijo del Decisor, no se almacenan aquí).

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `code` | VARCHAR(1) | NO | PK; letra K–Z |
| `slug` | VARCHAR(64) | NO | Unique |
| `title` | VARCHAR(128) | NO | |
| `definition_md` | TEXT | NO | |
| `verify_spec` | JSONB | NO | |
| `active` | BOOLEAN | NO | default true |
| `promoted_from` | UUID | YES | FK `confluence_candidates.id` |
| `created_at` | TIMESTAMPTZ | NO | |
| `deactivated_at` | TIMESTAMPTZ | YES | Reserva letra 30 días |

### 2.4 `trades`

Operación concreta (BUY → SELL).

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `id` | UUID | NO | PK |
| `decision_id` | UUID | YES | FK (deferred) `decisions.id` |
| `ts_open` | TIMESTAMPTZ | NO | |
| `ts_close` | TIMESTAMPTZ | YES | |
| `side` | VARCHAR(4) | NO | Lado de la **orden** Binance al abrir (`BUY` long, `SELL` short en futures) |
| `position_side` | VARCHAR(5) | NO | `LONG` \| `SHORT` — dirección económica (default `LONG`, migration 016) |
| `leverage` | NUMERIC(5,2) | YES | default `1` |
| `liquidation_price` | NUMERIC(18,8) | YES | Futures; null en Spot |
| `margin_mode` | VARCHAR(10) | YES | default `isolated` |
| `funding_paid_usdt` | NUMERIC(18,4) | YES | Acumulado funding al cerrar (futures) |
| `quantity_btc` | NUMERIC(18,8) | NO | |
| `entry_price` | NUMERIC(18,8) | NO | |
| `exit_price` | NUMERIC(18,8) | YES | |
| `pnl_usdt` | NUMERIC(18,4) | YES | Neto de fees |
| `pnl_pct` | NUMERIC(8,4) | YES | % según dirección (long/short) |
| `status` | VARCHAR(12) | NO | `open` \| `closed` \| `cancelled` |
| `stop_loss` | NUMERIC(18,8) | YES | |
| `take_profit` | NUMERIC(18,8) | YES | |
| `close_reason` | VARCHAR(30) | YES | `decisor_sell` \| `manual_close` \| `sl_triggered` \| `tp_triggered` \| `bracket_fill` |
| `order_id_open` | VARCHAR(50) | YES | id Binance |
| `order_id_close` | VARCHAR(50) | YES | id Binance |
| `order_id_sl` | VARCHAR(50) | YES | id orden SL del bracket OCO (migration 007) |
| `order_id_tp` | VARCHAR(50) | YES | id orden TP del bracket OCO (migration 007) |
| `fees_usdt` | NUMERIC(18,4) | YES | Sum apertura + cierre |
| `close_requested` | BOOLEAN | NO (default false) | Flag UI → engine (migration 002) |

- Índices: `idx_trades_status (status)`, `idx_trades_ts (ts_open)`.

### 2.5 `positions`

Vista en tiempo real de las posiciones abiertas (1 fila por trade abierto).

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `id` | UUID | NO | PK |
| `trade_id` | UUID | YES | FK `trades.id` |
| `symbol` | VARCHAR(20) | NO | `BTC/USDT` o `BTC/USDT:USDT` |
| `position_side` | VARCHAR(5) | YES | default `LONG` (016) |
| `leverage` | NUMERIC(5,2) | YES | default `1` |
| `liquidation_price` | NUMERIC(18,8) | YES | |
| `quantity_btc` | NUMERIC(18,8) | NO | |
| `entry_price` | NUMERIC(18,8) | NO | |
| `current_price` | NUMERIC(18,8) | YES | Actualizado cada 30 s |
| `unrealized_pnl` | NUMERIC(18,4) | YES | |
| `unrealized_pct` | NUMERIC(8,4) | YES | |
| `status` | VARCHAR(10) | YES | default `open` |
| `opened_at` | TIMESTAMPTZ | NO | |
| `updated_at` | TIMESTAMPTZ | YES | |

> Implementación actual: `Executor.execute_open` inserta `Position` con `status=open`; `execute_close` la marca `closed`. PnL no realizado es direccional (`position_manager.refresh_unrealized`).

### 2.6 `playbook_versions`

Versionado de playbook escrito por el Supervisor.

| Columna | Tipo | Nullable | Notas |
|---------|------|----------|-------|
| `id` | UUID | NO | PK |
| `version` | INT | NO | Único (UQ) |
| `ts_generated` | TIMESTAMPTZ | NO | `now()` |
| `content` | TEXT | NO | Markdown |
| `model` | VARCHAR(50) | YES | Provider efectivo |
| `trades_analyzed` | INT | YES | Métrica que sirvió de base |
| `win_rate` | NUMERIC(5,2) | YES | |
| `pnl_summary` | JSONB | YES | `{pnl_usdt, avg_win, ...}` |
| `active` | BOOLEAN | YES | default false |

- Índice único parcial `idx_playbook_active` sobre `(active) WHERE active = true`: garantiza que sólo haya **una** versión activa a la vez.

### 2.7 `config` + `config_history`

#### `config`

| Columna | Tipo | Notas |
|---------|------|-------|
| `key` | VARCHAR(60) | PK; debe estar definida en el enum `ConfigKey`. |
| `value` | TEXT | Valor en string; el código castea con `value_type`. |
| `value_type` | VARCHAR(20) | `int` \| `float` \| `bool` \| `string` \| `json`. |
| `description` | TEXT | Texto descriptivo para la UI. |
| `updated_at` | TIMESTAMPTZ | `now()`. |

#### `config_history`

| Columna | Tipo | Notas |
|---------|------|-------|
| `id` | UUID | PK. |
| `ts` | TIMESTAMPTZ | `now()`. |
| `key` | VARCHAR(60) | |
| `old_value` | TEXT | |
| `new_value` | TEXT | |
| `changed_by` | VARCHAR(60) | `system` \| `user` \| `supervisor`. |

Toda actualización vía `ConfigStore.set` escribe una fila aquí.

**Claves outcome attribution** (migration 015 agrega ventana compartida):

| Key | Tipo | Default | Notas |
|-----|------|---------|-------|
| `outcome_attribution_interval_min` | int | `60` | Intervalo del job (attribution + post-mortem encadenado). |
| `outcome_attribution_horizon_min` | int | `240` | Horizonte forward MFE/MAE (min). |
| `outcome_attribution_window_hours` | int | `25` | Ventana compartida attribution + post-mortem. Rango 12–72. |
| `outcome_coverage_threshold_pct` | int | `30` | Máx. % velas 1m faltantes antes de `UNKNOWN`. |

**Claves post-mortem** (migration 011 + defaults en código; 013 agrega fallback):

| Key | Tipo | Default | Notas |
|-----|------|---------|-------|
| `postmortem_enabled` | bool | `true` | Kill switch del job. |
| `postmortem_max_per_tick` | int | `5` | Máx. **decisiones** analizadas por tick (1 LLM call c/u). |
| `postmortem_provider` | string | `gemini-2.5-flash` | Primary LLM. |
| `postmortem_fallback_providers` | string (CSV) | ver migration 013 | Cascada ordenada; editable desde `/config`. |
| `postmortem_interval_min` | int | `60` | Reservado; hoy el job corre encadenado al tick de attribution. |
| `block_k_max_lines` | int | `8` | Límite de líneas en Bloque K del prompt Decisor. |
| `block_k_window_hours` | int | `72` | Ventana temporal de lecciones `remap`/`guidance`. |

### 2.8 `daily_stats`

Snapshot agregado por fecha (UTC).

| Columna | Tipo |
|---------|------|
| `date` | DATE PK |
| `decisions_total` | INT |
| `trades_executed` | INT |
| `wins` | INT |
| `losses` | INT |
| `pnl_usdt` | NUMERIC(18,4) |
| `pnl_pct` | NUMERIC(8,4) |
| `max_drawdown` | NUMERIC(8,4) |
| `breakdown` | JSONB |

> Actualmente el cómputo se hace en caliente desde `trades` y `decisions` (ver `web/api/stats.py`). La tabla está preparada para un job batch futuro.

### 2.9 `fee_snapshots`

| Columna | Tipo |
|---------|------|
| `id` | UUID PK |
| `ts` | TIMESTAMPTZ |
| `symbol` | VARCHAR(20) (`BTC/USDT`) |
| `maker_fee` | NUMERIC(8,6) |
| `taker_fee` | NUMERIC(8,6) |
| `raw` | JSONB (respuesta completa de Binance) |

- Índice `idx_fee_snapshots_ts (ts)`.

### 2.10 `balance_snapshots` (migration 003)

| Columna | Tipo |
|---------|------|
| `id` | UUID PK |
| `ts` | TIMESTAMPTZ |
| `usdt` | NUMERIC(18,4) |
| `btc` | NUMERIC(18,8) |
| `usdt_locked` | NUMERIC(18,4) | default 0 (migration 010) |
| `btc_locked` | NUMERIC(18,8) | default 0 (migration 010) |
| `margin_balance` | NUMERIC(18,4) | NULL en Spot; total margen en futures (016) |
| `available_margin` | NUMERIC(18,4) | NULL en Spot; margen disponible para sizing (016) |
| `source` | VARCHAR(20) (default `binance`) |

- Índice `idx_balance_snapshots_ts (ts)`.

---

## 3. Migraciones Alembic

Carpeta: `trading-engine/alembic/versions/`.

| Rev | Archivo | Cambios |
|-----|---------|---------|
| 001 | `001_initial_schema.py` | Crea todas las tablas iniciales con índices base. |
| 002 | `002_add_trade_close_requested.py` | `ALTER TABLE trades ADD close_requested BOOLEAN NOT NULL DEFAULT false`. |
| 003 | `003_add_balance_snapshots.py` | Crea `balance_snapshots` + índice por `ts`. |
| 004 | `004_add_decisor_v2_config.py` | Idempotente: inserta 6 filas en `config` con `INSERT … SELECT NOT EXISTS`. |
| 005 | `005_align_conf_base_defaults.py` | Alineación defaults `conf_base_*`. |
| 006 | `006_add_gin_indexes_and_missing_fk.py` | Índices GIN + índice parcial playbook + FK `trades.decision_id`. |
| 007 | `007_add_bracket_order_ids.py` | `trades.order_id_sl`, `trades.order_id_tp`. |
| 008 | `008_add_decision_outcomes.py` | Tabla `decision_outcomes` + índices. |
| 009 | `009_expand_close_reason_length.py` | `close_reason` VARCHAR(20) → VARCHAR(30). |
| 010 | `010_add_balance_locked_fields.py` | `balance_snapshots.usdt_locked`, `btc_locked`. |
| 011 | `011_add_decision_outcome_postmortem.py` | Columnas post-mortem en `decision_outcomes` + índice parcial pending. |
| 012 | `012_add_confluence_registry.py` | Tablas `confluence_candidates`, `confluence_registry`. |
| 013 | `013_add_postmortem_fallback_providers.py` | Seed idempotente `postmortem_fallback_providers` en `config`. |
| 015 | `015_add_outcome_attribution_window_hours.py` | Seed idempotente `outcome_attribution_window_hours` en `config`. |
| 016 | `016_add_futures_fields.py` | `trades`/`positions` direccionales; `balance_snapshots` margen. |
| 019 | `019_add_min_confluences_short.py` | Seed idempotente `min_confluences_short` en `config` (guía mínima SHORT en futuros). |

**Claves futures** (seed en `config_store`):

| Key | Tipo | Default | Notas |
|-----|------|---------|-------|
| `trading_product` | string | `spot` | `spot` \| `futures` — operator-only |
| `max_leverage` | int | `1` | operator-only |
| `margin_mode` | string | `isolated` | operator-only |
| `funding_rate_max_pct` | float | `0.05` | R15 |
| `liquidation_buffer_atr` | float | `2.0` | R13 — operator-only |

Comandos:

```bash
alembic upgrade head        # aplicar todo
alembic current             # ver revisión actual
alembic downgrade -1        # rollback granular
```

> Los índices GIN y el índice parcial `idx_playbook_active` están materializados en Postgres productivo vía migración **006**.

---

## 4. Patrones de acceso

### 4.1 Hot paths (engine)

| Acceso | Frecuencia | Operación típica |
|--------|-----------|------------------|
| Lectura `Indicators` última fila | ~12×/h (Decisor) | `ORDER BY time DESC LIMIT 1`. |
| Lectura `Indicators` 7d para ATR avg | ~12×/h | `ORDER BY time DESC LIMIT n` (n = velas/día × 7 según ATR tf). |
| Insert/upsert `Ohlcv` | ~60 filas/tick × 5 tf | UPSERT por `(time, timeframe)`. |
| Insert `Indicators` | 1/tick | UPSERT por `time`. |
| Insert `Decision` | 1/tick + 1/día | Sin update salvo `rejected_reason`. |
| Read `Decision` últimos 3 | 1/tick | `WHERE agent='decisor' ORDER BY ts DESC LIMIT 3`. |
| Insert/UPSERT `DecisionOutcome` | 1/h (configurable) | UPSERT por `decision_id`. |
| Update post-mortem columns | encadenado a attribution | Máx. `postmortem_max_per_tick` decisiones/tick; reintento de `failed` hasta 3 intentos. |
| UPSERT `ConfluenceCandidate` | encadenado a post-mortem | Por `pattern_tag`. |
| Read `ConfluenceRegistry WHERE active` | 1/tick Decisor | Pequeño (≤5 filas típico). |
| Read `Position WHERE status='open'` | 1/tick + 2/min (WS) | Pequeño. |
| Insert `Position` | en cada BUY | |
| Update `Position` | 1×/30s (refresh_unrealized) | Pequeño número de filas. |
| `select Trade WHERE status='open'` | 1×/30s (order tracker) | |

### 4.2 Hot paths (web)

| Endpoint | Query principal |
|----------|----------------|
| `GET /api/trades` | `SELECT ... LIMIT 100 ORDER BY ts_open DESC`. |
| `GET /api/decisions` | Idem sobre `decisions`. |
| `GET /api/stats/daily` | 3 selects (trades del día, decisions del día, positions abiertas). |
| `GET /api/playbook/active` | `WHERE active=true` con índice único parcial. |
| `GET /api/balance` | Last `balance_snapshot` + suma de qty de positions abiertas. |
| WS `/ws` | Polling 2 s a `decisions` y `positions`. |

### 4.3 Recomendaciones de retención

| Tabla | Política sugerida |
|-------|------------------|
| `ohlcv` | Conservar últimos 90 días por timeframe; archivar resto. |
| `indicators` | Conservar 30 días detallados; resumir a 1×/h después. |
| `decisions` | Mantener completo en BD operativa al menos 90 días; archivar a cold storage para análisis histórico. |
| `trades` | Histórico completo (auditoría). |
| `positions` | Histórico completo (relacionado a trades). |
| `playbook_versions` | Completo. |
| `config_history` | Completo (auditoría inmutable). |
| `fee_snapshots` / `balance_snapshots` | 90 días con downsampling diario opcional. |

Actualmente **no hay job de purga** implementado.

---

## 5. Convenciones SQLAlchemy / DDL

- Todas las columnas de tiempo: `DateTime(timezone=True)` ⇒ `TIMESTAMPTZ`.
- UUIDs: `UUID(as_uuid=True)` con `server_default=text("gen_random_uuid()")`. Requiere extensión `pgcrypto` (incluida por default en Postgres ≥13).
- Booleanos críticos con server_default explícito: `trades.close_requested` (`server_default="false"`).
- JSONB: usar siempre `JSONB`, no `JSON`, para soportar índices GIN.

---

## 6. Test database

- Tests unitarios usan `sqlite+aiosqlite://:memory:`.
- `web/main.py` ejecuta `Base.metadata.create_all` solo si la URL contiene `sqlite`.
- Los collectors detectan dialect (`_detect_dialect`) y usan `sqlite.insert` con `ON CONFLICT DO NOTHING|UPDATE` cuando corresponde.
