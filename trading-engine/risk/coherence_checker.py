"""
CoherenceChecker — detecta incoherencias y posibles alucinaciones del LLM Decisor.

Reglas C1–C7: producen CoherenceWarning (warnings auditables).
En modo strict (coherence_strict_mode=True), C1/C2/C3/C7 se convierten en rechazos duros.

C7 es siempre critical independientemente del strict_mode: el LLM no puede alucinar
el R:R porque el código lo recalcula con el precio real del contexto.

No reemplazan al Risk Gate; se ejecutan ANTES del Risk Gate para que las advertencias
queden registradas en decisions.output.coherence_warnings incluso cuando el Risk Gate
aprueba la ejecución.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from agents.confluence_registry import STATIC_CONFLUENCE_CODES, evaluate_verify_spec
from shared.schemas import DecisorOutput, DecisorAction

logger = structlog.get_logger()


@dataclass
class CoherenceWarning:
    rule_id: str          # "C1" … "C7"
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
        strict_mode: si True, C1/C2/C3 pasan de warning a critical y deben
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
        warnings.extend(self._c4_confidence_without_confluences(decision, ctx))
        warnings.extend(self._c5_buy_low_confidence_no_tag(decision, ctx))
        warnings.extend(self._c6_holding_vs_profile(decision, ctx))
        warnings.extend(self._c7_rr_ratio_verification(decision, ctx))
        warnings.extend(self._c8_extended_confluence_verify(decision, ctx))

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
        C1: El LLM declara confluencia "A" (RSI_OVERSOLD_BOUNCE) pero ninguno de
        los RSI relevantes (15m y 1h) está por debajo de 35.
        """
        if "A" not in decision.confluences:
            return []
        rsi_15m = ctx.get("rsi_15m") or 0
        rsi_1h = ctx.get("rsi_1h") or 0
        if rsi_15m < 35 or rsi_1h < 35:
            return []

        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C1",
            severity=severity,
            message=(
                f"Confluencia 'A' (RSI_OVERSOLD_BOUNCE) declarada pero "
                f"RSI(15m)={rsi_15m:.1f} y RSI(1h)={rsi_1h:.1f} — "
                f"ninguno está en zona de sobreventa (<35)."
            ),
            evidence={"rsi_15m": rsi_15m, "rsi_1h": rsi_1h},
        )]

    def _c2_macd_bullish_cross(self, decision: DecisorOutput,
                               ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C2: El LLM declara confluencia "B" (MACD_BULLISH_CROSS) pero MACD ≤ Signal
        tanto en 15m como en 1h.
        """
        if "B" not in decision.confluences:
            return []
        macd_15m = ctx.get("macd_15m") or 0
        sig_15m = ctx.get("sig_15m") or 0
        macd_1h = ctx.get("macd_1h") or 0
        sig_1h = ctx.get("sig_1h") or 0

        cross_15m = macd_15m > sig_15m
        cross_1h = macd_1h > sig_1h
        if cross_15m or cross_1h:
            return []

        severity = "critical" if self.strict_mode else "warning"
        return [CoherenceWarning(
            rule_id="C2",
            severity=severity,
            message=(
                f"Confluencia 'B' (MACD_BULLISH_CROSS) declarada pero "
                f"MACD(15m)={macd_15m:.2f}<={sig_15m:.2f} y "
                f"MACD(1h)={macd_1h:.2f}<={sig_1h:.2f}."
            ),
            evidence={
                "macd_15m": macd_15m, "sig_15m": sig_15m,
                "macd_1h": macd_1h, "sig_1h": sig_1h,
            },
        )]

    def _c3_regime_vs_indicators(self, decision: DecisorOutput,
                                 ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C3: Incoherencia entre el régimen declarado y los indicadores del ciclo.
        - TRENDING_UP declarado: requiere al menos EMAs alineadas en 1h O ADX > 20.
        - TRENDING_DOWN declarado: precio sobre EMA20(1h) y EMA50(1h) → sospechoso.
        """
        from shared.schemas import MarketRegime
        regime = decision.regime
        price = ctx.get("price") or 0
        ema20_1h = ctx.get("ema20_1h") or 0
        ema50_1h = ctx.get("ema50_1h") or 0

        # Obtener ADX del bloque C si está disponible
        tf_blocks = ctx.get("block_c_tf_blocks", {})
        adx_15m = (tf_blocks.get("15m") or {}).get("adx")
        adx_1h = (tf_blocks.get("1h") or {}).get("adx")
        adx = adx_1h or adx_15m

        warnings: list[CoherenceWarning] = []

        if regime == MarketRegime.TRENDING_UP:
            emas_aligned = price > ema20_1h > ema50_1h > 0
            adx_strong = adx is not None and adx > 20
            if not emas_aligned and not adx_strong:
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
                    f"La fórmula de 7 pasos difícilmente alcanza ese valor con <2 señales."
                ),
                evidence={"confidence": conf, "confluences": n},
            ))
        elif conf >= 0.70 and decision.action == DecisorAction.BUY and n == 0:
            warnings.append(CoherenceWarning(
                rule_id="C4",
                message=(
                    f"BUY con confidence={conf:.2f} (≥0.70) y 0 confluencias del catálogo."
                ),
                evidence={"confidence": conf, "confluences": n},
            ))

        return warnings

    def _c5_buy_low_confidence_no_tag(self, decision: DecisorOutput,
                                      ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C5: BUY con confidence < 0.50 sin tag explicativo en reasoning.
        El LLM puede operar con confianza baja, pero debe justificarlo con
        [CONTRA_REGIMEN] o [SIZING] en el campo reasoning.
        """
        if decision.action != DecisorAction.BUY:
            return []
        if decision.confidence >= 0.50:
            return []
        reasoning = decision.reasoning or ""
        has_tag = any(tag in reasoning for tag in ["[CONTRA_REGIMEN]", "[SIZING]", "[BAJA_CONFIANZA]"])
        if has_tag:
            return []
        return [CoherenceWarning(
            rule_id="C5",
            message=(
                f"BUY con confidence={decision.confidence:.2f} (<0.50) sin tag "
                f"explicativo ([CONTRA_REGIMEN], [SIZING] o [BAJA_CONFIANZA]) en reasoning."
            ),
            evidence={"confidence": decision.confidence, "action": decision.action},
        )]

    def _c6_holding_vs_profile(self, decision: DecisorOutput,
                               ctx: dict[str, Any]) -> list[CoherenceWarning]:
        """
        C6: expected_holding_min fuera del rango del perfil operativo.
        """
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
        CRITICAL si el resultado no supera min_rr_ratio.

        Siempre es critical: un BUY con R:R insuficiente no tiene corrección
        posible en two-pass (los niveles SL/TP no cambian con la revisión),
        por lo que el Decisor debe degradarlo a HOLD directamente.
        """
        if decision.action != DecisorAction.BUY:
            return []
        if decision.stop_loss is None or decision.take_profit is None:
            return []

        price = ctx.get("price") or 0.0
        min_rr = ctx.get("min_rr_ratio", 1.3)

        if price <= 0:
            return []

        sl_distance = price - decision.stop_loss
        if sl_distance <= 0:
            return []

        reward = decision.take_profit - price
        rr_real = reward / sl_distance

        if rr_real > min_rr:
            return []

        return [CoherenceWarning(
            rule_id="C7",
            severity="critical",
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
