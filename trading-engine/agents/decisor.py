from __future__ import annotations
import json
import re
from typing import Any
import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from shared.confidence import apply_server_confidence
from shared.db.models import Decision
from shared.position_sizing import apply_risk_based_sizing
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from agents.decisor_aggregate import aggregate_decisor_outputs
from agents.context_builder import ContextBuilder
from agents.llm_client import LLMClient, LLMProvider, AllProvidersExhaustedError
from agents.prompt_manager import PromptManager
from collectors.orderbook_collector import OrderBookSnapshot
from risk.coherence_checker import CoherenceChecker, CoherenceWarning

logger = structlog.get_logger()

_VALID_STATIC_CONFLUENCE_CODES = frozenset("ABCDEFGHIJ")


def _valid_confluence_codes(active_registry: frozenset[str]) -> frozenset[str]:
    return _VALID_STATIC_CONFLUENCE_CODES | active_registry


def _apply_server_confidence(
    decision: DecisorOutput,
    *,
    calibration: dict[str, Any],
    raw_confluences: list[str],
    trading_product: str = "spot",
) -> tuple[DecisorOutput, dict[str, Any]]:
    dropped = [c for c in raw_confluences if c not in decision.confluences]
    return apply_server_confidence(
        decision,
        calibration=calibration,
        confluences_dropped=dropped or None,
        trading_product=trading_product,
    )


def _filter_confluence_codes(
    confluences: list[str],
    active_registry: frozenset[str] | None = None,
) -> list[str]:
    """Elimina códigos fuera del catálogo A–H + letras activas en registry."""
    valid_set = _valid_confluence_codes(active_registry or frozenset())
    valid = [c for c in confluences if c in valid_set]
    invalid = [c for c in confluences if c not in valid_set]
    if invalid:
        logger.warning(
            "decisor.invalid_confluence_codes_filtered",
            invalid=invalid,
            remaining_valid=valid,
            valid_catalog=sorted(valid_set),
        )
    return valid

# Reglas de coherencia que disparan el two-pass.
# C1/C2/C3 son inconsistencias factuales (el LLM declaró algo que los
# indicadores no muestran) → vale la pena que el LLM lo revise.
# C4/C5/C6 son meta-reglas (confianza, holding) → no se benefician
# tanto de una segunda llamada.
# C7 (R:R insuficiente) sí va al two-pass: el second pass le provee el
# TP mínimo necesario y le pide que busque una resistencia válida por
# encima de ese nivel. Si no existe → debe emitir HOLD. Si el LLM
# vuelve a alucinarlo, C7 dispara de nuevo en la re-evaluación y
# el has_critical() lo bloquea a HOLD antes de ejecutar.
_TWO_PASS_TRIGGER_RULES = frozenset({"C1", "C2", "C3", "C1P", "C2P", "C3P", "C7"})


class Decisor:
    def __init__(self, *, session: AsyncSession, llm: LLMClient, symbol: str,
                 prompt_manager: PromptManager | None = None,
                 provider: LLMProvider = LLMProvider.GROQ_LLAMA,
                 fallbacks: list[LLMProvider] | None = None,
                 coherence_strict_mode: bool = False,
                 two_pass_enabled: bool = True,
                 llm_temperature: float = 0.1,
                 self_consistency_n: int = 0):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.prompt_manager = prompt_manager or PromptManager(session)
        self.context_builder = ContextBuilder(session, symbol=symbol)
        self.provider = provider
        self.fallbacks = fallbacks or [LLMProvider.GEMINI_FLASH]
        self.coherence_checker = CoherenceChecker(strict_mode=coherence_strict_mode)
        self.two_pass_enabled = two_pass_enabled
        self.llm_temperature = llm_temperature
        self.self_consistency_n = max(0, int(self_consistency_n))

    async def decide(self, *, orderbook: OrderBookSnapshot | None, usdt_balance: float,
                     btc_held: float, max_position_pct: float, max_simultaneous_trades: int,
                     daily_stop_pct: float, decisor_interval_min: int, mode: str,
                     taker_fee: float, maker_fee: float,
                     atr_timeframe: str = "15m", min_rr_ratio: float = 1.3,
                     sl_atr_multiplier: float = 0.3,
                     calibration: dict | None = None,
                     current_drawdown_pct: float = 0.0,
                     trading_product: str = "spot",
                     funding_rate: float = 0.0,
                     available_margin: float | None = None,
                     open_position_side: str | None = None,
                     liquidation_price: float | None = None) -> DecisorOutput:

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
            trading_product=trading_product,
            funding_rate=funding_rate,
            available_margin=available_margin,
            open_position_side=open_position_side,
            liquidation_price=liquidation_price,
        )

        if ctx.get("playbook"):
            ctx["playbook"] = _safe_substitute(ctx["playbook"], ctx)

        # ── Early-exit: critical indicators absent → HOLD without calling LLM ──
        if ctx.get("critical_null_indicator"):
            logger.warning("decisor.critical_null_indicator_early_exit")
            validated = _hold_datos_insuficientes(
                "[DATOS_INSUFICIENTES] Indicadores críticos nulos."
            )
            self.session.add(Decision(
                agent="decisor",
                model=self.provider.value,
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
                input={k: _serialize(v) for k, v in ctx.items()
                       if not isinstance(v, dict) or k == "block_f_cross_tf"},
                output={
                    **validated.model_dump(),
                    "coherence_warnings": [],
                    "two_pass_triggered": False,
                },
                executed=False,
                rejected_reason="critical_null_indicator",
            ))
            await self.session.commit()
            return validated

        active_ext = frozenset(ctx.get("active_registry_confluence_codes") or [])
        cal = calibration or {}

        system_prompt = self.prompt_manager.load_system_prompt("decisor")
        system_prompt = _safe_substitute(system_prompt, ctx)
        user_prompt = self.prompt_manager.render_user_prompt("decisor", ctx, strict=False)

        resp = None
        resp_review = None
        llm_responses: list[Any] = []
        coherence_warnings: list[CoherenceWarning] = []
        rejected_reason: str | None = None
        two_pass_triggered = False
        llm_error_tried: list[dict] | None = None
        confidence_meta: dict[str, Any] | None = None
        position_size_meta: dict[str, Any] | None = None
        self_consistency_meta: dict[str, Any] | None = None

        try:
            validated, confidence_meta, position_size_meta, self_consistency_meta, llm_responses = (
                await self._initial_llm_decision(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    active_ext=active_ext,
                    cal=cal,
                    ctx=ctx,
                    max_position_pct=max_position_pct,
                    usdt_balance=usdt_balance,
                )
            )
            resp = llm_responses[0] if llm_responses else None

            coherence_warnings = self.coherence_checker.evaluate(validated, ctx)

            # ── PASS 2: auto-revisión si hay inconsistencias factuales ─────
            trigger_warnings = [
                w for w in coherence_warnings
                if w.rule_id in _TWO_PASS_TRIGGER_RULES
            ]
            if trigger_warnings and self.two_pass_enabled:
                two_pass_triggered = True
                logger.info(
                    "decisor.two_pass_triggered",
                    rules=[w.rule_id for w in trigger_warnings],
                    action_pass1=validated.action,
                    confidence_pass1=validated.confidence,
                )
                review_ctx = _build_review_ctx(validated, trigger_warnings, ctx)
                review_template = self.prompt_manager.load_user_template("decisor_review")
                review_prompt = review_template.format_map(_DefaultReviewDict(review_ctx))

                try:
                    resp_review = await self.llm.call(
                        provider=self.provider,
                        system_prompt=system_prompt,
                        user_prompt=review_prompt,
                        fallbacks=self.fallbacks,
                        temperature=self.llm_temperature,
                    )
                    parsed_review = _parse_llm_output(resp_review.text)
                    validated_review, confidence_meta, position_size_meta = _post_process_decision(
                        parsed_review,
                        raw_confluences=list(parsed_review.confluences),
                        active_ext=active_ext,
                        calibration=cal,
                        ctx=ctx,
                        max_position_pct=max_position_pct,
                        usdt_balance=usdt_balance,
                    )
                    # Re-evaluar coherencia de la decisión revisada
                    coherence_warnings_review = self.coherence_checker.evaluate(
                        validated_review, ctx
                    )
                    remaining = [
                        w for w in coherence_warnings_review
                        if w.rule_id in _TWO_PASS_TRIGGER_RULES
                    ]
                    logger.info(
                        "decisor.two_pass_result",
                        action_pass1=validated.action,
                        action_pass2=validated_review.action,
                        confidence_pass1=validated.confidence,
                        confidence_pass2=validated_review.confidence,
                        warnings_pass1=[w.rule_id for w in trigger_warnings],
                        warnings_pass2=[w.rule_id for w in remaining],
                        inconsistencies_resolved=len(trigger_warnings) - len(remaining),
                    )
                    # La decisión revisada pasa a ser la decisión final.
                    # Mantenemos los warnings de ambos pases en el output.
                    validated = validated_review
                    coherence_warnings = coherence_warnings_review
                except Exception as e:
                    # Si el second pass falla, continuamos con la decisión del pass 1.
                    logger.warning("decisor.two_pass_error", error=str(e))

            # ── strict_mode: warnings críticos → HOLD ─────────────────────
            if self.coherence_checker.has_critical(coherence_warnings):
                critical_ids = [w.rule_id for w in coherence_warnings if w.severity == "critical"]
                logger.warning("decisor.coherence_strict_hold",
                               rules=critical_ids,
                               action=validated.action,
                               confidence=validated.confidence)
                validated = _hold_decision(
                    f"[coherence_strict] reglas {critical_ids} → HOLD de seguridad."
                )
                rejected_reason = f"coherence_strict: {critical_ids}"

            logger.info(
                "decisor.llm_decision_accepted",
                action=validated.action,
                regime=validated.regime,
                confidence=validated.confidence,
                position_size_pct=validated.position_size_pct,
                confluences=validated.confluences,
                coherence_warnings=[w.rule_id for w in coherence_warnings],
                two_pass=two_pass_triggered,
            )

        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            raw_text = resp.text if resp else None
            logger.warning(
                "decisor.parse_error",
                error=str(e),
                raw_llm_text=raw_text,
            )
            validated = _hold_decision("parse_error")
            rejected_reason = f"parse_error: {type(e).__name__}"
        except Exception as e:
            logger.error("decisor.llm_error", error=str(e))
            validated = _hold_decision("llm_error")
            rejected_reason = f"llm_error: {type(e).__name__}"
            if isinstance(e, AllProvidersExhaustedError):
                llm_error_tried = e.tried

        output_dict = validated.model_dump()
        output_dict["coherence_warnings"] = [w.to_dict() for w in coherence_warnings]
        output_dict["two_pass_triggered"] = two_pass_triggered
        if confidence_meta is not None:
            output_dict["confidence_meta"] = confidence_meta
        if position_size_meta is not None:
            output_dict["position_size_meta"] = position_size_meta
        if self_consistency_meta is not None:
            output_dict["self_consistency"] = self_consistency_meta
        if llm_error_tried is not None:
            output_dict["llm_error_tried"] = llm_error_tried

        tokens_in = sum(r.tokens_in for r in llm_responses)
        tokens_out = sum(r.tokens_out for r in llm_responses)
        latency_ms = sum(r.latency_ms for r in llm_responses)
        if resp_review is not None:
            tokens_in += resp_review.tokens_in
            tokens_out += resp_review.tokens_out
            latency_ms += resp_review.latency_ms
        model = llm_responses[0].provider if llm_responses else self.provider.value

        self.session.add(Decision(
            agent="decisor",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            input={k: _serialize(v) for k, v in ctx.items()
                   if not isinstance(v, dict) or k == "block_f_cross_tf"},
            output=output_dict,
            executed=False,
            rejected_reason=rejected_reason,
        ))
        await self.session.commit()

        logger.info(
            "decisor.decided",
            action=output_dict["action"],
            confidence=output_dict["confidence"],
            regime=output_dict["regime"],
            confluences=output_dict["confluences"],
            coherence_warnings_count=len(coherence_warnings),
            two_pass=two_pass_triggered,
            rejected=rejected_reason,
        )
        return validated

    async def _initial_llm_decision(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        active_ext: frozenset[str],
        cal: dict[str, Any],
        ctx: dict[str, Any],
        max_position_pct: float,
        usdt_balance: float,
    ) -> tuple[DecisorOutput, dict[str, Any] | None, dict[str, Any] | None,
               dict[str, Any] | None, list[Any]]:
        if self.self_consistency_n > 1:
            candidates: list[DecisorOutput] = []
            responses: list[Any] = []
            for _ in range(self.self_consistency_n):
                r = await self._llm_call(system_prompt, user_prompt)
                responses.append(r)
                parsed = _parse_llm_output(r.text)
                processed, _, _ = _post_process_decision(
                    parsed,
                    raw_confluences=list(parsed.confluences),
                    active_ext=active_ext,
                    calibration=cal,
                    ctx=ctx,
                    max_position_pct=max_position_pct,
                    usdt_balance=usdt_balance,
                    apply_sizing=False,
                )
                candidates.append(processed)
            validated, sc_meta = aggregate_decisor_outputs(candidates)
            raw_agg = list(validated.confluences)
            clean = _filter_confluence_codes(validated.confluences, active_ext)
            if len(clean) != len(validated.confluences):
                validated = validated.model_copy(update={"confluences": clean})
            validated, confidence_meta = _apply_server_confidence(
                validated,
                calibration=cal,
                raw_confluences=raw_agg,
                trading_product=ctx.get("trading_product", "spot"),
            )
            validated, position_meta = _apply_sizing_from_ctx(
                validated, calibration=cal, ctx=ctx,
                max_position_pct=max_position_pct, usdt_balance=usdt_balance,
            )
            return validated, confidence_meta, position_meta, sc_meta, responses

        r = await self._llm_call(system_prompt, user_prompt)
        parsed = _parse_llm_output(r.text)
        validated, confidence_meta, position_meta = _post_process_decision(
            parsed,
            raw_confluences=list(parsed.confluences),
            active_ext=active_ext,
            calibration=cal,
            ctx=ctx,
            max_position_pct=max_position_pct,
            usdt_balance=usdt_balance,
        )
        return validated, confidence_meta, position_meta, None, [r]

    async def _llm_call(self, system_prompt: str, user_prompt: str):
        return await self.llm.call(
            provider=self.provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallbacks=self.fallbacks,
            temperature=self.llm_temperature,
        )


def _post_process_decision(
    decision: DecisorOutput,
    *,
    raw_confluences: list[str],
    active_ext: frozenset[str],
    calibration: dict[str, Any],
    ctx: dict[str, Any],
    max_position_pct: float,
    usdt_balance: float,
    apply_sizing: bool = True,
) -> tuple[DecisorOutput, dict[str, Any] | None, dict[str, Any] | None]:
    validated = decision
    clean = _filter_confluence_codes(validated.confluences, active_ext)
    if len(clean) != len(validated.confluences):
        validated = validated.model_copy(update={"confluences": clean})
    validated, confidence_meta = _apply_server_confidence(
        validated,
        calibration=calibration,
        raw_confluences=raw_confluences,
        trading_product=ctx.get("trading_product", "spot"),
    )
    position_meta = None
    if apply_sizing:
        validated, position_meta = _apply_sizing_from_ctx(
            validated,
            calibration=calibration,
            ctx=ctx,
            max_position_pct=max_position_pct,
            usdt_balance=usdt_balance,
        )
    return validated, confidence_meta, position_meta


def _apply_sizing_from_ctx(
    decision: DecisorOutput,
    *,
    calibration: dict[str, Any],
    ctx: dict[str, Any],
    max_position_pct: float,
    usdt_balance: float,
) -> tuple[DecisorOutput, dict[str, Any] | None]:
    risk_pct = float(calibration.get("risk_per_trade_pct", 0.005))
    if risk_pct <= 0:
        return decision, None
    return apply_risk_based_sizing(
        decision,
        price=float(ctx.get("price") or 0),
        capital_total=float(ctx.get("capital_total") or ctx.get("usdt_available") or usdt_balance),
        usdt_available=float(ctx.get("usdt_available") or usdt_balance),
        risk_per_trade_pct=risk_pct,
        max_position_pct=max_position_pct,
        min_position_size=float(calibration.get("min_position_size", 0.005)),
        min_position_size_pct_notional=float(ctx.get("min_position_size_pct_notional", 0)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parsing del output LLM — extrae el primer objeto JSON balanceado tolerando
# prosa, fences markdown, y tags <think>.
# ─────────────────────────────────────────────────────────────────────────────

def _extract_first_json_object(text: str) -> dict:
    """Encuentra el primer objeto JSON balanceado en `text`.

    Tolera prosa antes/después, fences markdown (```json/```JSON),
    y tags de razonamiento <think>...</think>.
    """
    # 1. Eliminar tags <think> que algunos modelos emiten antes del JSON
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. Si el texto comienza con un fence markdown, extraer el bloque interior
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 3:
            block = parts[1]
            # Quitar etiqueta de lenguaje (json, JSON, python…) si la hay
            block = re.sub(r"^[a-zA-Z]+\s*\n?", "", block)
            text = block

    # 3. Buscar el primer '{' y extraer el objeto JSON balanceado
    start = text.find("{")
    if start == -1:
        raise ValueError("No se encontró objeto JSON en el texto del LLM")

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("Objeto JSON sin cerrar en el texto del LLM")


def _parse_llm_output(text: str) -> DecisorOutput:
    parsed = _extract_first_json_object(text)
    return DecisorOutput.model_validate(parsed)


# ---------------------------------------------------------------------------
# Two-pass helpers
# ---------------------------------------------------------------------------

def _build_review_ctx(decision: DecisorOutput,
                      warnings: list[CoherenceWarning],
                      original_ctx: dict[str, Any]) -> dict[str, Any]:
    """Arma el contexto para el template de auto-revisión.

    Recibe el ctx original del ciclo para inyectar niveles de precio (block_d_text)
    y el rango canónico de SL/TP calculado por el código, de modo que el LLM
    decida con información completa y no alucine resistencias por falta de contexto.
    """
    warnings_lines = "\n".join(
        f"  [{w.rule_id}] {w.message}" for w in warnings
    )

    ctx: dict[str, Any] = {
        "review_action": decision.action,
        "review_regime": decision.regime,
        "review_confidence": f"{decision.confidence:.2f}",
        "review_confluences": decision.confluences,
        "review_position_size_pct": decision.position_size_pct,
        "review_stop_loss": decision.stop_loss,
        "review_take_profit": decision.take_profit,
        "review_reasoning": (decision.reasoning or "")[:300],
        "review_warnings_block": warnings_lines,
        "review_has_c7": False,
        "review_c7_block": "",
        # Niveles de precio del ciclo actual para que el LLM los consulte
        "review_block_d": original_ctx.get("block_d_text", "  (sin datos)"),
    }

    # Si hay un warning C7, enriquecer con datos calculados en código
    # para que el LLM sepa exactamente qué TP necesita.
    c7_warnings = [w for w in warnings if w.rule_id == "C7"]
    if c7_warnings:
        ev = c7_warnings[0].evidence
        price = ev.get("price", 0.0)
        risk = ev.get("risk", 0.0)
        min_rr = ev.get("min_rr_ratio", 1.0)
        # TP mínimo calculado con el riesgo del SL que el LLM propuso en pass 1
        tp_min_llm = price + risk * min_rr if price > 0 and risk > 0 else None

        # TP mínimo canónico: calculado con el rango de SL que el código impone
        # (ATR × sl_atr_multiplier). Esto le da al LLM la referencia "piso" real.
        atr_ref = float(original_ctx.get("atr_ref") or 0)
        sl_mult = float(original_ctx.get("sl_atr_multiplier") or 0)
        canonical_risk = atr_ref * sl_mult if atr_ref > 0 and sl_mult > 0 else None
        tp_min_canonical = (
            price + canonical_risk * min_rr
            if canonical_risk and price > 0 else None
        )

        ctx["review_has_c7"] = True
        ctx["review_c7_block"] = (
            f"\n═══════════════════════════════════════════════════════════════\n"
            f"AJUSTE DE R:R REQUERIDO (C7)\n"
            f"═══════════════════════════════════════════════════════════════\n"
            f"El código calculó el R:R de tu decisión pass-1:\n"
            f"  precio_actual = ${price:,.2f}\n"
            f"  reward (TP − precio) = ${ev.get('reward', 0):.2f}\n"
            f"  risk   (precio − SL) = ${risk:.2f}  ← SL que propusiste\n"
            f"  R:R real             = {ev.get('rr_real', 0):.2f}  ←  mínimo requerido: {min_rr}\n"
            + (f"  TP mínimo con tu SL  = ${tp_min_llm:,.2f}\n" if tp_min_llm else "")
            + (
                f"\n"
                f"Referencia de rango canónico de SL (ATR × {sl_mult}):\n"
                f"  risk canónico (ATR={atr_ref:.0f} × {sl_mult}) = ${canonical_risk:.2f}\n"
                f"  TP mínimo canónico   = ${tp_min_canonical:,.2f}  "
                f"(precio + risk_canónico × {min_rr})\n"
                if canonical_risk and tp_min_canonical else ""
            )
            + f"\n"
            f"Niveles de precio disponibles en este ciclo:\n"
            f"{original_ctx.get('block_d_text', '  (sin datos)')}\n"
            f"\n"
            f"Tenés DOS opciones:\n"
            f"\n"
            f"  OPCIÓN A — Ajustar el TP:\n"
            f"    Buscá en los niveles de precio listados arriba una resistencia técnica\n"
            + (
                f"    ESTRICTAMENTE por encima de ${tp_min_canonical:,.2f} (TP mínimo canónico).\n"
                f"    Si tu SL es diferente al canónico, verificá que R:R = (TP_nuevo − {price:,.2f}) / risk > {min_rr}.\n"
                if tp_min_canonical else
                f"    que garantice un R:R > {min_rr} con tu SL actual.\n"
            )
            + f"    Si existe → emití BUY con ese nuevo TP (manteniendo o ajustando el SL dentro del rango canónico).\n"
            f"\n"
            f"  OPCIÓN B — Emitir HOLD:\n"
            f"    Si no existe ninguna resistencia técnica que cumpla el R:R,\n"
            f"    o si el precio ya está en zona de sobrecompra (BB%>95, Stoch>80, RSI>70),\n"
            f"    emití HOLD.\n"
        )

    return ctx


class _DefaultReviewDict(dict):
    """dict que devuelve '{key}' para claves faltantes en el template de revisión."""
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# ---------------------------------------------------------------------------
# Fallback de error
# ---------------------------------------------------------------------------

def _hold_decision(reason: str) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.RANGE, confluences=[], action=DecisorAction.HOLD,
        confidence_base=0.0, confidence_adjustment=0.0, confidence=0.0,
        stop_loss=None, take_profit=None, position_size_pct=0.0,
        expected_holding_min=1, reasoning=reason,
    )


def _hold_datos_insuficientes(reason: str) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.RANGE, confluences=[], action=DecisorAction.HOLD,
        confidence_base=0.95, confidence_adjustment=0.0, confidence=0.95,
        stop_loss=None, take_profit=None, position_size_pct=0.0,
        expected_holding_min=1, reasoning=reason,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_substitute(template: str, ctx: dict) -> str:
    """Replace {identifier} and {identifier:format_spec} placeholders found in ctx.

    Leaves unresolvable patterns (key not in ctx, invalid spec) untouched.
    Safe for templates that contain literal braces in JSON examples.
    """
    def replace(match: re.Match) -> str:
        key = match.group(1)
        fmt = match.group(2)
        if key not in ctx:
            return match.group(0)
        value = ctx[key]
        if isinstance(value, (dict, list)):
            return str(value)
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
