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

_VALID_CONFLUENCE_CODES = frozenset("ABCDEFGH")


def _filter_confluence_codes(confluences: list[str]) -> list[str]:
    """Elimina códigos fuera del catálogo A–H y loguea lo que se descarta.

    Garantía: el output siempre contiene solo códigos del catálogo aprobado.
    Si el LLM infló el conteo con códigos inventados, solo quedan los legítimos.
    """
    valid = [c for c in confluences if c in _VALID_CONFLUENCE_CODES]
    invalid = [c for c in confluences if c not in _VALID_CONFLUENCE_CODES]
    if invalid:
        logger.warning(
            "decisor.invalid_confluence_codes_filtered",
            invalid=invalid,
            remaining_valid=valid,
            valid_catalog=sorted(_VALID_CONFLUENCE_CODES),
        )
    return valid


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
            clean_confluences = _filter_confluence_codes(validated.confluences)
            if len(clean_confluences) != len(validated.confluences):
                validated = validated.model_copy(update={"confluences": clean_confluences})
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


_REGIME_THRESHOLD_KEY = {
    MarketRegime.TRENDING_UP: "conf_threshold_trending_up",
    MarketRegime.RANGE: "conf_threshold_range",
    MarketRegime.HIGH_VOLATILITY: "conf_threshold_high_vol",
}

_REGIME_THRESHOLD_DEFAULT = {
    MarketRegime.TRENDING_UP: 0.60,
    MarketRegime.RANGE: 0.70,
    MarketRegime.HIGH_VOLATILITY: 0.80,
}

# Piso absoluto de seguridad: ninguna configuración del Supervisor puede bajar
# el umbral de override por debajo de este valor.
_CONFIDENCE_FLOOR = 0.40


def _apply_deterministic_overrides(validated: DecisorOutput, max_position_pct: float,
                                   calibration: dict | None = None) -> DecisorOutput:
    """Enforce per-regime confidence threshold and step-function position sizing.

    Gate:   TRENDING_DOWN → HOLD siempre.
            Otros regímenes → usa conf_threshold_<regime> del calibration dict
            (ajustable por el Supervisor), con piso absoluto de _CONFIDENCE_FLOOR.
    Sizing: confidence >= 0.70 → min(max_position_pct, 0.25)
            confidence < 0.70  → 0.03 (tamaño reducido)

    El LLM recibe los mismos umbrales en el prompt; este layer garantiza que
    el runtime no pueda divergir aunque el LLM ignore las instrucciones.
    """
    if validated.action != DecisorAction.BUY:
        return validated

    # TRENDING_DOWN siempre bloqueado
    if validated.regime == MarketRegime.TRENDING_DOWN:
        logger.info("decisor.override_trending_down", confidence=validated.confidence)
        return validated.model_copy(update={
            "action": DecisorAction.HOLD,
            "stop_loss": None, "take_profit": None, "position_size_pct": 0.0,
            "reasoning": "[override] TRENDING_DOWN → BUY bloqueado.",
        })

    # Umbral por régimen desde calibration (con fallback al default y piso de seguridad)
    cal = calibration or {}
    key = _REGIME_THRESHOLD_KEY.get(validated.regime)
    default = _REGIME_THRESHOLD_DEFAULT.get(validated.regime, 0.60)
    threshold = max(_CONFIDENCE_FLOOR, float(cal.get(key, default)) if key else default)

    if validated.confidence < threshold:
        logger.info("decisor.override_below_threshold",
                    regime=validated.regime.value,
                    confidence=validated.confidence,
                    threshold=threshold)
        return validated.model_copy(update={
            "action": DecisorAction.HOLD,
            "stop_loss": None, "take_profit": None, "position_size_pct": 0.0,
            "reasoning": (
                f"[override] confidence {validated.confidence:.2f} < umbral "
                f"{threshold:.2f} para {validated.regime.value} → HOLD forzado."
            ),
        })

    # Sizing step-function (independiente del umbral de régimen)
    full_size = min(max_position_pct, 0.25)
    new_size = round(full_size if validated.confidence >= 0.70 else 0.03, 4)
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
