"""Tests for risk-based position sizing."""
import pytest

from shared.schemas import DecisorAction, DecisorOutput, MarketRegime
from shared.position_sizing import apply_risk_based_sizing


def _buy(sl: float, llm_pct: float = 0.02) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["A", "B"],
        action=DecisorAction.BUY,
        confidence_base=0.6,
        confidence_adjustment=0.0,
        confidence=0.6,
        stop_loss=sl,
        take_profit=110_000.0,
        position_size_pct=llm_pct,
        expected_holding_min=60,
        reasoning="test",
    )


def test_buy_sizing_from_risk_budget():
    decision = _buy(sl=95_000.0, llm_pct=0.02)
    updated, meta = apply_risk_based_sizing(
        decision,
        price=100_000.0,
        capital_total=10_000.0,
        usdt_available=8_000.0,
        risk_per_trade_pct=0.005,
        max_position_pct=0.15,
        min_position_size=0.005,
        min_position_size_pct_notional=0.0,
    )
    assert meta is not None
    assert meta["sl_distance_pct"] == 0.05
    assert updated.position_size_pct == pytest.approx(0.1)
    assert meta["position_size_pct_llm"] == 0.02
    assert meta["capped_by_max_position"] is False


def test_hold_unchanged():
    hold = DecisorOutput(
        regime=MarketRegime.RANGE,
        confluences=[],
        action=DecisorAction.HOLD,
        confidence_base=0.0,
        confidence_adjustment=0.0,
        confidence=0.0,
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        expected_holding_min=1,
        reasoning="hold",
    )
    out, meta = apply_risk_based_sizing(
        hold,
        price=100_000.0,
        capital_total=10_000.0,
        usdt_available=8_000.0,
        risk_per_trade_pct=0.005,
        max_position_pct=0.15,
        min_position_size=0.005,
        min_position_size_pct_notional=0.0,
    )
    assert out.position_size_pct == 0.0
    assert meta is None


def _short(stop_loss: float, take_profit: float, llm_pct: float = 0.02) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.TRENDING_DOWN,
        confluences=["A"],
        action=DecisorAction.SHORT,
        confidence_base=0.6,
        confidence_adjustment=0.0,
        confidence=0.6,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_pct=llm_pct,
        expected_holding_min=60,
        reasoning="test",
    )


def test_sizing_short_uses_directional_sl_distance():
    decision = _short(stop_loss=102_000.0, take_profit=96_000.0)
    updated, meta = apply_risk_based_sizing(
        decision,
        price=100_000.0,
        capital_total=1000.0,
        usdt_available=1000.0,
        risk_per_trade_pct=0.005,
        max_position_pct=0.10,
        min_position_size=0.0,
        min_position_size_pct_notional=0.0,
    )
    assert meta is not None
    assert abs(meta["sl_distance_pct"] - 0.02) < 1e-6
    assert abs(updated.position_size_pct - 0.10) < 1e-9


def test_capped_by_max_position():
    decision = _buy(sl=99_500.0)
    updated, meta = apply_risk_based_sizing(
        decision,
        price=100_000.0,
        capital_total=10_000.0,
        usdt_available=8_000.0,
        risk_per_trade_pct=0.01,
        max_position_pct=0.05,
        min_position_size=0.005,
        min_position_size_pct_notional=0.0,
    )
    assert updated.position_size_pct == 0.05
    assert meta["capped_by_max_position"] is True
