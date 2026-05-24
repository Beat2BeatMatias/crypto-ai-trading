# Post-mortem de decisiones + normalizador de lecciones + extensión del catálogo de confluencias

**Fecha:** 2026-05-24  
**Estado:** Borrador (pendiente de aprobación)  
**Autor:** Matías + Claude (sesión de brainstorm)  
**Audiencia:** Implementación + revisores de spec funcional/técnica.  
**Depende de:** Outcome attribution contrafactual entregado (F9, `decision_outcomes`, job hourly).  
**Tema disparador:** "Agregar un agente que analice malas decisiones, deduzca indicadores mal leídos y permita que el Decisor aprenda — eventualmente extendiendo el catálogo de confluencias A–H con patrones promovidos (I, J, K…, mismo formato de una letra)."

---

## 1. Premisa del cambio

> Hoy el outcome attribution **etiqueta** (`BAD_BUY`, `MISSED_OPPORTUNITY`, …) pero no explica **por qué** falló la lectura. El Supervisor agrega métricas agregadas 1×/día al playbook. El Decisor no recibe retroalimentación estructurada de errores recientes. El catálogo A–H es cerrado y no evoluciona con patrones descubiertos en producción.

Solución en **cuatro capas**:

1. **PostMortemAgent (LLM)** — analiza decisiones con clasificación negativa y produce un `lesson` JSON estructurado anclado al snapshot `decisions.input`.
2. **Normalizador** — clasifica cada `lesson` en una de tres rutas: `remap`, `candidate`, `guidance`. **No** promueve a confluencia directamente.
3. **Consumo corto plazo** — Bloque K del Decisor (lecciones activas) + enriquecimiento del prompt del Supervisor.
4. **Promoción a catálogo** — patrones repetidos y verificables pasan a `confluence_registry` como **letras sucesivas** (`I`, luego `J`, `K`, …) con el **mismo formato** que A–H: un carácter en el JSON del Decisor + definición operacional en el prompt.

Principio rector: **una confluencia es señal verificable en T**, no una lección retrospectiva. El post-mortem alimenta aprendizaje; la confluencia es el último paso del pipeline, no el primero.

---

## 2. Estado actual (referencia)

| Componente | Rol hoy | Gap |
|---|---|---|
| `outcome_attribution.py` | Clasificación contrafactual pura | Sin análisis causal |
| `outcome_attribution_job.py` | UPSERT en `decision_outcomes` cada ~60 min | Sin fase LLM |
| `ContextBuilder` | Bloque G: últimas 3 decisiones (sin outcome) | Sin lecciones |
| `decisor.py` | `_VALID_CONFLUENCE_CODES = ABCDEFGH` | Catálogo estático |
| `coherence_checker.py` | C1/C2 verifican A/B; C4 cuenta confluencias | Solo A–H |
| Supervisor | `top_misses_block`, playbook diario | Agregado, no por-decisión |

Clasificaciones elegibles para post-mortem (maduras, no `PENDING`/`UNKNOWN`):

| Clasificación | Prioridad | Motivo |
|---|---|---|
| `BAD_BUY` | Alta | Trade ejecutado con PnL ≤ 0 |
| `BAD_SELL` | Alta | Salida prematura o peor que hold |
| `MISSED_OPPORTUNITY` | Alta | HOLD con MFE ≥ TP y SL no tocado primero |
| `BLOCKED_GOOD_TRADE` | Media | Aprendizaje del Risk Gate / umbral conf |
| `CORRECTLY_BLOCKED` | — | Excluida (no es error) |
| `GOOD_*` | — | Excluidas (refuerzo positivo fuera de alcance v1) |

---

## 3. Arquitectura propuesta

```text
outcome_attribution_tick (existente, determinístico)
        │
        ▼
outcome_postmortem_tick (nuevo, LLM)
   │ query: decision_outcomes WHERE classification IN (BAD_*, MISSED, BLOCKED_GOOD)
   │        AND postmortem_status IS NULL
   │        ORDER BY severity DESC LIMIT N_per_tick
   ▼
   PostMortemAgent → lesson_raw JSONB → persistido en decision_outcomes
        │
        ▼
lesson_normalizer (nuevo: reglas + LLM ligero opcional)
   │ route ∈ { remap | candidate | guidance }
   ▼
   ┌─────────────┬──────────────────┬─────────────────────┐
   │   remap     │    candidate     │      guidance       │
   │ (A–H mal    │ (patrón nuevo,   │ (peso/lectura,      │
   │  aplicada)  │  cola promoción) │  no es señal)       │
   └──────┬──────┴────────┬─────────┴──────────┬──────────┘
          │               │                      │
          ▼               ▼                      ▼
   Bloque K          confluence_candidates   Bloque K +
   Decisor           + Supervisor            playbook (via Sup.)
          │               │
          │               ▼ (si promoción)
          │         confluence_registry
          │         letras I, J, K, …
          └───────────────┴──────────────────► Decisor (catálogo extendido)
```

Jobs:

| Job | Frecuencia | Modelo LLM sugerido |
|---|---|---|
| `outcome_attribution_tick` + `outcome_postmortem_tick` (encadenados) | `outcome_attribution_interval_min` (default 60 min) | Flash / Haiku |

El post-mortem corre **inmediatamente después** de outcome attribution en el mismo wrapper (`outcome_attribution_tick_wrapper`), garantizando que `decision_outcomes` esté actualizado antes del análisis LLM.

Límite de costo: **max 5 post-mortems por tick**, priorizados por `severity_score` (ver §5.3).

---

## 4. Modelo de datos

### 4.1 Extensión de `decision_outcomes` (migration 011)

```sql
ALTER TABLE decision_outcomes ADD COLUMN IF NOT EXISTS
  postmortem_status VARCHAR(16) DEFAULT NULL;
  -- NULL | pending | completed | skipped | failed

ALTER TABLE decision_outcomes ADD COLUMN IF NOT EXISTS
  lesson_raw JSONB DEFAULT NULL;

ALTER TABLE decision_outcomes ADD COLUMN IF NOT EXISTS
  lesson_normalized JSONB DEFAULT NULL;

ALTER TABLE decision_outcomes ADD COLUMN IF NOT EXISTS
  postmortem_at TIMESTAMPTZ DEFAULT NULL;

CREATE INDEX idx_decision_outcomes_postmortem_pending
  ON decision_outcomes (computed_at)
  WHERE postmortem_status IS NULL
    AND classification IN ('BAD_BUY','BAD_SELL','MISSED_OPPORTUNITY','BLOCKED_GOOD_TRADE');
```

### 4.2 Tabla nueva: `confluence_candidates`

Cola de patrones propuestos antes de promoción al catálogo.

```sql
CREATE TABLE confluence_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_tag     VARCHAR(64) NOT NULL,
    proposed_code   CHAR(1),              -- reservado: siguiente letra libre (I, J, …); asignado al promover
    title           VARCHAR(128) NOT NULL,
    definition_md   TEXT NOT NULL,        -- definición operacional
    verify_spec     JSONB NOT NULL,       -- reglas verificables (§6.3)
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    source_decision_ids UUID[] NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'open',
    -- open | promoted | rejected | merged
    promoted_at     TIMESTAMPTZ,
    reject_reason   TEXT,
    UNIQUE (pattern_tag)
);

CREATE INDEX idx_confluence_candidates_status ON confluence_candidates (status, occurrence_count DESC);
```

### 4.3 Tabla nueva: `confluence_registry`

Catálogo extendido más allá de A–H (promovido, activo).

```sql
CREATE TABLE confluence_registry (
    code            CHAR(1) PRIMARY KEY,       -- I, J, K, … (PK = letra emitida por el Decisor)
    slug            VARCHAR(64) NOT NULL UNIQUE, -- identificador interno (trazabilidad DB, no va al JSON)
    title           VARCHAR(128) NOT NULL,       -- ej. VOLUME_DIVERGENCE_RANGE
    definition_md   TEXT NOT NULL,             -- definición operacional (como A–H en decisor_system.txt)
    verify_spec     JSONB NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT true,
    promoted_from   UUID REFERENCES confluence_candidates(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deactivated_at  TIMESTAMPTZ
);
```

**Formato en output del Decisor** — idéntico al catálogo actual: una letra por confluencia.

```jsonc
// Actual
"confluences": ["B", "C", "G"]

// Con patrón promovido (mismo estilo)
"confluences": ["B", "G", "I"]
```

El `slug` vive solo en DB y en auditoría; el LLM **nunca** emite `"I:algo"` ni strings compuestos.

---

## 5. Schemas JSON

### 5.1 `lesson_raw` — salida del PostMortemAgent

Producido por LLM con `json_mode=True`. Validado con Pydantic antes de persistir.

```jsonc
{
  "version": 1,
  "classification": "BAD_BUY",
  "severity_score": 0.82,
  "root_cause_tag": "false_breakout_range",
  "summary": "BUY en RANGE con RSI oversold pero sin alineación 4h; precio revirtió antes de TP.",
  "decision_snapshot": {
    "regime_declared": "RANGE",
    "action": "BUY",
    "confidence": 0.71,
    "confluences_declared": ["H", "A"],
    "reasoning_excerpt": "..."
  },
  "forward_evidence": {
    "mfe_pct": 0.15,
    "mae_pct": -0.42,
    "forward_return_pct": -0.38,
    "time_to_mae_min": 12,
    "time_to_mfe_min": 45
  },
  "misread_indicators": [
    {
      "indicator_key": "rsi_15m",
      "value_at_decision": 32.1,
      "decisor_interpretation": "oversold bounce",
      "correct_interpretation": "oversold en RANGE sin volumen no implica reversión",
      "evidence_from_input": true
    }
  ],
  "ignored_signals": [
    {
      "indicator_key": "block_f_cross_tf.alignment",
      "value_at_decision": "mixed",
      "why_relevant": "TF superiores no alineadas para BUY"
    }
  ],
  "confluence_analysis": {
    "misapplied_codes": ["H", "A"],
    "should_have_used": [],
    "notes": "H válido en touch de soporte; aquí precio no tocó low_24h con confirmación"
  },
  "proposed_pattern": {
    "tag": "range_rsi_without_volume",
    "title": "RSI oversold sin volumen en RANGE",
    "definition_hint": "RANGE + vol_ratio < 0.8 + RSI < 35 → no contar H/A como suficientes",
    "maps_to_existing": null
  },
  "would_change": {
    "action": "HOLD",
    "rationale": "Esperar vol_ratio > 1.0 o alineación 1h+4h"
  },
  "hindsight_guardrails_passed": true
}
```

Reglas del prompt PostMortemAgent:

- Solo citar indicadores presentes en `decisions.input` (`evidence_from_input: true`).
- Prohibido inferir datos no disponibles al momento T.
- `misapplied_codes` ⊆ códigos declarados en `decisions.output.confluences`.
- `proposed_pattern.maps_to_existing` si el patrón ya encaja en A–H mal aplicada.

### 5.2 `lesson_normalized` — salida del Normalizador

```jsonc
{
  "version": 1,
  "route": "remap",  // remap | candidate | guidance
  "pattern_tag": "range_rsi_without_volume",
  "confidence": 0.85,
  "remap": {
    "misapplied_confluences": ["H", "A"],
    "correction": "En RANGE con vol_ratio < 0.8, H y A no alcanzan para BUY; exigir G o F.",
    "maps_to_existing_only": true
  },
  "candidate": null,
  "guidance": null,
  "block_k_line": "[2026-05-23 14:30 UTC] BAD_BUY: en RANGE+vol bajo, no operar rebote RSI sin G/F.",
  "dedupe_key": "remap:range_rsi_without_volume:H,A"
}
```

Ejemplo ruta `candidate`:

```jsonc
{
  "route": "candidate",
  "pattern_tag": "macd_hist_decay_range",
  "confidence": 0.78,
  "remap": null,
  "candidate": {
    "title": "MACD histograma decreciente en RANGE",
    "definition_md": "RANGE + MACD_hist 15m y 1h negativos o decrecientes → veto blando a BUY por rebote RSI.",
    "verify_spec": {
      "all": [
        {"ctx": "regime", "eq": "RANGE"},
        {"ctx": "hist_15m", "lt": 0},
        {"ctx": "hist_1h", "lt": 0}
      ]
    },
    "proposed_code_letter": "I"
  },
  "guidance": null,
  "block_k_line": "...",
  "dedupe_key": "candidate:macd_hist_decay_range"
}
```

Ejemplo ruta `guidance`:

```jsonc
{
  "route": "guidance",
  "pattern_tag": "overweight_1m_in_hibrido",
  "confidence": 0.9,
  "guidance": {
    "type": "tf_weight",
    "message": "Perfil HIBRIDO: priorizar lectura 15m/1h sobre 1m al evaluar confluencias.",
    "applies_when": {"block_a_profile": "HIBRIDO"}
  },
  "block_k_line": "[guidance] HIBRIDO: priorizar 15m/1h sobre 1m.",
  "dedupe_key": "guidance:overweight_1m_in_hibrido"
}
```

### 5.3 `severity_score` (determinístico, pre-LLM)

Calculado en código antes de invocar el PostMortemAgent para priorizar el tick:

| Clasificación | Fórmula base |
|---|---|
| `BAD_BUY` | `min(1, abs(pnl_pct) / 2)` o `abs(mae_pct) / sl_dist_pct` |
| `MISSED_OPPORTUNITY` | `mfe_pct / tp_target_pct` |
| `BAD_SELL` | `abs(opportunity_cost_pct) / tp_target_pct` |
| `BLOCKED_GOOD_TRADE` | `mfe_pct / tp_target_pct × 0.7` |

---

## 6. Normalizador — lógica de las tres rutas

### 6.1 Algoritmo (determinístico primero)

```text
function normalize(lesson_raw):
  if lesson_raw.proposed_pattern.maps_to_existing is not null:
    return route=remap, misapplied=confluence_analysis.misapplied_codes

  if len(misapplied_codes) > 0 AND proposed_pattern is null:
    return route=remap

  if proposed_pattern.tag already in confluence_registry (by slug):
    return route=remap  # ya promovido como letra I/J/K…; recordatorio de uso correcto

  if proposed_pattern.verify_spec can be built with ONLY ctx keys from decision.input:
    return route=candidate

  return route=guidance
```

LLM opcional solo si `confidence < 0.6` tras reglas (disputa entre remap y candidate).

### 6.2 Formato de código extendido — una letra (igual que A–H)

**Decisión:** mantener el formato actual del Decisor. Cada patrón promovido recibe la **siguiente letra libre** en orden alfabético: `I`, `J`, `K`, … hasta `Z`.

| Aspecto | Catálogo fijo (hoy) | Catálogo extendido (propuesto) |
|---|---|---|
| Formato JSON | `"A"`, `"B"`, … `"H"` | `"I"`, `"J"`, … (misma forma) |
| Definición | `decisor_system.txt` estático | `{confluence_registry_block}` dinámico desde DB |
| Naturaleza | Patrón compuesto verificable | Patrón compuesto verificable (no indicador suelto) |
| Validación | `_VALID_CONFLUENCE_CODES` + Coherence C1/C2 | Unión A–H + letras activas en registry |

Ejemplo de bloque inyectado en `decisor_system.txt` (mismo estilo que A–H):

```text
── Confluencias promovidas (activas) ──
I. VOLUME_DIVERGENCE_RANGE — RANGE + vol_ratio < 0.8 + MACD_hist 15m < 0 → no operar rebote RSI aislado.
J. MACD_HIST_DECAY_RANGE   — RANGE + hist 15m y 1h negativos → veto blando a BUY por rebote RSI.
```

Implementación en `decisor.py`:

```python
_VALID_STATIC = frozenset("ABCDEFGH")

def _valid_confluence_codes(active_registry: frozenset[str]) -> frozenset[str]:
    """A–H siempre válidas + letras I–Z activas en confluence_registry."""
    return _VALID_STATIC | active_registry

def _filter_confluence_codes(
    confluences: list[str], active_registry: frozenset[str],
) -> list[str]:
    valid_set = _valid_confluence_codes(active_registry)
    return [c for c in confluences if c in valid_set]
```

Límite práctico: letras `I`–`Z` → hasta **18** confluencias aprendidas simultáneas (config `confluence_registry_max_active`, default 5).

**Descartado explícitamente:**

- `"I:slug"` — rompe el formato actual; el slug queda solo en DB.
- `"I"` catch-all — una letra sin definición fija no es confluencia.
- `"I1"`, `"I2"` — inconsistente con el catálogo A–H.

### 6.3 `verify_spec` — contrato para Coherence Checker

JSON declarativo evaluado contra `ctx` del ciclo (mismas claves que `ContextBuilder`):

```jsonc
{
  "all": [
    {"ctx": "volatility_label", "in": ["normal", "low"]},
    {"ctx": "volume_ratio", "lt": 0.8},
    {"ctx": "rsi_15m", "lt": 35}
  ],
  "any": [
    {"ctx": "hist_15m", "lt": 0},
    {"ctx": "hist_1h", "lt": 0}
  ]
}
```

Operadores admitidos v1: `eq`, `lt`, `lte`, `gt`, `gte`, `in`, `not_in`.  
Regla de coherencia nueva **C8** (opcional v2): si el Decisor declara letra extendida (`I`, `J`, …) y `verify_spec` falla en el ciclo → warning (no bloqueo).

---

## 7. Criterios de promoción a catálogo

Un `confluence_candidate` pasa a `confluence_registry` cuando **todas** se cumplen:

| # | Criterio | Umbral default | Verificador |
|---|---|---|---|
| P1 | Ocurrencias con mismo `pattern_tag` | ≥ 3 en 7 días | SQL aggregate |
| P2 | `verify_spec` completo y testeable | 100% keys existen en ctx | unit test |
| P3 | No es subcaso de A–H existente | normalizador `maps_to_existing_only=false` | regla |
| P4 | Ratificación Supervisor | `status=promoted` en ciclo diario | LLM + guardrail |
| P5 | Sin conflicto con playbook activo | no contradice regla `[STRICT]` | regex / LLM |
| P6 | Efectividad no empeora (opcional v2) | miss_rate no sube >5pp post-promoción | métrica 14d |

Asignación de letra al promover:

1. Calcular siguiente letra libre en `I`–`Z` no presente en `confluence_registry` con `active=true`.
2. Asignar esa letra como `code` (ej. primera promoción → `I`, segunda → `J`).
3. Persistir `slug` interno (ej. `vol_div_range`) solo para trazabilidad y dedupe de candidatos.
4. Regenerar `{confluence_registry_block}` en runtime (no edit manual de `decisor_system.txt`).
5. Insertar fila en `confluence_registry` con `active=true`.

No reciclar letras desactivadas antes de 30 días (evita ambigüedad en decisiones históricas).

Rechazo: Supervisor o operador marca `rejected` con `reject_reason`; no repropone en 7 días salvo `occurrence_count` duplicado.

---

## 8. Consumo por agente

### 8.1 Decisor — Bloque K (nuevo)

Ubicación: `decisor_user.txt` entre Bloque G y Bloque H.

```text
════════════════════════════════════════════
BLOQUE K — LECCIONES RECIENTES (post-mortem, últimas 72 h)
════════════════════════════════════════════
{block_k_lessons}

Confluencias promovidas activas (I–Z):
{confluence_registry_block}
```

Query en `ContextBuilder`:

```sql
SELECT lesson_normalized->>'block_k_line', lesson_normalized->>'dedupe_key', ...
FROM decision_outcomes o
JOIN decisions d ON d.id = o.decision_id
WHERE o.postmortem_status = 'completed'
  AND o.computed_at >= now() - interval '72 hours'
  AND lesson_normalized->>'route' IN ('remap', 'guidance')
ORDER BY (lesson_normalized->>'confidence')::float DESC
LIMIT 20
-- dedupe in Python by dedupe_key, emit max 5 lines (~400 tokens)
```

Reglas en `decisor_system.txt`:

- Bloque K es **contexto operativo**, no reemplaza A–H.
- Si conflicto entre Bloque K y Playbook (J), **gana Playbook**.
- Letras `I`–`Z` solo válidas si están en `{confluence_registry_block}` activo.
- Cuentan para `min_confluences_buy` igual que A–H **solo** si `verify_spec` pasa en el ciclo (C8 warning si no).
- **No** emitir letras extendidas no listadas en el bloque; se filtran igual que códigos inválidos hoy.

### 8.2 Supervisor — consolidación diaria

Nuevo bloque en métricas:

- `candidate_patterns_block`: top 5 candidatos por `occurrence_count`.
- `promotion_recommendations`: candidatos que cumplen P1–P3, pendientes P4.

El Supervisor en Fase 2 (regeneración playbook):

- Promueve candidatos → actualiza registry + playbook con regla `[LEARNED]`.
- Fusiona lecciones `guidance` repetidas en texto markdown.
- **No** duplica verbatim más de 3 `block_k_line` en playbook (dedupe).

### 8.3 API / UI (v2)

| Endpoint | Descripción |
|---|---|
| `GET /api/decisions/outcomes?include_lessons=true` | Outcome + lesson_normalized |
| `GET /api/confluence/candidates` | Cola de promoción |
| `GET /api/confluence/registry` | Catálogo I–Z activo (letra + definición) |
| `POST /api/confluence/candidates/{id}/promote` | Manual override operador |

---

## 9. Módulos nuevos (trading-engine)

| Archivo | Responsabilidad |
|---|---|
| `agents/postmortem_agent.py` | Prompt + call LLM + validación Pydantic `LessonRaw` |
| `agents/postmortem_job.py` | `outcome_postmortem_tick` |
| `agents/lesson_normalizer.py` | `normalize(lesson_raw) → LessonNormalized` |
| `agents/confluence_registry.py` | CRUD registry + render `{confluence_registry_block}` |
| `tests/test_lesson_normalizer.py` | Rutas remap/candidate/guidance |
| `tests/test_postmortem_agent.py` | Mocks LLM + hindsight guardrails |

Cambios en existentes:

| Archivo | Cambio |
|---|---|
| `decisor.py` | `_filter_confluence_codes` acepta A–H + letras activas I–Z |
| `context_builder.py` | Bloque K + registry block |
| `supervisor.py` | Métricas candidatos + promoción P4 (asigna letra) |
| `coherence_checker.py` | C8 opcional para letras I–Z |
| `shared/db/models.py` | Columnas + tablas nuevas |

Config keys nuevas:

| Key | Default | Descripción |
|---|---|---|
| `postmortem_enabled` | `true` | Kill switch |
| `postmortem_max_per_tick` | `5` | Límite LLM |
| `postmortem_model` | `gemini-flash` | Provider |
| `confluence_promotion_min_occurrences` | `3` | P1 |
| `confluence_promotion_window_days` | `7` | P1 |
| `block_k_max_lines` | `5` | Token budget |
| `block_k_window_hours` | `72` | Ventana lecciones |
| `confluence_registry_max_active` | `5` | Máx. letras I–Z activas |

---

## 10. Rollout en PRs

| PR | Alcance | Validación |
|---|---|---|
| **PR1** | Migration 011 + postmortem job + `lesson_raw` + API `include_lessons` | Paper 48h, revisar lessons manualmente |
| **PR2** | Normalizador + Bloque K en Decisor (sin letras I–Z aún) | A/B miss_rate 7d |
| **PR3** | `confluence_candidates` + registry + promoción Supervisor + C8 | 1 candidato promovido como `I` en staging |
| **PR4** | UI candidatos + operador promote/reject | QA dashboard |

Cada PR reversible con config flag.

---

## 11. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Hindsight bias en PostMortemAgent | Prompt + `hindsight_guardrails_passed` + solo keys de `decision.input` |
| Conflicto Bloque K vs Playbook | Playbook gana; dedupe en Supervisor |
| Inflación catálogo I–Z | Promoción P1–P6; max 5 activas (`confluence_registry_max_active`) |
| Letra extendida sin verificación | C8 warning; no cuenta en confidence si verify falla (v2) |
| Costo LLM | max 5/tick, solo clasificaciones negativas maduras |
| Duplicación con Supervisor | Supervisor consolida; Bloque K es corto plazo |

---

## 12. Acceptance Criteria

| ID | Criterio |
|---|---|
| PM-01 | Tras madurar `BAD_BUY`, en ≤2h existe `lesson_raw` válido o `postmortem_status=skipped` con motivo. |
| PM-02 | Normalizador asigna exactamente una `route` por lesson. |
| PM-03 | Bloque K visible en prompt Decisor con ≤5 líneas deduplicadas. |
| PM-04 | Candidato con 3 ocurrencias aparece en métricas Supervisor. |
| PM-05 | Promoción crea fila `confluence_registry` con `code='I'` (o siguiente letra) y aparece en `{confluence_registry_block}`. |
| PM-06 | Decisor que emite `"I"` con letra inactiva o no promovida → filtrada como códigos inválidos hoy. |
| PM-07 | Post-mortem no corre sobre `GOOD_*` ni `PENDING`. |
| PM-08 | Tests unitarios normalizador: ≥10 casos (remap/candidate/guidance). |

---

## 13. Fuera de alcance v1

- Refuerzo positivo (`GOOD_BUY` → "seguí haciendo esto").
- Auto-promoción sin Supervisor (P4 siempre humano/LLM supervisor).
- Fine-tuning de modelos.
- Indicadores técnicos nuevos en pipeline (solo recombinación de existentes en `verify_spec`).
- WebSocket de lecciones en tiempo real.

---

## 14. Checklist de aprobación

- [ ] Acepto pipeline post-mortem → normalizador → Bloque K (§3).
- [ ] Acepto schemas `lesson_raw` y `lesson_normalized` (§5).
- [ ] Acepto formato de código **una letra** (`I`, `J`, `K`, … — igual que A–H) (§6.2).
- [ ] Acepto criterios de promoción P1–P6 (§7).
- [ ] Acepto extensión DB §4.1–4.3.
- [ ] Acepto rollout 4 PRs (§10).
- [ ] Confirmo AC PM-01…PM-08 (§12).

Una vez aprobado → `/meli.start` feature `decision-postmortem-learning` o plan TDD vía `writing-plans`.
