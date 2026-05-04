from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any
import structlog

logger = structlog.get_logger()


class LLMProvider(str, Enum):
    GEMINI_FLASH = "gemini-2.5-flash"
    GEMINI_PRO = "gemini-2.5-pro"
    GROQ_LLAMA = "groq-llama-3.3-70b"


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
                   fallback: LLMProvider | None = None) -> LLMResponse:
        try:
            return await self._call_with_retry(provider, system_prompt, user_prompt)
        except Exception as e:
            if fallback is None:
                raise
            logger.warning("llm.primary_failed_falling_back", primary=provider.value,
                           fallback=fallback.value, error=str(e))
            return await self._call_with_retry(fallback, system_prompt, user_prompt)

    async def _call_with_retry(self, provider: LLMProvider, system_prompt: str,
                                user_prompt: str) -> LLMResponse:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self._call_provider(provider, system_prompt, user_prompt)
            except Exception as e:
                last_err = e
                wait = self.backoff_base * (2 ** attempt)
                logger.warning("llm.retry", provider=provider.value, attempt=attempt + 1,
                               error=str(e), wait_s=wait)
                await asyncio.sleep(wait)
        assert last_err is not None
        raise last_err

    async def _call_provider(self, provider: LLMProvider, system_prompt: str,
                              user_prompt: str) -> LLMResponse:
        t0 = time.perf_counter()
        if provider in (LLMProvider.GEMINI_FLASH, LLMProvider.GEMINI_PRO):
            resp = await self._call_gemini(provider, system_prompt, user_prompt)
        elif provider == LLMProvider.GROQ_LLAMA:
            resp = await self._call_groq(system_prompt, user_prompt)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(text=resp["text"], tokens_in=resp["tokens_in"],
                           tokens_out=resp["tokens_out"], latency_ms=latency_ms,
                           provider=provider.value)

    async def _call_gemini(self, provider: LLMProvider, system_prompt: str,
                            user_prompt: str) -> dict[str, Any]:
        if self.gemini is None:
            raise RuntimeError("Gemini client not configured")
        response = await self.gemini.aio.models.generate_content(
            model=provider.value, contents=user_prompt,
            config={"system_instruction": system_prompt,
                    "response_mime_type": "application/json", "temperature": 0.4},
        )
        return {"text": response.text,
                "tokens_in": getattr(response.usage_metadata, "prompt_token_count", 0),
                "tokens_out": getattr(response.usage_metadata, "candidates_token_count", 0)}

    async def _call_groq(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if self.groq is None:
            raise RuntimeError("Groq client not configured")
        response = await self.groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"}, temperature=0.4,
        )
        return {"text": response.choices[0].message.content,
                "tokens_in": response.usage.prompt_tokens,
                "tokens_out": response.usage.completion_tokens}
