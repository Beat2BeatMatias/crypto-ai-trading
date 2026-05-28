# Diseño: Integración de Ollama como cliente LLM

**Fecha:** 2026-05-28  
**Estado:** Aprobado  

---

## Contexto

El proyecto usa `LLMClient` con soporte para Groq y Gemini, con sistema de cascade/fallback configurable desde la DB. Se agrega Ollama (cloud+thinking) como tercer proveedor: disponible como fallback y como proveedor configurable desde el dashboard.

---

## Decisiones de diseño

- **Enfoque A:** OpenAI SDK (`openai>=1.30.0`) con `base_url` de Ollama. Ollama documenta oficialmente este approach. Sin tocar el código de Groq/Gemini.
- **Modelos:** Solo modelos del catálogo cloud+thinking de Ollama (filtro `?c=thinking&c=cloud`).
- **Instancia:** Configurable via env vars (`OLLAMA_BASE_URL` + `OLLAMA_API_KEY`). Soporta cloud (ollama.com) y local (localhost:11434).
- **Inicialización condicional:** Si `OLLAMA_API_KEY` no está seteado, el cliente es `None` y usar un provider `ollama-*` lanza un `RuntimeError` claro.

---

## Sección 1: Modelos y Enum

Nuevas entradas en `LLMProvider` con prefijo `ollama-`:

```python
_OLLAMA_MODEL_IDS = {
    # Thinking / Reasoning
    "ollama-deepseek-v3.2":      "deepseek-v3.2",
    "ollama-deepseek-v4-flash":  "deepseek-v4-flash",
    "ollama-deepseek-v4-pro":    "deepseek-v4-pro",
    "ollama-kimi-k2-thinking":   "kimi-k2-thinking",
    "ollama-kimi-k2.6":          "kimi-k2.6",
    # General / Multilingüe
    "ollama-qwen3.5-32b":        "qwen3.5:32b",
    "ollama-qwen3.5-122b":       "qwen3.5:122b",
    "ollama-qwen3-next-80b":     "qwen3-next:80b",
    "ollama-gemma4-27b":         "gemma4:27b",
    # Alto poder
    "ollama-nemotron-3-super":   "nemotron-3-super:120b",
    "ollama-gpt-oss-20b":        "gpt-oss:20b",
    "ollama-gpt-oss-120b":       "gpt-oss:120b",
    # Otros cloud
    "ollama-glm-5":              "glm-5",
    "ollama-minimax-m2":         "minimax-m2",
}
```

Modelos con thinking activado (`think=True`):

```python
_OLLAMA_THINKING_MODELS = frozenset({
    LLMProvider.OLLAMA_DEEPSEEK_V3_2,
    LLMProvider.OLLAMA_DEEPSEEK_V4_FLASH,
    LLMProvider.OLLAMA_DEEPSEEK_V4_PRO,
    LLMProvider.OLLAMA_KIMI_K2_THINKING,
    LLMProvider.OLLAMA_KIMI_K2_6,
})
```

Nuevos métodos en `LLMProvider`:

```python
def is_ollama(self) -> bool:
    return self.value.startswith("ollama-")

def ollama_model_id(self) -> str:
    return _OLLAMA_MODEL_IDS[self.value]
```

---

## Sección 2: Configuración y Variables de Entorno

**`trading-engine/config.py`:**

```python
ollama_base_url: str = Field(default="https://ollama.com/api", env="OLLAMA_BASE_URL")
ollama_api_key: str | None = Field(default=None, env="OLLAMA_API_KEY")
```

**`trading-engine/main.py`:**

```python
from openai import AsyncOpenAI

ollama_client = None
if settings.ollama_api_key:
    ollama_client = AsyncOpenAI(
        base_url=settings.ollama_base_url,
        api_key=settings.ollama_api_key,
    )

llm = LLMClient(
    gemini_client=gemini_client,
    groq_client=groq_client,
    ollama_client=ollama_client,
)
```

**`.env.example` / `docker-compose.yml`:**

```env
OLLAMA_BASE_URL=https://ollama.com/api
OLLAMA_API_KEY=<tu-token>
```

---

## Sección 3: Cambios en `LLMClient`

**Constructor:**

```python
def __init__(self, gemini_client=None, groq_client=None, ollama_client=None):
    self._gemini = gemini_client
    self._groq = groq_client
    self._ollama = ollama_client
```

**Routing en `_call_provider()`:**

```python
elif provider.is_ollama():
    resp = await self._call_ollama(provider, system_prompt, user_prompt, json_mode)
```

**Nuevo método `_call_ollama()`:**

```python
async def _call_ollama(self, provider, system_prompt, user_prompt, json_mode) -> LLMResponse:
    if not self._ollama:
        raise RuntimeError("Ollama client no configurado (falta OLLAMA_API_KEY)")

    model_id = provider.ollama_model_id()
    is_thinking = provider in _OLLAMA_THINKING_MODELS

    kwargs = dict(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if is_thinking:
        kwargs["think"] = True

    t0 = time.monotonic()
    r = await self._ollama.chat.completions.create(**kwargs)
    latency_ms = int((time.monotonic() - t0) * 1000)

    msg = r.choices[0].message
    return LLMResponse(
        text=msg.content or "",
        tokens_in=r.usage.prompt_tokens,
        tokens_out=r.usage.completion_tokens,
        latency_ms=latency_ms,
        provider=f"ollama/{model_id}",
        reasoning=getattr(msg, "thinking", None),
    )
```

---

## Sección 4: ConfigStore y defaults

Nuevos modelos en fallbacks por defecto (`shared/config_store.py`):

```python
ConfigKey.FALLBACK_PROVIDERS: (
    "gemini-2.5-flash,"
    "groq-llama-4-scout,"
    "groq-gpt-oss-120b,"
    "groq-qwen3-32b,"
    "groq-llama-3.1-8b,"
    "ollama-deepseek-v4-flash,"
    "ollama-qwen3.5-32b"
),
ConfigKey.SUPERVISOR_FALLBACK_PROVIDERS: (
    "groq-llama-3.3-70b,"
    "groq-llama-4-scout,"
    "groq-gpt-oss-120b,"
    "gemini-2.5-flash,"
    "ollama-deepseek-v4-pro"
),
ConfigKey.POSTMORTEM_FALLBACK_PROVIDERS: (
    "groq-compound-mini,"
    "groq-llama-4-scout,"
    "groq-qwen3-32b,"
    "groq-gpt-oss-20b,"
    "groq-llama-3.1-8b,"
    "ollama-kimi-k2-thinking"
),
```

---

## Sección 5: Dependencias y Tests

**`requirements.txt`:**
```
openai>=1.30.0
```

**Tests nuevos en `trading-engine/tests/test_llm_client.py`:**

1. `test_call_ollama_when_provider_is_deepseek_should_send_think_true`
2. `test_call_ollama_when_json_mode_should_send_response_format`
3. `test_call_when_ollama_client_is_none_should_raise_runtime_error`
4. `test_cascade_when_ollama_in_fallbacks_should_call_ollama_after_groq_fails`
5. `test_call_ollama_when_non_thinking_model_should_not_send_think`

---

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `requirements.txt` | Agregar `openai>=1.30.0` |
| `trading-engine/agents/llm_client.py` | Nuevos enums, `_call_ollama()`, routing, `_OLLAMA_THINKING_MODELS` |
| `trading-engine/config.py` | `ollama_base_url`, `ollama_api_key` |
| `trading-engine/main.py` | Inicialización condicional `AsyncOpenAI` |
| `shared/config_store.py` | Nuevos modelos en fallback defaults |
| `docker-compose.yml` / `.env.example` | Nuevas env vars |
| `trading-engine/tests/test_llm_client.py` | 5 nuevos tests |
