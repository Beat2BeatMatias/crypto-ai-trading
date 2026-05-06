"""Tests for LLMClient with Gemini/Groq fallback and retries."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.llm_client import LLMClient, LLMProvider, LLMResponse


def _make_gemini_response(text: str = '{"action": "HOLD"}',
                           tokens_in: int = 100, tokens_out: int = 50) -> MagicMock:
    """Build a mock Gemini API response."""
    response = MagicMock()
    response.text = text
    response.usage_metadata.prompt_token_count = tokens_in
    response.usage_metadata.candidates_token_count = tokens_out
    return response


def _make_groq_response(text: str = '{"action": "HOLD"}',
                         tokens_in: int = 80, tokens_out: int = 40) -> MagicMock:
    """Build a mock Groq API response."""
    response = MagicMock()
    response.choices[0].message.content = text
    response.usage.prompt_tokens = tokens_in
    response.usage.completion_tokens = tokens_out
    return response


def _make_gemini_client(response: MagicMock) -> MagicMock:
    gemini = MagicMock()
    gemini.aio.models.generate_content = AsyncMock(return_value=response)
    return gemini


def _make_groq_client(response: MagicMock) -> MagicMock:
    groq = MagicMock()
    groq.chat.completions.create = AsyncMock(return_value=response)
    return groq


@pytest.mark.asyncio
async def test_gemini_call_returns_llm_response_with_correct_fields():
    # GIVEN a Gemini client that returns a valid response
    gemini_resp = _make_gemini_response(text='{"action":"BUY"}', tokens_in=120, tokens_out=60)
    gemini_client = _make_gemini_client(gemini_resp)
    client = LLMClient(gemini_client=gemini_client, max_retries=1)

    # WHEN calling with GEMINI_FLASH
    result = await client.call(
        provider=LLMProvider.GEMINI_FLASH,
        system_prompt="You are a trading agent.",
        user_prompt="Decide now.",
    )

    # THEN the result is a properly populated LLMResponse
    assert isinstance(result, LLMResponse)
    assert result.text == '{"action":"BUY"}'
    assert result.tokens_in == 120
    assert result.tokens_out == 60
    assert result.provider == LLMProvider.GEMINI_FLASH.value
    assert result.latency_ms >= 0
    gemini_client.aio.models.generate_content.assert_called_once()


@pytest.mark.asyncio
async def test_fallback_when_primary_fails():
    # GIVEN a Gemini client that always raises and a Groq client that succeeds
    gemini_client = MagicMock()
    gemini_client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("Gemini unavailable")
    )
    groq_resp = _make_groq_response(text='{"action":"HOLD"}', tokens_in=80, tokens_out=40)
    groq_client = _make_groq_client(groq_resp)

    client = LLMClient(gemini_client=gemini_client, groq_client=groq_client,
                       max_retries=1, backoff_base=0.0)

    # WHEN calling with Gemini as primary and Groq as fallback
    result = await client.call(
        provider=LLMProvider.GEMINI_FLASH,
        system_prompt="system",
        user_prompt="user",
        fallback=LLMProvider.GROQ_LLAMA,
    )

    # THEN the result comes from Groq
    assert result.provider == LLMProvider.GROQ_LLAMA.value
    assert result.text == '{"action":"HOLD"}'
    assert result.tokens_in == 80
    groq_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_no_fallback_raises():
    # GIVEN a Gemini client that always raises and no fallback configured
    gemini_client = MagicMock()
    gemini_client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("Gemini unavailable")
    )
    client = LLMClient(gemini_client=gemini_client, max_retries=1, backoff_base=0.0)

    # WHEN calling with no fallback
    # THEN the original exception propagates
    with pytest.raises(RuntimeError, match="Gemini unavailable"):
        await client.call(
            provider=LLMProvider.GEMINI_FLASH,
            system_prompt="system",
            user_prompt="user",
            fallback=None,
        )


@pytest.mark.asyncio
async def test_retry_on_transient_error_two_failures_then_success():
    # GIVEN a Gemini client that fails twice then succeeds
    success_resp = _make_gemini_response(text='{"action":"HOLD"}', tokens_in=50, tokens_out=25)
    gemini_client = MagicMock()
    gemini_client.aio.models.generate_content = AsyncMock(
        side_effect=[
            RuntimeError("transient error 1"),
            RuntimeError("transient error 2"),
            success_resp,
        ]
    )
    client = LLMClient(gemini_client=gemini_client, max_retries=3, backoff_base=0.0)

    # WHEN calling — retries will kick in
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.call(
            provider=LLMProvider.GEMINI_FLASH,
            system_prompt="system",
            user_prompt="user",
        )

    # THEN the result is returned from the third attempt
    assert result.text == '{"action":"HOLD"}'
    assert result.provider == LLMProvider.GEMINI_FLASH.value
    assert gemini_client.aio.models.generate_content.call_count == 3


@pytest.mark.asyncio
async def test_groq_json_mode_false_omits_response_format():
    # GIVEN a Groq client configured to receive markdown (not JSON)
    groq_resp = _make_groq_response(text="# Playbook v1\n\nContenido en markdown")
    groq_client = _make_groq_client(groq_resp)
    client = LLMClient(groq_client=groq_client, max_retries=1)

    # WHEN calling with json_mode=False
    result = await client.call(
        provider=LLMProvider.GROQ_LLAMA,
        system_prompt="Eres un supervisor de trading.",
        user_prompt="Genera el playbook.",
        json_mode=False,
    )

    # THEN response_format is NOT sent to Groq
    call_kwargs = groq_client.chat.completions.create.call_args[1]
    assert "response_format" not in call_kwargs
    assert result.text == "# Playbook v1\n\nContenido en markdown"


@pytest.mark.asyncio
async def test_gemini_json_mode_false_omits_response_mime_type():
    # GIVEN a Gemini client configured to receive markdown (not JSON)
    gemini_resp = _make_gemini_response(text="# Playbook v1\n\nContenido en markdown")
    gemini_client = _make_gemini_client(gemini_resp)
    client = LLMClient(gemini_client=gemini_client, max_retries=1)

    # WHEN calling with json_mode=False
    await client.call(
        provider=LLMProvider.GEMINI_FLASH,
        system_prompt="Eres un supervisor de trading.",
        user_prompt="Genera el playbook.",
        json_mode=False,
    )

    # THEN response_mime_type is NOT in the config sent to Gemini
    call_kwargs = gemini_client.aio.models.generate_content.call_args[1]
    assert "response_mime_type" not in call_kwargs.get("config", {})
