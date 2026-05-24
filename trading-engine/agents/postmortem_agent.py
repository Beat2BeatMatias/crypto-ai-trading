"""LLM post-mortem analysis for negative decision outcomes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from agents.llm_client import LLMClient, LLMProvider
from agents.postmortem_schemas import LessonRaw

logger = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_PROVIDER_MAP: dict[str, LLMProvider] = {
    "gemini-2.5-flash": LLMProvider.GEMINI_FLASH,
    "gemini-2.5-pro": LLMProvider.GEMINI_PRO,
    "groq-llama-3.3-70b": LLMProvider.GROQ_LLAMA,
}


def _resolve_provider(name: str) -> LLMProvider:
    return _PROVIDER_MAP.get(name, LLMProvider.GEMINI_FLASH)


def _parse_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _trim_input(inp: dict[str, Any], *, max_chars: int = 12000) -> dict[str, Any]:
    """Keep post-mortem payload bounded; preserve scalar keys and block_f_cross_tf."""
    out: dict[str, Any] = {}
    for k, v in inp.items():
        if isinstance(v, dict) and k != "block_f_cross_tf":
            continue
        if isinstance(v, str) and len(v) > 500:
            out[k] = v[:497] + "..."
        else:
            out[k] = v
    dumped = json.dumps(out, default=str)
    if len(dumped) <= max_chars:
        return out
    keys = sorted(out.keys(), key=lambda x: len(json.dumps(out[x], default=str)), reverse=True)
    for k in keys:
        if k in ("price", "regime", "rsi_15m", "hist_15m", "block_f_cross_tf", "volume_ratio"):
            continue
        del out[k]
        if len(json.dumps(out, default=str)) <= max_chars:
            break
    return out


class PostMortemAgent:
    def __init__(self, *, llm: LLMClient, provider: LLMProvider | None = None):
        self.llm = llm
        self.provider = provider or LLMProvider.GEMINI_FLASH

    async def analyze(
        self,
        *,
        decision: Any,
        outcome: Any,
        trade: Any | None,
        severity_score: float,
    ) -> LessonRaw:
        inp = _trim_input(decision.input or {})
        out = decision.output or {}
        system_prompt = (_PROMPTS_DIR / "postmortem_system.txt").read_text(encoding="utf-8")
        user_template = (_PROMPTS_DIR / "postmortem_user.txt").read_text(encoding="utf-8")

        trade_summary = "none"
        if trade is not None:
            trade_summary = (
                f"pnl_pct={getattr(trade, 'pnl_pct', None)} "
                f"close_reason={getattr(trade, 'close_reason', None)}"
            )

        user_prompt = user_template.format(
            classification=outcome.classification,
            severity_score=f"{severity_score:.3f}",
            decision_ts=decision.ts.isoformat(),
            decision_output_json=json.dumps(out, default=str, ensure_ascii=False)[:4000],
            decision_input_json=json.dumps(inp, default=str, ensure_ascii=False),
            forward_return_pct=outcome.forward_return_pct,
            mfe_pct=outcome.mfe_pct,
            mae_pct=outcome.mae_pct,
            time_to_mfe_min=outcome.time_to_mfe_min,
            time_to_mae_min=outcome.time_to_mae_min,
            sl_dist_pct=outcome.sl_dist_pct,
            tp_target_pct=outcome.tp_target_pct,
            trade_summary=trade_summary,
        )

        resp = await self.llm.call(
            provider=self.provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallbacks=[LLMProvider.GROQ_LLAMA],
            json_mode=True,
        )
        parsed = _parse_json(resp.text)
        parsed["classification"] = outcome.classification
        parsed["severity_score"] = severity_score
        lesson = LessonRaw.model_validate(parsed)
        logger.info(
            "postmortem.completed",
            decision_id=str(decision.id),
            root_cause=lesson.root_cause_tag,
            provider=resp.provider,
        )
        return lesson


def provider_from_config(name: str) -> LLMProvider:
    return _resolve_provider(name)
