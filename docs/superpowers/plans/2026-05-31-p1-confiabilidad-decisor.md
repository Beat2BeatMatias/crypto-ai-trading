# P1 — Confiabilidad del Decisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Modelo de ejecución:** subagentes con `model: sonnet`.

**Goal:** Recuperar el ~14 % de ciclos del Decisor que terminan en HOLD por fallas técnicas (87 `parse_error: ValidationError`, 157 `RateLimitError`, 91 `AllProvidersExhausted`, 62 `APIConnectionError`) corrigiendo el parser de JSON, las coerciones del schema, la extracción de reasoning en Ollama, y la detección de indicadores nulos.

**Architecture:** Cuatro cambios quirúrgicos e independientes entre sí, todos sin tocar la lógica de decisión del LLM. (1) `_parse_llm_output` pasa de split-de-fence frágil a extracción de `{...}` balanceado tolerante a prosa. (2) `DecisorOutput` clampea `confidence_adjustment` y acepta `NEUTRAL` como régimen válido con `field_validator(mode="before")`. (3) `context_builder` detecta indicadores críticos nulos y hace early-exit a HOLD antes de llamar al LLM, evitando el enmascaramiento null→0. (4) `_call_ollama` prueba primero el campo estructurado `response.message.thinking` antes del regex `<think>`.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, `re`, `json`.

**Contexto empírico:**
- 3.092 ciclos totales de Decisor; 436 (~14 %) fallaron por errores técnicos, no de estrategia.
- `_parse_llm_output` (`decisor.py:263-270`): split de `` ``` `` + `json.loads` — no tolera prosa, case-sensitive en `json`, no maneja fences sin cerrar.
- `confidence_adjustment: Field(ge=-0.10, le=0.10)` — si el LLM emite `0.101`, Pydantic lanza `ValidationError` → HOLD.
- `MarketRegime` no tiene `NEUTRAL` — si el LLM lo emite → `ValidationError` → HOLD.
- `context_builder.py:284-304`: `rsi_15m = self._get(...) or 0` → RSI nulo se presenta como 0 → labeler lo etiqueta `oversold` → señal A falsa → el CoherenceChecker C1 (que también usa `or 0`) no protege.
- `_call_ollama` (`llm_client.py:255`): extrae reasoning con `re.search(r"<think>.*?</think>")` — si el modelo no emite los tags, el razonamiento queda pegado al JSON → `json.loads` falla → HOLD.

---

## Estructura de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `trading-engine/agents/decisor.py:263-270` | Modificar | Reemplazar `_parse_llm_output` con extractor de `{...}` balanceado |
| `trading-engine/tests/test_decisor.py` | Modificar | Tests de parsing (prosa antes/después, fence incompleto, JSON válido) |
| `shared/schemas.py:13-17,25` | Modificar | Agregar `NEUTRAL` a `MarketRegime`; clampear `confidence_adjustment` |
| `trading-engine/tests/test_schemas.py` | Modificar | Tests de coerción y NEUTRAL |
| `trading-engine/agents/context_builder.py:284-304` | Modificar | Detectar nulls críticos y exponer `critical_null_indicator` en el ctx |
| `trading-engine/agents/decisor.py:111-123` | Modificar | Early-exit a HOLD si `critical_null_indicator=True` antes de llamar al LLM |
| `trading-engine/tests/test_context_builder.py` | Modificar | Tests de detección de nulls |
| `trading-engine/agents/llm_client.py:254-261` | Modificar | Structured field primero, `<think>` regex como fallback |
| `trading-engine/tests/test_llm_client.py` | Modificar | Tests de extracción de reasoning |

**Orden:** Tasks 1, 2, 3 y 4 son independientes entre sí. Task 5 (no-regresión) va último.

---

### Task 1: Parser JSON robusto en `_parse_llm_output`

**Files:**
- Modify: `trading-engine/agents/decisor.py:263-270`
- Test: `trading-engine/tests/test_decisor.py`

**Por qué:** El parser actual hace `text.split("```")[1]` y luego `json.loads`. Falla con prosa antes del JSON, fences sin cerrar, `JSON` en mayúsculas, y cualquier carácter extra. Con modelos locales Ollama y modelos de razonamiento esto es sistemático.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `trading-engine/tests/test_decisor.py`:

```python
# ---------------------------------------------------------------------------
# Tests de _parse_llm_output (Task P1-1)
# ---------------------------------------------------------------------------
from agents.decisor import _parse_llm_output
from shared.schemas import DecisorAction, MarketRegime


def _hold_json() -> str:
    return json.dumps({
        "regime": "RANGE",
        "confluences": [],
        "action": "HOLD",
        "confidence_base": 0.0,
        "confidence_adjustment": 0.0,
        "confidence": 0.0,
        "stop_loss": None,
        "take_profit": None,
        "position_size_pct": 0.0,
        "expected_holding_min": 1,
        "reasoning": "Test HOLD",
    })


def test_parse_llm_output_when_plain_json_should_parse_correctly():
    result = _parse_llm_output(_hold_json())
    assert result.action == DecisorAction.HOLD


def test_parse_llm_output_when_json_fence_should_parse_correctly():
    text = f"```json\n{_hold_json()}\n```"
    result = _parse_llm_output(text)
    assert result.action == DecisorAction.HOLD


def test_parse_llm_output_when_prose_before_json_should_parse_correctly():
    text = f"Aquí está mi análisis del mercado.\n\n{_hold_json()}"
    result = _parse_llm_output(text)
    assert result.action == DecisorAction.HOLD


def test_parse_llm_output_when_prose_after_json_should_parse_correctly():
    text = f"{_hold_json()}\n\nEspero que esto ayude."
    result = _parse_llm_output(text)
    assert result.action == DecisorAction.HOLD


def test_parse_llm_output_when_think_tags_before_json_should_parse_correctly():
    text = f"<think>Estoy pensando...</think>\n{_hold_json()}"
    result = _parse_llm_output(text)
    assert result.action == DecisorAction.HOLD


def test_parse_llm_output_when_JSON_uppercase_fence_should_parse_correctly():
    text = f"```JSON\n{_hold_json()}\n```"
    result = _parse_llm_output(text)
    assert result.action == DecisorAction.HOLD


def test_parse_llm_output_when_no_json_should_raise():
    with pytest.raises(Exception):
        _parse_llm_output("No hay JSON aquí, solo prosa.")
```

> **Nota:** `import json` ya debe existir en `test_decisor.py`; si no, agregarlo al bloque de imports.

- [ ] **Step 2: Verificar que fallan**

```bash
cd trading-engine && python -m pytest tests/test_decisor.py -k "parse_llm_output" -v -p no:cov 2>&1 | tail -15
```
Expected: varios FAIL con `json.JSONDecodeError` o assertion errors.

- [ ] **Step 3: Reemplazar `_parse_llm_output` en `decisor.py`**

En `trading-engine/agents/decisor.py`, reemplazar el bloque de líneas 259–270 (el comentario + la función) por:

```python
# ---------------------------------------------------------------------------
# Parsing del output LLM — extrae el primer objeto JSON balanceado del texto,
# tolerando prosa antes/después, fences markdown, y tags <think>.
# ---------------------------------------------------------------------------

def _extract_first_json_object(text: str) -> dict:
    """Encuentra el primer objeto JSON balanceado en `text`.

    Tolera:
    - Prosa antes y después del objeto JSON.
    - Fences markdown (```json ... ``` o ```JSON ... ```).
    - Tags de razonamiento (<think>...</think>) antes del JSON.
    """
    # Eliminar tags <think> que algunos modelos emiten antes del JSON
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Eliminar fences markdown si el texto empieza con ellos
    stripped = text.strip()
    if stripped.startswith("```"):
        inner = stripped.split("```")
        # Tomar el primer bloque entre fences; ignorar la etiqueta "json"/"JSON"
        if len(inner) >= 3:
            block = inner[1]
            # Quitar etiqueta de lenguaje si la hay (json, JSON, etc.)
            block = re.sub(r"^[a-zA-Z]+\s*\n?", "", block)
            text = block

    # Buscar el primer '{' y extraer el objeto JSON balanceado
    start = text.find("{")
    if start == -1:
        raise ValueError("No se encontró objeto JSON en el texto del LLM")

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("Objeto JSON sin cerrar en el texto del LLM")


def _parse_llm_output(text: str) -> DecisorOutput:
    parsed = _extract_first_json_object(text)
    return DecisorOutput.model_validate(parsed)
```

- [ ] **Step 4: Verificar que los tests pasan**

```bash
cd trading-engine && python -m pytest tests/test_decisor.py -k "parse_llm_output" -v -p no:cov 2>&1 | tail -15
```
Expected: 7 tests PASS.

- [ ] **Step 5: Verificar que la suite del decisor no rompe**

```bash
cd trading-engine && python -m pytest tests/test_decisor.py -v -p no:cov 2>&1 | tail -10
```
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add trading-engine/agents/decisor.py trading-engine/tests/test_decisor.py
git commit -m "fix(decisor): robust JSON extraction tolerating prose and think-tags

_parse_llm_output now finds the first balanced {...} in the LLM response,
tolerating preamble prose, <think> tags, markdown fences, and uppercase
JSON labels. Eliminates 87+ parse_error HOLDs per production data.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Schema coerciones — clampear `confidence_adjustment` + aceptar `NEUTRAL`

**Files:**
- Modify: `shared/schemas.py:13-17` (enum `MarketRegime`)
- Modify: `shared/schemas.py:25` (field `confidence_adjustment`)
- Test: `trading-engine/tests/test_schemas.py`

**Por qué:** (1) Si el LLM emite `confidence_adjustment=0.101` (un decimal fuera de rango), Pydantic lanza `ValidationError` → HOLD. Debería clampearse a 0.10 silenciosamente como ya se hace con `position_size_pct`. (2) `MarketRegime.NEUTRAL` no existe: el playbook puede inducir que el LLM emita `NEUTRAL` (mencionado como régimen válido en el prompt del Supervisor) → `ValidationError` → HOLD.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `trading-engine/tests/test_schemas.py`:

```python
# ---------------------------------------------------------------------------
# Tests de coerciones (Task P1-2)
# ---------------------------------------------------------------------------

def test_confidence_adjustment_when_slightly_above_max_should_clamp_to_010():
    payload = _valid_buy_payload()
    payload["confidence_adjustment"] = 0.101   # un decimal fuera del ±0.10
    output = DecisorOutput(**payload)
    assert output.confidence_adjustment == 0.10


def test_confidence_adjustment_when_below_min_should_clamp_to_minus_010():
    payload = _valid_buy_payload()
    payload["confidence_adjustment"] = -0.15
    output = DecisorOutput(**payload)
    assert output.confidence_adjustment == -0.10


def test_confidence_adjustment_when_none_should_default_to_zero():
    payload = _valid_buy_payload()
    payload["confidence_adjustment"] = None
    output = DecisorOutput(**payload)
    assert output.confidence_adjustment == 0.0


def test_market_regime_neutral_should_be_accepted():
    payload = _valid_buy_payload()
    payload["regime"] = "NEUTRAL"
    payload["action"] = "HOLD"
    payload["stop_loss"] = None
    payload["take_profit"] = None
    payload["position_size_pct"] = 0.0
    output = DecisorOutput(**payload)
    assert output.regime == MarketRegime.NEUTRAL
```

- [ ] **Step 2: Verificar que fallan**

```bash
cd trading-engine && python -m pytest tests/test_schemas.py -k "clamp or neutral or NEUTRAL" -v -p no:cov 2>&1 | tail -15
```
Expected: 4 FAIL — `ValidationError` en los primeros tres, `ValueError` en el último.

- [ ] **Step 3: Agregar `NEUTRAL` al enum y el `field_validator` de clampeo**

En `shared/schemas.py`, reemplazar el bloque del enum `MarketRegime` (líneas 13-17):

```python
class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    NEUTRAL = "NEUTRAL"
```

En el mismo archivo, **después** del campo `confidence_adjustment` (línea 25) y **antes** del campo `confidence`, agregar el validator de clampeo. El bloque resultante debe quedar:

```python
    confidence_base: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    confidence_adjustment: Annotated[float, Field(ge=-0.10, le=0.10)] = 0.0
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]

    @field_validator("confidence_adjustment", mode="before")
    @classmethod
    def _clamp_confidence_adjustment(cls, v: Any) -> float:
        if v is None:
            return 0.0
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(-0.10, min(0.10, v))
```

> **Nota:** El `Field(ge=-0.10, le=0.10)` en la anotación del tipo es la **validación Pydantic post-validator**. Como el validator `mode="before"` clampea antes de que Pydantic evalúe los bounds, ya no lanza `ValidationError`. Ambas capas son consistentes.

- [ ] **Step 4: Verificar que pasan**

```bash
cd trading-engine && python -m pytest tests/test_schemas.py -v -p no:cov 2>&1 | tail -15
```
Expected: todos PASS incluyendo los 4 nuevos.

- [ ] **Step 5: Commit**

```bash
git add shared/schemas.py trading-engine/tests/test_schemas.py
git commit -m "fix(schemas): clamp confidence_adjustment and accept NEUTRAL regime

LLM models emit confidence_adjustment=0.101 or 'NEUTRAL' regime, both
causing ValidationError → silent HOLD. Now confidence_adjustment is
clamped to [-0.10, 0.10] via field_validator(mode='before'), and NEUTRAL
is a valid MarketRegime variant.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Detección de indicadores nulos → early-exit a HOLD

**Files:**
- Modify: `trading-engine/agents/context_builder.py:284-304`
- Modify: `trading-engine/agents/decisor.py:111-123` (early-exit antes del LLM)
- Test: `trading-engine/tests/test_context_builder.py`

**Por qué:** `rsi_15m = self._get(ind, "15m", "rsi") or 0` enmascara nulls como ceros. RSI=0 → labeler dice `oversold` → confluencia A espuria; CoherenceChecker C1 usa el mismo `or 0` y no protege. El sistema puede operar con señales inventadas. El fix: detectar si algún indicador crítico es genuinamente nulo, exponer `critical_null_indicator: bool` en el contexto, y hacer early-exit a HOLD en el Decisor antes de llamar al LLM (ahorrando también el token cost).

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `trading-engine/tests/test_context_builder.py`. Primero mirá el archivo para entender la fixture de `ind` (el dict de indicadores). Luego agregar:

```python
# ---------------------------------------------------------------------------
# Tests de detección de indicadores críticos nulos (Task P1-3)
# ---------------------------------------------------------------------------

def _make_ind_with_null_rsi(base_ind: dict) -> dict:
    """Retorna una copia del ind con RSI nulo en todos los timeframes."""
    import copy
    ind = copy.deepcopy(base_ind)
    for tf in ("1m", "5m", "15m", "1h", "4h"):
        if tf in ind:
            ind[tf].pop("rsi", None)   # elimina la clave
    return ind


def test_context_builder_when_rsi_null_should_set_critical_null_indicator_true(
    # Usar la misma fixture de sesión/indicadores que el resto del archivo usa.
    # Si el archivo usa una fixture `ind` o `indicators`, reutilizarla.
    # Si no, construir un dict mínimo:
):
    # GIVEN un ContextBuilder con RSI nulo en todos los timeframes
    # (adaptar a cómo el archivo construye el contexto — ver tests existentes)
    # El assert central: cuando RSI es nulo, critical_null_indicator=True en el ctx
    # y los valores de RSI en el ctx son None (no 0)
    pass   # → implementar tras leer el archivo
```

> **IMPORTANTE:** Antes de escribir el test definitivo, **leer** `trading-engine/tests/test_context_builder.py` completo para entender cómo construye el `ind` dict y el ContextBuilder. El test debe seguir exactamente el mismo patrón.

El assert central que debe FALLAR antes de la implementación:
```python
assert ctx["critical_null_indicator"] is True
assert ctx["rsi_15m"] is None   # no 0
```

- [ ] **Step 2: Correr para verificar que falla**

```bash
cd trading-engine && python -m pytest tests/test_context_builder.py -k "critical_null" -v -p no:cov 2>&1 | tail -10
```
Expected: FAIL con `KeyError: 'critical_null_indicator'`.

- [ ] **Step 3: Modificar `context_builder.py:284-304`**

Reemplazar el bloque de indicadores flat (donde están los `or 0`). En vez de `or 0`, capturar el valor real y registrar si es nulo:

```python
# --- Indicadores flat para el user prompt ---
# Los valores son None si el indicador no fue calculado aún.
# critical_null_indicator=True cuando un indicador crítico para las reglas A-H falta.

def _get_raw(ind_: dict, tf: str, key: str):
    """Retorna el valor del indicador o None si no existe/es nulo."""
    return (ind_.get(tf) or {}).get(key)

_critical_keys = (
    ("15m", "rsi"), ("1h", "rsi"), ("15m", "macd"), ("1h", "macd"),
    ("1h", "ema20"), ("1h", "ema50"), ("1h", "atr"),
)
critical_null_indicator = any(
    _get_raw(ind, tf, key) is None for tf, key in _critical_keys
)

ctx.update({
    # Régimen
    "critical_null_indicator": critical_null_indicator,
    # RSI por timeframe (None si no disponible)
    "rsi_1m":  _get_raw(ind, "1m",  "rsi"),
    "rsi_5m":  _get_raw(ind, "5m",  "rsi"),
    "rsi_15m": _get_raw(ind, "15m", "rsi"),
    "rsi_1h":  _get_raw(ind, "1h",  "rsi"),
    "rsi_4h":  _get_raw(ind, "4h",  "rsi"),
    "bb_pct_1m": _get_raw(ind, "1m", "bb_pct"),
    "bb_pct_5m": _get_raw(ind, "5m", "bb_pct"),
    "macd_15m":  _get_raw(ind, "15m", "macd"),
    "sig_15m":   _get_raw(ind, "15m", "macd_signal"),
    "hist_15m":  _get_raw(ind, "15m", "macd_hist"),
    "macd_1h":   _get_raw(ind, "1h", "macd"),
    "sig_1h":    _get_raw(ind, "1h", "macd_signal"),
    "ema20_1h":  _get_raw(ind, "1h", "ema20"),
    # ema50_1h y ema200_1h ya se calculan antes en el bloque; mantenerlos
    "atr_1h":    _get_raw(ind, "1h", "atr"),
})
```

> **Nota de integración:** El user prompt usa `{rsi_15m:.0f}` en el template. Si `rsi_15m=None`, el format string falla. Para evitar esto, el ContextBuilder ya usa `_DefaultDict` o `format_map` con un fallback. Verificá en `prompt_manager.py` o `decisor.py` cómo se renderiza el user prompt y asegurate de que el fallback para `None` produzca `"N/D"` o `"-"` en lugar de lanzar `TypeError`. Si el render usa `_DefaultDict(str, ctx)` donde el valor `None` se renderiza como `"None"`, eso es aceptable — el LLM verá `"None"` y debe aplicar la regla DATOS_INSUFICIENTES.

- [ ] **Step 4: Agregar el early-exit en `decisor.py` antes del LLM call**

En `trading-engine/agents/decisor.py`, dentro del método `decide()`, **antes** de la llamada al LLM (alrededor de la línea 111 donde dice `resp = await self.llm.call(...)`), agregar:

```python
# Early-exit si faltan indicadores críticos — ahorra tokens y evita señales inventadas.
if ctx.get("critical_null_indicator"):
    logger.warning(
        "decisor.critical_null_indicator",
        null_indicators=[
            f"{tf}:{key}"
            for tf, key in (
                ("15m", "rsi"), ("1h", "rsi"), ("15m", "macd"), ("1h", "macd"),
                ("1h", "ema20"), ("1h", "ema50"), ("1h", "atr"),
            )
            if (ctx.get(f"{key}_{tf}") if key != "ema20" else ctx.get("ema20_1h")) is None
        ],
    )
    return _hold_decision("[DATOS_INSUFICIENTES] Indicadores críticos nulos — esperando datos.")
```

> El log `decisor.critical_null_indicator` es auditables — el operador puede ver cuándo y por qué se saltó el LLM.

- [ ] **Step 5: Verificar que los tests pasan**

```bash
cd trading-engine && python -m pytest tests/test_context_builder.py -k "critical_null" -v -p no:cov 2>&1 | tail -10
```
Expected: PASS.

```bash
cd trading-engine && python -m pytest tests/test_decisor.py tests/test_context_builder.py -v -p no:cov 2>&1 | tail -10
```
Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add trading-engine/agents/context_builder.py trading-engine/agents/decisor.py trading-engine/tests/test_context_builder.py
git commit -m "fix(decisor): early-exit HOLD on null critical indicators

context_builder now passes None (not 0) for missing indicators and sets
critical_null_indicator=True when any of RSI/MACD/EMA/ATR in key TFs
is absent. The decisor returns HOLD immediately without calling the LLM,
preventing false oversold signals (RSI=0 -> labeler tag A espuria).

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Ollama reasoning — campo estructurado primero, regex como fallback

**Files:**
- Modify: `trading-engine/agents/llm_client.py:254-261`
- Test: `trading-engine/tests/test_llm_client.py`

**Por qué:** El commit `6a92d44` pasó de `response.message.thinking` (campo estructurado nativo de Ollama) a `re.search(r"<think>.*?</think>")` sobre el `content`. Si el modelo no emite los tags `<think>`, el razonamiento queda pegado al JSON y `_parse_llm_output` (aunque ahora más robusto gracias al Task 1) puede recibir texto contaminado innecesariamente. El fix: probar el campo estructurado primero; si tiene contenido, usarlo directamente sin tocar `content`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `trading-engine/tests/test_llm_client.py`:

```python
# ---------------------------------------------------------------------------
# Tests de extracción de reasoning Ollama (Task P1-4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_ollama_when_structured_thinking_field_should_use_it():
    """Si response.message.thinking tiene contenido, usarlo como reasoning
    y dejar content intacto (sin correr el regex <think>)."""
    # GIVEN una respuesta con campo .thinking poblado
    ollama_resp = _make_ollama_response(text='{"action": "HOLD"}')
    ollama_resp.message.thinking = "Pensando en profundidad sobre el mercado..."
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(ollama_client=ollama_client, max_retries=1)

    # WHEN llamando al LLM
    result = await client.call(
        provider=LLMProvider.OLLAMA_QWEN35_32B,
        system_prompt="system",
        user_prompt="user",
    )

    # THEN usa el campo estructurado como reasoning y content queda intacto
    assert result.text == '{"action": "HOLD"}'
    assert result.reasoning == "Pensando en profundidad sobre el mercado..."


@pytest.mark.asyncio
async def test_call_ollama_when_no_structured_thinking_but_think_tags_should_extract():
    """Sin campo .thinking, extrae con regex <think>."""
    # GIVEN una respuesta sin campo .thinking pero con tags <think> en content
    ollama_resp = _make_ollama_response(
        text='<think>Razonamiento interno</think>{"action": "HOLD"}'
    )
    # Simular que .thinking no existe o está vacío
    ollama_resp.message.thinking = None
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(ollama_client=ollama_client, max_retries=1)

    # WHEN llamando al LLM
    result = await client.call(
        provider=LLMProvider.OLLAMA_QWEN35_32B,
        system_prompt="system",
        user_prompt="user",
    )

    # THEN extrae reasoning de los tags y text queda limpio
    assert '{"action": "HOLD"}' in result.text
    assert "Razonamiento interno" in result.reasoning


@pytest.mark.asyncio
async def test_call_ollama_when_no_thinking_at_all_should_return_none_reasoning():
    """Sin campo .thinking ni tags <think>, reasoning=None y text=content completo."""
    ollama_resp = _make_ollama_response(text='{"action": "HOLD"}')
    ollama_resp.message.thinking = None
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(ollama_client=ollama_client, max_retries=1)

    result = await client.call(
        provider=LLMProvider.OLLAMA_QWEN35_32B,
        system_prompt="system",
        user_prompt="user",
    )

    assert result.text == '{"action": "HOLD"}'
    assert result.reasoning is None
```

- [ ] **Step 2: Verificar que fallan**

```bash
cd trading-engine && python -m pytest tests/test_llm_client.py -k "structured_thinking or think_tags or no_thinking" -v -p no:cov 2>&1 | tail -15
```
Expected: el primero FAIL (el campo `thinking` no se consulta — el código actual solo hace regex). El segundo puede pasar o fallar dependiendo de si el regex captura la etiqueta. El tercero PASS (sin tags → reasoning=None).

- [ ] **Step 3: Modificar `_call_ollama` en `llm_client.py`**

Reemplazar las líneas 253-261 (desde `raw_content = ...` hasta el `return`) por:

```python
    raw_content = response.message.content or ""

    # Estrategia 1: campo estructurado .thinking (Ollama >= 0.5 con modelos QwQ/DeepSeek).
    # Si el servidor lo soporta, el razonamiento llega aquí limpio y `content` ya no lo trae.
    structured_thinking: str | None = getattr(response.message, "thinking", None) or None

    if structured_thinking:
        reasoning: str | None = structured_thinking.strip() or None
        text = raw_content
    else:
        # Estrategia 2: regex <think>...</think> como fallback para modelos que embeben
        # el razonamiento en el campo content.
        think_match = re.search(r"<think>(.*?)</think>", raw_content, re.DOTALL)
        if think_match:
            reasoning = think_match.group(1).strip() or None
            text = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
        else:
            reasoning = None
            text = raw_content

    if reasoning:
        logger.debug("llm.reasoning_received", model=model_id,
                     reasoning_chars=len(reasoning))
    return {
        "text": text,
        "tokens_in": response.prompt_eval_count or 0,
        "tokens_out": response.eval_count or 0,
        "reasoning": reasoning,
    }
```

- [ ] **Step 4: Verificar que los tests pasan**

```bash
cd trading-engine && python -m pytest tests/test_llm_client.py -v -p no:cov 2>&1 | tail -15
```
Expected: todos PASS incluyendo los 3 nuevos.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/llm_client.py trading-engine/tests/test_llm_client.py
git commit -m "fix(llm): use structured thinking field first, regex as fallback

Commit 6a92d44 replaced response.message.thinking with regex on content.
Now we check the structured field first (Ollama >= 0.5 native support),
falling back to <think> regex. Prevents reasoning from contaminating
JSON content when models don't emit the tags.

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Suite completa no-regresión

**Files:** ninguno nuevo (verificación)

- [ ] **Step 1: Correr la suite completa del engine**

```bash
cd trading-engine && python -m pytest -q 2>&1 | tail -20
```
Expected: todos los tests PASS, coverage ≥ 70 %. Si hay fallas:
- Si es un test de `test_context_builder.py` que asume `rsi_15m=0` → actualizarlo para `rsi_15m=0` (los tests que usan indicadores completos no cambian — el `_get_raw` sigue devolviendo el valor cuando existe).
- Si es un test de `test_coherence_checker.py` que usa `ctx["rsi_15m"] or 0` → actualizarlo para que el contexto de test tenga el valor numérico directamente (los tests del checker pasan sus propios ctxs sintéticos).
- Si es un test de `test_decisor.py` que mockea `ctx["critical_null_indicator"]` → agregar `"critical_null_indicator": False` al dict del mock.

- [ ] **Step 2: Commit si hubo ajustes de regresión**

```bash
git add -A
git commit -m "test: fix regressions after P1 reliability changes

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Cobertura de los 5 problemas del análisis:**
1. Parser JSON frágil → Task 1 (`_extract_first_json_object`). ✅
2. `confidence_adjustment` ValidationError → Task 2 (clampeo). ✅
3. `MarketRegime.NEUTRAL` missing → Task 2 (enum). ✅
4. Indicadores null→0 con señales falsas → Task 3 (early-exit). ✅
5. Ollama reasoning regex frágil → Task 4 (campo estructurado primero). ✅

**Scan de placeholders:** Task 3, Step 3 tiene una nota de integración sobre el `_DefaultDict` del render — es una advertencia de verificación, no un placeholder de implementación. El test del Step 1 de Task 3 tiene un `pass` con instrucción explícita de leerlo antes de completarlo — necesario porque el test depende del API exacto del ContextBuilder que varía con la fixture. El implementador DEBE leer el archivo antes de escribir el test final.

**Consistencia de tipos:**
- `_extract_first_json_object(text: str) -> dict` definida en Task 1, llamada en `_parse_llm_output` del mismo task. ✅
- `critical_null_indicator: bool` en Task 3 context_builder, leído con `ctx.get("critical_null_indicator")` en decisor.py del mismo task. ✅
- `structured_thinking: str | None` en Task 4, patrón idéntico a `reasoning` ya existente. ✅

**Riesgo de Task 3 (el más complejo):** El user prompt usa format specs tipo `{rsi_15m:.0f}`. Si el render es un f-string directo sobre el dict, `None:.0f` lanza `TypeError`. El implementador debe verificar cómo se renderiza el prompt y asegurarse de que el `None` se convierte a string antes del format. Si el render usa `str.format_map` con `_DefaultDict`, `None` se renderiza como `"None"` — aceptable, el LLM lo interpreta como dato faltante.
