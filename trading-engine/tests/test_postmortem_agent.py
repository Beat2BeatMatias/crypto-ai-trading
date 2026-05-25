"""Tests for PostMortemAgent with mocked LLM."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from agents.llm_client import LLMClient, LLMProvider, LLMResponse
from agents.postmortem_agent import PostMortemAgent


def _lesson_json() -> str:
    return json.dumps({
        "version": 1,
        "classification": "BAD_BUY",
        "severity_score": 0.75,
        "root_cause_tag": "false_breakout_range",
        "summary": "BUY en RANGE sin volumen.",
        "decision_snapshot": {
            "regime_declared": "RANGE",
            "action": "BUY",
            "confidence": 0.71,
            "confluences_declared": ["H", "A"],
            "reasoning_excerpt": "rebote soporte",
        },
        "forward_evidence": {
            "mfe_pct": 0.1,
            "mae_pct": -0.42,
            "forward_return_pct": -0.38,
            "time_to_mae_min": 12,
            "time_to_mfe_min": 45,
        },
        "misread_indicators": [],
        "ignored_signals": [],
        "confluence_analysis": {
            "misapplied_codes": ["H"],
            "should_have_used": [],
            "notes": "",
        },
        "proposed_pattern": None,
        "would_change": {"action": "HOLD", "rationale": "esperar volumen"},
        "hindsight_guardrails_passed": True,
    })


@pytest.mark.asyncio
async def test_postmortem_agent_parses_llm_response():
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=LLMResponse(
        text=_lesson_json(),
        tokens_in=100,
        tokens_out=200,
        latency_ms=50,
        provider=LLMProvider.GEMINI_FLASH.value,
    ))
    agent = PostMortemAgent(llm=llm)
    decision = SimpleNamespace(
        id=uuid4(),
        ts=datetime.now(tz=timezone.utc),
        input={"price": 100.0, "rsi_15m": 32.0, "volume_ratio": 0.6},
        output={
            "action": "BUY",
            "regime": "RANGE",
            "confidence": 0.71,
            "confluences": ["H", "A"],
            "reasoning": "rebote",
        },
        trade_id=None,
    )
    outcome = SimpleNamespace(
        classification="BAD_BUY",
        forward_return_pct=-0.38,
        mfe_pct=0.1,
        mae_pct=-0.42,
        time_to_mfe_min=45,
        time_to_mae_min=12,
        sl_dist_pct=0.3,
        tp_target_pct=0.39,
    )
    lesson = await agent.analyze(
        decision=decision, outcome=outcome, trade=None, severity_score=0.75,
    )
    assert lesson.root_cause_tag == "false_breakout_range"
    assert lesson.confluence_analysis.misapplied_codes == ["H"]
    llm.call.assert_awaited_once()
    call_kwargs = llm.call.await_args.kwargs
    assert call_kwargs["fallbacks"] == []


@pytest.mark.asyncio
async def test_postmortem_agent_passes_configured_fallbacks():
    llm = MagicMock(spec=LLMClient)
    llm.call = AsyncMock(return_value=LLMResponse(
        text=_lesson_json(),
        tokens_in=100,
        tokens_out=200,
        latency_ms=50,
        provider=LLMProvider.GEMINI_FLASH.value,
    ))
    agent = PostMortemAgent(
        llm=llm,
        fallbacks=[LLMProvider.GROQ_COMPOUND_MINI, LLMProvider.GROQ_LLAMA4_SCOUT],
    )
    decision = SimpleNamespace(
        id=uuid4(),
        ts=datetime.now(tz=timezone.utc),
        input={"price": 100.0},
        output={"action": "HOLD", "confidence": 0.6, "confluences": []},
        trade_id=None,
    )
    outcome = SimpleNamespace(
        classification="MISSED_OPPORTUNITY",
        forward_return_pct=0.5,
        mfe_pct=0.5,
        mae_pct=-0.1,
        time_to_mfe_min=10,
        time_to_mae_min=5,
        sl_dist_pct=None,
        tp_target_pct=0.4,
    )
    await agent.analyze(decision=decision, outcome=outcome, trade=None, severity_score=0.5)
    assert llm.call.await_args.kwargs["fallbacks"] == [
        LLMProvider.GROQ_COMPOUND_MINI,
        LLMProvider.GROQ_LLAMA4_SCOUT,
    ]
