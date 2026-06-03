"""Tests for Pydantic schemas in shared/schemas.py."""
import pytest
from pydantic import ValidationError

from shared.schemas import (
    DecisorAction,
    DecisorOutput,
    Direction,
    MarketRegime,
    TradeOutcome,
    direction_for_action,
)


def test_direction_enum_values():
    assert Direction.LONG.value == "LONG"
    assert Direction.SHORT.value == "SHORT"


def test_decisor_action_includes_short():
    assert DecisorAction.SHORT.value == "SHORT"


def test_direction_for_action_maps_entries():
    assert direction_for_action(DecisorAction.BUY) == Direction.LONG
    assert direction_for_action(DecisorAction.SHORT) == Direction.SHORT
    assert direction_for_action(DecisorAction.SELL) is None
    assert direction_for_action(DecisorAction.HOLD) is None


def _valid_short_payload() -> dict:
    return {
        "regime": "TRENDING_DOWN",
        "confluences": ["RSI overbought 5m"],
        "action": "SHORT",
        "confidence_base": 0.60,
        "confidence_adjustment": 0.0,
        "confidence": 0.60,
        "stop_loss": 102000.0,
        "take_profit": 96000.0,
        "position_size_pct": 0.05,
        "reasoning": "Régimen bajista con rechazo en resistencia",
    }


def test_short_requires_sl_and_tp():
    payload = _valid_short_payload()
    payload["stop_loss"] = None
    payload["take_profit"] = None
    with pytest.raises(ValidationError) as exc_info:
        DecisorOutput(**payload)
    assert "stop_loss is required when action=SHORT" in str(exc_info.value)


def test_short_with_sl_tp_ok():
    output = DecisorOutput(**_valid_short_payload())
    assert output.action == DecisorAction.SHORT
    assert output.stop_loss == 102000.0
    assert output.take_profit == 96000.0


def _valid_buy_payload() -> dict:
    return {
        "regime": "TRENDING_UP",
        "confluences": ["RSI oversold 5m", "EMA50 support 1h"],
        "action": "BUY",
        "confidence_base": 0.70,
        "confidence_adjustment": 0.05,
        "confidence": 0.75,
        "stop_loss": 60000.0,
        "take_profit": 62000.0,
        "position_size_pct": 0.05,
        "reasoning": "RSI saliendo de sobreventa, soporte EMA50, volumen creciente",
    }


def test_valid_buy_passes():
    # GIVEN a fully valid BUY payload
    payload = _valid_buy_payload()

    # WHEN parsed
    output = DecisorOutput(**payload)

    # THEN all fields are set correctly
    assert output.action == DecisorAction.BUY
    assert output.regime == MarketRegime.TRENDING_UP
    assert output.stop_loss == 60000.0
    assert output.take_profit == 62000.0
    assert output.confidence == 0.75
    assert output.position_size_pct == 0.05


def test_buy_without_stop_loss_raises():
    # GIVEN a BUY payload missing stop_loss
    payload = _valid_buy_payload()
    payload["stop_loss"] = None

    # WHEN parsed
    # THEN ValidationError is raised
    with pytest.raises(ValidationError) as exc_info:
        DecisorOutput(**payload)

    assert "stop_loss is required when action=BUY" in str(exc_info.value)


def test_position_size_pct_above_max_raises():
    # GIVEN a payload with position_size_pct > 0.25
    payload = _valid_buy_payload()
    payload["position_size_pct"] = 0.26

    # WHEN parsed
    # THEN ValidationError is raised
    with pytest.raises(ValidationError):
        DecisorOutput(**payload)


def test_confidence_above_one_raises():
    # GIVEN a payload with confidence > 1.0
    payload = _valid_buy_payload()
    payload["confidence"] = 1.01

    # WHEN parsed
    # THEN ValidationError is raised
    with pytest.raises(ValidationError):
        DecisorOutput(**payload)


def test_hold_without_stop_loss_passes():
    # GIVEN a HOLD payload with no stop_loss (valid because only BUY requires it)
    payload = {
        "regime": "RANGE",
        "confluences": [],
        "action": "HOLD",
        "confidence": 0.5,
        "stop_loss": None,
        "take_profit": None,
        "position_size_pct": 0.0,
        "reasoning": "Mercado sin tendencia clara, esperando confirmacion",
    }

    # WHEN parsed
    output = DecisorOutput(**payload)

    # THEN no error and action is HOLD
    assert output.action == DecisorAction.HOLD
    assert output.stop_loss is None
    assert output.confidence == pytest.approx(0.0)  # recomputed from confidence_base=0.0 + confidence_adjustment=0.0


def test_buy_without_take_profit_raises():
    # GIVEN a BUY payload missing take_profit
    payload = _valid_buy_payload()
    payload["take_profit"] = None

    # WHEN parsed
    # THEN ValidationError is raised
    with pytest.raises(ValidationError) as exc_info:
        DecisorOutput(**payload)

    assert "take_profit is required when action=BUY" in str(exc_info.value)


def test_hold_with_expected_holding_min_zero_coerced_to_one():
    # GIVEN un payload HOLD donde el LLM retorna expected_holding_min=0
    # (caso real: Gemini 2.5 Flash env\u00eda 0 para decisiones HOLD)
    payload = {
        "regime": "TRENDING_DOWN",
        "confluences": ["A", "H"],
        "action": "HOLD",
        "confidence_base": 0.0,
        "confidence_adjustment": 0.0,
        "confidence": 0.0,
        "stop_loss": None,
        "take_profit": None,
        "position_size_pct": 0.0,
        "expected_holding_min": 0,
        "reasoning": "HOLD. No se cumplen criterios de entrada.",
    }

    # WHEN parseado
    output = DecisorOutput(**payload)

    # THEN el validador coerciona 0 → 1 sin lanzar ValidationError
    assert output.expected_holding_min == 1
    assert output.action == DecisorAction.HOLD


def test_hold_with_expected_holding_min_none_coerced_to_one():
    # GIVEN un payload HOLD donde el LLM retorna expected_holding_min=null
    payload = {
        "regime": "RANGE",
        "confluences": [],
        "action": "HOLD",
        "confidence": 0.0,
        "stop_loss": None,
        "take_profit": None,
        "position_size_pct": 0.0,
        "expected_holding_min": None,
        "reasoning": "HOLD sin confluencias.",
    }

    # WHEN parseado
    output = DecisorOutput(**payload)

    # THEN el validador coerciona None → 1 sin lanzar ValidationError
    assert output.expected_holding_min == 1


def test_reasoning_above_1000_chars_truncated():
    # GIVEN a payload with reasoning exceeding 1000 characters
    # the _truncate_reasoning validator silently truncates instead of raising
    payload = _valid_buy_payload()
    payload["reasoning"] = "x" * 1100

    # WHEN parsed
    output = DecisorOutput(**payload)

    # THEN reasoning is truncated to 1000 chars (997 + "...")
    assert len(output.reasoning) == 1000
    assert output.reasoning.endswith("...")


# ─────────────── P1-T2: coerciones ───────────────

def test_confidence_adjustment_when_slightly_above_max_should_clamp_to_010():
    payload = _valid_buy_payload()
    payload["confidence_adjustment"] = 0.101
    output = DecisorOutput(**payload)
    assert output.confidence_adjustment == pytest.approx(0.10)


def test_confidence_adjustment_when_below_min_should_clamp_to_minus_010():
    payload = _valid_buy_payload()
    payload["confidence_adjustment"] = -0.15
    output = DecisorOutput(**payload)
    assert output.confidence_adjustment == pytest.approx(-0.10)


def test_confidence_adjustment_when_none_should_default_to_zero():
    payload = _valid_buy_payload()
    payload["confidence_adjustment"] = None
    output = DecisorOutput(**payload)
    assert output.confidence_adjustment == 0.0


def test_market_regime_neutral_should_be_accepted():
    payload = _valid_buy_payload()
    payload["regime"] = "NEUTRAL"
    payload["action"] = "HOLD"
    payload["stop_loss"] = None
    payload["take_profit"] = None
    payload["position_size_pct"] = 0.0
    output = DecisorOutput(**payload)
    assert output.regime == MarketRegime.NEUTRAL
