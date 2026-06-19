"""
CoherenceChecker — detecta incoherencias y posibles alucinaciones del LLM Decisor.

Reglas C1–C10 (+ C1P/C2P/C3P/C4P bajistas): producen CoherenceWarning (warnings auditables).
En modo strict (coherence_strict_mode=True), C1/C2/C3/C1P/C2P/C3P/C4P/C7/C10 se convierten en rechazos duros.
C9 (mezcla alcista+bajista): en strict_mode filtra confluencias desalineadas y recalcula confianza.

        C7 es critical solo en strict_mode: el Risk Gate (R5) ya verifica R:R mínimo,
        por lo que no necesita bloqueo adicional fuera de strict_mode.

C10 (TP en extremo de rango 24h): warning por defecto; critical en strict_mode o cuando
el TP proyecta más allá del Low/High sin [TP_PROYECTADO] + justificación de ruptura.

No reemplazan al Risk Gate; se ejecutan ANTES del Risk Gate para que las advertencias
queden registradas en decisions.output.coherence_warnings incluso cuando el Risk Gate
aprueba la ejecución.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from agents.confluence_registry import STATIC_CONFLUENCE_CODES, evaluate_verify_spec
from shared.confluence_direction import (
    classify_confluences_by_direction,
    filter_confluences_for_direction,
    implied_signal_direction,
    registry_direction_allows_action,
)
from shared.schemas import DecisorOutput, DecisorAction, Direction, direction_for_action

logger = structlog.get_logger()


def _confluence_verify_tfs(ctx: dict[str, Any]) -> tuple[str, str]:
    primary = str(ctx.get("confluence_primary_tf") or "15m")
    secondary = str(ctx.get("confluence_secondary_tf") or "1h")
    if primary == secondary:
        structural = str(ctx.get("confluence_structural_tf") or "1h")
        if structural != primary:
            secondary = structural
    return primary, secondary


def _tf_block_float(ctx: dict[str, Any], tf: str, key: str) -> float:
    """Read indicator value from block_c_tf_blocks nested dict (replaces flat keys).
    Returns 0.0 if missing or None."""
    blocks = ctx.get("block_c_tf_blocks", {})
    blk = blocks.get(tf, {})
    if isinstance(blk, dict):
        val = blk.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0


def _tf_block_opt_float(ctx: dict[str, Any], tf: str, key: str) -> float | None:
    """Like _tf_block_float but returns None (not 0) when value is absent."""
    blocks = ctx.get("block_c_tf_blocks", {})
    blk = blocks.get(tf, {})
    if isinstance(blk, dict):
        val = blk.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None

def _ctx_tf_float(ctx: dict[str, Any], key_prefix: str, tf: str) -> float:
    val = ctx.get(f"{key_prefix}_{tf}")
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class CoherenceWarning:
    rule_id: str          # "C1" … "C10", "C1P" … "C4P"
    message: str
    severity: str = "warning"   # "warning" | "critical" (cuando strict_mode)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "severity": self.severity,
            "evidence": self.evidence,
        }


class CoherenceChecker:
    """
    Evalúa la consistencia del output del LLM contra la evidencia técnica del ciclo.

    Args:
        strict_mode: si True, C1/C2/C3/C1P/C2P/C3P pasan de warning a critical y deben
                     tratarse como rechazo en el Decisor.
    """

    def __init__(self, *, strict_mode: bool = False):
        self.strict_mode = strict_mode

    def evaluate(self, decision: DecisorOutput,
                 ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        Retorna la lista de CoherenceWarning encontradas para esta decisión.
        Lista vacía significa sin incoherencias detectadas.
        """
        warnings: list[CoherenceWarning] = []

        warnings.extend(self._c1_rsi_oversold_bounce(decision, ctx))
        warnings.extend(self._c2_macd_bullish_cross(decision, ctx))
        warnings.extend(self._c3_regime_vs_indicators(decision, ctx))
        warnings.extend(self._c1p_rsi_overbought_rejection(decision, ctx))
        warnings.extend(self._c2p_macd_bearish_cross(decision, ctx))
        warnings.extend(self._c3p_short_regime_vs_indicators(decision, ctx))
        warnings.extend(self._c4_confidence_without_confluences(decision, ctx))
        warnings.extend(self._c5_buy_low_confidence_no_tag(decision, ctx))
        warnings.extend(self._c5p_short_low_confidence_no_tag(decision, ctx))
        warnings.extend(self._c6_holding_vs_profile(decision, ctx))
        warnings.extend(self._c7_rr_ratio_verification(decision, ctx))
        warnings.extend(self._c10_tp_range_extreme_anchoring(decision, ctx))
        warnings.extend(self._c8_extended_confluence_verify(decision, ctx))
        warnings.extend(self._c9_opposing_confluence_mix(decision, ctx))
        warnings.extend(self._c4p_bb_upper_rejection(decision, ctx))
        warnings.extend(self._c9_promoted_direction_vs_action(decision, ctx))

        if warnings:
            logger.warning(
                "coherence.warnings_detected",
                action=decision.action,
                regime=decision.regime,
                confidence=decision.confidence,
                warnings=[w.rule_id for w in warnings],
                strict_mode=self.strict_mode,
            )

        return warnings

    def has_critical(self, warnings: list[CoherenceWarning]) -> bool:
        """True si hay algún warning crítico (requiere bloqueo en strict_mode)."""
        return any(w.severity == "critical" for w in warnings)

    # ------------------------------------------------------------------ #
    # Reglas individuales
    # ------------------------------------------------------------------ #

    def _c1_rsi_oversold_bounce(self, decision: DecisorOutput,
                                ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C1: Confluencia A declarada pero RSI no en sobreventa en TFs de verificación
        (primary/secondary alineados a atr_timeframe + perfil).
        """
        if "A" not in decision.confluences:
            return []
        primary, secondary = _confluence_verify_tfs(ctx)
        rsi_primary = _tf_block_float(ctx, primary, "rsi")
        rsi_secondary = _tf_block_float(ctx, secondary, "rsi")
        if rsi_primary < 35 or rsi_secondary < 35:
            return []

        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C1",
            severity=severity,
            message=(
                f"Confluencia 'A' (RSI_OVERSOLD_BOUNCE) declarada pero "
                f"RSI({primary})={rsi_primary:.1f} y RSI({secondary})={rsi_secondary:.1f} — "
                f"ninguno está en zona de sobreventa (<35)."
            ),
            evidence={
                "rsi_primary": rsi_primary,
                "rsi_secondary": rsi_secondary,
                "confluence_primary_tf": primary,
                "confluence_secondary_tf": secondary,
            },
        )]

    def _c2_macd_bullish_cross(self, decision: DecisorOutput,
                               ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C2: Confluencia B declarada pero MACD ≤ Signal en TFs de verificación.
        """
        if "B" not in decision.confluences:
            return []
        primary, secondary = _confluence_verify_tfs(ctx)
        macd_p = _tf_block_float(ctx, primary, "macd")
        sig_p = _tf_block_float(ctx, primary, "macd_signal")
        macd_s = _tf_block_float(ctx, secondary, "macd")
        sig_s = _tf_block_float(ctx, secondary, "macd_signal")

        if macd_p > sig_p or macd_s > sig_s:
            return []

        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C2",
            severity=severity,
            message=(
                f"Confluencia 'B' (MACD_BULLISH_CROSS) declarada pero "
                f"MACD({primary})={macd_p:.2f}<={sig_p:.2f} y "
                f"MACD({secondary})={macd_s:.2f}<={sig_s:.2f}."
            ),
            evidence={
                "macd_primary": macd_p, "sig_primary": sig_p,
                "macd_secondary": macd_s, "sig_secondary": sig_s,
                "confluence_primary_tf": primary,
                "confluence_secondary_tf": secondary,
            },
        )]

    def _c3_regime_vs_indicators(self, decision: DecisorOutput,
                                 ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C3: Incoherencia entre el régimen declarado y los indicadores del ciclo.
        - TRENDING_UP declarado: requiere EMAs alineadas 1h O ADX >20
          O precio sobre EMA9 en TF primario (early entry en tendencia nueva).
        - TRENDING_DOWN declarado: precio sobre EMA20(1h) y EMA50(1h) → sospechoso.
        """
        from shared.schemas import MarketRegime
        regime = decision.regime
        price = ctx.get("price") or 0
        ema20_1h = _tf_block_float(ctx, "1h", "ema20")
        ema50_1h = _tf_block_float(ctx, "1h", "ema50")

        tf_blocks = ctx.get("block_c_tf_blocks", {})
        adx_15m = (tf_blocks.get("15m") or {}).get("adx")
        adx_1h = (tf_blocks.get("1h") or {}).get("adx")
        adx = adx_1h or adx_15m

        primary, _ = _confluence_verify_tfs(ctx)
        close_primary = _tf_block_float(ctx, primary, "close")
        ema9_primary = _tf_block_float(ctx, primary, "ema9")

        warnings: list[CoherenceWarning] = []

        if regime == MarketRegime.TRENDING_UP:
            emas_aligned = price > ema20_1h > ema50_1h > 0
            adx_strong = adx is not None and adx > 20
            price_over_ema9 = close_primary > ema9_primary > 0
            if not emas_aligned and not adx_strong and not price_over_ema9:
                severity = "critical" if self.strict_mode else "warning"
                warnings.append(CoherenceWarning(
                    rule_id="C3",
                    severity=severity,
                    message=(
                        f"Régimen TRENDING_UP declarado pero EMAs(1h) no alineadas "
                        f"(precio={price:,.0f}, EMA20={ema20_1h:,.0f}, EMA50={ema50_1h:,.0f}) "
                        f"y ADX={adx or 'n/d'} (<20 o desconocido)."
                    ),
                    evidence={
                        "price": price, "ema20_1h": ema20_1h,
                        "ema50_1h": ema50_1h, "adx": adx,
                    },
                ))

        elif regime == MarketRegime.TRENDING_DOWN:
            if price > ema20_1h > 0 and price > ema50_1h > 0:
                severity = "critical" if self.strict_mode else "warning"
                warnings.append(CoherenceWarning(
                    rule_id="C3",
                    severity=severity,
                    message=(
                        f"Régimen TRENDING_DOWN declarado pero precio ({price:,.0f}) "
                        f"está sobre EMA20(1h)={ema20_1h:,.0f} y EMA50(1h)={ema50_1h:,.0f}."
                    ),
                    evidence={
                        "price": price, "ema20_1h": ema20_1h, "ema50_1h": ema50_1h,
                    },
                ))

        return warnings

    def _c1p_rsi_overbought_rejection(self, decision: DecisorOutput,
                                      ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C1P: Confluencia I declarada pero RSI no en sobrecompra en TFs de verificación.
        """
        if "I" not in decision.confluences:
            return []
        primary, secondary = _confluence_verify_tfs(ctx)
        rsi_primary = _tf_block_float(ctx, primary, "rsi")
        rsi_secondary = _tf_block_float(ctx, secondary, "rsi")
        overbought_threshold = float(ctx.get("rsi_overbought_threshold", 65))
        if rsi_primary > overbought_threshold or rsi_secondary > overbought_threshold:
            return []

        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C1P",
            severity=severity,
            message=(
                f"Confluencia 'I' (RSI_OVERBOUGHT_REJECTION) declarada pero "
                f"RSI({primary})={rsi_primary:.1f} y RSI({secondary})={rsi_secondary:.1f} — "
                f"ninguno está en zona de sobrecompra (>{overbought_threshold:.0f})."
            ),
            evidence={
                "rsi_primary": rsi_primary,
                "rsi_secondary": rsi_secondary,
                "overbought_threshold": overbought_threshold,
                "confluence_primary_tf": primary,
                "confluence_secondary_tf": secondary,
            },
        )]

    def _c2p_macd_bearish_cross(self, decision: DecisorOutput,
                                ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C2P: Confluencia J declarada pero MACD ≥ Signal en TFs de verificación.
        """
        if "J" not in decision.confluences:
            return []
        primary, secondary = _confluence_verify_tfs(ctx)
        macd_p = _tf_block_float(ctx, primary, "macd")
        sig_p = _tf_block_float(ctx, primary, "macd_signal")
        macd_s = _tf_block_float(ctx, secondary, "macd")
        sig_s = _tf_block_float(ctx, secondary, "macd_signal")

        if macd_p < sig_p or macd_s < sig_s:
            return []

        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C2P",
            severity=severity,
            message=(
                f"Confluencia 'J' (MACD_BEARISH_CROSS) declarada pero "
                f"MACD({primary})={macd_p:.2f}>={sig_p:.2f} y "
                f"MACD({secondary})={macd_s:.2f}>={sig_s:.2f}."
            ),
            evidence={
                "macd_primary": macd_p, "sig_primary": sig_p,
                "macd_secondary": macd_s, "sig_secondary": sig_s,
                "confluence_primary_tf": primary,
                "confluence_secondary_tf": secondary,
            },
        )]

    def _c3p_short_regime_vs_indicators(self, decision: DecisorOutput,
                                        ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C3P (C3′): Incoherencia régimen/estructura para aperturas SHORT.
        - TRENDING_DOWN + SHORT: requiere EMAs bajistas 1h O ADX >20
          O precio bajo EMA9 en TF primario (early entry en tendencia nueva).
        - TRENDING_UP + SHORT: régimen alcista declarado con acción bajista.
        """
        if decision.action != DecisorAction.SHORT:
            return []

        from shared.schemas import MarketRegime
        regime = decision.regime
        price = ctx.get("price") or 0
        ema20_1h = _tf_block_float(ctx, "1h", "ema20")
        ema50_1h = _tf_block_float(ctx, "1h", "ema50")

        tf_blocks = ctx.get("block_c_tf_blocks", {})
        adx_15m = (tf_blocks.get("15m") or {}).get("adx")
        adx_1h = (tf_blocks.get("1h") or {}).get("adx")
        adx = adx_1h or adx_15m

        primary, _ = _confluence_verify_tfs(ctx)
        close_primary = _tf_block_float(ctx, primary, "close")
        ema9_primary = _tf_block_float(ctx, primary, "ema9")

        warnings: list[CoherenceWarning] = []
        severity = "critical" if self.strict_mode else "warning"

        if regime == MarketRegime.TRENDING_UP:
            warnings.append(CoherenceWarning(
                rule_id="C3P",
                severity=severity,
                message=(
                    "SHORT declarado con régimen TRENDING_UP — "
                    "estructura alcista incompatible con apertura bajista."
                ),
                evidence={"regime": regime.value, "action": decision.action.value},
            ))
            return warnings

        if regime == MarketRegime.TRENDING_DOWN:
            emas_bearish = (
                price > 0 and ema20_1h > 0 and ema50_1h > 0
                and price < ema20_1h < ema50_1h
            )
            adx_strong = adx is not None and adx > 20
            price_under_ema9 = 0 < close_primary < ema9_primary
            if not emas_bearish and not adx_strong and not price_under_ema9:
                warnings.append(CoherenceWarning(
                    rule_id="C3P",
                    severity=severity,
                    message=(
                        f"SHORT con régimen TRENDING_DOWN pero EMAs(1h) no alineadas "
                        f"a la baja (precio={price:,.0f}, EMA20={ema20_1h:,.0f}, "
                        f"EMA50={ema50_1h:,.0f}) y ADX={adx or 'n/d'} (<20 o desconocido)."
                    ),
                    evidence={
                        "price": price, "ema20_1h": ema20_1h,
                        "ema50_1h": ema50_1h, "adx": adx,
                    },
                ))

        return warnings

    def _c4_confidence_without_confluences(self, decision: DecisorOutput,
                                           ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C4: confidence muy alta con pocas confluencias.
        - confidence ≥ 0.85 con < 2 confluencias.
        - confidence ≥ 0.70 en BUY con 0 confluencias.
        """
        conf = decision.confidence
        n = len(decision.confluences)
        warnings: list[CoherenceWarning] = []

        if conf >= 0.85 and n < 2:
            warnings.append(CoherenceWarning(
                rule_id="C4",
                message=(
                    f"confidence={conf:.2f} (≥0.85) pero solo {n} confluencias. "
                    f"La base server-side difícilmente alcanza ese valor con <2 señales."
                ),
                evidence={"confidence": conf, "confluences": n},
            ))
        elif conf >= 0.70 and decision.action in (DecisorAction.BUY, DecisorAction.SHORT) and n == 0:
            warnings.append(CoherenceWarning(
                rule_id="C4",
                message=(
                    f"{decision.action.value} con confidence={conf:.2f} (≥0.70) "
                    f"y 0 confluencias del catálogo."
                ),
                evidence={"confidence": conf, "confluences": n, "action": decision.action.value},
            ))

        return warnings

    def _low_confidence_entry_without_tag(
        self, decision: DecisorOutput, *, rule_id: str,
    ) -> list[CoherenceWarning]:
        if decision.confidence >= 0.50:
            return []
        reasoning = decision.reasoning or ""
        tags = ("[CONTRA_REGIMEN]", "[SIZING]", "[BAJA_CONFIANZA]")
        if any(tag in reasoning for tag in tags):
            return []
        return [CoherenceWarning(
            rule_id=rule_id,
            message=(
                f"{decision.action.value} con confidence={decision.confidence:.2f} (<0.50) "
                f"sin tag explicativo ({', '.join(tags)}) en reasoning."
            ),
            evidence={"confidence": decision.confidence, "action": decision.action.value},
        )]

    def _c5_buy_low_confidence_no_tag(self, decision: DecisorOutput,
                                      ctx: dict[str, Any]) -> list[CoherenceWarning]:
        if decision.action != DecisorAction.BUY:
            return []
        return self._low_confidence_entry_without_tag(decision, rule_id="C5")

    def _c5p_short_low_confidence_no_tag(self, decision: DecisorOutput,
                                         ctx: dict[str, Any]) -> list[CoherenceWarning]:
        if decision.action != DecisorAction.SHORT:
            return []
        return self._low_confidence_entry_without_tag(decision, rule_id="C5P")

    def _c6_holding_vs_profile(self, decision: DecisorOutput,
                               ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C6: expected_holding_min fuera del rango del perfil operativo.
        Aplica a BUY y SHORT — para HOLD/SELL el campo es semánticamente irrelevante.
        """
        if decision.action not in (DecisorAction.BUY, DecisorAction.SHORT):
            return []

        holding = decision.expected_holding_min
        profile = ctx.get("block_a_profile", "HIBRIDO")
        min_hold = ctx.get("block_a_holding_range_min", 10)
        max_hold = ctx.get("block_a_holding_range_max", 480)

        if holding is None or holding < 1:
            return []
        if min_hold <= holding <= max_hold:
            return []

        return [CoherenceWarning(
            rule_id="C6",
            message=(
                f"expected_holding_min={holding} fuera del rango del perfil "
                f"{profile} ({min_hold}–{max_hold} min)."
            ),
            evidence={
                "expected_holding_min": holding,
                "profile": profile,
                "holding_range": [min_hold, max_hold],
            },
        )]

    def _c7_rr_ratio_verification(self, decision: DecisorOutput,
                                   ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C7: R:R real calculado en código no supera el mínimo configurado.

        El LLM tiende a alucinar el R:R reportado en reasoning. Esta regla
        lo recalcula deterministicamente con el precio real del contexto
        (ctx["price"] — mismo valor que usará el RiskGate) y emite un warning
        CRITICAL solo en strict_mode. Fuera de strict_mode es warning: el Risk
        Gate (R5) ya rechaza trades con R:R insuficiente, por lo que C7 sería
        redundante como bloqueo.
        """
        direction = direction_for_action(decision.action)
        if direction is None:
            return []
        if decision.stop_loss is None or decision.take_profit is None:
            return []

        price = ctx.get("price") or 0.0
        min_rr = ctx.get("min_rr_ratio", 1.3)

        if price <= 0:
            return []

        if direction == Direction.LONG:
            sl_distance = price - decision.stop_loss
            reward = decision.take_profit - price
        else:
            sl_distance = decision.stop_loss - price
            reward = price - decision.take_profit

        if sl_distance <= 0:
            return []

        rr_real = reward / sl_distance

        if rr_real > min_rr:
            return []

        return [CoherenceWarning(
            rule_id="C7",
            severity="critical" if self.strict_mode else "warning",
            message=(
                f"R:R real={rr_real:.2f} ≤ min_rr_ratio={min_rr} "
                f"(TP={decision.take_profit:,.2f}, precio={price:,.2f}, "
                f"SL={decision.stop_loss:,.2f}). "
                f"reward={reward:.2f}, risk={sl_distance:.2f}."
            ),
            evidence={
                "rr_real": round(rr_real, 4),
                "min_rr_ratio": min_rr,
                "price": price,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
                "reward": round(reward, 2),
                "risk": round(sl_distance, 2),
            },
        )]

    @staticmethod
    def _reasoning_has_tag(reasoning: str | None, tag: str) -> bool:
        return tag.upper() in (reasoning or "").upper()

    @staticmethod
    def _reasoning_has_rupture(reasoning: str | None) -> bool:
        r = (reasoning or "").lower()
        keywords = (
            "ruptura", "breakout", "cierre por debajo", "cierre bajo",
            "close below", "break abajo", "perforación", "perforacion",
            "invalidación", "invalidacion", "cierre por encima", "break arriba",
        )
        return any(k in r for k in keywords)

    def _c10_tp_range_extreme_anchoring(
        self,
        decision: DecisorOutput,
        ctx: dict[str, Any],
    ) -> list[CoherenceWarning]:
        """
        C10: TP proyectado más allá del Low/High 24h estando el precio en el extremo
        del rango, sin [TP_PROYECTADO] y justificación de ruptura en el reasoning.

        Complementa C7: el R:R puede ser válido pero el TP queda "flotante" lejos
        de niveles del Bloque D sin tesis de breakout.
        """
        direction = direction_for_action(decision.action)
        if direction is None or decision.take_profit is None:
            return []

        low_24h = float(ctx.get("block_d_low_24h") or ctx.get("low_24h") or 0)
        high_24h = float(ctx.get("block_d_high_24h") or ctx.get("high_24h") or 0)
        if low_24h <= 0 or high_24h <= 0:
            return []

        at_range_low = bool(ctx.get("at_range_low"))
        at_range_high = bool(ctx.get("at_range_high"))
        has_tp_proj = self._reasoning_has_tag(decision.reasoning, "[TP_PROYECTADO]")
        has_rupture = self._reasoning_has_rupture(decision.reasoning)
        justified = has_tp_proj and has_rupture

        tp = float(decision.take_profit)
        violations: list[tuple[str, float, float]] = []

        if direction == Direction.SHORT and at_range_low and tp < low_24h:
            violations.append(("below_low_24h", low_24h, tp))
        if direction == Direction.LONG and at_range_high and tp > high_24h:
            violations.append(("above_high_24h", high_24h, tp))

        if not violations or justified:
            return []

        kind, anchor, projected_tp = violations[0]
        if not has_tp_proj:
            severity = "critical"
        elif self.strict_mode:
            severity = "critical"
        else:
            severity = "warning"

        anchor_label = "Low 24h" if kind == "below_low_24h" else "High 24h"
        return [CoherenceWarning(
            rule_id="C10",
            severity=severity,
            message=(
                f"TP=${projected_tp:,.2f} proyectado más allá de {anchor_label} "
                f"(${anchor:,.2f}) con precio en extremo de rango "
                f"(at_range_low={at_range_low}, at_range_high={at_range_high}). "
                f"Requiere [TP_PROYECTADO] y ruptura/breakout explícitos, "
                f"o anclar TP en nivel del Bloque D."
            ),
            evidence={
                "kind": kind,
                "anchor_level": anchor,
                "take_profit": projected_tp,
                "at_range_low": at_range_low,
                "at_range_high": at_range_high,
                "has_tp_proyectado": has_tp_proj,
                "has_rupture_justification": has_rupture,
            },
        )]

    def _c4p_bb_upper_rejection(self, decision: DecisorOutput,
                                ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C4P: Confluencia L (BB_UPPER_REJECTION) declarada pero BB% 5m no >95.
        """
        if "L" not in decision.confluences:
            return []
        bb_pct_5m = _tf_block_opt_float(ctx, "5m", "bb_pct")
        if bb_pct_5m is None:
            return []
        if bb_pct_5m > 95:
            return []
        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C4P",
            severity=severity,
            message=(
                f"Confluencia 'L' (BB_UPPER_REJECTION) declarada pero "
                f"BB% 5m={bb_pct_5m:.0f}% ≤ 95% — "
                f"no hay evidencia de sobrecompra extrema en Bollinger Bands."
            ),
            evidence={
                "bb_pct_5m": bb_pct_5m,
                "threshold": 95.0,
            },
        )]

    def _c8_extended_confluence_verify(
        self,
        decision: DecisorOutput,
        ctx: dict[str, Any],
    ) -> list[CoherenceWarning]:
        specs = ctx.get("registry_verify_specs") or {}
        if not specs:
            return []
        warnings: list[CoherenceWarning] = []
        for code in decision.confluences:
            if code in STATIC_CONFLUENCE_CODES or code not in specs:
                continue
            if evaluate_verify_spec(specs[code], ctx):
                continue
            warnings.append(CoherenceWarning(
                rule_id="C8",
                severity="warning",
                message=(
                    f"Confluencia extendida '{code}' declarada pero verify_spec "
                    "no se cumple con la evidencia del ciclo."
                ),
                evidence={"code": code, "verify_spec": specs[code]},
            ))
        return warnings

    def _c9_opposing_confluence_mix(
        self,
        decision: DecisorOutput,
        ctx: dict[str, Any],
    ) -> list[CoherenceWarning]:
        """
        C9: Mezcla de confluencias alcistas (A–H) y bajistas (I–P) en la misma decisión.
        En strict_mode la severidad es critical para habilitar filtrado server-side.
        """
        registry = ctx.get("registry_direction_by_code") or {}
        long_codes, short_codes, _ = classify_confluences_by_direction(
            decision.confluences, registry,
        )
        if not long_codes or not short_codes:
            return []

        trading_product = str(ctx.get("trading_product") or "spot").lower()
        preferred = implied_signal_direction(decision, trading_product=trading_product)
        keep, drop = filter_confluences_for_direction(
            decision.confluences, preferred, registry,
        )
        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C9",
            severity=severity,
            message=(
                f"Mezcla de confluencias alcistas ({long_codes}) y bajistas ({short_codes}). "
                f"Declarar ambas infla la confianza sin señal operativa clara."
            ),
            evidence={
                "kind": "opposing_mix",
                "long_codes": long_codes,
                "short_codes": short_codes,
                "preferred_direction": preferred.value if preferred else None,
                "confluences_to_keep": keep,
                "confluences_to_drop": drop,
            },
        )]

    def _c9_promoted_direction_vs_action(
        self,
        decision: DecisorOutput,
        ctx: dict[str, Any],
    ) -> list[CoherenceWarning]:
        directions = ctx.get("registry_direction_by_code") or {}
        if not directions:
            return []
        trading_product = str(ctx.get("trading_product") or "spot").lower()
        warnings: list[CoherenceWarning] = []
        for code in decision.confluences:
            if code in STATIC_CONFLUENCE_CODES or code not in directions:
                continue
            direction = directions[code]
            if registry_direction_allows_action(
                direction,
                decision.action,
                trading_product=trading_product,
            ):
                continue
            warnings.append(CoherenceWarning(
                rule_id="C9",
                severity="warning",
                message=(
                    f"Confluencia promovida '{code}' etiquetada [{direction}] "
                    f"no aplica a action={decision.action} "
                    f"(trading_product={trading_product})."
                ),
                evidence={
                    "kind": "registry_tag",
                    "code": code,
                    "direction": direction,
                    "action": decision.action,
                    "trading_product": trading_product,
                },
            ))
        return warnings
