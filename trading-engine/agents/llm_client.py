from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any
import structlog

logger = structlog.get_logger()

# Maps each Groq provider enum value to the actual model ID in the Groq API.
# Gemini providers use their enum value directly as model ID.
_GROQ_MODEL_IDS: dict[str, str] = {
    "groq-llama-3.3-70b":   "llama-3.3-70b-versatile",
    "groq-compound-beta":   "compound-beta",
    "groq-compound-mini":   "compound-beta-mini",
    "groq-llama-3.1-8b":    "llama-3.1-8b-instant",
    "groq-llama-4-scout":   "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq-gpt-oss-120b":    "openai/gpt-oss-120b",
    "groq-gpt-oss-20b":     "openai/gpt-oss-20b",
    "groq-qwen3-32b":       "qwen/qwen3-32b",
}


class LLMProvider(str, Enum):
    # Gemini
    GEMINI_FLASH = "gemini-2.5-flash"
    GEMINI_PRO   = "gemini-2.5-pro"
    # Groq — ordered roughly by reasoning capability (used as cascade suggestion)
    GROQ_LLAMA        = "groq-llama-3.3-70b"
    GROQ_COMPOUND     = "groq-compound-beta"
    GROQ_COMPOUND_MINI = "groq-compound-mini"
    GROQ_LLAMA4_SCOUT = "groq-llama-4-scout"
    GROQ_GPT_OSS_120B = "groq-gpt-oss-120b"
    GROQ_GPT_OSS_20B  = "groq-gpt-oss-20b"
    GROQ_QWEN3_32B    = "groq-qwen3-32b"
    GROQ_LLAMA_8B     = "groq-llama-3.1-8b"

    def is_groq(self) -> bool:
        return self.value in _GROQ_MODEL_IDS

    def groq_model_id(self) -> str:
        return _GROQ_MODEL_IDS[self.value]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    provider: str


class LLMClient:
    def __init__(self, gemini_client: Any | None = None, groq_client: Any | None = None,
                 *, max_retries: int = 3, backoff_base: float = 0.5):
        self.gemini = gemini_client
        self.groq = groq_client
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    async def call(self, *, provider: LLMProvider, system_prompt: str, user_prompt: str,
                   fallbacks: list[LLMProvider] | None = None,
                   # kept for backwards compat — single fallback wraps to list
                   fallback: LLMProvider | None = None,
                   json_mode: bool = True) -> LLMResponse:
        cascade = [provider] + (fallbacks or ([fallback] if fallback else []))
        last_err: Exception | None = None
        failures: list[dict] = []
        for idx, p in enumerate(cascade):
            try:
                return await self._call_with_retry(p, system_prompt, user_prompt,
                                                   json_mode=json_mode)
            except Exception as e:
                last_err = e
                is_rl = _is_rate_limit(e)
                failures.append({
                    "provider": p.value,
                    "rate_limited": is_rl,
                    "too_large": _is_too_large(e),
                })
                remaining = cascade[idx + 1:]
                if remaining:
                    logger.warning("llm.provider_failed_trying_next",
                                   failed=p.value, next=remaining[0].value,
                                   rate_limited=is_rl, error=str(e))
                else:
                    logger.error("llm.all_providers_exhausted",
                                 tried=[c.value for c in cascade], error=str(e))
        assert last_err is not None
        raise AllProvidersExhaustedError(tried=failures, last_err=last_err)

    async def _call_with_retry(self, provider: LLMProvider, system_prompt: str,
                                user_prompt: str, *, json_mode: bool = True) -> LLMResponse:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._call_provider(provider, system_prompt, user_prompt,
                                                 json_mode=json_mode)
            except Exception as e:
                last_err = e
                if _is_rate_limit(e):
                    # Rate limit: skip retries, surface immediately to try next provider
                    raise
                wait = self.backoff_base * (2 ** attempt)
                logger.warning("llm.retry", provider=provider.value, attempt=attempt + 1,
                               error=str(e), wait_s=wait)
                await asyncio.sleep(wait)
        assert last_err is not None
        raise last_err

    async def _call_provider(self, provider: LLMProvider, system_prompt: str,
                              user_prompt: str, *, json_mode: bool = True) -> LLMResponse:
        t0 = time.perf_counter()
        if provider.is_groq():
            resp = await self._call_groq(provider.groq_model_id(), system_prompt, user_prompt,
                                         json_mode=json_mode)
        elif provider in (LLMProvider.GEMINI_FLASH, LLMProvider.GEMINI_PRO):
            resp = await self._call_gemini(provider, system_prompt, user_prompt,
                                           json_mode=json_mode)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(text=resp["text"], tokens_in=resp["tokens_in"],
                           tokens_out=resp["tokens_out"], latency_ms=latency_ms,
                           provider=provider.value)

    async def _call_gemini(self, provider: LLMProvider, system_prompt: str,
                            user_prompt: str, *, json_mode: bool = True) -> dict[str, Any]:
        if self.gemini is None:
            raise RuntimeError("Gemini client not configured")
        config: dict[str, Any] = {"system_instruction": system_prompt, "temperature": 0.4}
        if json_mode:
            config["response_mime_type"] = "application/json"
        response = await self.gemini.aio.models.generate_content(
            model=provider.value, contents=user_prompt, config=config,
        )
        return {"text": response.text,
                "tokens_in": getattr(response.usage_metadata, "prompt_token_count", 0),
                "tokens_out": getattr(response.usage_metadata, "candidates_token_count", 0)}

    async def _call_groq(self, model: str, system_prompt: str, user_prompt: str,
                          *, json_mode: bool = True) -> dict[str, Any]:
        if self.groq is None:
            raise RuntimeError("Groq client not configured")
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": 0.4,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self.groq.chat.completions.create(**kwargs)
        return {"text": response.choices[0].message.content,
                "tokens_in": response.usage.prompt_tokens,
                "tokens_out": response.usage.completion_tokens}


class AllProvidersExhaustedError(Exception):
    """Se lanza cuando todos los providers del cascade fallaron.

    Atributo ``tried``: lista de dicts con keys ``provider``, ``rate_limited``,
    ``too_large`` para cada intento fallido (en orden).
    """
    def __init__(self, tried: list[dict], last_err: Exception) -> None:
        self.tried = tried
        self.last_err = last_err
        super().__init__(f"All providers exhausted: {[t['provider'] for t in tried]}")


def _is_rate_limit(exc: Exception) -> bool:
    """Detecta errores 429 de Groq y Gemini para saltar directo al siguiente provider."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return (
        "ratelimit" in name
        or "rate_limit" in name
        or "429" in msg
        or "resource_exhausted" in msg
        or "rate limit" in msg
    )


def _is_too_large(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "413" in msg or "request too large" in msg or "too large" in msg
