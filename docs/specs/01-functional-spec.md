# Especificación Funcional — Crypto AI Trading

> Audiencia: Product, Risk, Trading, Stakeholders.
> Versión: 1.7 — 2026-05-25.
>
> Cambios v1.7: Post-mortem endurecido: `coerce_lesson_raw` antes de Pydantic, reintento de `failed` (máx. 3 intentos), `postmortem_fallback_providers` configurable (UI + config). Semántica documentada: 1 llamada LLM por decisión, `postmortem_max_per_tick` limita decisiones/tick. UI `/config` sección Post-mortem. Migración 013.
>
> Cambios v1.5: Aprendizaje desde post-mortem (§F10): job LLM encadenado a outcome attribution, Bloque K, normalizador de lecciones, catálogo extendido I–Z (`confluence_registry`), promoción Supervisor + UI operador (`/confluence`). CoherenceChecker C7/C8. Migraciones 011–012.
>
> Cambios v1.4: Outcome attribution contrafactual (§F9), notificaciones Telegram, endpoints de reset (`/drawdown/reset`, `/circuit-breaker/reset`), balance con saldos locked, flujo del Decisor sin overrides (corrige §5.1), WebSocket completo (8 eventos).
>
> Cambios v1.3: Rediseño LLM-centric del Decisor. El LLM decide `action` y `position_size_pct` con autonomía total (se eliminan los overrides deterministas de TRENDING_DOWN, confidence-floor y sizing). Se agrega CoherenceChecker (§F2.bis.5) y two-pass (§F2.bis.6). Se enriquecen indicadores en §F1. Se actualiza §F2.bis, §6.3, §6.5, §F6, AC-02 y AC-11. El Risk Gate (R1–R10) permanece como única barrera hard-blocking.

---

## 1. Visión del producto

**Crypto AI Trading** es un bot autónomo de *day trading* sobre el par **BTC/USDT** en **Binance Spot** que combina:

- **Inteligencia técnica determinística**: cálculo de indicadores (RSI, MACD, EMAs, Bollinger, ATR) y profundidad de order book en múltiples timeframes (1m, 5m, 15m, 1h, 4h).
- **Dos agentes LLM** que cooperan:
  - **Decisor**: cada N minutos decide `BUY` / `SELL` / `HOLD` con un JSON estructurado.
  - **Supervisor**: diariamente (00:00 UTC) reescribe el *playbook* y sugiere ajustes finos de configuración.
- **Risk Gate determinístico**: verifica reglas absolutas (R1–R10) antes de ejecutar cualquier orden; las decisiones del LLM nunca eluden los límites de riesgo configurados.
- **Dashboard web** en español (es-AR) con métricas en vivo, historial de trades, decisiones, playbooks y configuración editable.

El bot opera en dos modos: **`PAPER_TRADING`** (default, Binance Testnet, sin dinero real) y **`LIVE`** (mainnet, requiere confirmación explícita).

---

## 2. Objetivos de negocio

| Objetivo | Métrica | Umbral mínimo |
|----------|---------|---------------|
| Generar P&L positivo ajustado por riesgo | Sharpe ratio anualizado | > 1.0 |
| Limitar pérdidas catastróficas | Max drawdown | < 5% (LIVE), < 10% (backtest gate) |
| Tasa de acierto sostenible | Win rate | > 52% (LIVE), > 48% (backtest gate) |
| Eficiencia operativa | Profit factor | > 1.5 (LIVE), > 1.3 (backtest gate) |
| Confiabilidad técnica | Decisiones LLM sin errores de parseo | > 99% en 48 h continuas |
| Costo operativo | LLM tokens/día (Gemini Flash + Groq) | Dentro del free tier permanente |

---

## 3. Roles y usuarios

### 3.1 Operador

Persona responsable del bot. Sus tareas diarias son:

1. Revisar dashboard (`/`) — 5 min/día.
2. Ver log de decisiones (`/decisions`) para detectar anomalías.
3. Revisar el playbook activo (`/playbook`).
4. Intervenir solo si:
   - El bot se pausa por *circuit breaker*.
   - El Supervisor produce un playbook que empeora las métricas → rollback.
   - Alguna métrica de la semana queda fuera de umbrales.
5. Ejecutar **Kill Switch** en emergencias.

### 3.2 Sistema (agentes automáticos)

| Agente | Frecuencia | Responsabilidad |
|--------|-----------|----------------|
| Decisor | cada `decisor_interval_min` (default 5 min) | Producir 1 decisión estructurada por ciclo. |
| Supervisor | 1×/día (`0 0 * * *` UTC) o disparo manual | Reescribir el playbook y sugerir ajustes de configuración dentro de guardrails. |
| Order Tracker | cada 30 s | Detectar fills de SL/TP en Binance y cerrar trades en BD. |
| Position Refresher | cada 30 s | Actualizar P&L no realizado de posiciones abiertas. |
| Price/OHLCV Collector | en cada tick del Decisor | Persistir velas y recomputar indicadores. |
| Fee Manager | cada 24 h | Refrescar maker/taker fees desde Binance. |
| Balance Refresher | cada 60 s | Persistir snapshot USDT/BTC (free + locked) desde Binance. |
| Outcome Attribution | cada `outcome_attribution_interval_min` (default 60 min) | Clasificar decisiones del Decisor con MFE/MAE forward → `decision_outcomes`. |
| Post-mortem LLM | encadenado al final de Outcome Attribution (mismo tick) | Analizar outcomes negativos → `lesson_raw` + normalizador → candidatos o Bloque K. |

---

## 4. Mapa de capacidades funcionales

### F1. Recolección de datos de mercado

- Captura OHLCV de Binance vía CCXT para los timeframes **1m, 5m, 15m, 1h, 4h** (~250 velas cada uno).
- Mantiene en memoria un snapshot de **order book enriquecido**: top 20 niveles vía CCXT WS, spread, imbalance, walls, y profundidad acumulada en cuatro bandas (±0.1%, ±0.25%, ±0.5%, ±1%) con estimación de impacto en el precio medio.
- Calcula **indicadores técnicos por timeframe** (conjunto extendido v1.3):
  - **Tendencia/momentum**: RSI (Wilder), MACD(12/26/9), EMA 9/20/50/200, ADX(14) con +DI/−DI, Stochastic(14,3,3).
  - **Volatilidad/bandas**: Bollinger Bands (20, 2σ), ATR con *winsorización* de TR (cap a 3× mediana móvil), percentil ATR 30 días.
  - **Precio**: VWAP intradiario con reset UTC y bandas ±1σ/±2σ, estructura de precio (HH/HL/LH/LL, últimas 20 velas), ratios mecha/cuerpo (últimas 3 velas).
  - **Volumen**: OBV (slope 20), delta de volumen aproximado (Lee-Ready), ratio volumen vs. media 20.
  - **Niveles**: Pivot Points clásicos diarios (PP, R1/R2/R3, S1/S2/S3).
- Genera **etiquetas interpretativas** (`labelers.py`) junto a cada valor numérico: `rsi_label`, `macd_label`, `trend_label`, `stoch_label`, `vwap_label`, `structure_label`. Son sugerencias de interpretación estándar; el LLM puede discrepar si el contexto completo lo justifica.

### F2. Toma de decisiones (Decisor)

- Cada `decisor_interval_min` minutos arma un **contexto estructurado en bloques** (A–K) que incluye:
  - **A**: Perfil operativo (SCALPING / HÍBRIDO / DAY_TRADING) derivado de `decisor_interval_min` y `atr_timeframe`.
  - **B**: Market Snapshot (precio, cambios %, ATR con expansión/contracción, rango SL válido).
  - **C**: Indicadores por timeframe en orden de prioridad según perfil, con etiquetas interpretativas.
  - **D**: Niveles clave (EMAs 1h/4h, VWAP, Pivot Points, highs/lows 24h, walls).
  - **E**: Order book enriquecido (depth a 4 bandas, estimación de impacto, imbalance).
  - **F**: Alineación cross-timeframe (resumen de consenso/divergencia entre TFs).
  - **G**: Últimas 3 decisiones con detalle de `coherence_warnings` del ciclo anterior (para que el LLM aprenda de sus inconsistencias pasadas).
  - **H**: Estado del portfolio (capital, P&L, drawdown, posiciones abiertas).
  - **I**: Config de riesgo compacta (todos los parámetros numéricos en una vista).
  - **J**: Playbook activo.
- Llama al LLM Decisor con *system prompt* + *user prompt* renderizados con el contexto enriquecido.
- Espera **JSON estricto** validado por Pydantic (`DecisorOutput`).
- **Sin overrides deterministas**: el LLM tiene autonomía total sobre `action` y `position_size_pct`. El sistema no sobrescribe su decisión (salvo el Risk Gate R1–R10 y el CoherenceChecker en `strict_mode`).
- Persiste **toda** la decisión (input + output + `coherence_warnings` + `two_pass_triggered`) en la tabla `decisions`.
- El campo `reasoning` (max 1000 chars) usa un **formato estructurado de 5 secciones**. Ejemplo:

  ```
  [DECISIÓN]: Esperar (HOLD) — no hay señales suficientes para una entrada segura.
  [MERCADO]: Lateral (RANGE) — precio sin dirección clara entre soportes y resistencias.
  [SEÑALES]: 2 detectadas: (A) RSI 15m saliendo de sobreventa; (H) toque de soporte.
  [CONFIANZA]: 56% — estructura alcista pero ADX bajo, confirmar tendencia.
  [NIVELES]: SL: $83,200 | TP: $85,000 | R:R: 1.8×
  ```

### F2.bis. Autonomía del Decisor (qué decide el LLM vs. qué decide el sistema)

El Decisor es **LLM-centric**: el LLM toma todas las decisiones de trading con autonomía total. Las únicas capas que pueden modificar o bloquear su output son el **Risk Gate** (reglas de riesgo absolutas R1–R10, §F3) y el **CoherenceChecker** en modo `strict` (§F2.bis.5).

#### F2.bis.1 Reparto de responsabilidades

| Campo del output | Decide | Margen real | Capa que lo puede pisar |
|------------------|--------|-------------|-------------------------|
| `regime` | LLM | Libre dentro del enum `TRENDING_UP / TRENDING_DOWN / RANGE / HIGH_VOLATILITY / NEUTRAL`. | Pydantic (validación de enum). |
| `confluences` | LLM | Subset del catálogo **A–H** (fijo) + letras **I–Z** activas en `confluence_registry`. Mínimo 2 para BUY (guía). Códigos no válidos se filtran silenciosamente. | `_filter_confluence_codes` en `decisor.py`. |
| `action` | **LLM** | Libre `BUY / SELL / HOLD` sin overrides deterministas. SELL requiere posición abierta (R6/R7). | Risk Gate R6/R7. CoherenceChecker en strict_mode. |
| `confidence_base` y `confidence` | LLM | El LLM calcula y declara su propia confianza. El sistema no fuerza HOLD por umbral. | Solo el Risk Gate (R1–R10) puede bloquear la acción. |
| `confidence_adjustment` | LLM | Bounded a `[-0.10, +0.10]`. Justificación obligatoria. | Pydantic (cap absoluto). |
| `stop_loss` / `take_profit` | LLM | Libre dentro de bandas: SL ∈ `[sl_atr_multiplier × ATR, sl_atr_max_multiplier × ATR]`, R:R ≥ `min_rr_ratio`. | Risk Gate R2/R3/R4/R5/R10. |
| `position_size_pct` | **LLM** | Libre entre `[min_position_size, max_position_pct]`. El LLM debe justificar su elección en `reasoning`. | Risk Gate R1 (cap a `max_position_pct`). |
| `expected_holding_min` | LLM | Libre `≥ 1`. Debe ser coherente con el perfil operativo (Bloque A). CoherenceChecker C6. | Solo CoherenceChecker (warning). |
| `reasoning` | LLM | Libre dentro del formato de 5 secciones. Truncado silencioso a 1000 chars. | — |

#### F2.bis.2 Lo que el LLM **no** puede hacer, por construcción

1. **No puede violar R1–R10**: aunque produzca SL inválido, R:R bajo, size excesivo, o pida BUY con `kill_switch=true`, el Risk Gate lo bloquea y persiste `rejected_reason`. Esta es la **única barrera hard-blocking** sobre la `action`.
2. **No puede inventar confluencias nuevas ad hoc**: el catálogo A–H está fijo en el system prompt; letras I–Z solo son válidas si están promovidas y listadas en `{confluence_registry_block}`; cualquier otro código se descarta.
3. **No puede shortear**: spot-only; SELL solo cierra una posición LONG existente (R6/R7).
4. **No puede forzar un `position_size_pct > max_position_pct`**: el Risk Gate (R1) bloquea el trade si excede el límite.

#### F2.bis.3 Lo que el LLM **sí** decide con autonomía real (v1.3)

- **Clasificar el régimen** del mercado y actuar contra él si ve confluencias suficientes (e.g., BUY en TRENDING_DOWN con confirmación multi-TF).
- **Elegir qué confluencias A–H e I–Z activas** declarar (mínimo 2 para BUY).
- **Decidir la `action`** BUY/SELL/HOLD sin override de confidence-floor ni de régimen.
- **Elegir `position_size_pct`** dentro de `[min_position_size, max_position_pct]` según su lectura de riesgo/oportunidad.
- **Ubicar SL y TP exactos** dentro de las bandas ATR y del rango R:R configurado, priorizando soportes/resistencias y walls del order book.
- **Asignar `confidence_adjustment` ∈ [-0.10, +0.10]** con justificación.
- **Decidir SELL anticipado** sobre una posición abierta cuando se invalida la tesis original.

#### F2.bis.4 Jerarquía declarada de decisión

Reproducida en el system prompt del Decisor (orden de precedencia descendente):

1. **Reglas absolutas R1–R10** (Risk Gate — no negociables).
2. **Parámetros de riesgo** (umbrales, multiplicadores, factores numéricos — contexto informativo para el LLM).
3. **Playbook activo** (guía cualitativa táctica y de aprendizaje histórico).
4. **Confluencias técnicas** del ciclo actual (evidencia del mercado en este momento).

#### F2.bis.5 CoherenceChecker — detección de inconsistencias del LLM

Componente post-LLM que verifica la coherencia lógica entre lo que el LLM **declara** (régimen, confluencias, reasoning) y lo que los **indicadores muestran** en ese ciclo. Ejecuta 8 reglas:

| Regla | Verifica |
|-------|----------|
| C1 | RSI no está en zona de sobreventa cuando el LLM declara confluencia A (RSI_OVERSOLD_BOUNCE). |
| C2 | MACD no cruza al alza cuando el LLM declara confluencia B (MACD_BULLISH_CROSS). |
| C3 | ADX bajo o EMAs no alineadas cuando el LLM declara TRENDING_UP. |
| C4 | Confianza alta (≥0.85) con menos de 2 confluencias declaradas. |
| C5 | BUY con confianza baja (<0.60) sin justificación explícita en reasoning. |
| C6 | `expected_holding_min` fuera del rango del perfil operativo derivado (Bloque A). |
| C7 | R:R real calculado en código ≤ `min_rr_ratio` (siempre **critical**). |
| C8 | Confluencia extendida I–Z declarada pero `verify_spec` del registry no cumple en el ciclo (warning). |

Por defecto, los warnings son **solo informativos**: se persisten en `decisions.output.coherence_warnings` y se inyectan en el Bloque G del **próximo ciclo** para que el LLM aprenda de sus propias inconsistencias. No bloquean la decisión.

Con `COHERENCE_STRICT_MODE=true` (configurable), los warnings de C1/C2/C3 se vuelven **críticos** y fuerzan HOLD.

#### F2.bis.6 Two-pass — auto-revisión dentro del ciclo

Si el CoherenceChecker detecta warnings de C1, C2 o C3 (inconsistencias factuales), el Decisor realiza una **segunda llamada al LLM** en el mismo ciclo. El LLM recibe su propia decisión + los warnings y puede:

- **Corregir** su decisión si reconoce el error.
- **Mantenerla con justificación** explícita (`[REVISADO-MANTENIDO]` en reasoning), explicando por qué la inconsistencia detectada no invalida su análisis.

El resultado del segundo pass es la decisión final. El campo `two_pass_triggered` en el output queda registrado para auditoría. Controlable vía `TWO_PASS_ENABLED` (default: `true`).

### F3. Validación de riesgo (Risk Gate)

Antes de ejecutar la decisión, el Risk Gate verifica reglas absolutas (ver `05-risk-and-safety.md`). Si cualquiera falla → `decision.executed = false` y `rejected_reason` con el motivo. `HOLD` siempre pasa.

### F4. Ejecución de órdenes

- **BUY**: orden market con `quoteOrderQty = usdt_balance × position_size_pct`. Crea un par **bracket** SL/TP en Binance (STOP_LOSS_LIMIT + LIMIT).
- **SELL**: orden market que cierra una posición abierta. Registra `pnl_usdt`, `pnl_pct`, `fees_usdt`, `close_reason`.
- **Bracket fill por Binance**: si Binance ejecutó el SL o TP sin participación del bot, el `OrderTracker` detecta el fill, calcula `close_reason` (`sl_triggered` / `tp_triggered` / `bracket_fill`) y actualiza la BD sin emitir órdenes adicionales.
- **Cierre manual**: el operador puede solicitar cerrar un trade desde la UI (`POST /api/trades/{id}/close`); el `OrderTracker` lo procesa en su próximo ciclo.

### F5. Aprendizaje (Supervisor)

- Diariamente analiza últimas 24 h:
  - Trades cerrados, win rate, profit factor, P&L, drawdown.
  - Distribución de acciones (BUY/SELL/HOLD).
  - Cantidad de rechazos del Risk Gate.
  - Resumen de mercado (precio open/close, low/high, ATR, label de volatilidad).
- Modo **`diagnostic`** si hay menos trades cerrados que `min_trades` (5): genera diagnóstico (mercado lateral/bajista, playbook restrictivo, etc.).
- Modo **`normal`** con suficientes trades: optimiza el playbook.
- **Decisión binaria en dos fases (ver §F5.bis.5)**:
  1. **Evaluación** — Primera llamada LLM corta (JSON estricto) responde si el playbook activo sigue siendo válido (`ratify`) o si requiere regeneración (`regenerate`). Antes de consultar al LLM, se evalúan **guardrails determinísticos** (`max_playbook_age_days`, delta WR, cambio de régimen, kill switch disparado) que pueden forzar regeneración sin opinión del LLM.
  2. **Regeneración** (sólo si la fase 1 resolvió `regenerate`) — Segunda llamada LLM produce el nuevo `PlaybookVersion` (`active=true`, anteriores `active=false`).
- Si la fase 1 resuelve **`ratify`**: **no** se inserta nueva versión. El playbook activo se mantiene y la actividad queda auditada en `decisions` (`agent="supervisor"`, `output.ratified=true`, `output.ratify_reason`).
- **Llamada LLM adicional** para sugerencias de configuración (independiente del resultado anterior): propone valores nuevos para parámetros dentro de `_SAFE_BOUNDS`.
- **Promoción de confluencias** (fase 4 del ciclo): evalúa candidatos en `confluence_candidates` que cumplen P1–P3 y los promueve a `confluence_registry` asignando letra I–Z. Resultado en `decisions.output.confluence_promotions`. Ver §F5.bis.6.

### F5.bis. Aprendizaje: alcance y límites

El sistema "aprende día a día" en un sentido **acotado y trazable**, no por entrenamiento de pesos. Esta sección define qué significa exactamente "aprender" en este producto, para alinear expectativas con stakeholders.

#### F5.bis.1 Dos lazos de aprendizaje

| Lazo | Frecuencia | Qué cambia | Quién aplica | Reversible |
|------|-----------|------------|--------------|------------|
| Lazo 1 — Playbook | 1×/día (00:00 UTC) o manual. **No siempre produce una nueva versión** (ver §F5.bis.5). | Markdown del playbook activo (`setups`, `patrones a evitar`, `reglas específicas`, régimen esperado). | Supervisor LLM en dos fases: (a) `ratify` mantiene el playbook activo; (b) `regenerate` produce `PlaybookVersion` con `active=true`. | Sí, rollback a versión anterior con 1 click. |
| Lazo 2 — Configuración | 1×/día junto con lazo 1 (siempre se ejecuta, ratifique o no). | Valores numéricos de parámetros dentro de `_SAFE_BOUNDS`. | Supervisor LLM → `ConfigStore.set` con `changed_by="supervisor"`. | Sí, vía `config_history` y override manual desde la UI. |
| Lazo 3 — Confluencias I–Z | Continuo (post-mortem) + 1×/día (promoción Supervisor) + manual (UI). | Patrones compuestos verificables más allá de A–H. | Post-mortem → normalizador → `confluence_candidates` → `confluence_registry`. | Sí, desactivar letra en UI; no recicla letra <30 días. |

#### F5.bis.2 Qué **no** es este aprendizaje

- **No es fine-tuning** del modelo LLM: si el provider actualiza la versión del modelo, no hay continuidad de pesos.
- **No es memoria embeddings / RAG**: no hay vector store; la "memoria" del Decisor es texto markdown + las últimas 3 decisiones inyectadas en el contexto.
- **No introduce confluencias A–H nuevas**: el catálogo base A–H permanece fijo en el prompt. Patrones aprendidos pueden promoverse a letras **I–Z** vía pipeline post-mortem (§F10), con trazabilidad en `confluence_registry` y aprobación Supervisor u operador.
- **No ajusta `daily_stop_pct`, `max_drawdown_pct`, `decisor_interval_min` ni `atr_timeframe`** automáticamente: están explícitamente excluidos del auto-apply del Supervisor.

#### F5.bis.3 Memoria de corto, mediano y largo plazo

| Horizonte | Mecanismo | Ubicación |
|-----------|-----------|-----------|
| Corto plazo (1 ciclo) | Indicadores del ciclo + order book snapshot. | `agents/context_builder.py`. |
| Mediano plazo (últimas 3 decisiones) | Bloque `ULTIMAS DECISIONES` del user prompt. Habilita el cooldown post-SELL. | `agents/context_builder.py`. |
| Largo plazo (24 h+) | Playbook markdown + valores de configuración persistidos + confluencias promovidas I–Z. | `playbook_versions`, `config`, `config_history`, `confluence_registry`. |
| Lecciones recientes (72 h) | Bloque K: lecciones post-mortem normalizadas (remap/guidance). | `decision_outcomes.lesson_normalized`, `ContextBuilder`. |

#### F5.bis.4 Modo `diagnostic` del Supervisor

Si `closed_trades < min_trades` (default 5) en la ventana de 24 h, el Supervisor entra en modo `diagnostic`: en lugar de optimizar prematuramente, **diagnostica la causa** de la ausencia de trades:

- **(a)** Mercado desfavorable → mantener playbook, actualizar contexto.
- **(b)** Playbook demasiado restrictivo → `[FLEX]` reglas específicas.
- **(c)** Risk Gate bloqueando entradas → ajuste vía sistema de configuración.
- **(d)** Poca actividad del Decisor → analizar si el mercado justifica HOLD.
- **(e)** Edge negativo (WR < 30% o PF < 0.8) → `[STRICT]` endurecer criterios.

> Implicancia operativa: en mercados laterales o bajistas prolongados, el playbook puede mantenerse igual varios días seguidos sin que eso sea un defecto. Está documentado como comportamiento esperado y se materializa vía la fase de **ratificación** (§F5.bis.5), que evita inflar `playbook_versions` con copias casi idénticas.

#### F5.bis.5 Ratificación del playbook (fase 1 del Lazo 1)

El Supervisor no está obligado a generar una nueva versión por ciclo. Antes de consultar al LLM para regenerar, resuelve un veredicto binario `ratify | regenerate` siguiendo el orden:

1. **Guardrails determinísticos (cortocircuito)** — fuerzan `regenerate` sin consultar al LLM si se cumple alguna:
   - `days_since_active >= max_playbook_age_days` (default `7`). Garantiza que ningún playbook quede activo indefinidamente.
   - `abs(win_rate_24h − playbook.win_rate_baseline) > playbook_force_regen_wr_delta_pct` (default `15`). Detecta degradación o mejora material vs. la línea base que justificó el playbook activo.
   - Cambio de régimen estructural entre el playbook activo y la métrica 24h.
   - Kill switch activado en algún momento del período.
   - Modo `diagnostic` con causa identificada `(b)` (playbook restrictivo) o `(e)` (edge negativo) — ver §F5.bis.4.
2. **Consulta LLM (sólo si ningún guardrail cortocircuita)** — prompt corto con JSON estricto `{ratify: bool, reason: str, suggested_change_summary?: str }`. El LLM decide si las métricas y el contexto del período justifican rediseñar el playbook.

Persistencia según el veredicto:

| Veredicto | `playbook_versions` | `decisions` (siempre) | Evento WebSocket |
|-----------|---------------------|------------------------|------------------|
| `ratify` | sin cambios | 1 fila `output.ratified=true, ratify_reason, mode` | `supervisor_ran` |
| `regenerate` | nueva fila `active=true`, anteriores `active=false` | 1 fila `output.ratified=false, force_regen_reason \| null, playbook, mode` | `playbook_updated` |

> Auditoría: el operador siempre puede ver que el Supervisor corrió (vía `decisions` o `/api/decisions?agent=supervisor`), independientemente de si generó una versión nueva.

#### F5.bis.6 Promoción de confluencias extendidas (I–Z)

Criterios para pasar de `confluence_candidates` → `confluence_registry` (todos deben cumplirse):

| # | Criterio | Default | Verificador |
|---|----------|---------|-------------|
| P1 | Mismo `pattern_tag` con ≥ N ocurrencias en ventana | 3 en 7 días | SQL + config |
| P2 | `verify_spec` completo y testeable (keys en ctx del Decisor) | — | `verify_spec_testable()` |
| P3 | No es subcaso trivial de A–H | — | Normalizador (ruta `candidate`) |
| P4 | Ratificación Supervisor u operador | ciclo diario o manual | Supervisor auto + UI `/confluence` |
| P5 | Sin conflicto con reglas `[STRICT]` del playbook | — | Heurística en código |
| P6 | Máximo de letras activas simultáneas | 5 (`confluence_registry_max_active`) | SQL count |

Asignación de letra: siguiente libre en I–Z no reservada (incluye letras desactivadas hace <30 días). Formato JSON del Decisor: **una letra** (`"I"`), nunca `"I:slug"`.

### F6. Control operativo (Web)

| Capacidad | Endpoint | Quién puede |
|-----------|----------|-------------|
| Ver estado del bot | `GET /api/health` | Cualquiera |
| Ver balance del exchange | `GET /api/balance` | Operador |
| Listar/cerrar trades | `GET /api/trades`, `POST /api/trades/{id}/close` | Operador |
| Listar decisiones | `GET /api/decisions` | Operador |
| Ver posiciones abiertas | `GET /api/positions` | Operador |
| Stats del día | `GET /api/stats/daily` | Operador |
| Stats del decisor (v1.3) — rechazos por rule_id, warnings C1–C6, histogramas confidence/sizing | `GET /api/decisions/stats?window=24` | Operador |
| Playbook activo / historial / activar versión / editar contenido | `GET /api/playbook/...`, `POST /api/playbook/{v}/activate`, `PATCH /api/playbook/{v}/content` | Operador |
| Configuración (60+ parámetros tipados) | `GET /api/config`, `PUT /api/config/{key}` | Operador |
| Kill Switch | `POST /api/kill-switch` | Operador |
| Cambiar modo PAPER ↔ LIVE | `POST /api/mode` (requiere frase `CONFIRMO TRADING REAL`) | Operador |
| Disparar Supervisor manualmente | `POST /api/supervisor/run` | Operador |
| Reset circuit breaker operacional | `POST /api/circuit-breaker/reset` | Operador |
| Reset ancla de drawdown (high-water mark) | `POST /api/drawdown/reset` | Operador |
| Outcomes contrafactuales del Decisor | `GET /api/decisions/outcomes` (`include_lessons=true` opcional) | Operador |
| Candidatos / catálogo confluencias I–Z | `GET /api/confluence/candidates`, `GET /api/confluence/registry`, `POST …/promote`, `POST …/reject`, `POST …/deactivate` | Operador |
| Historial ratificaciones Supervisor | `GET /api/supervisor/runs` | Operador |
| Velas OHLCV (chart) | `GET /api/ohlcv?timeframe=&limit=` | Operador |
| Sugerencias de configuración pendientes | `GET /api/config/suggestions` | Operador |

### F7. Dashboard en vivo

Páginas (React + Tailwind):

- **`/` Dashboard** — balance Binance, posiciones abiertas, última decisión, estado del día (P&L realizado/no realizado, trades, decisiones).
- **`/trades`** — listado, filtros por status, botón cerrar.
- **`/decisions`** — historial detallado con input/output JSON.
- **`/confluence`** — cola de candidatos post-mortem, catálogo I–Z activo, promover/rechazar/desactivar.
- **`/playbook`** — markdown del playbook activo + historial con rollback y edición inline.
- **`/config`** — formulario de los ~60 parámetros tipados; incluye sección **Post-mortem — Aprendizaje** (provider LLM, fallback chain, Bloque K) además de Decisor/Supervisor.
- **`/health`** — estado de motor, DB, Binance.

WebSocket `/ws` empuja:
- `ticker` (precio BTC/USDT cada 5 s vía REST público).
- `decision` (nueva decisión persistida).
- `positions` (snapshot de posiciones abiertas cada 2 s, incluye SL/TP y P&L proyectado).
- `trade_opened` / `trade_closed` (evento por cada cambio de estado de un trade).
- `playbook_updated` (nueva versión en `playbook_versions`).
- `supervisor_ran` (cada ejecución del Supervisor, ratifique o regenere).
- `kill_switch_triggered` (cambio de estado del kill switch).

### F9. Outcome attribution (aprendizaje contrafactual)

Job periódico que evalúa **ex post** cada decisión del Decisor usando velas OHLCV 1m y el trade asociado (si existe). No modifica decisiones pasadas; alimenta al Supervisor y al operador con señales de calidad.

- **Horizonte**: `outcome_attribution_horizon_min` (default 240 min). Decisiones más recientes quedan `PENDING` hasta madurar.
- **Métricas forward**: `forward_return_pct`, `mfe_pct`, `mae_pct`, `time_to_mfe_min`, `time_to_mae_min`.
- **Clasificaciones**: `GOOD_BUY`, `BAD_BUY`, `GOOD_HOLD`, `MISSED_OPPORTUNITY`, `BLOCKED_GOOD_TRADE`, `CORRECTLY_BLOCKED`, `PENDING`, `UNKNOWN`.
- **Persistencia**: tabla `decision_outcomes` (1:1 con `decisions.id`, UPSERT idempotente).
- **Exposición**: `GET /api/decisions/outcomes` y bloque `outcome_attribution` en `GET /api/health`.

### F10. Aprendizaje desde post-mortem (lecciones de malas decisiones)

Pipeline encadenado al job de outcome attribution (mismo intervalo; no job separado):

```text
outcome_attribution → post-mortem LLM → normalizador → remap | candidate | guidance
  ├─ remap/guidance → Bloque K (Decisor user prompt)
  └─ candidate      → confluence_candidates (upsert por pattern_tag)
       └─ Supervisor / operador → confluence_registry (I, J, K…)
```

**Elegibilidad post-mortem** (clasificaciones): `BAD_BUY`, `BAD_SELL`, `MISSED_OPPORTUNITY`, `BLOCKED_GOOD_TRADE`.

**Granularidad LLM**: **1 decisión elegible = 1 llamada LLM** (no batching). Cada análisis recibe el snapshot completo `decisions.input` + output + métricas forward de esa decisión. Motivo: calidad del razonamiento causal, trazabilidad 1:1 en BD y aislamiento de fallos de parseo.

**Throughput por tick**: `postmortem_max_per_tick` (default **5**) = máximo de **decisiones** analizadas por ejecución del job (no llamadas extra por decisión). Las elegibles se ordenan por `severity_score` descendente; el resto espera ticks futuros. Con intervalo 60 min → hasta ~5 post-mortems/hora.

**Providers LLM** (configurables en `/config`, igual patrón que Decisor/Supervisor):
- Primary: `postmortem_provider` (default `gemini-2.5-flash`).
- Fallback: `postmortem_fallback_providers` (CSV ordenado; default modelos Groq livianos + sin duplicar el primary).

**Robustez**:
- `coerce_lesson_raw()` normaliza JSON del LLM (arrays como strings, `proposed_pattern.tag` faltante) antes de validar con Pydantic.
- Reintento: outcomes con `postmortem_status = failed` o sin status vuelven a la cola; tras **3 intentos** fallidos quedan `failed` permanentes (`lesson_raw._meta.attempts`).

**Salidas persistidas** en `decision_outcomes`:
- `postmortem_status`: `completed` | `failed` | `null`
- `lesson_raw`: JSON del PostMortemAgent (validado Pydantic)
- `lesson_normalized`: ruta `remap` | `candidate` | `guidance` + `block_k_line`, `dedupe_key`, payloads

**Bloque K** (user prompt del Decisor, entre Bloque G y H): hasta `block_k_max_lines` (default 5) lecciones deduplicadas por `dedupe_key`, ventana `block_k_window_hours` (default 72 h). Solo rutas `remap` y `guidance` (no `candidate`).

**Bloque dinámico `{confluence_registry_block}`**: definiciones operacionales de letras I–Z activas, mismo estilo que A–H en system prompt.

**Kill switch operativo**: `postmortem_enabled` (default `true`).

> Diseño detallado: `docs/superpowers/specs/2026-05-24-decision-postmortem-learning-design.md`

### F11. Notificaciones Telegram (opcional)

Si `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` están configurados, el engine envía alertas push ante eventos críticos: kill switch activado/desactivado, pausa del engine (circuit breaker), rachas de rechazos del Risk Gate, y trades cerrados con P&L significativo.

### F8. Gráfico de precios en vivo (Chart BTC/USDT)

Componente full-width integrado en la página principal del Dashboard (ruta `/`), implementado con **TradingView Lightweight Charts**.

**Candlesticks:**
- Lee velas desde `GET /api/ohlcv?timeframe=&limit=300`.
- Selector de timeframe: `1m / 5m / 15m / 1h / 4h`. Default dinámico: coincide con `config.decisor_interval_min`.
- Vela en formación actualizada en tiempo real via evento WS `ticker` (cada 5 s): ajusta `close`, `high`, `low` sin re-render del chart. Al cruzar el bucket del TF, hace refetch incremental.

**Órdenes superpuestas (L1 — must have):**
- **Líneas horizontales** por cada posición abierta:
  - Entry: azul, línea punteada.
  - Stop Loss: rojo, línea sólida.
  - Take Profit: verde, línea sólida.
  - Cada línea tiene label con precio en el eje de precios derecho.
- **Marcador de entrada** (flecha azul debajo de la vela) en el `ts_open` de cada posición abierta.

**Trades cerrados (L2):**
- Marcadores en los últimos 50 trades cerrados: círculo en `ts_open` + flecha en `ts_close`.
- Color verde si PnL ≥ 0 (win), rojo si PnL < 0 (loss). Texto = PnL en USD.

**Indicadores técnicos (L3):**
- **EMAs 20 / 50 / 200**: calculadas en frontend sobre la serie de velas visible (misma fórmula EWM que el engine).
- **Bollinger Bands (20, 2σ)**: banda superior, media (punteada), banda inferior.
- **Marcadores de decisiones del Decisor** (últimas 80): flecha ejecutada vs. círculo semi-transparente si fue bloqueada por Risk Gate. Las decisiones HOLD se omiten.
- **Volume pane**: barras en el 18% inferior del chart, coloreadas por dirección de vela.

**Controles:**
- Selector de TF con resaltado de la selección activa.
- Toggles para activar/desactivar capas: `EMAs`, `BB`, `Trades cerrados`, `Decisiones`.
- Precio en vivo del WS `ticker` en el header del componente.
- Leyenda de colores en el pie del componente.

---

## 5. Flujos operativos clave

### 5.1 Ciclo del Decisor (cada `decisor_interval_min`)

```
1. ¿engine_paused? → log "engine.paused" y salir.
2. ¿supervisor_run_now=true? → ejecutar Supervisor y resetear flag.
3. Leer config runtime (mode, max_position_pct, daily_stop_pct,
   provider/fallbacks, calibración completa).
4. PriceCollector: fetch_ohlcv para 1m/5m/15m/1h/4h + compute_indicators.
5. FeeManager: get_or_refresh (cada 24 h).
6. fetch_balance → BalanceSnapshot (fallback DB si exchange caído).
7. ContextBuilder.build(orderbook, balances, playbook, calibración, …).
8. Decisor.decide → LLM → parseo JSON → DecisorOutput validado.
9. CoherenceChecker.evaluate → two-pass opcional si C1/C2/C3.
10. Persistir Decision (input + output + coherence_warnings + two_pass_triggered).
11. RiskGate.validate(decision, current_price, atr_ref, balances, kill_switch, daily_pnl, drawdown, …).
12. Si rechazado → update rejected_reason y return.
13. Si BUY → Executor.execute_buy (market + OCO bracket SL/TP).
    Si SELL → Executor.execute_sell sobre la primera posición abierta.
    Si HOLD → nada.
```

### 5.2 Ciclo del Supervisor (00:00 UTC o `supervisor_run_now=true`)

```
1. Compute metrics 24h (trades, decisiones, P&L, regime, ATR, vol_label).
2. Si closed_trades < min_trades → mode = "diagnostic", inyectar header.
3. Fase 1 — Evaluación (§F5.bis.5):
   a. Guardrails determinísticos (max_playbook_age_days, delta WR,
      cambio de régimen, kill switch, modo diagnostic con causa b|e).
      Si alguno cortocircuita → verdict = "regenerate" con force_regen_reason.
   b. Caso contrario → Llamada LLM Supervisor #1 (JSON estricto):
        { ratify: bool, reason: str, suggested_change_summary?: str }
4. Fase 2 — Regeneración (sólo si verdict == "regenerate"):
   a. Llamada LLM Supervisor #2 → playbook markdown nuevo.
   b. Guardar PlaybookVersion (version = prev+1, active=true, anteriores false).
5. Llamada LLM #3 → sugerencias de configuración estructuradas (JSON).
6. Aplicar sólo claves dentro de _SAFE_BOUNDS; persistir rejected con motivo.
7. Registrar Decision (agent="supervisor"):
   - Si ratify: output={ratified:true, ratify_reason, mode, config_*}.
   - Si regenerate: output={ratified:false, force_regen_reason|null,
                            playbook, mode, config_*}.
```

### 5.3 Cierre de un trade

Tres rutas, todas convergen en `Trade.status = "closed"`:

1. **Decisor emite SELL** → `Executor.execute_sell` (market order) → reason `decisor_sell`.
2. **Binance ejecutó SL/TP** → `OrderTracker.poll_once` detecta fill → `record_bracket_fill` → reason `sl_triggered` / `tp_triggered` / `bracket_fill`.
3. **Operador solicita cierre** desde UI → `close_requested=true` → en el próximo tick del OrderTracker, `execute_sell` con reason `manual_close`.

### 5.4 Onboarding paper → LIVE (roadmap de 6 pasos)

| Paso | Gate |
|------|------|
| 1. Binance Testnet keys configuradas | Sin errores de auth en logs. |
| 2. LLM keys (Gemini + Groq) configuradas | Decisiones LLM logueadas 48 h sin errores. |
| 3. Backtest 90 d | Sharpe > 1.0, DD < 10%, WR > 48%, PF > 1.3. |
| 4. Paper trading 4 semanas | Cada semana: Sharpe > 1.0, DD < 5%, WR > 52%, PF > 1.5, errores LLM < 1%, sin semana con DD > 3%. Si falla, reinicia el contador. |
| 5. API keys Mainnet | Permisos mínimos (sin retiros, sin margin), IP restringida. |
| 6. Switch LIVE | `POST /api/mode {mode:"LIVE", confirmation:"CONFIRMO TRADING REAL"}`. Capital inicial $200–500 USDT. |

---

## 6. Reglas de negocio

### 6.1 Mercado y producto

- **Único par**: `BTC/USDT`. Spot. No futuros, no margin, **nunca shorts** (R7).
- **Capital inicial recomendado LIVE**: $200–500 USDT. Hasta 8 semanas LIVE no incrementar.

### 6.2 Riesgo (resumen — detalle en `05-risk-and-safety.md`)

| Parámetro | Default | Función |
|-----------|---------|---------|
| `max_position_pct` | 0.10 | Máximo % del capital por trade. |
| `max_simultaneous_trades` | 2 | Posiciones abiertas en paralelo. |
| `daily_stop_pct` | -0.03 | Si P&L del día ≤ −3% → HOLD forzado. |
| `max_drawdown_pct` | -0.10 | Si drawdown total ≤ −10% → kill switch. |
| `min_rr_ratio` | 1.3 | Reward/risk mínimo para aprobar BUY. |
| `sl_atr_multiplier` | 0.3 | Distancia SL mínima como múltiplo de ATR. |
| `sl_atr_max_multiplier` | 1.5 | Distancia SL máxima como múltiplo de ATR. |
| `min_fees_to_tp_ratio` | 3.0 | El movimiento al TP debe cubrir ≥3× los fees round-trip (R10). |

### 6.3 Confianza y sizing

- El LLM declara su propia `confidence` (0.0–1.0). El sistema **no** fuerza HOLD por umbral de confianza ni sobrescribe el sizing.
- Los umbrales `conf_threshold_*` se mantienen en config como **referencia para el LLM** (inyectados en el prompt), pero el LLM puede desviarse con justificación explícita en `reasoning`.
- El LLM elige `position_size_pct` ∈ `[min_position_size, max_position_pct]`. Si elige más de `max_position_pct`, el Risk Gate R1 bloquea el trade.
- El **sizing guiado** en el system prompt (no obligatorio) sugiere:
  - Alta confianza (≥0.80) + régimen favorable → hacia `max_position_pct`.
  - Confianza media (0.60–0.79) o régimen neutro → 50–75% de `max_position_pct`.
  - Confianza baja (<0.60) o múltiples warnings de coherencia → abstenerse o tamaño mínimo.

### 6.4 Confluencias técnicas (catálogo cerrado A–H)

| Código | Confluencia | Resumen |
|--------|-------------|---------|
| A | RSI_OVERSOLD_BOUNCE | RSI 15m/1h saliendo de <30 con vela alcista. |
| B | MACD_BULLISH_CROSS | Cruce MACD>Signal 15m/1h con hist creciente. |
| C | EMA_SUPPORT_HOLD | Rebote en EMA20/50/200 (1h o 4h) con mecha. |
| D | BB_LOWER_REVERSAL | BB% 5m <5 con vela de reversión. |
| E | ORDERBOOK_BID_PRESSURE | Imbalance > 0.6 + bid wall < 0.3% del precio. |
| F | BREAKOUT_VOL_CONFIRMED | Ruptura con volumen > 1.5× media 20. |
| G | HIGHER_TF_ALIGNMENT | RSI 4h >50 + EMA20_4h > EMA50_4h + precio > EMA20_1h. |
| H | RANGE_SUPPORT_TOUCH | Precio en banda inferior de rango definido. |

- Mínimo de confluencias para BUY: **2** (`min_confluences_buy`).
- Cooldown post-SELL: **15 min** (`cooldown_after_sell_min`).

### 6.4.bis Confluencias extendidas (I–Z)

Letras **I–Z** no están fijas en el prompt: se generan dinámicamente desde `confluence_registry` cuando un patrón aprendido es promovido (§F5.bis.6, §F10).

| Aspecto | Regla |
|---------|-------|
| Origen | Post-mortem → normalizador ruta `candidate` → `confluence_candidates` → promoción |
| Formato Decisor | Una letra (`"I"`), nunca `"I:slug"` |
| Validación | `_filter_confluence_codes()` acepta A–H + letras activas en registry; C8 audita `verify_spec` |
| Límite activas | `confluence_registry_max_active` (default 5) |
| Desactivación | UI `/confluence` o SQL; letra reservada 30 días tras desactivar |

### 6.5 Régimen de mercado

| Régimen | Guía para el LLM | Observación |
|---------|-----------------|-------------|
| TRENDING_UP | BUY preferido con 2+ confluencias y buen R:R. | Régimen más favorable. |
| RANGE | BUY solo cerca de soporte sólido con confirmación de volumen. | Mayor exigencia de confluencias. |
| HIGH_VOLATILITY | SL amplio (hacia `sl_atr_max_multiplier`×ATR); sizing conservador. | Riesgo de fakeout. |
| TRENDING_DOWN | BUY **desaconsejado**; el LLM puede aun así ejecutarlo si hay confluencias multi-TF muy fuertes y lo justifica explícitamente. HOLD/SELL preferido. | Sin override; el LLM es responsable de la decisión. |
| NEUTRAL | Sin sesgo direccional claro; priorizar HOLD. | — |

> Nota: TRENDING_DOWN ya no fuerza HOLD automáticamente. El LLM puede operar contra el régimen declarando las confluencias que respaldan su tesis (e.g., divergencia alcista, soporte estructural fuerte). El CoherenceChecker C3 auditará la coherencia de esa decisión.

### 6.6 Reglas de absoluta seguridad

1. **Kill switch** activo → solo se permiten SELL para cerrar posiciones. Cualquier BUY se rechaza.
2. **Cambio de modo a LIVE** exige confirmación exacta `CONFIRMO TRADING REAL` en el body del request.
3. **Operación spot-only**: nunca short, nunca margin, nunca retiros desde API key.
4. **Configuración del Supervisor**: solo claves dentro de `_SAFE_BOUNDS` se auto-aplican; las críticas (`daily_stop_pct`, `max_drawdown_pct`) están explícitamente excluidas y requieren intervención manual.

---

## 7. Modos de operación

| Modo | Exchange | Riesgo financiero | Activación |
|------|----------|------------------|------------|
| `PAPER_TRADING` (default) | Binance Spot **Testnet** (`testnet.binance.vision`) | Ninguno | `BINANCE_TESTNET=true` |
| `LIVE` | Binance Spot **Mainnet** | Real | `BINANCE_TESTNET=false` + `POST /api/mode {mode:"LIVE", confirmation:"CONFIRMO TRADING REAL"}` |

En testnet, los fees suelen ser 0, por lo que la regla R10 (movimiento TP cubre fees) no aplica automáticamente.

---

## 8. Criterios de aceptación

### 8.1 Funcionales

| Id | Criterio |
|----|----------|
| AC-01 | El Decisor produce 1 decisión por ciclo; siempre persistida en `decisions` (incluso con error). |
| AC-02 | El Decisor **no aplica overrides deterministas de action ni sizing**. Una decisión BUY con `confidence < 0.60` o régimen `TRENDING_DOWN` es válida y se envía al Risk Gate. Si el LLM declara una inconsistencia (e.g., TRENDING_UP sin ADX fuerte), el CoherenceChecker emite warning C3. En `coherence_strict_mode=true`, warnings C1/C2/C3 fuerzan HOLD. |
| AC-03 | Toda decisión BUY que cruce el Risk Gate ejecuta una market order + bracket SL/TP. |
| AC-04 | Toda decisión rechazada queda con `executed=false` y `rejected_reason` poblado. |
| AC-05 | El Supervisor evalúa cada 24 h (`ratify | regenerate`, §F5.bis.5). Si decide `regenerate`, genera una nueva `PlaybookVersion` con `active=true` y desactiva las anteriores. Si decide `ratify`, **no** inserta nueva versión y persiste una `Decision` con `output.ratified=true`. |
| AC-06 | Las sugerencias del Supervisor **fuera de `_SAFE_BOUNDS`** se persisten como rechazadas con `reject_reason`, sin aplicarse. |
| AC-07 | El WebSocket emite eventos `ticker`, `decision` y `positions` continuamente sin requerir polling adicional desde la UI. |
| AC-08 | Cambiar de modo a `LIVE` sin la frase exacta de confirmación responde HTTP 400. |
| AC-09 | Toda escritura en `config` queda registrada en `config_history` con `changed_by`. |
| AC-10 | El operador puede activar una versión anterior del playbook con un click (rollback). |
| AC-11 | Un BUY del LLM con `position_size_pct > max_position_pct` es **rechazado** por el Risk Gate (R1) con `rejected_reason="risk_gate: R1"`. No hay reescritura silenciosa; el LLM debe aprender del rechazo vía el Bloque G del ciclo siguiente. |
| AC-15 | `GET /api/decisions/stats?window=24` retorna rechazos desglosados por `rule_id` (R0–R11), warnings del CoherenceChecker por regla (C1–C8), histogramas de distribución de `confidence` y `position_size_pct`, y tasa de `two_pass_triggered`. |
| AC-16 | `GET /api/health` incluye los bloques `risk_gate`, `coherence`, `outcome_attribution`, `postgres` (table counts + DB size), `llm` (latency p50/p95/p99) y `circuit_breaker`. |
| AC-17 | El job de outcome attribution persiste una fila en `decision_outcomes` por decisión madura; `GET /api/decisions/outcomes` expone clasificación y métricas MFE/MAE. |
| AC-12 | El Supervisor en modo `diagnostic` (con `closed_trades < min_trades`) puede ratificar el playbook activo. Cuando regenera, el nuevo `PlaybookVersion` conserva la estructura obligatoria de secciones y **no** introduce confluencias fuera del catálogo A–H ni valores de parámetros del sistema. |
| AC-13 | El Supervisor fuerza la regeneración del playbook (sin consultar al LLM en la fase 1) cuando se cumple alguno de los guardrails determinísticos: `days_since_active >= max_playbook_age_days`, `abs(wr_24h − wr_baseline) > playbook_force_regen_wr_delta_pct`, cambio de régimen estructural, o kill switch activado en el período. `force_regen_reason` queda registrado en la `Decision`. |
| AC-14 | Cada ejecución del Supervisor (ratifique o regenere) inserta exactamente **una** fila en `decisions` con `agent="supervisor"`. El operador puede auditar la actividad vía `GET /api/decisions?agent=supervisor` aunque no haya nuevas versiones de playbook. |
| AC-18 | Tras outcome attribution, el pipeline post-mortem procesa outcomes elegibles (`BAD_BUY`, `BAD_SELL`, `MISSED_OPPORTUNITY`, `BLOCKED_GOOD_TRADE`) y persiste `postmortem_status`, `lesson_raw` y `lesson_normalized` en `decision_outcomes`. |
| AC-19 | Lecciones `remap`/`guidance` aparecen en Bloque K del Decisor; candidatos `candidate` hacen upsert en `confluence_candidates`. El Supervisor (o operador vía UI) puede promover candidatos a `confluence_registry` cumpliendo P1–P6. |
| AC-20 | La página `/confluence` lista candidatos y registry I–Z; `POST …/promote`, `POST …/reject` y `POST …/deactivate` responden 400 con código estructurado ante reglas de negocio violadas. |
| AC-21 | `postmortem_provider` y `postmortem_fallback_providers` son configurables desde `/config` (select + cadena de fallback). El engine usa la cascada en cada análisis; errores de validación reintentan hasta 3 veces antes de marcar `failed`. |

### 8.2 No funcionales

| Id | Criterio |
|----|----------|
| AC-N1 | Tiempos UTC en BD (`TIMESTAMPTZ`) y en logs JSON. |
| AC-N2 | Montos en `NUMERIC` (precisión exacta). |
| AC-N3 | El engine sigue operativo aunque Binance esté caído: cae a `usdt=0` y `btc` derivado de posiciones abiertas, evitando nuevos BUYs (R1: balance insuficiente). |
| AC-N4 | Si el LLM falla, el cascade de fallbacks intenta hasta 5 providers; tras 5 fallas consecutivas el `CircuitBreaker` pausa el engine. |
| AC-N5 | Si Binance falla 5 ciclos consecutivos para órdenes/balance, el engine se pausa. |
| AC-N6 | UI en español (`es-AR`); formato de números/fechas con locale local. |

---

## 9. Riesgos funcionales y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|------------|
| LLM "alucina" un valor numérico fuera de R0–R11 | Pérdida potencial > límite | Risk Gate determinístico bloquea; CoherenceChecker audita inconsistencias lógicas. |
| Supervisor cambia parámetros críticos | Estrategia degradada | `_SAFE_BOUNDS` excluye `daily_stop_pct` y `max_drawdown_pct`; rollback de playbook a un click. |
| Supervisor "se acomoda" ratificando indefinidamente | Playbook stale en mercado cambiante | Guardrails determinísticos (§F5.bis.5): `max_playbook_age_days` (techo de edad) y `playbook_force_regen_wr_delta_pct` (delta WR) fuerzan regeneración sin opinión del LLM. |
| Velas anómalas en testnet (flash low) inflan ATR | SL/TP irreales | TR winsorizado a 3× mediana móvil. |
| Provider LLM saturado / rate limit | Sin decisión por ciclo | Cascade de fallbacks (Gemini + 5 Groq); `CircuitBreaker` corta tras 5 fallas. |
| Pérdida de conexión a Binance | Trades zombi | `OrderTracker` cada 30 s reconcilia fills; engine pausa tras 5 fallas. |
| Operador olvida un kill switch | Riesgo sostenido | `daily_stop_pct` y `max_drawdown_pct` automáticos detienen actividad sin intervención. |
| Drift entre playbook y parámetros del sistema | Decisiones inconsistentes | Regla obligatoria en system prompt del Decisor: parámetros del sistema prevalecen; el LLM debe loguear `[DRIFT CONFIG]` en `reasoning`. |
| Cambio accidental a LIVE | Pérdida real | Frase de confirmación literal obligatoria en el endpoint `/api/mode`. |

---

## 10. Glosario rápido

- **OHLCV**: Open/High/Low/Close/Volume (candlestick).
- **ATR**: Average True Range, mide volatilidad.
- **R:R**: Reward/Risk ratio = (TP − entry) / (entry − SL).
- **Bracket**: par SL + TP que protege la entrada.
- **Confluencia**: condición técnica del catálogo A–H que justifica un BUY.
- **Playbook**: documento markdown versionado que guía al Decisor; reescrito por el Supervisor.
- **Risk Gate**: capa determinística entre LLM y exchange.
- **Circuit Breaker**: pausa global del engine ante fallas en cadena.
- **Kill Switch**: detención de emergencia que sólo permite SELL.
