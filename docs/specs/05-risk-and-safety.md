# Riesgo y Seguridad — Crypto AI Trading

> Audiencia: Risk / Compliance / SRE.
> Versión: 1.2 — 2026-05-17.
>
> Cambios v1.1: se agregó §1.bis (Autonomía del LLM: garantías del modelo de defensa) con referencias cruzadas a `01-functional-spec.md §F2.bis` y `02-technical-spec.md §2.6.bis`. Se ampliaron en §12 dos riesgos conocidos (override con umbral plano vs. `conf_threshold_*`, y validación de confluencias inválidas como warning solamente).
> Cambios v1.2: R4 y R5 actualizados con thresholds reales operativos (SL 0.3–1.5×ATR, R:R 1.3). §12 ítem `daily_pnl_pct/total_drawdown_pct` marcado como ✅ RESUELTO. Nota al pie de §2 actualizada.

Este documento centraliza las reglas absolutas, controles deterministas, circuit breakers y gates de pasaje entre paper trading y LIVE. Su único propósito es asegurar que **ninguna decisión de un LLM pueda exceder los límites de riesgo configurados**.

---

## 1. Modelo de defensa en profundidad

```
┌─────────────────────────────────────────────────────────┐
│ Capa 1: LLM (system prompt + playbook + reglas R1-R10) │
│  - Instrucciones explícitas para respetar parámetros    │
│  - Catálogo cerrado de confluencias                      │
│  - Cálculo determinístico de confidence (7 pasos)        │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼  JSON validado por Pydantic
┌─────────────────────────────────────────────────────────┐
│ Capa 2: Override determinístico (Decisor)               │
│  - TRENDING_DOWN o confidence<0.60 → HOLD forzado       │
│  - Sizing por escalones (≥0.70=max | 0.60-0.69=0.03)    │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Capa 3: Risk Gate (reglas R1-R10)                       │
│  - Bloqueo absoluto antes de emitir orden al exchange   │
│  - Persiste rejected_reason en la decisión              │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Capa 4: Circuit Breaker (fallas en cadena)              │
│  - 5 fallas LLM/exchange consecutivas → engine_paused   │
│  - daily_stop / max_drawdown → flag pause               │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Capa 5: Operador humano                                 │
│  - Kill Switch (1 click)                                │
│  - Rollback de playbook                                 │
│  - Cambio manual de modo (frase literal)                │
└─────────────────────────────────────────────────────────┘
```

---

## 1.bis Autonomía del LLM: garantías del modelo de defensa

Esta sección formaliza qué garantías de seguridad ofrece el modelo de defensa frente a la autonomía del Decisor. La perspectiva funcional está en `01-functional-spec.md §F2.bis`; la técnica en `02-technical-spec.md §2.6.bis`.

### 1.bis.1 Principio rector

> **El LLM Decisor propone una decisión estructurada; el sistema valida, reescribe si es necesario y ejecuta sólo si pasa el Risk Gate.** Ninguna salida del LLM puede provocar una acción que viole R1–R10 ni los guardrails del Supervisor (`_SAFE_BOUNDS`).

### 1.bis.2 Garantías invariantes (contrato de Risk)

Para cualquier ciclo del Decisor, el sistema garantiza que toda decisión con `executed=true` cumple simultáneamente:

| ID | Garantía | Capa que la enforce |
|----|----------|---------------------|
| GA-1 | `output.action != "BUY"` ó `output.regime != "TRENDING_DOWN"`. | Capa 3 (override) — fuerza HOLD. |
| GA-2 | `output.action != "BUY"` ó `output.confidence >= 0.60`. | Capa 3 (override) — fuerza HOLD. |
| GA-3 | `output.action != "BUY"` ó `output.position_size_pct <= max_position_pct + 1e-9`. | Capa 3 (cap) + Capa 4 R1. |
| GA-4 | `output.action != "BUY"` ó SL ∈ `[sl_atr_multiplier × ATR, sl_atr_max_multiplier × ATR]`. | Capa 4 R4. |
| GA-5 | `output.action != "BUY"` ó R:R ≥ `min_rr_ratio`. | Capa 4 R5. |
| GA-6 | `output.action != "SELL"` ó hay posición LONG abierta. | Capa 4 R6. |
| GA-7 | `output.action != "BUY"` con `kill_switch=true`. | Capa 4 (kill switch check). |
| GA-8 | Si `daily_pnl_pct <= daily_stop_pct` → cero BUYs ejecutados ese día. | Capa 4 R9 (**pendiente de instrumentar con datos reales** — ver §12). |

### 1.bis.3 Lo que el LLM no puede sobrescribir

| Parámetro | Quien lo controla | Mecanismo de protección |
|-----------|-------------------|------------------------|
| `daily_stop_pct` | Operador / config manual | Excluido de `_SAFE_BOUNDS`. El Supervisor solo puede **sugerirlo** en el reporte. |
| `max_drawdown_pct` | Operador / config manual | Excluido de `_SAFE_BOUNDS`. |
| `position_size_pct` (ejecutado) | Sistema | Override determinístico reescribe el output del LLM. |
| Bracket SL/TP (post-orden) | Sistema (Binance) | Una vez emitido, el bracket se ejecuta server-side; el LLM no puede modificarlo en ciclos siguientes. |
| Catálogo de confluencias A–H | Sistema (prompt + Supervisor system prompt) | El playbook no puede introducir códigos nuevos; el Supervisor tiene regla explícita. |

### 1.bis.4 Trazabilidad de la autonomía

Toda decisión queda en `decisions` con:

- `agent="decisor"`, `model`, `tokens_in/out`, `latency_ms`.
- `input`: snapshot completo del contexto inyectado al LLM (incluye playbook activo y últimas 3 decisiones).
- `output`: JSON resultante (post-override).
- `executed` + `rejected_reason` (si correspondiese).

Cualquier divergencia entre lo que el LLM emitió y lo que el sistema ejecutó queda reflejada en los logs `decisor.override_below_threshold` y `decisor.override_size`. El operador puede auditar la frecuencia con la que el LLM intenta superar los umbrales revisando `decisions.output.reasoning` y los warnings del engine.

---

## 2. Reglas absolutas R1–R10 (Risk Gate)

Verificadas en `trading-engine/risk/risk_gate.py:RiskGate.validate`. `HOLD` siempre pasa automáticamente.

| ID | Regla | Comportamiento si falla |
|----|-------|------------------------|
| **R1** | `position_size_pct ≤ max_position_pct + 1e-9` | Reject `position_size_pct X > max Y`. |
| **R2** | `action=BUY` requiere `stop_loss` no nulo y `stop_loss < current_price` | Reject `BUY requires stop_loss` / `stop_loss must be < current_price`. |
| **R3** | `action=BUY` requiere `take_profit` no nulo y `take_profit > current_price` | Reject `BUY requires take_profit` / `take_profit must be > current_price`. |
| **R4** | Distancia SL entre `sl_atr_multiplier × ATR` y `sl_atr_max_multiplier × ATR` (timeframe `atr_timeframe`). Defaults operativos: **0.3×ATR (mín)** y **1.5×ATR (máx)**; ambos configurables vía `ConfigKey`. | Reject `SL distance X < ...` o `SL distance X > ...`. |
| **R5** | `R:R = (take_profit - current_price) / (current_price - stop_loss) > min_rr_ratio`. Default operativo: **1.3** (configurable vía `ConfigKey.MIN_RR_RATIO`). El design doc original indicaba 1.5; el valor actual de 1.3 reduce rechazos excesivos en mercados con spreads amplios. | Reject `R:R ratio X ≤ min`. |
| **R6** | `action=SELL` requiere `btc_held > 0` y al menos 1 posición abierta | Reject `SELL requested but no open position to close`. |
| **R7** | Nunca shortear: la única semántica de SELL es cerrar una posición LONG previamente abierta | Asegurado estructuralmente: `execute_buy` es la única ruta de apertura. |
| **R8** | `open_positions < max_simultaneous_trades` | Reject `max_simultaneous_trades reached: N`. |
| **R9** | `daily_pnl_pct > daily_stop_pct` | Reject `daily P&L breach: X`. |
| **R10** | Movimiento al TP cubre fees round-trip: `(TP - price)/price × 100 ≥ min_fees_to_tp_ratio × roundtrip_fee_pct` | Reject `R10: TP move (X%) < N×fees (Y%)`. **No aplica** si `roundtrip_fee_pct == 0` (testnet). |

Además del bloque R1–R10, el Risk Gate valida:

- **Drawdown total**: si `total_drawdown_pct ≤ max_drawdown_pct` ⇒ Reject `max_drawdown breached`.
- **Kill switch**: si `kill_switch=true` y la decisión no es SELL para cerrar ⇒ Reject `kill_switch active — only SELL-to-close allowed`.

> ✅ `main.py` calcula `daily_pnl_pct` y `total_drawdown_pct` reales en cada tick mediante `_compute_risk_metrics()` y los pasa al Risk Gate y al `CircuitBreaker.evaluate()` (resuelto en 2026-05-17).

---

## 3. Override determinístico (Capa 2)

`trading-engine/agents/decisor.py:_apply_deterministic_overrides`. Se ejecuta **después** de la validación Pydantic del output del LLM y **antes** de persistir.

| Condición | Acción |
|-----------|--------|
| `action != BUY` | Sin cambios. |
| `regime == TRENDING_DOWN` ∨ `confidence < 0.60` | Reescribe a `action=HOLD`, `stop_loss=null`, `take_profit=null`, `position_size_pct=0.0`, `reasoning="[override] confidence X < 0.60 …"`. |
| `confidence ≥ 0.70` | `position_size_pct = min(max_position_pct, 0.25)` clipeado a piso 0.01. |
| `confidence 0.60–0.69` | `position_size_pct = min(0.03, max_position_pct)` clipeado a piso 0.01. |

> Este código es la **última línea de defensa antes del Risk Gate**. Aunque el system prompt instruye lo mismo, se reaplica para evitar divergencias.

---

## 4. Circuit Breaker

`trading-engine/risk/circuit_breaker.py`. Estado global en memoria del engine.

### 4.1 Pausa automática del engine

- 5 fallas **consecutivas** de LLM → `engine_paused = True`.
- 5 fallas **consecutivas** de exchange (orden o balance) → `engine_paused = True`.

Cuando `engine_paused = True`, cada tick del Decisor sale tempranamente con `log "engine.paused"`. La pausa se libera **manualmente** reiniciando el proceso (no hay reset automático aún).

### 4.2 Daily stop y max drawdown

`CircuitBreaker.evaluate` puede llamarse en cualquier momento para detectar:

- `daily_pnl_pct ≤ daily_stop_pct` (default −3%) ⇒ log `circuit.daily_stop_triggered`.
- `total_drawdown_pct ≤ max_drawdown_pct` (default −10%) ⇒ log `circuit.kill_switch_triggered`.

> Estos triggers también se reflejan en el Risk Gate (rechazo de BUYs). El reset del daily stop ocurre naturalmente al cambiar de fecha (cálculo de `daily_pnl_pct` desde 00:00 UTC).

### 4.3 Actualización de umbrales

`update_thresholds(daily_stop_pct, max_drawdown_pct)` se invoca en cada tick para leer la última config (caso de cambios en caliente desde la UI).

---

## 5. Kill Switch

Disparado vía `POST /api/kill-switch {enabled:true}`.

- Setea `config.kill_switch = "true"` + fila en `config_history`.
- El Risk Gate, al ver el flag, rechaza cualquier acción **excepto** SELL para cerrar (`btc_held > 0`).
- El operador debe desactivarlo explícitamente (`enabled:false`).

> El kill switch **no** cancela órdenes ya enviadas a Binance ni cierra posiciones automáticamente. Para eso debe emitirse un SELL manual via el flujo de cierre de trade.

---

## 6. Cambio de modo PAPER ↔ LIVE

Endpoint: `POST /api/mode {mode, confirmation}`.

- `mode = "PAPER_TRADING"` → cambio libre.
- `mode = "LIVE"` → requiere `confirmation == "CONFIRMO TRADING REAL"` (string literal). Cualquier otro valor responde `400`.

> **Importante**: el modo "efectivo" del exchange está determinado por `BINANCE_TESTNET` (env var). Cambiar `config.mode` a LIVE **sin** ajustar `BINANCE_TESTNET=false` mantiene al engine apuntando a testnet. La operativa real requiere ambos cambios.

---

## 7. Guardrails del Supervisor

`trading-engine/agents/supervisor.py`.

### 7.1 `_SAFE_BOUNDS` — claves elegibles para auto-apply

```python
{
  "sl_atr_multiplier":          (0.1, 0.8),
  "min_rr_ratio":               (1.0, 3.0),
  "decisor_interval_min":       (5, 60),
  "max_position_pct":           (0.01, 0.20),
  "conf_threshold_trending_up": (0.40, 0.85),
  "conf_threshold_range":       (0.50, 0.90),
  "conf_threshold_high_vol":    (0.60, 0.95),
}
_VALID_ATR_TIMEFRAMES = {"5m", "15m", "1h"}
```

### 7.2 Claves **excluidas** del auto-apply

- `daily_stop_pct`
- `max_drawdown_pct`

Estas son **explícitamente** marcadas como demasiado críticas. El Supervisor puede sugerirlas en el reporte, pero el código rechaza la aplicación automática y deja constancia en `output.config_rejected`.

### 7.3 Diagnostic mode

Si `closed_trades < min_trades` (default 5), el Supervisor entra en modo `diagnostic`: inyecta un header al prompt pidiendo análisis de por qué no hubo trades en lugar de optimizar prematuramente. El playbook resultante se guarda igualmente como nueva versión.

### 7.4 Rollback

Cualquier versión histórica puede activarse desde la UI o `POST /api/playbook/{version}/activate`. El índice único parcial sobre `(active) WHERE active=true` garantiza una sola activa a la vez.

### 7.5 Auto-rollback automático (fuera de scope v1)

El design doc original describía un auto-rollback automático: "si 7 días post-update muestran >2× drawdown vs. prior 7 días, revertir a versión previa + alert". Esta funcionalidad **no está implementada en v1** por las siguientes razones:

- Requiere una ventana mínima de 7 días de datos para ser estadísticamente significativa.
- El cálculo de "2× drawdown vs. período previo" es sensible a outliers cuando los volúmenes de trades son bajos (< 10 trades por semana en la fase inicial de paper trading).
- El rollback automático puede interactuar negativamente con optimizaciones manuales del operador.

**Para v1**, el operador realiza el rollback manualmente desde `/playbook` cuando detecta degradación en las métricas semanales (ver checklist §13). El auto-rollback se evaluará para v2 cuando haya suficiente histórico (≥ 4 semanas de paper trading con ≥ 5 trades/semana).

---

## 8. Cascade de LLM providers

`trading-engine/agents/llm_client.py:LLMClient.call`.

- Provider primario (config `decisor_provider` / `supervisor_provider`).
- Lista de fallbacks (config `fallback_providers` / `supervisor_fallback_providers`).
- Si todos fallan ⇒ excepción → en el Decisor se traduce a `_hold_decision("llm_error")` y `cb.record_llm_failure()`.
- Si `_is_rate_limit(e)` (mensajes que contienen `429` / `ResourceExhausted` / `rate_limit`) ⇒ **no** reintenta el mismo provider; salta al siguiente sin gastar el budget de retries.
- Retries normales: 3 con backoff `0.5 × 2^attempt`.

---

## 9. Resiliencia ante caídas

| Falla | Comportamiento |
|-------|---------------|
| Postgres caído | Engine crashea; Docker restart `unless-stopped` lo reintenta. |
| Binance REST caído (balance) | `usdt=0.0`, `btc` derivado de `positions WHERE status='open'`. No se pueden abrir BUYs (no hay USDT). |
| Binance REST caído (fees) | Usa último `fee_snapshot` o `(0.001, 0.001)` por defecto. |
| Binance REST caído (orden) | `cb.record_exchange_failure()`; 5 consecutivos ⇒ pausa engine. |
| Binance WS (order book) caído | `OrderBookCollector` reintenta cada 2 s; `snapshot()` devuelve `None`; el Decisor opera con valores neutros (`spread=0`, `imbalance=1.0`, etc.). |
| LLM provider caído / 429 | Cascade. 5 fallas seguidas (todos los providers) ⇒ pausa engine. |
| Web service caído | Engine sigue operando autónomamente; UI no muestra datos. |
| Frontend caído | Sin impacto operativo. |

---

## 10. Gates de pasaje a LIVE

Resumen del roadmap (detalle en README.md):

| Paso | Gate cuantitativo |
|------|------------------|
| 1 Testnet keys configuradas | Sin errores `API-key format invalid` en logs. |
| 2 LLM keys configuradas | 48 h continuas con `decision.persisted` y < 1% errores. |
| 3 Backtesting 90 d (`backtesting/runner.py`) | Sharpe > 1.0, DD < 10%, WR > 48%, PF > 1.3. |
| 4 Paper trading 4 semanas consecutivas | Sharpe > 1.0, DD < 5%, WR > 52%, PF > 1.5, errores LLM < 1%, ninguna semana con DD > 3%. Si falla 1 semana, contador a 0. |
| 5 API keys mainnet | Permisos mínimos: lectura + spot trading. **Sin** margin, **sin** retiros. IP restringida. |
| 6 Switch LIVE | `.env`: `BINANCE_TESTNET=false`. UI: `mode=LIVE` con confirmación literal. Capital inicial $200–500 USDT. |

---

## 11. Auditoría

- **`decisions`** es append-only. Cada llamada LLM queda con tokens, latencia, input, output, y `rejected_reason` si correspondiese.
- **`config_history`** es append-only y registra cada cambio con `changed_by` (`system` / `user` / `supervisor`).
- **`playbook_versions`** mantiene todas las versiones; nada se borra.
- **`trades`** mantiene histórico completo con razón de cierre.

Para auditoría externa basta con un dump de estas tablas más `balance_snapshots` y `fee_snapshots`.

---

## 12. Riesgos conocidos / TODOs de seguridad

| Tema | Estado | Acción sugerida |
|------|--------|-----------------|
| `daily_pnl_pct` y `total_drawdown_pct` pasados como 0.0 al Risk Gate | ✅ RESUELTO | `_compute_risk_metrics()` calcula valores reales en cada tick desde `trades` (PnL diario desde 00:00 UTC) y `balance_snapshots` (high-water mark para drawdown). El CB y el Risk Gate reciben datos válidos desde 2026-05-17. |
| Override con umbral plano `0.60` vs. `conf_threshold_trending_up/range/high_vol` (0.60 / 0.70 / 0.80) | ✅ RESUELTO | `_apply_deterministic_overrides` ahora usa el umbral por régimen del `calibration` dict (TRENDING_UP: 0.60, RANGE: 0.70, HIGH_VOL: 0.80, ajustables por Supervisor). Piso absoluto de seguridad: 0.40. TRENDING_DOWN siempre bloqueado. |
| Confluencias inválidas (fuera del catálogo A–H) solo loguean warning | ✅ RESUELTO | `_filter_confluence_codes()` en `decisor.py` filtra y descarta códigos inválidos antes de los overrides. El audit log solo persiste códigos válidos; si el LLM infló con códigos inventados, quedan solo los legítimos. |
| Web API sin autenticación | Pendiente | Para producción remota, agregar auth (token / OIDC) y/o restringir el dashboard a red privada (VPN/SSH tunnel). |
| Engine sin reset automático de pausa | ✅ RESUELTO | `CircuitBreaker.maybe_auto_reset()`: pausa operativa (LLM/exchange) se auto-resetea tras `operational_cooldown_sec` (default 10 min) sin nuevas fallas. Pausa financiera (daily_stop/drawdown) requiere intervención humana explícita. |
| Sin notificaciones (telegram/email) | ✅ RESUELTO | `notifications/telegram.py`: notifica via Telegram Bot API en kill switch, daily stop, drawdown, engine paused/resumed, racha LLM/exchange. Configurable con `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`. Sin configurar → no-op silencioso. |
| Reintento de órdenes parcialmente filleadas | Pendiente | Hoy `RuntimeError` si `filled=0` o `avg_price=0`; conviene política explícita. |
| Almacenamiento de API keys | En `.env` local | Para producción, mover a un secret manager (Vault / AWS Secrets Manager / SOPS). |
| Backups de Postgres | Manual (`pg_dump`) | Job programado + retención + restauración probada periódicamente. |

---

## 13. Checklist de revisión semanal (operador)

```
[ ] Sharpe ratio semanal > 1.0
[ ] DD semanal < 3%
[ ] Win rate semanal > 52%
[ ] Profit factor semanal > 1.5
[ ] 0 errores LLM no recuperados
[ ] 0 horas con engine_paused no atendidas
[ ] Backup de DB del fin de semana validado (`pg_restore --list`)
[ ] Sin pendientes en config/suggestions sin revisar
[ ] Playbook activo es el esperado (no rollback olvidado)
[ ] Kill switch=false
```
