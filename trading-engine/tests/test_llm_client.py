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
