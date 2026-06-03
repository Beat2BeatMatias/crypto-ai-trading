from __future__ import annotations
import asyncio
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any
import structlog

logger = structlog.get_logger()

# Maps each Groq provider enum value to the actual model ID in the Groq API.
# Gemini providers use their enum value directly as model ID.
_GROQ_MODEL_IDS: dict[str, str] = {
    "groq-llama-3.3-70b":          "llama-3.3-70b-versatile",
    "groq-compound-beta":          "compound-beta",
    "groq-compound-mini":          "compound-beta-mini",
    "groq-llama-3.1-8b":           "llama-3.1-8b-instant",
    "groq-llama-4-scout":          "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq-gpt-oss-120b":           "openai/gpt-oss-120b",
    "groq-gpt-oss-20b":            "openai/gpt-oss-20b",
    "groq-qwen3-32b":              "qwen/qwen3-32b",
}

# Reasoning config per Groq model ID.
# "parsed"  → reasoning_format="parsed": reasoning separado en message.reasoning,
#              content contiene solo la respuesta final. Compatible con json_mode.
# "include" → include_reasoning=True: usado por modelos GPT-OSS.
#              reasoning también queda en message.reasoning.
# Ambos modos son compatibles con response_format=json_object.
# "raw" no se usa: incompatible con json_mode (retorna 400).
_GROQ_REASONING_CONFIG: dict[str, str] = {
    "qwen/qwen3-32b":      "parsed",
    "openai/gpt-oss-120b": "include",
    "openai/gpt-oss-20b":  "include",
}

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
    "ollama-deepseek-v4-flash",
    "ollama-deepseek-v4-pro",
    "ollama-kimi-k2-thinking",
})


class LLMProvider(str, Enum):
    # Gemini
    GEMINI_FLASH = "gemini-2.5-flash"
    GEMINI_PRO   = "gemini-2.5-pro"
    # Groq — ordered roughly by reasoning capability (used as cascade suggestion)
    GROQ_LLAMA            = "groq-llama-3.3-70b"
    GROQ_COMPOUND         = "groq-compound-beta"
    GROQ_COMPOUND_MINI    = "groq-compound-mini"
    GROQ_LLAMA4_SCOUT     = "groq-llama-4-scout"
    GROQ_GPT_OSS_120B     = "groq-gpt-oss-120b"      # supports include_reasoning
    GROQ_GPT_OSS_20B      = "groq-gpt-oss-20b"       # supports include_reasoning
    GROQ_QWEN3_32B        = "groq-qwen3-32b"         # supports reasoning_format=parsed
    GROQ_LLAMA_8B         = "groq-llama-3.1-8b"

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

    def is_groq(self) -> bool:
        return self.value in _GROQ_MODEL_IDS

    def groq_model_id(self) -> str:
        return _GROQ_MODEL_IDS[self.value]

    def is_ollama(self) -> bool:
        return self.value in _OLLAMA_MODEL_IDS

    def ollama_model_id(self) -> str:
        return _OLLAMA_MODEL_IDS[self.value]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    provider: str
    reasoning: str | None = None


class LLMClient:
    def __init__(self, gemini_client: Any | None = None, groq_client: Any | None = None,
                 ollama_client: Any | None = None,
                 *, max_retries: int = 3, backoff_base: float = 0.5):
        self.gemini = gemini_client
        self.groq = groq_client
        self.ollama = ollama_client
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    async def call(self, *, provider: LLMProvider, system_prompt: str, user_prompt: str,
                   fallbacks: list[LLMProvider] | None = None,
                   # kept for backwards compat — single fallback wraps to list
                   fallback: LLMProvider | None = None,
                   json_mode: bool = True,
                   temperature: float | None = None) -> LLMResponse:
        cascade = [provider] + (fallbacks or ([fallback] if fallback else []))
        last_err: Exception | None = None
        failures: list[dict] = []
        for idx, p in enumerate(cascade):
            try:
                return await self._call_with_retry(
                    p, system_prompt, user_prompt,
                    json_mode=json_mode, temperature=temperature,
                )
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
                                user_prompt: str, *, json_mode: bool = True,
                                temperature: float | None = None) -> LLMResponse:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._call_provider(
                    provider, system_prompt, user_prompt,
                    json_mode=json_mode, temperature=temperature,
                )
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
                              user_prompt: str, *, json_mode: bool = True,
                              temperature: float | None = None) -> LLMResponse:
        t0 = time.perf_counter()
        if provider.is_groq():
            resp = await self._call_groq(
                provider.groq_model_id(), system_prompt, user_prompt,
                json_mode=json_mode, temperature=temperature,
            )
        elif provider in (LLMProvider.GEMINI_FLASH, LLMProvider.GEMINI_PRO):
            resp = await self._call_gemini(
                provider, system_prompt, user_prompt,
                json_mode=json_mode, temperature=temperature,
            )
        elif provider.is_ollama():
            resp = await self._call_ollama(
                provider, system_prompt, user_prompt,
                json_mode=json_mode, temperature=temperature,
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(text=resp["text"], tokens_in=resp["tokens_in"],
                           tokens_out=resp["tokens_out"], latency_ms=latency_ms,
                           provider=provider.value, reasoning=resp.get("reasoning"))

    async def _call_gemini(self, provider: LLMProvider, system_prompt: str,
                            user_prompt: str, *, json_mode: bool = True,
                            temperature: float | None = None) -> dict[str, Any]:
        if self.gemini is None:
            raise RuntimeError("Gemini client not configured")
        temp = 0.4 if temperature is None else temperature
        config: dict[str, Any] = {"system_instruction": system_prompt, "temperature": temp}
        if json_mode:
            config["response_mime_type"] = "application/json"
        response = await self.gemini.aio.models.generate_content(
            model=provider.value, contents=user_prompt, config=config,
        )
        return {"text": response.text,
                "tokens_in": getattr(response.usage_metadata, "prompt_token_count", 0),
                "tokens_out": getattr(response.usage_metadata, "candidates_token_count", 0)}

    async def _call_groq(self, model: str, system_prompt: str, user_prompt: str,
                          *, json_mode: bool = True,
                          temperature: float | None = None) -> dict[str, Any]:
        if self.groq is None:
            raise RuntimeError("Groq client not configured")
        reasoning_mode = _GROQ_REASONING_CONFIG.get(model)
        if temperature is not None:
            temp = temperature
        else:
            temp = 0.6 if reasoning_mode else 0.4
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": user_prompt}],
            "temperature": temp,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if reasoning_mode == "parsed":
            # qwen3-32b: reasoning separado en message.reasoning, compatible con json_mode
            kwargs["reasoning_format"] = "parsed"
        elif reasoning_mode == "include":
            # gpt-oss-120b / gpt-oss-20b: usan include_reasoning vía extra_body (no tipado en SDK 0.13.x)
            kwargs["extra_body"] = {"include_reasoning": True}
        response = await self.groq.chat.completions.create(**kwargs)
        message = response.choices[0].message
        reasoning: str | None = getattr(message, "reasoning", None) or None
        if reasoning:
            logger.debug("llm.reasoning_received", model=model,
                         reasoning_chars=len(reasoning))
        return {
            "text": message.content or "",
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
            "reasoning": reasoning,
        }

    async def _call_ollama(self, provider: LLMProvider, system_prompt: str,
                            user_prompt: str, *, json_mode: bool = True,
                            temperature: float | None = None) -> dict[str, Any]:
        if self.ollama is None:
            raise RuntimeError("Ollama client not configured (missing OLLAMA_API_KEY)")
        model_id = provider.ollama_model_id()
        is_thinking = provider.value in _OLLAMA_THINKING_MODEL_IDS
        if temperature is not None:
            temp = temperature
        else:
            temp = 0.6 if is_thinking else 0.4
        response = await self.ollama.chat(
            model=model_id,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            format="json" if json_mode else None,
            options={"temperature": temp},
        )
        raw_content = response.message.content or ""

        # Estrategia 1: campo estructurado .thinking (Ollama >= 0.5 con QwQ/DeepSeek).
        # Si está disponible, el reasoning llega limpio y `content` no lo trae.
        structured_thinking: str | None = getattr(response.message, "thinking", None) or None

        if structured_thinking:
            reasoning: str | None = structured_thinking.strip() or None
            text = raw_content
        else:
            # Estrategia 2: regex <think> como fallback para modelos que embeben
            # el razonamiento en content.
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
