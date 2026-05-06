"""Tests for Pydantic schemas in shared/schemas.py."""
import pytest
from pydantic import ValidationError

from shared.schemas import DecisorAction, DecisorOutput, MarketRegime, TradeOutcome


def _valid_buy_payload() -> dict:
    return {
        "regime": "TRENDING_UP",
        "confluences": ["RSI oversold 5m", "EMA50 support 1h"],
        "action": "BUY",
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


def test_buy_without_take_profit_raises():
    # GIVEN a BUY payload missing take_profit
    payload = _valid_buy_payload()
    payload["take_profit"] = None

    # WHEN parsed
    # THEN ValidationError is raised
    with pytest.raises(ValidationError) as exc_info:
        DecisorOutput(**payload)

    assert "take_profit is required when action=BUY" in str(exc_info.value)


def test_reasoning_above_240_chars_raises():
    # GIVEN a payload with reasoning exceeding 240 characters
    payload = _valid_buy_payload()
    payload["reasoning"] = "x" * 241

    # WHEN parsed
    # THEN ValidationError is raised
    with pytest.raises(ValidationError):
        DecisorOutput(**payload)
