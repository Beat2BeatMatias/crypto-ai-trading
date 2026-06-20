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


def test_short_sizing_raises_floor_for_tp_notional_futures_3x():
    decision = _short(stop_loss=64_150.0, take_profit=61_314.7, llm_pct=0.02)
    updated, meta = apply_risk_based_sizing(
        decision,
        price=63_204.9,
        capital_total=250.0,
        usdt_available=250.0,
        risk_per_trade_pct=0.005,
        max_position_pct=0.20,
        min_position_size=0.0,
        min_position_size_pct_notional=50.0 / (250.0 * 3),
        min_notional_usdt=50.0,
        leverage=3.0,
        trading_product="futures",
    )
    assert meta is not None
    assert updated.position_size_pct >= 0.02
    notional = 250.0 * updated.position_size_pct * 3.0
    qty = notional / 63_204.9
    assert qty * 61_314.7 >= 50.0


def test_min_qty_base_raises_floor():
    """Cuando min_qty_base > 0, el position_size_pct debe asegurar qty >= min_qty_base."""
    decision = _buy(sl=64_000.0, llm_pct=0.02)
    updated, meta = apply_risk_based_sizing(
        decision,
        price=64_500.0,
        capital_total=250.0,
        usdt_available=250.0,
        risk_per_trade_pct=0.005,
        max_position_pct=0.30,
        min_position_size=0.0,
        min_position_size_pct_notional=0.0,
        min_notional_usdt=5.0,
        min_qty_base=0.001,
        leverage=1.0,
        trading_product="spot",
    )
    assert meta is not None
    notional = 250.0 * updated.position_size_pct
    qty = notional / 64_500.0
    assert qty >= 0.001, f"qty {qty:.6f} < min_qty_base 0.001"


def test_min_qty_base_futures_3x():
    """min_qty_base con apalancamiento 3x en futuros."""
    decision = _short(stop_loss=65_000.0, take_profit=63_000.0, llm_pct=0.02)
    updated, meta = apply_risk_based_sizing(
        decision,
        price=64_500.0,
        capital_total=250.0,
        usdt_available=250.0,
        risk_per_trade_pct=0.005,
        max_position_pct=0.20,
        min_position_size=0.0,
        min_position_size_pct_notional=0.0,
        min_notional_usdt=5.0,
        min_qty_base=0.001,
        leverage=3.0,
        trading_product="futures",
    )
    assert meta is not None
    notional = 250.0 * updated.position_size_pct * 3.0
    qty = notional / 64_500.0
    assert qty >= 0.001, f"qty {qty:.6f} < min_qty_base 0.001"


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
