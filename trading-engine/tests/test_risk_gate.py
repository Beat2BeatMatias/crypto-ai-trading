"""Tests for RiskGate — 10 deterministic checks."""
from __future__ import annotations

import pytest
from shared.schemas import DecisorOutput, DecisorAction, MarketRegime
from risk.risk_gate import RiskGate, RiskVerdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gate(
    *,
    max_position_pct: float = 0.25,
    max_simultaneous_trades: int = 2,
    daily_stop_pct: float = -0.05,
    max_drawdown_pct: float = -0.20,
    max_slippage_pct: float = 0.005,
    taker_fee_pct: float = 0.001,
) -> RiskGate:
    return RiskGate(
        max_position_pct=max_position_pct,
        max_simultaneous_trades=max_simultaneous_trades,
        daily_stop_pct=daily_stop_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_slippage_pct=max_slippage_pct,
        taker_fee_pct=taker_fee_pct,
    )


def _buy_decision(
    stop_loss: float = 66000.0,
    take_profit: float = 69000.0,
    position_size_pct: float = 0.10,
) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["RSI oversold"],
        action=DecisorAction.BUY,
        confidence=0.8,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size_pct=position_size_pct,
        reasoning="Test BUY decision.",
    )


def _sell_decision() -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.TRENDING_DOWN,
        confluences=[],
        action=DecisorAction.SELL,
        confidence=0.7,
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        reasoning="Test SELL decision.",
    )


def _hold_decision() -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.RANGE,
        confluences=[],
        action=DecisorAction.HOLD,
        confidence=0.5,
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        reasoning="Test HOLD decision.",
    )


_COMMON_KWARGS = dict(
    current_price=67000.0,
    atr_1h=500.0,
    open_positions_count=0,
    daily_pnl_pct=0.0,
    total_drawdown_pct=-0.05,
    kill_switch=False,
    usdt_balance=10000.0,
    btc_held=0.0,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_valid_buy_passes_all_checks():
    # GIVEN a valid BUY decision meeting all constraints
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0, position_size_pct=0.10)

    # WHEN validated
    verdict = gate.validate(decision=decision, **_COMMON_KWARGS)

    # THEN it passes
    assert verdict.passed is True
    assert verdict.reason is None


def test_buy_without_stop_loss_rejected():
    # GIVEN a BUY decision with stop_loss=None (bypass Pydantic via object.__setattr__)
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0)
    object.__setattr__(decision, "stop_loss", None)

    # WHEN validated
    verdict = gate.validate(decision=decision, **_COMMON_KWARGS)

    # THEN it is rejected with the appropriate reason
    assert verdict.passed is False
    assert "stop_loss" in verdict.reason


def test_position_size_above_max_rejected():
    # GIVEN a BUY decision with position_size_pct exceeding the gate maximum
    gate = _make_gate(max_position_pct=0.10)
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0, position_size_pct=0.20)

    # WHEN validated
    verdict = gate.validate(decision=decision, **_COMMON_KWARGS)

    # THEN it is rejected
    assert verdict.passed is False
    assert "position_size_pct" in verdict.reason


def test_max_simultaneous_trades_exceeded_rejected():
    # GIVEN open_positions_count already at the limit
    gate = _make_gate(max_simultaneous_trades=2)
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0)
    kwargs = {**_COMMON_KWARGS, "open_positions_count": 2}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected
    assert verdict.passed is False
    assert "max_simultaneous_trades" in verdict.reason


def test_daily_stop_breach_rejects_buy():
    # GIVEN daily_pnl_pct below the daily_stop threshold
    gate = _make_gate(daily_stop_pct=-0.05)
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0)
    kwargs = {**_COMMON_KWARGS, "daily_pnl_pct": -0.06}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected
    assert verdict.passed is False
    assert "daily" in verdict.reason.lower()


def test_kill_switch_rejects_buy():
    # GIVEN kill_switch is active and decision is BUY
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0)
    kwargs = {**_COMMON_KWARGS, "kill_switch": True}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected
    assert verdict.passed is False
    assert "kill_switch" in verdict.reason


def test_kill_switch_allows_sell_to_close():
    # GIVEN kill_switch is active, decision is SELL, and btc_held > 0
    gate = _make_gate()
    decision = _sell_decision()
    kwargs = {**_COMMON_KWARGS, "kill_switch": True, "btc_held": 0.5, "open_positions_count": 1}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it passes — SELL-to-close is always allowed during kill switch
    assert verdict.passed is True


def test_sell_without_open_position_rejected():
    # GIVEN a SELL decision but no BTC held and no open positions
    gate = _make_gate()
    decision = _sell_decision()
    kwargs = {**_COMMON_KWARGS, "btc_held": 0.0, "open_positions_count": 0}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected
    assert verdict.passed is False
    assert "no open position" in verdict.reason


def test_rr_below_1_5_rejected():
    # GIVEN stop_loss=66800, take_profit=67100, current_price=67000
    # reward = 67100 - 67000 = 100, risk = 67000 - 66800 = 200 → R:R = 0.5 < 1.5
    # Use atr_1h=300 so 0.5*ATR=150 < SL_distance=200 (passes ATR check, fails R:R)
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66800.0, take_profit=67100.0)
    kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_1h": 300.0}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected for poor R:R
    assert verdict.passed is False
    assert "R:R" in verdict.reason


def test_sl_distance_below_half_atr_rejected():
    # GIVEN SL distance = 67000 - 66800 = 200, ATR = 500 → 0.5*ATR = 250 > 200
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66800.0, take_profit=None)
    kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_1h": 500.0}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected for SL too close
    assert verdict.passed is False
    assert "SL distance" in verdict.reason or "0.5*ATR" in verdict.reason


def test_total_drawdown_breach_rejects_buy():
    # GIVEN total_drawdown_pct at or below the max_drawdown_pct threshold
    gate = _make_gate(max_drawdown_pct=-0.20)
    decision = _buy_decision(stop_loss=66000.0, take_profit=69500.0)
    kwargs = {**_COMMON_KWARGS, "total_drawdown_pct": -0.20}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN it is rejected
    assert verdict.passed is False
    assert "max_drawdown" in verdict.reason


def test_hold_always_passes_even_with_kill_switch_and_daily_breach():
    # GIVEN kill_switch is active and daily P&L is breached, but action is HOLD
    gate = _make_gate()
    decision = _hold_decision()
    kwargs = {
        **_COMMON_KWARGS,
        "kill_switch": True,
        "daily_pnl_pct": -0.99,
        "total_drawdown_pct": -0.99,
    }

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN HOLD always passes regardless of other conditions
    assert verdict.passed is True
