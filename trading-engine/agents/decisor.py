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
                 fallbacks: list[LLMProvider] | None = None):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.prompt_manager = prompt_manager or PromptManager(session)
        self.context_builder = ContextBuilder(session, symbol=symbol)
        self.provider = provider
        self.fallbacks = fallbacks or [LLMProvider.GEMINI_FLASH]

    async def decide(self, *, orderbook: OrderBookSnapshot | None, usdt_balance: float,
                     btc_held: float, max_position_pct: float, max_simultaneous_trades: int,
                     daily_stop_pct: float, decisor_interval_min: int, mode: str,
                     taker_fee: float, maker_fee: float,
                     atr_timeframe: str = "15m", min_rr_ratio: float = 1.3,
                     sl_atr_multiplier: float = 0.3,
                     calibration: dict | None = None,
                     current_drawdown_pct: float = 0.0) -> DecisorOutput:
        playbook = await self.prompt_manager.get_active_playbook()
        playbook_content = playbook.content if playbook else "# No playbook."

        ctx = await self.context_builder.build(
            orderbook=orderbook, usdt_balance=usdt_balance, btc_held=btc_held,
            playbook_content=playbook_content, max_position_pct=max_position_pct,
            max_simultaneous_trades=max_simultaneous_trades,
            daily_stop_pct=daily_stop_pct, decisor_interval_min=decisor_interval_min,
            mode=mode, taker_fee_pct=taker_fee, maker_fee_pct=maker_fee,
            atr_timeframe=atr_timeframe, min_rr_ratio=min_rr_ratio,
            sl_atr_multiplier=sl_atr_multiplier, calibration=calibration,
            current_drawdown_pct=current_drawdown_pct,
        )
        # Resolve any {config_variable} placeholders in the playbook using the full context,
        # so config changes propagate automatically without editing the playbook manually.
        if ctx.get("playbook"):
            ctx["playbook"] = _safe_substitute(ctx["playbook"], ctx)

        system_prompt = self.prompt_manager.load_system_prompt("decisor")
        system_prompt = _safe_substitute(system_prompt, ctx)
        user_prompt = self.prompt_manager.render_user_prompt("decisor", ctx, strict=False)

        resp = None
        try:
            resp = await self.llm.call(
                provider=self.provider, system_prompt=system_prompt,
                user_prompt=user_prompt, fallbacks=self.fallbacks,
            )
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw.strip())
            validated = DecisorOutput.model_validate(parsed)
            validated = _apply_deterministic_overrides(validated, max_position_pct, calibration)
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
            agent="decisor", model=resp.provider if resp else self.provider.value,
            tokens_in=resp.tokens_in if resp else 0,
            tokens_out=resp.tokens_out if resp else 0,
            latency_ms=resp.latency_ms if resp else 0,
            input={k: _serialize(v) for k, v in ctx.items()},
            output=output_dict, executed=False, rejected_reason=rejected_reason,
        ))
        await self.session.commit()
        logger.info("decisor.decided", action=output_dict["action"],
                    confidence=output_dict["confidence"],
                    regime=output_dict["regime"],
                    confluences=output_dict["confluences"],
                    reasoning=output_dict["reasoning"],
                    rejected=rejected_reason)
        return validated


def _hold_decision(reason: str) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.RANGE, confluences=[], action=DecisorAction.HOLD,
        confidence_base=0.0, confidence_adjustment=0.0, confidence=0.0,
        stop_loss=None, take_profit=None, position_size_pct=0.0,
        expected_holding_min=1, reasoning=reason,
    )


def _resolve_threshold(regime: MarketRegime, cal: dict) -> float:
    if regime == MarketRegime.TRENDING_UP:
        return cal.get("conf_threshold_trending_up", 0.60)
    if regime == MarketRegime.RANGE:
        return cal.get("conf_threshold_range", 0.70)
    if regime == MarketRegime.HIGH_VOLATILITY:
        return cal.get("conf_threshold_high_vol", 0.80)
    return 1.01  # TRENDING_DOWN: never allow BUY


def _factor_conf(confidence: float, cal: dict) -> float:
    if confidence >= 0.90: return cal.get("factor_conf_90", 1.00)
    if confidence >= 0.80: return cal.get("factor_conf_80", 0.85)
    if confidence >= 0.70: return cal.get("factor_conf_70", 0.70)
    if confidence >= 0.60: return cal.get("factor_conf_60", 0.50)
    return 0.0


def _factor_regime(regime: MarketRegime, cal: dict) -> float:
    return 1.00 if regime == MarketRegime.TRENDING_UP else cal.get("factor_regime_non_trending", 0.50)


def _apply_deterministic_overrides(validated: DecisorOutput, max_position_pct: float,
                                   calibration: dict | None = None) -> DecisorOutput:
    """Enforce regime-specific confidence threshold and deterministic position sizing.

    The LLM is instructed in the system prompt to compute these the same way, but we
    re-apply them here so the runtime behavior cannot diverge from the documented rules.
    All thresholds and factors come from calibration (config) — no hardcoded values.
    """
    if validated.action != DecisorAction.BUY:
        return validated

    cal = calibration or {}
    threshold = _resolve_threshold(validated.regime, cal)
    if validated.confidence < threshold:
        logger.info("decisor.override_below_threshold",
                    regime=validated.regime.value, confidence=validated.confidence,
                    threshold=threshold)
        return validated.model_copy(update={
            "action": DecisorAction.HOLD,
            "stop_loss": None,
            "take_profit": None,
            "position_size_pct": 0.0,
            "reasoning": f"[override] confidence {validated.confidence:.2f} < umbral "
                         f"{threshold:.2f} en {validated.regime.value} → HOLD forzado.",
        })

    new_size = round(max_position_pct * _factor_conf(validated.confidence, cal) *
                     _factor_regime(validated.regime, cal), 4)
    new_size = max(0.01, new_size)
    if abs(new_size - validated.position_size_pct) > 1e-6:
        logger.info("decisor.override_size",
                    original=validated.position_size_pct, new=new_size,
                    confidence=validated.confidence, regime=validated.regime.value)
        return validated.model_copy(update={"position_size_pct": new_size})
    return validated


def _safe_substitute(template: str, ctx: dict) -> str:
    """Replace {identifier} and {identifier:format_spec} placeholders found in ctx.

    Leaves unresolvable patterns (key not in ctx, invalid spec) untouched.
    Safe for templates that contain literal braces in JSON examples — those use
    quoted keys like {"regime": ...} which don't match the identifier regex.
    """
    def replace(match: re.Match) -> str:
        key = match.group(1)
        fmt = match.group(2)  # e.g. ":.4f" or None
        if key not in ctx:
            return match.group(0)
        value = ctx[key]
        if fmt:
            try:
                return format(value, fmt.lstrip(":"))
            except (ValueError, TypeError):
                return str(value)
        return str(value)
    return re.sub(r'\{([a-zA-Z_][a-zA-Z0-9_]*)(:[^}]*)?\}', replace, template)


def _serialize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return str(value)
