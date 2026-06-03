"""Tests unitarios para CoherenceChecker — reglas C1 a C7."""
from __future__ import annotations

import pytest

from risk.coherence_checker import CoherenceChecker
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime


# ---------------------------------------------------------------------------
# Helper para construir un DecisorOutput mínimo
# ---------------------------------------------------------------------------

def _buy(
    *,
    stop_loss: float = 76900.0,
    take_profit: float = 78500.0,
    confluences: list[str] | None = None,
    confidence: float = 0.75,
    expected_holding_min: int = 45,
    reasoning: str = "[DECISION] test [MERCADO] test [SENALES] test [CONFIANZA] test [NIVELES] test",
) -> DecisorOutput:
    return DecisorOutput(
        action=DecisorAction.BUY,
        regime=MarketRegime.RANGE,
        confluences=confluences or ["H", "C"],
        confidence_base=confidence,
        confidence_adjustment=0.0,
        confidence=confidence,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_pct=0.05,
        expected_holding_min=expected_holding_min,
        reasoning=reasoning,
    )


def _hold() -> DecisorOutput:
    return DecisorOutput(
        action=DecisorAction.HOLD,
        regime=MarketRegime.RANGE,
        confluences=[],
        confidence_base=0.5,
        confidence_adjustment=0.0,
        confidence=0.5,
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        expected_holding_min=30,
        reasoning="[DECISION] hold [MERCADO] test [SENALES] - [CONFIANZA] test",
    )


def _ctx(**overrides) -> dict:
    base = {
        "price": 78000.0,
        "rsi_15m": 55.0,
        "rsi_1h": 52.0,
        "macd_15m": 10.0,
        "sig_15m": 8.0,
        "macd_1h": 5.0,
        "sig_1h": 3.0,
        "ema20_1h": 77500.0,
        "ema50_1h": 77000.0,
        "block_a_profile": "HIBRIDO",
        "block_a_holding_range_min": 15,
        "block_a_holding_range_max": 240,
        "block_c_tf_blocks": {
            "15m": {"adx": 22.0},
            "1h": {"adx": 25.0},
        },
        "min_rr_ratio": 1.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# C1P / C2P / C3P — espejo bajista (SHORT)
# ---------------------------------------------------------------------------

def _short(**kwargs) -> DecisorOutput:
    defaults = dict(
        action=DecisorAction.SHORT,
        regime=MarketRegime.TRENDING_DOWN,
        confluences=["I", "J"],
        confidence_base=0.75,
        confidence_adjustment=0.0,
        confidence=0.75,
        stop_loss=68500.0,
        take_profit=65000.0,
        position_size_pct=0.05,
        expected_holding_min=45,
        reasoning="[DECISION] short [MERCADO] test [SENALES] test [CONFIANZA] test [NIVELES] test",
    )
    defaults.update(kwargs)
    return DecisorOutput(**defaults)


class TestC1pC2pC3pBearish:

    def test_c1p_no_warning_when_rsi_overbought(self):
        decision = _short(confluences=["I", "F"])
        ctx = _ctx(rsi_15m=68.0, rsi_1h=55.0)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c1p = [w for w in warnings if w.rule_id == "C1P"]

        assert c1p == []

    def test_c1p_warning_when_rsi_not_overbought(self):
        decision = _short(confluences=["I", "F"])
        ctx = _ctx(rsi_15m=55.0, rsi_1h=52.0)

        warnings = CoherenceChecker(strict_mode=False).evaluate(decision, ctx)
        c1p = [w for w in warnings if w.rule_id == "C1P"]

        assert len(c1p) == 1
        assert c1p[0].severity == "warning"
        assert "RSI_OVERBOUGHT" in c1p[0].message

    def test_c1p_critical_in_strict_mode(self):
        decision = _short(confluences=["I"])
        ctx = _ctx(rsi_15m=50.0, rsi_1h=50.0)

        warnings = CoherenceChecker(strict_mode=True).evaluate(decision, ctx)
        c1p = [w for w in warnings if w.rule_id == "C1P"]

        assert len(c1p) == 1
        assert c1p[0].severity == "critical"

    def test_c2p_no_warning_when_macd_bearish_on_one_tf(self):
        decision = _short(confluences=["J", "F"])
        ctx = _ctx(macd_15m=5.0, sig_15m=8.0, macd_1h=10.0, sig_1h=8.0)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c2p = [w for w in warnings if w.rule_id == "C2P"]

        assert c2p == []

    def test_c2p_warning_when_macd_not_bearish(self):
        decision = _short(confluences=["J", "F"])
        ctx = _ctx(macd_15m=10.0, sig_15m=8.0, macd_1h=10.0, sig_1h=8.0)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c2p = [w for w in warnings if w.rule_id == "C2P"]

        assert len(c2p) == 1
        assert "MACD_BEARISH" in c2p[0].message

    def test_c3p_warning_short_in_trending_up(self):
        decision = _short(regime=MarketRegime.TRENDING_UP, confluences=["F"])
        ctx = _ctx(price=78000.0, ema20_1h=77500.0, ema50_1h=77000.0)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c3p = [w for w in warnings if w.rule_id == "C3P"]

        assert len(c3p) == 1
        assert "TRENDING_UP" in c3p[0].message

    def test_c3p_warning_trending_down_without_bearish_structure(self):
        decision = _short(regime=MarketRegime.TRENDING_DOWN, confluences=["F"])
        ctx = _ctx(
            price=78000.0,
            ema20_1h=77500.0,
            ema50_1h=77000.0,
            block_c_tf_blocks={"15m": {"adx": 15.0}, "1h": {"adx": 18.0}},
        )

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c3p = [w for w in warnings if w.rule_id == "C3P"]

        assert len(c3p) == 1

    def test_c3p_no_warning_when_bearish_emas_aligned(self):
        decision = _short(regime=MarketRegime.TRENDING_DOWN, confluences=["F"])
        ctx = _ctx(price=76000.0, ema20_1h=77000.0, ema50_1h=77500.0)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c3p = [w for w in warnings if w.rule_id == "C3P"]

        assert c3p == []

    def test_c3p_skipped_for_buy(self):
        decision = _buy(confluences=["A"])
        ctx = _ctx(rsi_15m=55.0, rsi_1h=52.0)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c3p = [w for w in warnings if w.rule_id == "C3P"]

        assert c3p == []


# ---------------------------------------------------------------------------
# C6 — expected_holding_min vs rango del perfil
# ---------------------------------------------------------------------------

class TestC6HoldingVsProfile:

    def test_c6_skipped_for_hold_with_holding_min_1(self):
        """Regresión a4ca161b: HOLD con expected_holding_min=1 no debe generar C6."""
        # GIVEN decisión HOLD con expected_holding_min=1 (valor por defecto del fallback)
        decision = DecisorOutput(
            action=DecisorAction.HOLD,
            regime=MarketRegime.RANGE,
            confluences=[],
            confidence_base=0.55,
            confidence_adjustment=0.0,
            confidence=0.55,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.0,
            expected_holding_min=1,
            reasoning="[DECISION] HOLD [MERCADO] test [SENALES] - [CONFIANZA] test",
        )
        ctx = _ctx(block_a_profile="HIBRIDO", block_a_holding_range_min=30, block_a_holding_range_max=180)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c6 = [w for w in warnings if w.rule_id == "C6"]

        # THEN
        assert c6 == []

    def test_c6_no_warning_when_buy_holding_within_range(self):
        # GIVEN BUY con holding dentro del rango del perfil
        decision = _buy(expected_holding_min=60)
        ctx = _ctx(block_a_profile="HIBRIDO", block_a_holding_range_min=30, block_a_holding_range_max=180)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c6 = [w for w in warnings if w.rule_id == "C6"]

        # THEN
        assert c6 == []

    def test_c6_warning_when_buy_holding_below_range(self):
        # GIVEN BUY con holding por debajo del mínimo del perfil
        decision = _buy(expected_holding_min=10)
        ctx = _ctx(block_a_profile="HIBRIDO", block_a_holding_range_min=30, block_a_holding_range_max=180)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c6 = [w for w in warnings if w.rule_id == "C6"]

        # THEN
        assert len(c6) == 1
        assert c6[0].severity == "warning"
        assert "expected_holding_min=10" in c6[0].message
        assert c6[0].evidence["expected_holding_min"] == 10

    def test_c6_warning_when_buy_holding_above_range(self):
        # GIVEN BUY con holding por encima del máximo del perfil
        decision = _buy(expected_holding_min=300)
        ctx = _ctx(block_a_profile="HIBRIDO", block_a_holding_range_min=30, block_a_holding_range_max=180)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c6 = [w for w in warnings if w.rule_id == "C6"]

        # THEN
        assert len(c6) == 1
        assert "expected_holding_min=300" in c6[0].message

    def test_c6_boundary_exactly_at_minimum_passes(self):
        # GIVEN BUY con holding exactamente en el mínimo del rango
        decision = _buy(expected_holding_min=30)
        ctx = _ctx(block_a_profile="HIBRIDO", block_a_holding_range_min=30, block_a_holding_range_max=180)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c6 = [w for w in warnings if w.rule_id == "C6"]

        # THEN
        assert c6 == []

    def test_c5p_warning_when_short_low_confidence_without_tag(self):
        decision = _short(
            confidence_base=0.42,
            confidence_adjustment=0.0,
            confidence=0.42,
            reasoning="[DECISION] short sin tags",
        )
        warnings = CoherenceChecker().evaluate(decision, _ctx())
        c5p = [w for w in warnings if w.rule_id == "C5P"]
        assert len(c5p) == 1

    def test_c6_applies_to_short_holding_out_of_range(self):
        decision = _short(expected_holding_min=500)
        ctx = _ctx(block_a_profile="HIBRIDO", block_a_holding_range_min=30, block_a_holding_range_max=180)
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c6 = [w for w in warnings if w.rule_id == "C6"]
        assert len(c6) == 1


# ---------------------------------------------------------------------------
# C7 — R:R real calculado en código
# ---------------------------------------------------------------------------

class TestC7RRRatioVerification:

    def test_c7_no_warning_when_rr_above_minimum(self):
        # GIVEN BUY con TP/SL que dan R:R > min_rr_ratio
        # price=78000, sl=77100 (risk=900), tp=79100 (reward=1100) → R:R=1.22 > 1.0
        decision = _buy(stop_loss=77100.0, take_profit=79100.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay warning C7
        assert c7 == [], f"No debería haber C7 cuando R:R es suficiente, got: {c7}"

    def test_c7_critical_when_rr_below_minimum(self):
        # GIVEN el caso real: price=78000, sl=77100 (risk=900), tp=78500 (reward=500)
        # R:R real = 500/900 = 0.56 < 1.0
        decision = _buy(stop_loss=77100.0, take_profit=78500.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN hay un warning C7 crítico
        assert len(c7) == 1
        assert c7[0].severity == "critical"
        assert "0.56" in c7[0].message
        assert c7[0].evidence["rr_real"] == pytest.approx(0.5556, rel=1e-2)

    def test_c7_critical_always_regardless_of_strict_mode(self):
        # GIVEN strict_mode=False — C7 es critical igualmente
        decision = _buy(stop_loss=77100.0, take_profit=78500.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker(strict_mode=False).evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN sigue siendo critical aunque strict_mode=False
        assert len(c7) == 1
        assert c7[0].severity == "critical"

    def test_c7_uses_context_price_not_llm_reported(self):
        # GIVEN el LLM podría reportar R:R=1.02 pero el precio real da 0.56
        # price=78000, sl=77100, tp=78500
        decision = _buy(
            stop_loss=77100.0,
            take_profit=78500.0,
            reasoning="[NIVELES] SL $77,100. TP $78,500. R:R = 1.02",
        )
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN detecta que el R:R real es 0.56, no el 1.02 que dijo el LLM
        assert len(c7) == 1
        assert c7[0].evidence["rr_real"] == pytest.approx(0.5556, rel=1e-2)

    def test_c7_skipped_for_hold(self):
        # GIVEN acción HOLD (sin SL/TP)
        decision = _hold()
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay C7
        assert c7 == []

    def test_c7_skipped_when_no_price_in_ctx(self):
        # GIVEN price=0 en el contexto (datos insuficientes)
        decision = _buy(stop_loss=77100.0, take_profit=78500.0)
        ctx = _ctx(price=0.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay C7 (sin precio no se puede calcular)
        assert c7 == []

    def test_c7_skipped_when_sl_above_price(self):
        # GIVEN sl >= price (ya lo rechaza R2 del RiskGate — C7 no evalúa)
        decision = _buy(stop_loss=78100.0, take_profit=79000.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay C7 (sl_distance <= 0)
        assert c7 == []

    def test_c7_evidence_contains_all_fields(self):
        # GIVEN BUY con R:R insuficiente
        decision = _buy(stop_loss=77100.0, take_profit=78500.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN el evidence tiene todos los campos para auditoría
        ev = c7[0].evidence
        assert "rr_real" in ev
        assert "min_rr_ratio" in ev
        assert "price" in ev
        assert "stop_loss" in ev
        assert "take_profit" in ev
        assert "reward" in ev
        assert "risk" in ev
        assert ev["reward"] == pytest.approx(500.0)
        assert ev["risk"] == pytest.approx(900.0)

    def test_c7_boundary_exactly_at_minimum_is_rejected(self):
        # GIVEN R:R = exactamente min_rr_ratio (debe rechazarse — condición es >)
        # price=78000, sl=77500 (risk=500), tp=78500 (reward=500) → R:R=1.0 = min
        decision = _buy(stop_loss=77500.0, take_profit=78500.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN se rechaza porque R:R=1.0 no es MAYOR que 1.0
        assert len(c7) == 1

    def test_c7_boundary_just_above_minimum_passes(self):
        # GIVEN R:R ligeramente superior al mínimo
        # price=78000, sl=77499 (risk=501), tp=78500 (reward=500) → R:R≈0.998 ← aún bajo
        # usamos sl=77400 (risk=600), tp=78700 (reward=700) → R:R=1.167 > 1.0
        decision = _buy(stop_loss=77400.0, take_profit=78700.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay C7
        assert c7 == []

    def test_c7_respects_min_rr_ratio_from_context(self):
        # GIVEN min_rr_ratio=1.5 en el contexto
        # price=78000, sl=77100 (risk=900), tp=79500 (reward=1500) → R:R=1.67 > 1.5
        decision = _buy(stop_loss=77100.0, take_profit=79500.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.5)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay C7 porque 1.67 > 1.5
        assert c7 == []

    def test_c7_triggers_with_higher_min_rr_ratio(self):
        # GIVEN min_rr_ratio=1.5 pero R:R=1.2 — no alcanza
        # price=78000, sl=77500 (risk=500), tp=78600 (reward=600) → R:R=1.2 < 1.5
        decision = _buy(stop_loss=77500.0, take_profit=78600.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.5)

        # WHEN
        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN hay C7
        assert len(c7) == 1
        assert "1.5" in c7[0].message

    def test_c7_short_critical_when_rr_below_minimum(self):
        decision = DecisorOutput(
            action=DecisorAction.SHORT,
            regime=MarketRegime.TRENDING_DOWN,
            confluences=["H"],
            confidence_base=0.7,
            confidence_adjustment=0.0,
            confidence=0.7,
            stop_loss=68500.0,
            take_profit=66000.0,
            position_size_pct=0.05,
            expected_holding_min=30,
            reasoning="[DECISION] short test",
        )
        ctx = _ctx(price=67000.0, min_rr_ratio=1.3)

        warnings = CoherenceChecker().evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        assert len(c7) == 1
        assert c7[0].severity == "critical"


# ---------------------------------------------------------------------------
# C8 — verify_spec para confluencias extendidas I–Z
# ---------------------------------------------------------------------------

def test_c8_extended_confluence_verify_fails_when_spec_not_met():
    checker = CoherenceChecker(strict_mode=False)
    decision = _buy(confluences=["H", "K"])
    ctx = _ctx(
        registry_verify_specs={
            "K": {"all": [{"ctx": "volume_ratio", "lt": 0.8}]},
        },
        volume_ratio=1.2,
    )

    warnings = checker.evaluate(decision, ctx)
    c8 = [w for w in warnings if w.rule_id == "C8"]

    assert len(c8) == 1
    assert c8[0].severity == "warning"


def test_c8_skips_static_catalog_codes():
    checker = CoherenceChecker(strict_mode=False)
    decision = _buy(confluences=["A", "H"])
    ctx = _ctx(registry_verify_specs={"A": {"all": [{"ctx": "rsi_15m", "lt": 10}]}})

    warnings = checker.evaluate(decision, ctx)
    assert [w for w in warnings if w.rule_id == "C8"] == []


# ---------------------------------------------------------------------------
# has_critical — C7 siempre activa el bloqueo
# ---------------------------------------------------------------------------

class TestHasCritical:

    def test_has_critical_true_when_c7_present(self):
        # GIVEN BUY con R:R insuficiente
        decision = _buy(stop_loss=77100.0, take_profit=78500.0)
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)
        checker = CoherenceChecker(strict_mode=False)

        # WHEN
        warnings = checker.evaluate(decision, ctx)

        # THEN has_critical es True independientemente de strict_mode
        assert checker.has_critical(warnings) is True

    def test_has_critical_false_when_only_non_critical_warnings(self):
        # GIVEN BUY con R:R suficiente pero con warning C4 (no critical en no-strict)
        decision = DecisorOutput(
            action=DecisorAction.BUY,
            regime=MarketRegime.RANGE,
            confluences=[],
            confidence_base=0.9,
            confidence_adjustment=0.0,
            confidence=0.9,
            stop_loss=77100.0,
            take_profit=79500.0,
            position_size_pct=0.05,
            expected_holding_min=45,
            reasoning="[DECISION] test [MERCADO] test [SENALES] test [CONFIANZA] test [NIVELES] test",
        )
        # R:R = (79500-78000)/(78000-77100) = 1500/900 = 1.67 > 1.0 — C7 no dispara
        ctx = _ctx(price=78000.0, min_rr_ratio=1.0)
        checker = CoherenceChecker(strict_mode=False)

        # WHEN
        warnings = checker.evaluate(decision, ctx)
        c7 = [w for w in warnings if w.rule_id == "C7"]

        # THEN no hay C7
        assert c7 == []
