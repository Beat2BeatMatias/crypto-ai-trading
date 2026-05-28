# Ollama LLM Client Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar Ollama como tercer proveedor LLM (junto a Groq y Gemini), seleccionable desde el dashboard y disponible como fallback en el cascade.

**Architecture:** Usar el SDK `openai` con el endpoint OpenAI-compatible de Ollama (`/v1/chat/completions`). Se agrega `_call_ollama()` a `LLMClient` siguiendo el mismo patrón que `_call_groq()`. Los modelos thinking reciben `extra_body={"think": True}`. El cliente es opcional: si `OLLAMA_API_KEY` no está seteado, el cliente es `None` y usar un provider `ollama-*` lanza un `RuntimeError` claro.

**Tech Stack:** `openai>=1.30.0` (nuevo), `pydantic-settings`, `structlog`, `pytest-asyncio`

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `trading-engine/requirements.txt` | Modificar | Agregar `openai>=1.30.0` |
| `trading-engine/agents/llm_client.py` | Modificar | Enums, maps, `_call_ollama()`, routing, helpers |
| `trading-engine/config.py` | Modificar | `ollama_base_url`, `ollama_api_key` |
| `trading-engine/main.py` | Modificar | Inicialización condicional de `AsyncOpenAI` |
| `shared/config_store.py` | Modificar | Defaults de fallback providers con modelos Ollama |
| `trading-engine/tests/test_llm_client.py` | Modificar | 5 nuevos tests + 2 helpers de mock |

---

### Task 1: Agregar dependencia openai

**Files:**
- Modify: `trading-engine/requirements.txt`

- [ ] **Step 1: Agregar openai al requirements.txt**

En `trading-engine/requirements.txt`, agregar después de `groq==0.13.1` (bajo el bloque `# LLMs`):

```
openai>=1.30.0
```

- [ ] **Step 2: Verificar**

```bash
grep openai trading-engine/requirements.txt
```
Expected: `openai>=1.30.0`

- [ ] **Step 3: Commit**

```bash
git add trading-engine/requirements.txt
git commit -m "feat(llm): add openai SDK dependency for Ollama integration"
```

---

### Task 2: Agregar enum values y model maps en llm_client.py

**Files:**
- Modify: `trading-engine/agents/llm_client.py`

- [ ] **Step 1: Agregar `_OLLAMA_MODEL_IDS` y `_OLLAMA_THINKING_MODEL_IDS` después del bloque `_GROQ_REASONING_CONFIG` (línea 35)**

Insertar después de la línea `}` que cierra `_GROQ_REASONING_CONFIG`:

```python
_OLLAMA_MODEL_IDS: dict[str, str] = {
    "ollama-deepseek-v3.2":      "deepseek-v3.2",
    "ollama-deepseek-v4-flash":  "deepseek-v4-flash",
    "ollama-deepseek-v4-pro":    "deepseek-v4-pro",
    "ollama-kimi-k2-thinking":   "kimi-k2-thinking",
    "ollama-kimi-k2.6":          "kimi-k2.6",
    "ollama-qwen3.5-32b":        "qwen3.5:32b",
    "ollama-qwen3.5-122b":       "qwen3.5:122b",
    "ollama-qwen3-next-80b":     "qwen3-next:80b",
    "ollama-gemma4-27b":         "gemma4:27b",
    "ollama-nemotron-3-super":   "nemotron-3-super:120b",
    "ollama-gpt-oss-20b":        "gpt-oss:20b",
    "ollama-gpt-oss-120b":       "gpt-oss:120b",
    "ollama-glm-5":              "glm-5",
    "ollama-minimax-m2":         "minimax-m2",
}

_OLLAMA_THINKING_MODEL_IDS: frozenset[str] = frozenset({
    "ollama-deepseek-v3.2",
    "ollama-deepseek-v4-flash",
    "ollama-deepseek-v4-pro",
    "ollama-kimi-k2-thinking",
    "ollama-kimi-k2.6",
})
```

- [ ] **Step 2: Agregar 14 nuevos miembros al enum `LLMProvider` después de `GROQ_LLAMA_8B`**

Después de la línea `GROQ_LLAMA_8B = "groq-llama-3.1-8b"`, agregar:

```python
    # Ollama Cloud+Thinking — ordered roughly by reasoning capability
    OLLAMA_DEEPSEEK_V3_2       = "ollama-deepseek-v3.2"
    OLLAMA_DEEPSEEK_V4_FLASH   = "ollama-deepseek-v4-flash"
    OLLAMA_DEEPSEEK_V4_PRO     = "ollama-deepseek-v4-pro"
    OLLAMA_KIMI_K2_THINKING    = "ollama-kimi-k2-thinking"
    OLLAMA_KIMI_K2_6           = "ollama-kimi-k2.6"
    OLLAMA_QWEN35_32B          = "ollama-qwen3.5-32b"
    OLLAMA_QWEN35_122B         = "ollama-qwen3.5-122b"
    OLLAMA_QWEN3_NEXT_80B      = "ollama-qwen3-next-80b"
    OLLAMA_GEMMA4_27B          = "ollama-gemma4-27b"
    OLLAMA_NEMOTRON_3_SUPER    = "ollama-nemotron-3-super"
    OLLAMA_GPT_OSS_20B         = "ollama-gpt-oss-20b"
    OLLAMA_GPT_OSS_120B        = "ollama-gpt-oss-120b"
    OLLAMA_GLM_5               = "ollama-glm-5"
    OLLAMA_MINIMAX_M2          = "ollama-minimax-m2"
```

- [ ] **Step 3: Agregar métodos `is_ollama()` y `ollama_model_id()` a `LLMProvider`**

Después del método `groq_model_id()`:

```python
    def is_ollama(self) -> bool:
        return self.value in _OLLAMA_MODEL_IDS

    def ollama_model_id(self) -> str:
        return _OLLAMA_MODEL_IDS[self.value]
```

- [ ] **Step 4: Verificar que los tests existentes siguen pasando**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_llm_client.py -v
```
Expected: Los 5 tests existentes PASS

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/llm_client.py
git commit -m "feat(llm): add Ollama provider enum values, model ID maps and helpers"
```

---

### Task 3: Escribir los tests que fallan para `_call_ollama()`

**Files:**
- Modify: `trading-engine/tests/test_llm_client.py`

- [ ] **Step 1: Agregar helpers de mock para Ollama después de `_make_groq_client` (línea 39)**

```python
def _make_ollama_response(text: str = '{"action": "HOLD"}',
                           tokens_in: int = 90, tokens_out: int = 45) -> MagicMock:
    """Build a mock Ollama API response (OpenAI-compatible format)."""
    response = MagicMock()
    response.choices[0].message.content = text
    response.choices[0].message.thinking = None
    response.usage.prompt_tokens = tokens_in
    response.usage.completion_tokens = tokens_out
    return response


def _make_ollama_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client
```

- [ ] **Step 2: Agregar los 5 tests al final del archivo**

```python
@pytest.mark.asyncio
async def test_call_ollama_when_provider_is_deepseek_should_send_think_true():
    # GIVEN an Ollama client and a thinking model
    ollama_resp = _make_ollama_response(text='{"action": "HOLD"}')
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(ollama_client=ollama_client, max_retries=1)

    # WHEN calling with a thinking model
    result = await client.call(
        provider=LLMProvider.OLLAMA_DEEPSEEK_V4_FLASH,
        system_prompt="system",
        user_prompt="user",
    )

    # THEN think=True is sent in extra_body
    call_kwargs = ollama_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("extra_body", {}).get("think") is True
    assert result.provider == LLMProvider.OLLAMA_DEEPSEEK_V4_FLASH.value
    assert result.text == '{"action": "HOLD"}'


@pytest.mark.asyncio
async def test_call_ollama_when_non_thinking_model_should_not_send_think():
    # GIVEN an Ollama client and a non-thinking model
    ollama_resp = _make_ollama_response(text='{"action": "HOLD"}')
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(ollama_client=ollama_client, max_retries=1)

    # WHEN calling with a non-thinking model
    await client.call(
        provider=LLMProvider.OLLAMA_QWEN35_32B,
        system_prompt="system",
        user_prompt="user",
    )

    # THEN extra_body with think is NOT sent
    call_kwargs = ollama_client.chat.completions.create.call_args[1]
    assert "think" not in call_kwargs.get("extra_body", {})


@pytest.mark.asyncio
async def test_call_ollama_when_json_mode_should_send_response_format():
    # GIVEN an Ollama client
    ollama_resp = _make_ollama_response(text='{"action": "BUY"}', tokens_in=110, tokens_out=55)
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(ollama_client=ollama_client, max_retries=1)

    # WHEN calling with json_mode=True (default)
    result = await client.call(
        provider=LLMProvider.OLLAMA_QWEN35_32B,
        system_prompt="system",
        user_prompt="user",
        json_mode=True,
    )

    # THEN response_format=json_object is sent
    call_kwargs = ollama_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("response_format") == {"type": "json_object"}
    assert result.tokens_in == 110
    assert result.tokens_out == 55


@pytest.mark.asyncio
async def test_call_when_ollama_client_is_none_should_raise_runtime_error():
    # GIVEN an LLMClient with no Ollama client configured
    client = LLMClient(ollama_client=None, max_retries=1)

    # WHEN calling with an Ollama provider
    # THEN a RuntimeError is raised mentioning OLLAMA_API_KEY
    with pytest.raises(RuntimeError, match="OLLAMA_API_KEY"):
        await client.call(
            provider=LLMProvider.OLLAMA_DEEPSEEK_V4_PRO,
            system_prompt="system",
            user_prompt="user",
        )


@pytest.mark.asyncio
async def test_cascade_when_ollama_in_fallbacks_should_call_ollama_after_groq_fails():
    # GIVEN a Groq client that always fails and an Ollama client that succeeds
    groq_client = MagicMock()
    groq_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("Groq unavailable")
    )
    ollama_resp = _make_ollama_response(text='{"action": "HOLD"}', tokens_in=70, tokens_out=35)
    ollama_client = _make_ollama_client(ollama_resp)
    client = LLMClient(groq_client=groq_client, ollama_client=ollama_client,
                       max_retries=1, backoff_base=0.0)

    # WHEN calling with Groq as primary and Ollama as fallback
    result = await client.call(
        provider=LLMProvider.GROQ_LLAMA,
        system_prompt="system",
        user_prompt="user",
        fallbacks=[LLMProvider.OLLAMA_QWEN35_32B],
    )

    # THEN Ollama is called after Groq fails
    assert result.provider == LLMProvider.OLLAMA_QWEN35_32B.value
    assert result.text == '{"action": "HOLD"}'
    assert result.tokens_in == 70
    ollama_client.chat.completions.create.assert_called_once()
```

- [ ] **Step 3: Correr para confirmar que los 5 nuevos tests FALLAN**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_llm_client.py -v -k "ollama"
```
Expected: 5 tests FAIL con `TypeError: LLMClient.__init__() got an unexpected keyword argument 'ollama_client'`

- [ ] **Step 4: Commit los tests que fallan**

```bash
git add trading-engine/tests/test_llm_client.py
git commit -m "test(llm): add failing tests for Ollama provider"
```

---

### Task 4: Implementar `_call_ollama()` y el routing

**Files:**
- Modify: `trading-engine/agents/llm_client.py`

- [ ] **Step 1: Actualizar `LLMClient.__init__` para aceptar `ollama_client`**

Reemplazar la firma actual del `__init__`:
```python
    def __init__(self, gemini_client: Any | None = None, groq_client: Any | None = None,
                 *, max_retries: int = 3, backoff_base: float = 0.5):
        self.gemini = gemini_client
        self.groq = groq_client
        self.max_retries = max_retries
        self.backoff_base = backoff_base
```
Con:
```python
    def __init__(self, gemini_client: Any | None = None, groq_client: Any | None = None,
                 ollama_client: Any | None = None,
                 *, max_retries: int = 3, backoff_base: float = 0.5):
        self.gemini = gemini_client
        self.groq = groq_client
        self.ollama = ollama_client
        self.max_retries = max_retries
        self.backoff_base = backoff_base
```

- [ ] **Step 2: Actualizar el routing en `_call_provider()` para incluir Ollama**

Reemplazar el método `_call_provider` completo:
```python
    async def _call_provider(self, provider: LLMProvider, system_prompt: str,
                              user_prompt: str, *, json_mode: bool = True) -> LLMResponse:
        t0 = time.perf_counter()
        if provider.is_groq():
            resp = await self._call_groq(provider.groq_model_id(), system_prompt, user_prompt,
                                         json_mode=json_mode)
        elif provider in (LLMProvider.GEMINI_FLASH, LLMProvider.GEMINI_PRO):
            resp = await self._call_gemini(provider, system_prompt, user_prompt,
                                           json_mode=json_mode)
        elif provider.is_ollama():
            resp = await self._call_ollama(provider, system_prompt, user_prompt,
                                           json_mode=json_mode)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(text=resp["text"], tokens_in=resp["tokens_in"],
                           tokens_out=resp["tokens_out"], latency_ms=latency_ms,
                           provider=provider.value, reasoning=resp.get("reasoning"))
```

- [ ] **Step 3: Agregar `_call_ollama()` después del método `_call_groq()` (antes de `class AllProvidersExhaustedError`)**

```python
    async def _call_ollama(self, provider: LLMProvider, system_prompt: str,
                            user_prompt: str, *, json_mode: bool = True) -> dict[str, Any]:
        if self.ollama is None:
            raise RuntimeError("Ollama client not configured (missing OLLAMA_API_KEY)")
        model_id = provider.ollama_model_id()
        is_thinking = provider.value in _OLLAMA_THINKING_MODEL_IDS
        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": 0.6 if is_thinking else 0.4,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if is_thinking:
            kwargs["extra_body"] = {"think": True}
        response = await self.ollama.chat.completions.create(**kwargs)
        message = response.choices[0].message
        reasoning: str | None = getattr(message, "thinking", None) or None
        if reasoning:
            logger.debug("llm.ollama_reasoning_received", model=model_id,
                         reasoning_chars=len(reasoning))
        return {
            "text": message.content or "",
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
            "reasoning": reasoning,
        }
```

- [ ] **Step 4: Correr los tests para confirmar que los 5 nuevos tests PASAN**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_llm_client.py -v
```
Expected: Los 10 tests PASS (5 existentes + 5 nuevos)

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/llm_client.py
git commit -m "feat(llm): implement _call_ollama() with think support and cascade routing"
```

---

### Task 5: Agregar config vars e inicializar el cliente en main.py

**Files:**
- Modify: `trading-engine/config.py`
- Modify: `trading-engine/main.py`

- [ ] **Step 1: Agregar `ollama_base_url` y `ollama_api_key` a `config.py`**

En `trading-engine/config.py`, agregar después de `groq_api_key`:

```python
    ollama_base_url: str = Field("https://ollama.com/api", alias="OLLAMA_BASE_URL")
    ollama_api_key: str | None = Field(None, alias="OLLAMA_API_KEY")
```

- [ ] **Step 2: Agregar inicialización condicional del cliente Ollama en `main.py`**

En `trading-engine/main.py`, agregar después del bloque de inicialización de `groq_client` (después del `logger.warning("groq.client_init_failed")`):

```python
    try:
        from openai import AsyncOpenAI
        ollama_client = (
            AsyncOpenAI(base_url=settings.ollama_base_url, api_key=settings.ollama_api_key)
            if settings.ollama_api_key
            else None
        )
        if ollama_client is None:
            logger.info("ollama.client_not_configured")
    except Exception:
        ollama_client = None
        logger.warning("ollama.client_init_failed")
```

- [ ] **Step 3: Pasar `ollama_client` al constructor de `LLMClient`**

En `trading-engine/main.py`, reemplazar:
```python
    llm = LLMClient(gemini_client=gemini_client, groq_client=groq_client)
```
Con:
```python
    llm = LLMClient(gemini_client=gemini_client, groq_client=groq_client,
                    ollama_client=ollama_client)
```

- [ ] **Step 4: Correr los tests para confirmar que todo sigue pasando**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_llm_client.py -v
```
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add trading-engine/config.py trading-engine/main.py
git commit -m "feat(llm): add OLLAMA_BASE_URL/OLLAMA_API_KEY config and conditional client init"
```

---

### Task 6: Actualizar ConfigStore defaults con modelos Ollama

**Files:**
- Modify: `shared/config_store.py`

- [ ] **Step 1: Actualizar `FALLBACK_PROVIDERS` default**

Reemplazar el valor en `ConfigKey.FALLBACK_PROVIDERS`:
```python
    ConfigKey.FALLBACK_PROVIDERS: _Default(
        "gemini-2.5-flash,groq-llama-4-scout,groq-gpt-oss-120b,groq-qwen3-32b,groq-llama-3.1-8b",
        "string",
        "Cascada de fallback para decisor (CSV ordenado). Opciones: gemini-2.5-flash | groq-llama-3.3-70b | groq-compound-beta | groq-compound-mini | groq-llama-4-scout | groq-gpt-oss-120b | groq-gpt-oss-20b | groq-qwen3-32b* | groq-llama-3.1-8b  (* soporta reasoning_effort)",
    ),
```
Con:
```python
    ConfigKey.FALLBACK_PROVIDERS: _Default(
        "gemini-2.5-flash,groq-llama-4-scout,groq-gpt-oss-120b,groq-qwen3-32b,groq-llama-3.1-8b,ollama-deepseek-v4-flash,ollama-qwen3.5-32b",
        "string",
        "Cascada de fallback para decisor (CSV ordenado). Opciones: gemini-* | groq-* | ollama-* (ver LLMProvider enum)",
    ),
```

- [ ] **Step 2: Actualizar `SUPERVISOR_FALLBACK_PROVIDERS` default**

Reemplazar:
```python
    ConfigKey.SUPERVISOR_FALLBACK_PROVIDERS: _Default(
        "groq-llama-3.3-70b,groq-llama-4-scout,groq-gpt-oss-120b,gemini-2.5-flash",
        "string",
        "Cascada de fallback para supervisor (CSV ordenado). Mismas opciones que fallback_providers",
    ),
```
Con:
```python
    ConfigKey.SUPERVISOR_FALLBACK_PROVIDERS: _Default(
        "groq-llama-3.3-70b,groq-llama-4-scout,groq-gpt-oss-120b,gemini-2.5-flash,ollama-deepseek-v4-pro",
        "string",
        "Cascada de fallback para supervisor (CSV ordenado). Opciones: gemini-* | groq-* | ollama-* (ver LLMProvider enum)",
    ),
```

- [ ] **Step 3: Actualizar `POSTMORTEM_FALLBACK_PROVIDERS` default**

Reemplazar:
```python
    ConfigKey.POSTMORTEM_FALLBACK_PROVIDERS: _Default(
        "groq-compound-mini,groq-llama-4-scout,groq-qwen3-32b,groq-gpt-oss-20b,groq-llama-3.1-8b",
        "string",
        "Cascada de fallback para post-mortem (CSV ordenado). Mismas opciones que fallback_providers.",
    ),
```
Con:
```python
    ConfigKey.POSTMORTEM_FALLBACK_PROVIDERS: _Default(
        "groq-compound-mini,groq-llama-4-scout,groq-qwen3-32b,groq-gpt-oss-20b,groq-llama-3.1-8b,ollama-kimi-k2-thinking",
        "string",
        "Cascada de fallback para post-mortem (CSV ordenado). Opciones: gemini-* | groq-* | ollama-* (ver LLMProvider enum)",
    ),
```

- [ ] **Step 4: Actualizar descripción de `DECISOR_PROVIDER` para mencionar Ollama**

Reemplazar:
```python
    ConfigKey.DECISOR_PROVIDER: _Default(
        "groq-llama-3.3-70b", "string",
        "Primary LLM for decisor (chat). Options: groq-llama-3.3-70b | groq-compound-beta | groq-qwen3-32b* | groq-llama-4-scout | groq-gpt-oss-120b | gemini-2.5-flash  (* soporta reasoning_effort)",
    ),
```
Con:
```python
    ConfigKey.DECISOR_PROVIDER: _Default(
        "groq-llama-3.3-70b", "string",
        "Primary LLM for decisor. Options: groq-* | gemini-* | ollama-* (ver LLMProvider enum)",
    ),
```

- [ ] **Step 5: Actualizar descripción de `SUPERVISOR_PROVIDER` para mencionar Ollama**

Reemplazar:
```python
    ConfigKey.SUPERVISOR_PROVIDER: _Default(
        "gemini-2.5-pro", "string",
        "LLM for supervisor (chat). Options: gemini-2.5-pro | groq-llama-3.3-70b | groq-compound-beta | groq-qwen3-32b* | groq-llama-4-scout | groq-gpt-oss-120b  (* soporta reasoning_effort)",
    ),
```
Con:
```python
    ConfigKey.SUPERVISOR_PROVIDER: _Default(
        "gemini-2.5-pro", "string",
        "Primary LLM for supervisor. Options: gemini-* | groq-* | ollama-* (ver LLMProvider enum)",
    ),
```

- [ ] **Step 6: Correr suite completa de tests**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/ -v
```
Expected: Todos los tests PASS

- [ ] **Step 7: Commit**

```bash
git add shared/config_store.py
git commit -m "feat(llm): add Ollama models to fallback defaults in ConfigStore"
```
