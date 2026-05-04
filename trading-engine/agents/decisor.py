from __future__ import annotations
import json
import re
import uuid
from typing import Any
import structlog
from pydantic import ValidationError
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Decision
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from agents.context_builder import ContextBuilder
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager
from collectors.orderbook_collector import OrderBookSnapshot

logger = structlog.get_logger()


class Decisor:
    def __init__(self, *, session: AsyncSession, llm: LLMClient, symbol: str,
                 prompt_manager: PromptManager | None = None,
                 provider: LLMProvider = LLMProvider.GROQ_LLAMA,
                 fallback: LLMProvider | None = LLMProvider.GEMINI_FLASH):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.prompt_manager = prompt_manager or PromptManager(session)
        self.context_builder = ContextBuilder(session, symbol=symbol)
        self.provider = provider
        self.fallback = fallback

    async def decide(self, *, orderbook: OrderBookSnapshot | None, usdt_balance: float,
                     btc_held: float, max_position_pct: float, max_simultaneous_trades: int,
                     daily_stop_pct: float, decisor_interval_min: int, mode: str,
                     taker_fee: float, maker_fee: float) -> DecisorOutput:
        playbook = await self.prompt_manager.get_active_playbook()
        playbook_content = playbook.content if playbook else "# No playbook."

        ctx = await self.context_builder.build(
            orderbook=orderbook, usdt_balance=usdt_balance, btc_held=btc_held,
            playbook_content=playbook_content, max_simultaneous_trades=max_simultaneous_trades,
            daily_stop_pct=daily_stop_pct, decisor_interval_min=decisor_interval_min,
            mode=mode, taker_fee_pct=taker_fee, maker_fee_pct=maker_fee,
        )

        system_prompt = self.prompt_manager.load_system_prompt("decisor")
        system_prompt = _safe_substitute(system_prompt, ctx)
        user_prompt = self.prompt_manager.render_user_prompt("decisor", ctx, strict=False)

        resp = None
        try:
            resp = await self.llm.call(
                provider=self.provider, system_prompt=system_prompt,
                user_prompt=user_prompt, fallback=self.fallback,
            )
            parsed = json.loads(resp.text)
            validated = DecisorOutput.model_validate(parsed)
            output_dict = validated.model_dump()
            rejected_reason = None
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("decisor.parse_error", error=str(e))
            validated = _hold_decision("parse_error")
            output_dict = validated.model_dump()
            rejected_reason = f"parse_error: {type(e).__name__}"
        except Exception as e:
            logger.error("decisor.llm_error", error=str(e))
            validated = _hold_decision("llm_error")
            output_dict = validated.model_dump()
            rejected_reason = f"llm_error: {type(e).__name__}"

        self.session.add(Decision(
            agent="decisor", model=self.provider.value,
            tokens_in=resp.tokens_in if resp else 0,
            tokens_out=resp.tokens_out if resp else 0,
            latency_ms=resp.latency_ms if resp else 0,
            input={k: _serialize(v) for k, v in ctx.items()},
            output=output_dict, executed=False, rejected_reason=rejected_reason,
        ))
        await self.session.commit()
        logger.info("decisor.decided", action=output_dict["action"],
                    confidence=output_dict["confidence"], rejected=rejected_reason)
        return validated


def _hold_decision(reason: str) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.RANGE, confluences=[], action=DecisorAction.HOLD,
        confidence=0.0, stop_loss=None, take_profit=None, position_size_pct=0.0,
        reasoning=reason,
    )


def _safe_substitute(template: str, ctx: dict) -> str:
    """Replace only {valid_identifier} placeholders that exist in ctx.
    Leaves all other curly-brace content (JSON examples, pipes, etc.) untouched."""
    def replace(match: re.Match) -> str:
        key = match.group(1)
        return str(ctx[key]) if key in ctx else match.group(0)
    return re.sub(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', replace, template)


def _serialize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return str(value)
