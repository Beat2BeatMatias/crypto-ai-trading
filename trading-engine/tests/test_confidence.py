"""Tests for shared.confidence — server-side confidence_base."""
import pytest

from shared.confidence import (
    apply_server_confidence,
    compute_confidence_base,
    effective_confluence_count,
    quality_factor,
    regime_factor,
)
from shared.schemas import DecisorAction, DecisorOutput, Direction, MarketRegime


def _hold_output(**overrides) -> DecisorOutput:
    payload = {
        "regime": "RANGE",
        "confluences": [],
        "action": "HOLD",
        "confidence_base": 0.0,
        "confidence_adjustment": 0.0,
        "confidence": 0.0,
        "stop_loss": None,
        "take_profit": None,
        "position_size_pct": 0.0,
        "expected_holding_min": 1,
        "reasoning": "test",
    }
    payload.update(overrides)
    return DecisorOutput.model_validate(payload)


def test_effective_confluence_count_includes_static_and_extended():
    assert effective_confluence_count(["B", "I"]) == 2


def test_quality_factor_with_g_is_one():
    assert quality_factor(["A", "G"]) == pytest.approx(1.0)
    assert quality_factor(["B", "C"]) == pytest.approx(0.85)


def test_regime_factor_trending_down_long_is_zero():
    assert regime_factor(
        MarketRegime.TRENDING_DOWN, {}, direction=Direction.LONG,
    ) == pytest.approx(0.0)


def test_regime_factor_neutral_when_direction_unknown():
    assert regime_factor(MarketRegime.TRENDING_DOWN, {}) == pytest.approx(0.85)


def test_regime_factor_short_favors_trending_down():
    cal = {}
    assert regime_factor(MarketRegime.TRENDING_DOWN, cal, direction=Direction.SHORT) > 0.5
    assert regime_factor(MarketRegime.TRENDING_UP, cal, direction=Direction.SHORT) == 0.0
    assert regime_factor(MarketRegime.TRENDING_DOWN, cal, direction=Direction.LONG) == 0.0
    assert regime_factor(MarketRegime.TRENDING_UP, cal, direction=Direction.LONG) == 1.0


def test_compute_confidence_base_two_confluences_range():
    base, meta = compute_confidence_base(
        ["B", "C"], MarketRegime.RANGE, {},
    )
    assert base == pytest.approx(0.70 * 0.85 * 0.85)
    assert meta["confluence_count"] == 2
    assert meta["extended_confluence_weight"] == 1.0


def test_compute_confidence_base_three_with_promoted_i():
    base, meta = compute_confidence_base(
        ["B", "C", "I"], MarketRegime.RANGE, {},
    )
    assert meta["confluence_count"] == 3
    assert base == pytest.approx(0.85 * 0.85 * 0.85)


def test_apply_server_confidence_overrides_llm_base_and_recomputes_confidence():
    decision = _hold_output(
        confluences=["B", "C"],
        confidence_base=0.0,
        confidence_adjustment=0.05,
        confidence=0.99,
    )
    updated, meta = apply_server_confidence(decision, calibration={})
    assert updated.confidence_base == pytest.approx(0.70 * 0.85 * 0.85)
    assert updated.confidence_adjustment == pytest.approx(0.05)
    assert updated.confidence == pytest.approx(updated.confidence_base + 0.05)
    assert "confluences_dropped" not in meta


def test_hold_trending_down_futures_uses_short_regime_factor():
    decision = _hold_output(
        regime="TRENDING_DOWN",
        confluences=["I", "J"],
    )
    updated, meta = apply_server_confidence(
        decision, calibration={}, trading_product="futures",
    )
    assert meta["regime_factor"] == pytest.approx(1.0)
    assert meta.get("hold_signal_direction") == "SHORT"
    assert updated.confidence_base == pytest.approx(0.70 * 0.85 * 1.0)
    assert updated.confidence > 0.0


def test_hold_trending_down_futures_b_and_j_counts_only_short():
    decision = _hold_output(
        regime="TRENDING_DOWN",
        confluences=["B", "J"],
    )
    updated, meta = apply_server_confidence(
        decision, calibration={}, trading_product="futures",
    )
    assert meta["confluence_count"] == 1
    assert meta["confluences_counted"] == ["J"]
    assert meta["confluences_excluded_direction"] == ["B"]
    assert updated.confidence_base == pytest.approx(0.55 * 0.85 * 1.0)
    assert meta["confidence_base_inflated"] == pytest.approx(0.70 * 0.85 * 1.0)


def test_hold_trending_down_spot_uses_neutral_regime_factor():
    decision = _hold_output(regime="TRENDING_DOWN", confluences=["A", "C"])
    updated, meta = apply_server_confidence(
        decision, calibration={}, trading_product="spot",
    )
    assert meta["regime_factor"] == pytest.approx(0.85)
    assert updated.confidence_base > 0.0


def test_apply_server_confidence_records_dropped_codes():
    decision = _hold_output(confluences=["B"])
    updated, meta = apply_server_confidence(
        decision,
        calibration={},
        confluences_dropped=["J", "X"],
    )
    assert updated.confluences == ["B"]
    assert meta["confluences_dropped"] == ["J", "X"]
