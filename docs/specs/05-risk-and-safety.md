# Riesgo y Seguridad — Crypto AI Trading

> Audiencia: Risk / Compliance / SRE.
> Versión: 1.8 — 2026-06-02.
>
> Cambios v1.8: CoherenceChecker **C9**; catálogo A–J fijas vs K–Z promovidas; `min_confluences_short`.
>
> Cambios v1.7: Futuros USDT-M y shorts. Reglas **R12–R15** (leverage, buffer de liquidación, margen, funding). R2/R3/R4/R5/R10 **direccionales** (BUY y SHORT). R6/R0_kill_switch usan `has_open_position` (no solo `btc_held`). Se **elimina** la antigua R7 “nunca shortear”. Capa 3 pasa a R0–R15.
>
> Cambios v1.6: Claves post-mortem (`postmortem_provider`, `postmortem_fallback_providers`, `postmortem_max_per_tick`) son **solo operador** — excluidas del auto-apply del Supervisor (§7.2). Costo LLM acotado por `postmortem_max_per_tick` (default 5 decisiones/tick).
>
> Cambios v1.5: CoherenceChecker C7 (R:R real ≤ mínimo, siempre critical) y C8 (confluencia K–Z con `verify_spec` no cumplido). Promoción vía `confluence_registry`; filtro `_filter_confluence_codes` acepta A–J + K–Z activas.
>
> Cambios v1.4: Reglas R0 (drawdown/kill_switch) y R11 (notional mínimo Binance). Circuit breaker bifurcado (operacional auto-reset vs financiero manual). Endpoints `/circuit-breaker/reset` y `/drawdown/reset`. Elimina §3 overrides (removidos en v1.3). Se elimina la Capa 2 (override determinístico). Se agrega CoherenceChecker como Capa 2 nueva (auditoría de inconsistencias del LLM). Se actualiza §1 (modelo de defensa), §1.bis (garantías — GA-1 y GA-2 revisadas), §1.bis.4 (nueva sección CoherenceChecker). El Risk Gate (Capa 3) pasa a ser la **única** barrera hard-blocking sobre la `action`.
>
> Cambios v1.2: R4/R5 con thresholds reales; §12 ítem daily_pnl marcado ✅ RESUELTO.
> Cambios v1.1: §1.bis agregado.

Este documento centraliza las reglas absolutas, controles deterministas, circuit breakers y gates de pasaje entre paper trading y LIVE. Su único propósito es asegurar que **ninguna decisión de un LLM pueda exceder los límites de riesgo configurados**.

---

## 1. Modelo de defensa en profundidad

```
┌─────────────────────────────────────────────────────────┐
│ Capa 1: LLM Decisor (LLM-centric v1.3)                 │
│  - Contexto en bloques A–K con indicadores enriquecidos │
│  - Catálogo A–H fijo + letras I–Z activas en registry   │
│  - Autonomía total sobre action y position_size_pct     │
│  - Output: JSON estricto validado por Pydantic          │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼  JSON validado por Pydantic
┌─────────────────────────────────────────────────────────┐
│ Capa 2: CoherenceChecker (NUEVO — auditoría)            │
│  - Reglas C1–C9 + C1P/C2P/C3P/C5P (bajistas)           │
│  - C1/C2/C3/C1P/C2P/C3P → two-pass                     │
│  - strict_mode: C1–C3/C1P–C3P → HOLD (configurable)    │
│  - NO bloquea por defecto; persiste warnings para       │
│    retroalimentación al LLM en el ciclo siguiente       │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Capa 3: Risk Gate (reglas R0–R15) — ÚNICA BARRERA HARD  │
│  - Bloqueo absoluto antes de emitir orden al exchange   │
│  - Cada rechazo lleva rule_id estructurado              │
│  - Persiste rejected_reason en la decisión              │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Capa 4: Circuit Breaker (fallas en cadena + breaches)   │
│  - Pausa operacional (LLM/exchange): auto-reset ~10 min  │
│  - Pausa financiera (daily_stop/drawdown): reset manual  │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Capa 5: Operador humano                                 │
│  - Kill Switch (1 click)                                │
│  - Rollback de playbook                                 │
│  - Cambio manual de modo (frase literal)                │
│  - coherence_strict_mode (activar rigor de auditoría)   │
└─────────────────────────────────────────────────────────┘
```

---

## 1.bis Autonomía del LLM: garantías del modelo de defensa

Esta sección formaliza qué garantías de seguridad ofrece el modelo de defensa frente a la autonomía del Decisor. La perspectiva funcional está en `01-functional-spec.md §F2.bis`; la técnica en `02-technical-spec.md §2.6.bis`.

### 1.bis.1 Principio rector

> **El LLM Decisor toma decisiones con autonomía total sobre `action` y `position_size_pct`. El CoherenceChecker audita la coherencia lógica. El Risk Gate (R0–R15) es la única barrera hard-blocking.** Ninguna salida del LLM puede provocar una acción que viole R0–R15 ni los guardrails del Supervisor (`_SAFE_BOUNDS`).

### 1.bis.2 Garantías invariantes (contrato de Risk — v1.3)

Para cualquier ciclo del Decisor, el sistema garantiza que toda decisión con `executed=true` cumple simultáneamente:

| ID | Garantía | Capa que la enforce | Estado v1.3 |
|----|----------|---------------------|-------------|
| GA-1 | Si `executed=true` y `action` es apertura (`BUY` o `SHORT`), el LLM puede haber declarado cualquier régimen. | Risk Gate R0–R15 — no hay bloqueo por régimen. | **REVISADA** — sin override TRENDING_DOWN. |
| GA-2 | Si `executed=true` y `action` es apertura, el LLM puede tener cualquier `confidence`. | Risk Gate R0–R15 — no hay bloqueo por confidence-floor. | **REVISADA** — sin umbral mínimo de confianza. |
| GA-3 | `output.action != "BUY"` ó `output.position_size_pct <= max_position_pct`. | Capa 3 Risk Gate R1. | Sin cambios. |
| GA-4 | `output.action != "BUY"` ó SL ∈ `[sl_atr_multiplier × ATR, sl_atr_max_multiplier × ATR]`. | Capa 3 Risk Gate R4. | Sin cambios. |
| GA-5 | `output.action != "BUY"` ó R:R ≥ `min_rr_ratio`. | Capa 3 Risk Gate R5. | Sin cambios. |
| GA-6 | `output.action != "SELL"` ó hay posición abierta (long o short). | Capa 3 Risk Gate R6 (`has_open_position`). | **REVISADA** v1.7 — ya no depende de `btc_held > 0`. |
| GA-11 | Apertura SHORT solo si geometría short (SL > precio > TP) y reglas futures R12–R15 pasan. | Risk Gate R2/R3/R12–R15. | **NUEVA** v1.7. |
| GA-7 | `output.action != "BUY"` con `kill_switch=true`. | Capa 3 Risk Gate (kill switch check). | Sin cambios. |
| GA-8 | Si `daily_pnl_pct <= daily_stop_pct` → cero BUYs ejecutados ese día. | Capa 3 Risk Gate R9. | Sin cambios. |
| GA-9 | `decisions.output.coherence_warnings` siempre presente (lista, puede ser vacía). | Capa 2 CoherenceChecker. | **NUEVA**. |
| GA-10 | `decisions.output.two_pass_triggered` siempre presente (bool). | `decisor.py`. | **NUEVA**. |

### 1.bis.3 Lo que el LLM no puede sobrescribir (v1.3)

| Parámetro | Quien lo controla | Mecanismo de protección |
|-----------|-------------------|------------------------|
| `daily_stop_pct` | Operador / config manual | Excluido de `_SAFE_BOUNDS`. El Supervisor solo puede **sugerirlo** en el reporte. |
| `max_drawdown_pct` | Operador / config manual | Excluido de `_SAFE_BOUNDS`. |
| `position_size_pct` ejecutado > `max_position_pct` | Risk Gate R1 | Bloquea la orden; LLM debe proponer valores dentro del rango. A diferencia de v1.2, **no hay reescritura silenciosa**: el trade queda bloqueado y el LLM aprende del rechazo. |
| Bracket SL/TP (post-orden) | Sistema (Binance) | Una vez emitido, el bracket se ejecuta server-side; el LLM no puede modificarlo en ciclos siguientes. |
| Catálogo de confluencias A–J | Sistema (prompt + Supervisor system prompt) | A–H alcistas, I–J bajistas (SHORT). Letras K–Z solo vía `confluence_registry` promovido. |

### 1.bis.4 CoherenceChecker — detección de inconsistencias del LLM

El `CoherenceChecker` (`trading-engine/risk/coherence_checker.py`) es la Capa 2 del nuevo modelo. Su propósito es **detectar y auditar** cuando el LLM declara algo que los indicadores numéricos no respaldan.

| Regla | Condición de warning |
|-------|----------------------|
| C1 | El LLM declara confluencia A (RSI_OVERSOLD_BOUNCE) pero RSI(15m) y RSI(1h) no están en zona de sobreventa (<35). |
| C2 | El LLM declara confluencia B (MACD_BULLISH_CROSS) pero el MACD no cruzó al alza en el ciclo actual. |
| C3 | El LLM declara régimen TRENDING_UP/TRENDING_DOWN pero ADX/EMAs no respaldan el régimen alcista/bajista declarado. |
| C1P | Confluencia I (RSI_OVERBOUGHT_REJECTION) pero RSI(15m) y RSI(1h) no están en sobrecompra (>65). Espejo de C1 para SHORT. |
| C2P | Confluencia J (MACD_BEARISH_CROSS) pero MACD ≥ Signal en 15m y 1h. Espejo de C2 para SHORT. |
| C3P | `action=SHORT` con régimen/EMAs incoherentes (p. ej. TRENDING_UP, o TRENDING_DOWN sin estructura bajista ni ADX>20). |
| C4 | Confianza ≥ 0.85 con menos de 2 confluencias en `decision.confluences` (post-filtro: A–J + K–Z activas, cada una peso 1.0 en el cálculo de base). |
| C5 | El LLM emite BUY con confianza < 0.60 sin justificación explícita en `reasoning`. |
| C5P | El LLM emite SHORT con confianza < 0.50 sin tag explicativo en `reasoning`. |
| C6 | `expected_holding_min` está fuera del rango típico del perfil operativo derivado. |
| C7 | R:R real calculado en código ≤ `min_rr_ratio` (**siempre critical** — fuerza HOLD/rechazo independiente de `strict_mode`). |
| C8 | Confluencia promovida **K–Z** declarada pero `verify_spec` del registry no cumple en el ciclo actual (warning). |
| C9 | Confluencia **K–Z** con etiqueta direccional en `definition_md` incompatible con `action` (p. ej. `[SHORT]` citada en BUY). Sin etiqueta → no aplica (warning). |

**Comportamiento por defecto (non-blocking)**: los warnings C1–C6, C1P–C3P, C5P, C8 y C9 se persisten en `decisions.output.coherence_warnings` y se inyectan en el Bloque G del ciclo siguiente. **C7 siempre bloquea** (equivalente a rechazo duro).

**Con `COHERENCE_STRICT_MODE=true`**: warnings de C1/C2/C3/C1P/C2P/C3P (inconsistencias factuales) se vuelven críticos y fuerzan HOLD de seguridad, como si el Risk Gate hubiera rechazado la decisión. C7 bloquea en cualquier modo.

**Two-pass**: si hay C1/C2/C3/C1P/C2P/C3P y `TWO_PASS_ENABLED=true`, se realiza una segunda llamada al LLM en el mismo ciclo para auto-corrección antes de llegar al Risk Gate.

### 1.bis.5 Trazabilidad de la autonomía (v1.3)

Toda decisión queda en `decisions` con:

- `agent="decisor"`, `model`, `tokens_in/out` (acumulados de ambos passes si hay two-pass), `latency_ms`.
- `input`: snapshot completo del contexto en bloques A–K inyectado al LLM.
- `output`: JSON resultante + `coherence_warnings` (lista C1–C9, C1P–C3P, C5P) + `two_pass_triggered` (bool).
- `executed` + `rejected_reason` (si correspondiere).
- `rule_id` en el rejected_reason (Risk Gate): `"R0_drawdown"`, `"R0_kill_switch"`, `"R1"`…`"R15"`.

El operador puede auditar la calidad del LLM via `GET /api/decisions/stats?window=24` que desglosa rechazos por `rule_id` y warnings por regla de coherencia.

---

## 2. Reglas absolutas R0–R15 (Risk Gate)

Verificadas en `trading-engine/risk/risk_gate.py:RiskGate.validate`. `HOLD` siempre pasa automáticamente. `SELL` solo valida cierre (R6); no aplica R2–R5 ni R10–R15.

**Firma relevante (v1.7):** además de `usdt_balance` / `btc_held` (Spot), el gate acepta `available_margin`, `has_open_position`, `open_position_side`, `leverage`, `liquidation_price`, `funding_rate`, `funding_rate_max_pct`, `liquidation_buffer_atr`. El notional y R14 usan `available_margin` cuando está presente.

| ID | Regla | Comportamiento si falla |
|----|-------|------------------------|
| **R0_drawdown** | `total_drawdown_pct > max_drawdown_pct` (drawdown no superado) | Reject `max_drawdown breached`. Evaluado antes de checks de apertura/cierre. |
| **R0_kill_switch** | Con `kill_switch=true`, solo `SELL` con posición abierta | Reject `kill_switch active — only close (SELL) allowed`. |
| **R1** | `position_size_pct ≤ max_position_pct + 1e-9` (aperturas BUY/SHORT) | Reject `position_size_pct X > max Y`. |
| **R2** | Apertura requiere `stop_loss` del lado correcto: LONG `SL < precio`; SHORT `SL > precio` | Reject según dirección. |
| **R3** | Apertura requiere `take_profit` del lado correcto: LONG `TP > precio`; SHORT `TP < precio` | Reject según dirección. |
| **R4** | `\|precio − SL\|` entre `sl_atr_multiplier × ATR` y `sl_atr_max_multiplier × ATR` | Reject distancia SL fuera de banda. |
| **R5** | `reward / risk > min_rr_ratio` con reward/risk simétricos por dirección | Reject `R:R ratio X < min`. |
| **R6** | `action=SELL` requiere `has_open_position` y `open_positions_count > 0` | Reject `SELL requested but no open position to close`. |
| **R7** | *(Obsoleta v1.7)* Antigua regla “nunca shortear”. Reemplazada por producto `spot` + acción `SHORT` solo en futures. | — |
| **R8** | `open_positions < max_simultaneous_trades` (aperturas) | Reject `max_simultaneous_trades reached`. |
| **R9** | `daily_pnl_pct > daily_stop_pct` (aperturas) | Reject `daily P&L breach`. |
| **R10** | Movimiento absoluto al TP cubre `min_fees_to_tp_ratio × fees` + slippage | Reject `R10: TP move …`. No aplica si `roundtrip_fee_pct == 0`. |
| **R11** | Notional de entrada, SL y TP ≥ `min_notional_usdt` (del símbolo) | Reject `notional … < min_notional`. |
| **R12** | `leverage ≤ max_leverage` | Reject `leverage X > max Y`. |
| **R13** | Precio de liquidación más allá del SL por ≥ `liquidation_buffer_atr × ATR` | Reject `liquidation too close to SL`. Si `liquidation_price` es null, no evalúa. |
| **R14** | `notional ≤ available_margin` | Reject `insufficient available margin`. |
| **R15** | `\|funding_rate\| ≤ funding_rate_max_pct` | Reject `funding rate exceeds max`. |

> ✅ `main.py` calcula `daily_pnl_pct` y `total_drawdown_pct` reales en cada tick mediante `_compute_risk_metrics()` (portfolio USDT + BTC×precio, anclado a `drawdown_reset_ts`).

---

## 3. Overrides determinísticos — ELIMINADOS (v1.3)

Desde el rediseño LLM-centric (2026-05-17), **`_apply_deterministic_overrides` fue eliminado**. El LLM tiene autonomía sobre `action`; en BUY el **`position_size_pct` ejecutado** lo fija el servidor (`risk_per_trade_pct / sl_distance_pct`, cap R1). La defensa post-LLM es CoherenceChecker (auditoría) + Risk Gate (bloqueo).

---

## 4. Circuit Breaker

`trading-engine/risk/circuit_breaker.py`. Estado persistido en `config.engine_paused` + `config.engine_pause_reason`.

### 4.1 Pausa operacional (auto-reset)

Motivos: `llm_failures`, `exchange_failures`.

- 5 fallas **consecutivas** de LLM → pausa con `PauseReason.LLM_FAILURES`.
- 5 fallas **consecutivas** de exchange → pausa con `PauseReason.EXCHANGE_FAILURES`.
- **Auto-reset**: tras `operational_cooldown_sec` (default 600 s = 10 min) sin nuevas fallas, el engine retoma automáticamente.
- Reset manual: `POST /api/circuit-breaker/reset`.

### 4.2 Pausa financiera (reset manual)

Motivos: `daily_stop`, `drawdown`.

- `daily_pnl_pct ≤ daily_stop_pct` → pausa con `PauseReason.DAILY_STOP`. Se resetea naturalmente al cambiar de fecha UTC.
- `total_drawdown_pct ≤ max_drawdown_pct` (2 breaches consecutivos) → pausa con `PauseReason.DRAWDOWN`. Requiere intervención del operador.
- Reset drawdown: `POST /api/drawdown/reset` (re-ancla high-water mark via `drawdown_reset_ts`).
- Reset pausa: `POST /api/circuit-breaker/reset` (solo si la causa es operacional; pausas financieras requieren acción explícita del operador).

### 4.3 Comportamiento cuando pausado

Cuando `engine_paused = True`, cada tick del Decisor sale tempranamente con log `engine.paused`. El OrderTracker y balance refresh continúan operando.

### 4.4 Actualización de umbrales

`update_thresholds(daily_stop_pct, max_drawdown_pct)` se invoca en cada tick para leer la última config.

---

## 5. Kill Switch

Disparado vía `POST /api/kill-switch {enabled:true}`.

- Setea `config.kill_switch = "true"` + fila en `config_history`.
- El Risk Gate, al ver el flag, rechaza cualquier apertura (**BUY** y **SHORT**) y permite solo **SELL** si `has_open_position`.
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
  "sl_atr_max_multiplier":      (0.5, 20.0),
  "min_rr_ratio":               (1.0, 3.0),
  "max_position_pct":           (0.01, 0.20),
  "min_fees_to_tp_ratio":       (1.5, 6.0),
  "expected_holding_max_min":   (30, 1440),
  "cooldown_after_sell_min":    (0, 120),
  "conf_threshold_trending_up": (0.40, 0.85),
  "conf_threshold_range":       (0.50, 0.90),
  "conf_threshold_high_vol":    (0.60, 0.95),
}
```

### 7.2 Claves **excluidas** del auto-apply

- `daily_stop_pct`
- `max_drawdown_pct`
- `decisor_interval_min` — frecuencia del ciclo del Decisor; solo operador.
- `atr_timeframe` — timeframe del ATR de referencia; solo operador.
- `trading_product`, `max_leverage`, `margin_mode`, `liquidation_buffer_atr` — producto y riesgo de derivados; solo operador.
- `postmortem_provider`, `postmortem_fallback_providers`, `postmortem_enabled`, `postmortem_max_per_tick` (configuración de aprendizaje — solo operador vía `/config`)

Estas son **explícitamente** marcadas como demasiado críticas o fuera del dominio de optimización automática. El Supervisor puede sugerirlas en el reporte (salvo post-mortem), pero el código rechaza la aplicación automática y deja constancia en `output.config_rejected`.

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
| LLM provider caído / 429 | Cascade por agente (Decisor, Supervisor, Post-mortem tienen CSV independientes). Post-mortem: primary + `postmortem_fallback_providers`. Validación Pydantic fallida → reintento en próximo tick (máx. 3). 5 fallas seguidas en Decisor/Supervisor ⇒ pausa engine. |
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
| Confluencias inválidas filtradas silenciosamente | ✅ RESUELTO | `_filter_confluence_codes()` acepta A–J + K–Z activas en registry. C8 audita `verify_spec`; C9 audita etiqueta direccional (`shared/confluence_direction.py`). |
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
