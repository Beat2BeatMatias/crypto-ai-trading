# Decisor LLM-centric — Spec de diseño

**Fecha:** 2026-05-17
**Estado:** Borrador v2 (pendiente de aprobación)
**Autor:** Matías + Claude (sesión de brainstorm)
**Audiencia:** Implementación (`writing-plans` skill) + revisores de spec funcional/técnica.
**Alcance del PR esperado:**
- Código: `trading-engine/collectors/indicators.py`, `trading-engine/agents/context_builder.py`, `trading-engine/agents/decisor.py`, `trading-engine/agents/prompts/decisor_system.txt`, `trading-engine/agents/prompts/decisor_user.txt`, `trading-engine/risk/risk_gate.py`, `shared/schemas.py`.
- Tests: `trading-engine/tests/test_decisor.py`, `trading-engine/tests/test_risk_gate.py`, `trading-engine/tests/test_indicators.py`, `trading-engine/tests/test_context_builder.py`.
- Specs: `docs/specs/01-functional-spec.md` (§F1, §F2, §F2.bis, §6, §8), `docs/specs/02-technical-spec.md` (§2.6, nueva §2.x), `docs/specs/05-risk-and-safety.md` (reformulación del rol del Risk Gate).

---

## 1. Premisa del cambio

> "Inyectar al Decisor la información técnica más completa posible, adecuada al timeframe y al perfil operativo, para que tome la decisión final. El Risk Gate se mantiene, pero su objetivo es **detectar incoherencias y alucinaciones del LLM**, no limitar la estrategia."

Tres ejes de trabajo:

1. **Enriquecimiento técnico** — más indicadores, mejor organizados, jerarquizados por perfil operativo derivado de `decisor_interval_min` + `atr_timeframe`.
2. **Autonomía del LLM** — el LLM decide `action` y `position_size_pct` con autonomía; el código no sobrescribe la decisión.
3. **Risk Gate reposicionado** — `R1–R10` se mantienen como hard constraints (rechazan ejecución); se agregan **reglas de coherencia `C1–Cn`** que detectan inconsistencias entre el output del LLM y la evidencia técnica del ciclo (producen warnings auditables, no rechazos por default).

---

## 2. Estado actual (referencia)

### 2.1 Indicadores hoy (`collectors/indicators.py::compute_indicators`)
Por cada timeframe (1m / 5m / 15m / 1h / 4h):
- RSI (Wilder, 14)
- MACD (12/26/9): macd, signal, histograma
- EMAs 20, 50, 200
- Bollinger Bands (SMA20 ± 2σ): upper, middle, lower, bb_pct
- ATR (Wilder, 14, con TR winsorizado a 3× mediana móvil)
- Volume current + volume_avg_20
- last_close

### 2.2 Contexto hoy (`agents/context_builder.py::build`)
Plano (un solo nivel), claves por timeframe (`rsi_1h`, `macd_15m`, etc.). Incluye order book agregado (spread, imbalance, wall sizes/distancias), balances, posiciones abiertas, últimas 3 decisiones, métricas del día, subset de config (~15 claves), playbook completo.

### 2.3 Decisor hoy (`agents/decisor.py`)
- Renderiza system + user prompts.
- Valida `DecisorOutput` con Pydantic.
- Filtra confluencias fuera de A–H.
- **Aplica overrides deterministas** (`_apply_deterministic_overrides`):
  - `TRENDING_DOWN` → `HOLD`.
  - `confidence < threshold(regime)` → `HOLD` (umbrales 0.60/0.70/0.80 por régimen, piso `_CONFIDENCE_FLOOR=0.40`).
  - `position_size_pct` reescrito por escalón (`0.03` o `min(max_position_pct, 0.25)`).
- Persiste `Decision`.

### 2.4 Risk Gate hoy (`risk/risk_gate.py::validate`)
Rechaza ejecución si:
- `total_drawdown_pct <= max_drawdown_pct`.
- Kill switch activo (salvo SELL-to-close).
- SELL sin posición abierta.
- BUY con `stop_loss` ausente, ≥ precio, distancia fuera de `[sl_atr_multiplier, sl_atr_max_multiplier]×ATR`.
- `position_size_pct > max_position_pct`.
- `open_positions_count >= max_simultaneous_trades`.
- `daily_pnl_pct <= daily_stop_pct`.
- BUY con `take_profit` ausente o ≤ precio.
- R:R `(TP - precio) / (precio - SL) <= min_rr_ratio`.
- R10: movimiento al TP no cubre `min_fees_to_tp_ratio × roundtrip_fee_pct`.

---

## 3. Diseño propuesto

### 3.1 Enriquecimiento técnico (eje 1)

#### 3.1.1 Indicadores nuevos a calcular en `compute_indicators`

Por timeframe (1m / 5m / 15m / 1h / 4h, salvo donde se aclare):

| Indicador | Propósito | Notas de cálculo |
|---|---|---|
| **ADX (14)** | Fuerza de tendencia (separa TRENDING de RANGE objetivamente) | Wilder, valores > 25 = tendencia clara, < 20 = range |
| **Stochastic (14, 3, 3)** | Oversold/overbought más sensible que RSI | `%K`, `%D` |
| **VWAP intradiario** (1m, 5m, 15m) | Precio promedio ponderado por volumen del día, ancla de mean-reversion | Reset diario UTC |
| **VWAP bands** (1σ, 2σ) | Zonas estadísticas alrededor del VWAP | Para entradas mean-reversion |
| **Pivot points clásicos (diarios)** | Niveles psicológicos S1/S2/S3, R1/R2/R3, PP | Calculado del día anterior, válido 1m–1h |
| **EMA 9** (1m, 5m, 15m) | EMA rápida para scalping | Extender al timeframe corto |
| **Wick ratio últimas 3 velas** | Detección de rechazo (mecha larga / cuerpo) | `(high − max(open,close)) / abs(open-close)` y simétrico para low |
| **Body/range ratio última vela** | Conviction de la vela actual | `abs(close-open) / (high-low)` |
| **Volume delta (1m, 5m)** | Aproximación de taker buy − taker sell vía deltas de close | Sin trades agregados granulares; usar regla de Lee-Ready aproximada |
| **OBV (On Balance Volume) slope 20** | Confirmación de tendencia con flujo | Pendiente normalizada |
| **Structure**: HH/HL/LH/LL últimas 20 velas | Detectar `higher highs + higher lows` (uptrend), `lower highs + lower lows` (downtrend), o consolidación | Output: `"uptrend" \| "downtrend" \| "consolidation"` |
| **Cross-TF alignment summary** | Booleano por bloque: `trend_1h_bull`, `trend_4h_bull`, `momentum_aligned_15m_1h` | Derivado de las EMAs/MACD ya existentes |
| **ATR percentile 30d** | Contexto de volatilidad actual vs histórica (ya existe `atr_avg_7d`, agregar percentil) | Percentil del ATR actual sobre 30 días |

#### 3.1.2 Order book enriquecido (`collectors/orderbook_collector.py`)

Lo que ya hay (spread, imbalance, walls top): se mantiene.

Nuevo:
- **Depth a 0.1%, 0.25%, 0.5%, 1.0%** (bid y ask, en BTC y USDT): cuántos BTC se necesitan para mover el precio X%.
- **Cumulative volume profile top-20 niveles**: suma acumulada a cada lado.
- **Bid/Ask wall cluster**: detectar múltiples walls adyacentes y reportar el rango "muralla".
- **Mid-price impact estimate** para una orden hipotética del tamaño del trade típico (USDT del balance × `max_position_pct`).

#### 3.1.3 Bloques en el contexto del LLM (`ContextBuilder`)

Reorganización del `ctx` y del prompt en **bloques semánticos**, no claves planas. El prompt los referencia con marcadores claros.

**Bloque A — `OPERATIONAL_PROFILE`**
- Perfil derivado (`SCALPING` / `HIBRIDO` / `DAY_TRADING`) en base a `decisor_interval_min` + `atr_timeframe`.
- Lista explícita de timeframes prioritarios para este perfil.
- Confluencias del catálogo A–H más relevantes para este perfil (informativo, no exclusivo).

**Bloque B — `MARKET_SNAPSHOT`**
- Precio, mid-price del order book, spread, % cambio 1h/4h/24h, ATR ref ± percentil 30d, volatility_label calculado del percentil ATR.

**Bloque C — `TECHNICAL_TIMEFRAMES`** (multi-TF, ordenado por relevancia del perfil)
- Para cada TF (en orden de prioridad del perfil):
  ```
  [15m] (prioridad ALTA)
    RSI=53.2 (neutral)  Stoch=%K 45 %D 50 (neutral)
    MACD=12.5 sig=8.1 hist=4.4 (bullish, hist creciente)
    EMAs: 20=84120, 50=83900, 200=82500 (precio>20>50>200 → uptrend)
    BB: upper=84800 mid=84120 lower=83440 bb%=58 (medio)
    ATR=180 (1.2% del precio, percentil30d=65)
    ADX=28 (tendencia clara)
    VWAP=84050 (precio +0.08% vs VWAP)
    Volume: actual 1.4x avg20 (incremental)
    Structure: HH+HL últimas 20 velas (uptrend confirmado)
    Wick últimas 3: 0.4, 0.3, 0.2 (sin rechazo significativo)
  [1h] (prioridad ALTA)
    ...
  [4h] (prioridad MEDIA)
    ...
  [5m] (prioridad BAJA — solo confirmación)
    ...
  [1m] (omitido en HIBRIDO)
  ```
- Para SCALPING se invierte la prioridad (1m, 5m altas; 1h, 4h medias; 4h opcional).
- Para cada TF se incluye una **etiqueta interpretativa** (neutral / bullish / bearish / overextended) computada por el `ContextBuilder` para que el LLM no tenga que re-derivar lo trivial.

**Bloque D — `KEY_LEVELS`**
- Pivot points diarios (P, S1/S2/S3, R1/R2/R3) y distancia al precio actual.
- Soportes/resistencias derivados (EMAs 50/200 1h y 4h con distancia %).
- Walls del order book con distancia.
- Highs/lows 24h y 7d.
- VWAP intradiario y bandas.

**Bloque E — `ORDER_BOOK_DEPTH`**
- Spread, mid-price.
- Imbalance global + por niveles (top 1%, top 0.5%, top 0.25%).
- Depth para mover ±0.1% / ±0.25% / ±0.5% / ±1%.
- Mid-price impact estimate para el trade típico.

**Bloque F — `CROSS_TF_ALIGNMENT`**
- Tabla compacta:
  ```
  Trend:    1m=up  5m=up  15m=up  1h=up  4h=neutral   → ALINEADO ALCISTA
  Momentum: 1m=+  5m=+  15m=+   1h=+   4h=+          → ALINEADO
  Structure 15m: HH+HL  | 1h: HH+HL  | 4h: range
  Conclusión: tendencia alcista intacta en 1h, 4h en consolidación, sin divergencias.
  ```

**Bloque G — `RECENT_DECISIONS`**
- Últimas 3 decisiones del decisor (timestamp, action, confidence, regime, outcome si aplica).
- Tiempo desde la última SELL (para evaluar cooldown).

**Bloque H — `PORTFOLIO_STATE`**
- Balance USDT, BTC, capital total, P&L día (realizado y no realizado).
- Posiciones abiertas (entry, SL, TP, P&L actual, % al SL/TP).
- `daily_pnl_pct` vs `daily_stop_pct`, `total_drawdown_pct` vs `max_drawdown_pct`.

**Bloque I — `RISK_CONFIG`** (compacto, no la config completa — solo lo que el LLM necesita razonar)
```
max_position_pct: 0.10  (cap absoluto por trade — R1)
min_rr_ratio: 1.3       (R:R mínimo — R5)
sl_atr_range: [0.3, 1.5]×ATR(15m)=$180 → SL válido entre $54 y $270
min_fees_to_tp_ratio: 3 × roundtrip_fee 0.0%=$0 (testnet → R10 no aplica)
daily_stop_pct: -3%  (hoy llevamos -0.8% → margen 2.2%)
max_simultaneous_trades: 2  (abiertas: 1 → cupo 1)
cooldown_after_sell_min: 15  (última SELL hace 47m → sin cooldown)
```

**Bloque J — `PLAYBOOK`**
- Markdown del playbook activo (igual que hoy).

**Bloque K — `OPERATIONAL_GUIDELINES`** (estático, del system prompt)
- Catálogo cerrado A–H.
- Guía de sizing por confidence/regime (orientativa, no override).
- Jerarquía de decisión.
- Formato del campo `reasoning`.

#### 3.1.4 Etiquetado interpretativo

El `ContextBuilder` precomputa etiquetas de bajo nivel para que el LLM no tenga que recalcular:
- `rsi_label`: `oversold (<30)` / `weak_bear (30-45)` / `neutral (45-55)` / `weak_bull (55-70)` / `overbought (>70)`.
- `macd_label`: `bullish_cross` / `bullish_extending` / `bullish_weakening` / `neutral` / `bearish_*`.
- `trend_label` por TF: `strong_up` / `up` / `consolidation` / `down` / `strong_down` (combinando EMAs + ADX).
- `volatility_label`: `low (atr_pct30d < 30)` / `normal (30-70)` / `elevated (70-90)` / `extreme (>90)`.

Estas etiquetas se calculan en `ContextBuilder._labelers` (módulo nuevo) con reglas determinísticas simples. **No reemplazan al juicio del LLM**, lo aceleran.

### 3.2 Autonomía del LLM (eje 2)

#### 3.2.1 Eliminación de overrides deterministas en `decisor.py`

- **Eliminar** `_apply_deterministic_overrides` del flujo.
- **Eliminar** constantes `_CONFIDENCE_FLOOR`, `_REGIME_THRESHOLD_KEY`, `_REGIME_THRESHOLD_DEFAULT` del módulo.
- **Mantener** `_filter_confluence_codes` (validación de catálogo, no override de decisión).
- **Mantener** persistencia íntegra en `decisions`.
- Nuevo log informativo `decisor.llm_decision_accepted` con `action`, `regime`, `confidence`, `position_size_pct`, `confluences`.

#### 3.2.2 Schema `DecisorOutput`

- `position_size_pct: float = Field(ge=0.0, le=1.0)` — sin cap dinámico en Pydantic; el cap por `max_position_pct` lo aplica el Risk Gate R1.
- `confidence_adjustment: float = Field(ge=-0.10, le=0.10)` — se mantiene.
- Sin más cambios.

#### 3.2.3 Cambios en `decisor_system.txt`

1. Reescribir sección "JERARQUIA DE DECISION":
   > "Sos el responsable final de la decisión de `action` y `position_size_pct`. El código no va a sobrescribir tu output salvo por validación de formato. El **Risk Gate determinístico (R1–R10)** puede **rechazar la ejecución** de tu decisión si viola límites duros (la decisión queda persistida con `rejected_reason`). Las **reglas de coherencia (C1–Cn)** del Risk Gate evalúan si tu output es consistente con la evidencia del ciclo y producen warnings auditables."
2. Nueva sección "GUIA DE SIZING" (reemplaza el escalón):
   ```
   Elegí position_size_pct libre en (min_position_size, max_position_pct]:
     • confidence ≥ 0.85 + 3+ confluencias + TRENDING_UP → cerca de max_position_pct.
     • confidence 0.70–0.84 → 50–80% de max_position_pct.
     • confidence 0.60–0.69 → 20–40% de max_position_pct (entrada de prueba).
     • confidence < 0.60 → preferir HOLD; si tu juicio dice operar, sizing ≤ 20% de max_position_pct y justificá [SIZING] en reasoning por qué la confianza baja vale la entrada.
   ```
3. Sección "REGIMEN DE MERCADO": reemplazar "TRENDING_DOWN → BUY bloqueado" por "TRENDING_DOWN → BUY desincentivado. Solo si tenés evidencia técnica fuerte de reversión (≥3 confluencias incluyendo A/D/H + bid pressure clara). En la mayoría de los casos la respuesta correcta es HOLD. Si decidís BUY, agregá `[CONTRA_REGIMEN]` en `reasoning`."
4. Nuevo bloque al inicio: prompt referencia los bloques A–K del contexto (con marcadores tipo `=== BLOCK_A: OPERATIONAL_PROFILE ===`).
5. Reforzar Risk Gate R1–R10 como hard constraints con el matiz: "El Gate los va a verificar y rechazar tu BUY si los violás. Producí decisiones que los respeten."

### 3.3 Risk Gate como detector de incoherencias (eje 3)

#### 3.3.1 Reglas R1–R10 (hard constraints — rechazan ejecución)

Sin cambios funcionales. Sólo **reframing semántico** en docs y en logs:
- Mensaje de log nuevo: `risk_gate.rejected` con `rule_id="R1"`, `category="hard_constraint"`, `reason=...`.

Las R1–R10 ya cubren naturalmente la mayoría de las "incoherencias estructurales":
- SL ≥ precio (BUY): incoherencia obvia.
- TP ≤ precio (BUY): incoherencia obvia.
- `position_size_pct > max_position_pct`: violación de R1.
- R:R imposible: violación de R5.

#### 3.3.2 Reglas de coherencia C1–C6 (warnings, no rechazos por default)

Capa nueva en `risk/coherence_checker.py` (módulo separado, llamado desde el Decisor después del LLM y antes del Risk Gate):

| Id | Regla | Trigger | Severidad por default |
|----|-------|---------|----------------------|
| **C1** | Coherencia de confluencia A (RSI_OVERSOLD_BOUNCE) | LLM declara confluencia "A" pero RSI(15m) > 35 y RSI(1h) > 35 | warning |
| **C2** | Coherencia de confluencia B (MACD_BULLISH_CROSS) | LLM declara "B" pero MACD(15m) ≤ Signal(15m) y MACD(1h) ≤ Signal(1h) | warning |
| **C3** | Coherencia de régimen vs indicadores | LLM declara `regime=TRENDING_UP` pero ADX(15m) < 20 o EMAs no alineadas; o `regime=TRENDING_DOWN` con todo lo contrario | warning |
| **C4** | Coherencia confidence vs confluencias | `confidence ≥ 0.85` con < 2 confluencias del catálogo; o `confidence ≥ 0.70` con 0 confluencias en BUY | warning |
| **C5** | Coherencia interna confidence vs action | `action=BUY` con `confidence < 0.50` y sin tag `[CONTRA_REGIMEN]` o `[SIZING]` en reasoning | warning |
| **C6** | Coherencia expected_holding_min vs perfil | `expected_holding_min` fuera del rango del perfil operativo (e.g. 6h en SCALPING o 5min en DAY_TRADING) | warning |

#### 3.3.3 Política de los warnings de coherencia

- Por default: **log + persistencia en `decisions.output.coherence_warnings`** + métrica observable. **NO bloquean ejecución.**
- Métrica `coherence_warnings_24h` por regla en `/api/health` y `/api/decisions/stats`.
- Si el porcentaje de warnings de una regla específica supera **20% en ventana 24h** → alerta en dashboard ("posible alucinación recurrente, revisar prompt o cambiar modelo").
- Config nueva opcional `coherence_strict_mode: bool` (default `false`). Si `true`, ciertos warnings críticos (C1, C2, C3) pasan a ser **rechazos duros** (`rejected_reason="C1_..."`). Permite al operador endurecer si detecta alucinaciones sistemáticas.

#### 3.3.4 Integración en el flujo

```
Decisor.decide():
  1. ContextBuilder.build() → ctx
  2. Prompt rendering
  3. LLM call → DecisorOutput
  4. _filter_confluence_codes()
  5. CoherenceChecker.evaluate(decision, ctx) → list[CoherenceWarning]  # NUEVO
  6. Persist Decision (con coherence_warnings)
  7. Log "decisor.llm_decision_accepted" + warnings count
  → return decision (warnings y todo)

Engine loop:
  decision = decisor.decide(...)
  verdict = risk_gate.validate(decision, ...)   # R1–R10
  if verdict.passed:
      executor.execute(...)
  else:
      update decision.rejected_reason
```

---

## 4. Cambios concretos por archivo

| Archivo | Cambio |
|---|---|
| `trading-engine/collectors/indicators.py` | Agregar cómputo de ADX, Stochastic, VWAP intradiario, VWAP bands, EMA9, wick/body ratios, volume delta aprox, OBV slope, estructura HH/HL, ATR percentile 30d. |
| `trading-engine/collectors/orderbook_collector.py` | Agregar depth a 0.1/0.25/0.5/1.0%, cumulative profile top-20, wall clusters, mid-price impact estimate. |
| `trading-engine/agents/context_builder.py` | Reescribir `build()` para producir bloques A–K (no claves planas). Agregar `_labelers` para etiquetado interpretativo. Agregar `_format_pivot_points()`, `_format_cross_tf_summary()`, etc. |
| `trading-engine/agents/decisor.py` | Eliminar `_apply_deterministic_overrides`, constantes de threshold/floor. Agregar invocación a `CoherenceChecker`. |
| `trading-engine/agents/prompts/decisor_system.txt` | Reescribir secciones (jerarquía, sizing, régimen). Reemplazar contexto plano por referencias a bloques A–K. |
| `trading-engine/agents/prompts/decisor_user.txt` | Reorganizar como bloques A–K. |
| `trading-engine/risk/coherence_checker.py` | **Nuevo módulo.** Implementa C1–C6. |
| `trading-engine/risk/risk_gate.py` | Sin cambios funcionales. Solo `rule_id` en logs/reason. |
| `shared/schemas.py` | `DecisorOutput.position_size_pct: ge=0.0, le=1.0` (sin cap dinámico). Agregar `CoherenceWarning` dataclass y opcional `coherence_warnings: list[CoherenceWarning]` en el output enriquecido (persistido en `decisions.output`). |
| `shared/config_store.py` | Agregar `MIN_POSITION_SIZE` (default `0.005`), `COHERENCE_STRICT_MODE` (default `false`), `DECISOR_LLM_CENTRIC` (feature flag de rollout, default `true` en paper, `false` en LIVE inicialmente). |

---

## 5. Impacto en specs

### 5.1 `docs/specs/01-functional-spec.md`

- **§F1 (Recolección)** — agregar indicadores nuevos al listado.
- **§F2 (Decisor)** — reescribir flujo: "el LLM recibe contexto bloque-estructurado y decide; el código no aplica overrides de acción o sizing".
- **§F2.bis** — tabla de reparto: `action` y `position_size_pct` ahora son del LLM con safety net = Risk Gate. Eliminar §F2.bis.2 puntos 1, 2, 3. Mantener 4, 5, 6.
- **§6.3 (Confianza y sizing)** — reformular como guía orientativa, no override.
- **§6.5 (Régimen)** — TRENDING_DOWN ya no bloquea, solo desincentiva.
- **§8 Acceptance Criteria** — AC-02 y AC-11 reformulados (ver §7 de este spec).

### 5.2 `docs/specs/02-technical-spec.md`

- **§2.6 (Overrides)** — documentar eliminación de overrides de régimen, confianza y sizing. Mantener filtro de confluencias A–H.
- **Nueva §2.x (Bloques de contexto)** — documentar bloques A–K, su contenido y orden.
- **Nueva §2.y (Coherence Checker)** — documentar reglas C1–C6, severidad, `coherence_strict_mode`.

### 5.3 `docs/specs/05-risk-and-safety.md`

- Reformular el rol del Risk Gate: además de "límites no negociables R1–R10", **detecta incoherencias del LLM (C1–C6)**.
- Agregar tabla C1–C6 con descripción, trigger y severidad.

---

## 6. Tests

### 6.1 A actualizar / eliminar
- Tests de `_apply_deterministic_overrides` → **eliminar**.
- Tests que asumen "TRENDING_DOWN → HOLD forzado" → reescribir.
- Tests de AC-11 (reescritura de sizing) → reescribir como "Risk Gate R1 rechaza con `rejected_reason`".

### 6.2 Nuevos
- `test_indicators_adx`, `test_indicators_stochastic`, `test_indicators_vwap`, `test_indicators_pivot_points`, `test_indicators_structure_hh_hl`.
- `test_orderbook_depth_levels`, `test_orderbook_mid_impact`.
- `test_context_builder_blocks` — verifica orden y presencia de bloques A–K.
- `test_context_builder_profile_scalping` / `_hibrido` / `_day_trading` — verifica que la prioridad de TFs cambia según perfil.
- `test_context_builder_labels` — verifica etiquetas interpretativas.
- `test_decisor_no_override_regime` — LLM emite BUY en TRENDING_DOWN, persiste tal cual.
- `test_decisor_no_override_confidence` — LLM emite BUY con `confidence=0.55`, persiste tal cual.
- `test_decisor_size_exceeds_max_rejected_by_risk_gate` — `position_size_pct=0.30` con `max=0.10` → Risk Gate R1 rechaza.
- `test_coherence_c1_rsi_inconsistent` — LLM declara "A" con RSI=60 → warning C1.
- `test_coherence_c3_regime_inconsistent` — `TRENDING_UP` con ADX=12 → warning C3.
- `test_coherence_c4_confidence_without_confluences` — `confidence=0.90` con 0 confluencias → warning C4.
- `test_coherence_strict_mode_blocks` — con `coherence_strict_mode=true`, C1 pasa a rechazo R-equivalente.

---

## 7. Acceptance Criteria del cambio

| Id | Criterio |
|----|----------|
| LCD-01 | `compute_indicators` retorna ADX, Stochastic, VWAP, VWAP bands, EMA9, wick/body ratios, OBV slope, ATR percentile 30d y estructura HH/HL para los timeframes especificados. |
| LCD-02 | `OrderBookCollector` retorna depth a 0.1/0.25/0.5/1.0% y mid-price impact estimate. |
| LCD-03 | `ContextBuilder.build()` produce un `ctx` con bloques `OPERATIONAL_PROFILE`, `MARKET_SNAPSHOT`, `TECHNICAL_TIMEFRAMES` (ordenado por perfil), `KEY_LEVELS`, `ORDER_BOOK_DEPTH`, `CROSS_TF_ALIGNMENT`, `RECENT_DECISIONS`, `PORTFOLIO_STATE`, `RISK_CONFIG`, `PLAYBOOK`. |
| LCD-04 | El system prompt renderizado contiene los marcadores `=== BLOCK_A: OPERATIONAL_PROFILE ===` … `=== BLOCK_K: OPERATIONAL_GUIDELINES ===`. |
| LCD-05 | `decisor.py` no contiene `_apply_deterministic_overrides` ni mutaciones de `action` / `position_size_pct` post-LLM. |
| LCD-06 | Una decisión BUY con `regime=TRENDING_DOWN` y `confidence=0.65` se persiste tal cual; el Risk Gate evalúa por sus reglas (R1–R10). |
| LCD-07 | Una decisión con `position_size_pct=0.30` y `max_position_pct=0.10` queda `executed=false`, `rejected_reason="R1_position_size_exceeds_max"`. |
| LCD-08 | `CoherenceChecker.evaluate()` retorna list[CoherenceWarning] correcta para C1–C6 según fixtures. |
| LCD-09 | `decisions.output.coherence_warnings` se persiste no-vacío cuando aplica; observable en `/api/decisions`. |
| LCD-10 | `/api/health` expone `risk_gate.rejection_rate_24h` y `coherence.warning_rate_24h` por regla. |
| LCD-11 | Con `coherence_strict_mode=true`, warnings de C1/C2/C3 pasan a `rejected_reason="C{n}_*"`. |
| LCD-12 | Specs §F1, §F2, §F2.bis, §6.3, §6.5, §8 (AC-02, AC-11) actualizadas. §2.x y §2.y nuevas en technical-spec. §5 risk-and-safety reformulada. |
| LCD-13 | Feature flag `DECISOR_LLM_CENTRIC` permite rollback al comportamiento anterior sin redeploy. |

### Reformulaciones de AC existentes

- **AC-02 (nuevo)** — "Una decisión BUY con `confidence < 0.60` o `regime=TRENDING_DOWN` se persiste sin modificación; si el LLM no agregó tag `[CONTRA_REGIMEN]` o `[SIZING]` en `reasoning`, el `CoherenceChecker` emite warning C5; la decisión sigue evaluándose por R1–R10."
- **AC-11 (nuevo)** — "Un BUY del LLM con `position_size_pct > max_position_pct` queda **rechazado** por R1 con `rejected_reason='R1_position_size_exceeds_max'`. El sistema no reescribe el valor."

---

## 8. Métricas y observabilidad (nuevas)

- `risk_gate.rejection_rate_24h` desglosado por `R1`…`R10`.
- `coherence.warning_rate_24h` desglosado por `C1`…`C6`.
- `decisor.size_chosen_distribution_24h` — histograma de `position_size_pct` elegido por el LLM.
- `decisor.confidence_distribution_24h` — histograma de `confidence`.
- `decisor.buy_against_regime_count_24h` — BUYs con `TRENDING_DOWN`.
- `decisor.buy_below_confidence_floor_count_24h` — BUYs con `confidence < 0.60`.

Exponer en `/api/decisions/stats?window=24h` y en el dashboard.

---

## 9. Plan de rollout

1. Implementar indicadores nuevos + tests.
2. Implementar order book enriquecido + tests.
3. Implementar `ContextBuilder` reorganizado + tests.
4. Implementar `CoherenceChecker` + tests.
5. Implementar cambios en `Decisor` + tests.
6. Actualizar prompts y specs.
7. Validar 1 semana en paper trading con `DECISOR_LLM_CENTRIC=true`.
8. Métricas de éxito (semana 1 paper):
   - `risk_gate.rejection_rate_24h` ≤ 15%.
   - `coherence.warning_rate_24h` ≤ 25% por regla individual.
   - Win rate, profit factor y Sharpe **no peores** que la baseline previa con overrides.
9. Si métrica de éxito: pasar a LIVE con feature flag `true`.
10. Si métrica no se cumple: iterar prompt antes de LIVE; opcionalmente activar `coherence_strict_mode=true` o mantener `_apply_deterministic_overrides` desactivable.

---

## 10. Fuera de alcance

- Memoria embeddings / RAG.
- Cambios en Supervisor.
- Cambios en confluencias A–H (catálogo cerrado).
- Indicadores que requieran datos de fuera de Binance Spot (funding, OI, on-chain).

---

## 11. Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Aumento del tamaño del prompt → más tokens, más latencia, posible degradación | Alta | Medio | Compactar bloques (etiquetas en vez de raw); medir tokens/llamada; mantener perfil que omite TFs irrelevantes |
| LLM emite BUY en TRENDING_DOWN con SL apropiado y pierde | Media | Alto | Risk Gate R5/R10 mitigan; monitoreo; rollback con flag; Supervisor ajusta playbook |
| LLM elige sizing alto en confianza baja | Media | Alto | R1 cap absoluto; `daily_stop_pct`; `max_drawdown_pct`; kill switch |
| Alucinaciones recurrentes de una regla específica | Media | Medio | Métrica de warnings por regla; alerta automática si > 20%; `coherence_strict_mode` |
| Más indicadores = más cómputo por ciclo | Baja | Bajo | Indicadores con pandas siguen siendo O(N) y N≤250 |
| Etiquetas interpretativas mal calibradas sesgan al LLM | Media | Medio | Bandas conservadoras; el LLM siempre ve también los valores numéricos crudos |

---

## 12. Checklist de aprobación (para Matías)

- [ ] Acepto la lista de indicadores nuevos de §3.1.1 y §3.1.2.
- [ ] Acepto la reorganización del contexto en bloques A–K (§3.1.3).
- [ ] Acepto el etiquetado interpretativo (§3.1.4).
- [ ] Acepto la eliminación de `_apply_deterministic_overrides` (§3.2.1).
- [ ] Acepto las reglas de coherencia C1–C6 y su política de warnings (§3.3.2–§3.3.3).
- [ ] Acepto los cambios de prompt de §3.2.3.
- [ ] Acepto los cambios a specs de §5.
- [ ] Acepto los criterios de aceptación de §7.
- [ ] Acepto el plan de rollout (§9) con feature flag `DECISOR_LLM_CENTRIC` y métricas de éxito.

Una vez aprobado, paso al `writing-plans` skill para generar el plan de implementación detallado (TDD por capa).
