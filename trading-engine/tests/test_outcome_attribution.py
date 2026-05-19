from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agents.outcome_attribution import (
    DecisionAttribution,
    _compute_mfe_mae,
    attribute,
)


def _candle(t, *, high, low, close=None, open_=None):
    return SimpleNamespace(
        time=t,
        open=Decimal(str(open_ if open_ is not None else low)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close if close is not None else high)),
    )


def test_decision_attribution_dataclass_is_frozen():
    attr = DecisionAttribution(
        decision_id=uuid4(),
        horizon_min=240,
        matured=False,
        forward_return_pct=None,
        mfe_pct=None,
        mae_pct=None,
        time_to_mfe_min=None,
        time_to_mae_min=None,
        sl_dist_pct=None,
        tp_target_pct=None,
        classification="UNKNOWN",
        computed_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        attr.classification = "GOOD_HOLD"  # type: ignore[misc]


def test_attribute_returns_unknown_when_decision_missing_inputs():
    """Una decisión sin price/atr_pct en su input se clasifica UNKNOWN."""
    decision = _make_decision(input={}, output={"action": "HOLD"})
    result = attribute(
        decision=decision,
        ohlcv_1m=[],
        associated_trade=None,
        horizon_min=240,
        now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert result.classification == "UNKNOWN"
    assert result.decision_id == decision.id


def test_compute_mfe_mae_simple_rally():
    """Precio sube de 100 a 102, sin caer abajo de 99.95 — mfe +2 %, mae -0.05 %."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.5, low=99.95),
        _candle(t0 + timedelta(minutes=2), high=101.0, low=99.98),
        _candle(t0 + timedelta(minutes=3), high=102.0, low=100.5),
    ]
    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(price_t=100.0, candles=candles, ts0=t0)
    assert mfe == pytest.approx(2.0, abs=1e-6)
    assert mae == pytest.approx(-0.05, abs=1e-6)
    assert t_mfe == 3
    assert t_mae == 1


def test_compute_mfe_mae_empty_candles():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(price_t=100.0, candles=[], ts0=t0)
    assert (mfe, mae, t_mfe, t_mae) == (None, None, None, None)


def test_compute_mfe_mae_drop_then_recover():
    """Cae primero a 99, sube a 101 — t_mae < t_mfe."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.0),
        _candle(t0 + timedelta(minutes=2), high=101.0, low=100.0),
    ]
    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(price_t=100.0, candles=candles, ts0=t0)
    assert mfe == pytest.approx(1.0, abs=1e-6)
    assert mae == pytest.approx(-1.0, abs=1e-6)
    assert t_mae == 1
    assert t_mfe == 2


def test_classify_hold_as_missed_when_mfe_exceeds_target_and_no_sl_hit():
    """AC OA-02: price_t=100, atr_pct=1.0, sl_mult=0.3, rr=1.3 → SL_dist=0.3%, TP_target=0.39%.
    MFE +0.5% > tp_target, MAE -0.05% > -SL_dist, MFE alcanzado primero → MISSED_OPPORTUNITY.
    """
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={
            "price": 100.0, "atr_ref_pct": 1.0,
            "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3,
        },
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.05, low=99.95, close=100.05),
        _candle(t0 + timedelta(minutes=10), high=100.5, low=100.0, close=100.5),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "MISSED_OPPORTUNITY"
    assert result.mfe_pct == pytest.approx(0.5, abs=1e-3)
    assert result.tp_target_pct == pytest.approx(0.39, abs=1e-3)
    assert result.matured is True


def test_classify_hold_as_good_when_mae_exceeds_sl_before_mfe():
    """AC OA-03: el precio cae a -0.4% (mae < -SL_dist) ANTES de subir a +0.5%.
    El SL hubiera pegado primero → GOOD_HOLD, no MISSED.
    """
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={
            "price": 100.0, "atr_ref_pct": 1.0,
            "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3,
        },
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.6, close=99.7),
        _candle(t0 + timedelta(minutes=5), high=100.5, low=99.9, close=100.4),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_HOLD"


def test_classify_hold_as_good_when_mfe_below_tp_target():
    """Subió pero sin alcanzar el TP_target → GOOD_HOLD (no era oportunidad real)."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.3, low=99.95, close=100.2),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_HOLD"


def test_classify_buy_rejected_as_blocked_good_when_mfe_hits_first():
    """AC OA-04: BUY rechazado, MFE llega al TP_target sin MAE cruzar SL → BLOCKED_GOOD_TRADE."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=False,
    )
    candles = [
        _candle(t0 + timedelta(minutes=2), high=100.5, low=99.95, close=100.4),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "BLOCKED_GOOD_TRADE"


def test_classify_buy_rejected_as_correctly_blocked_when_mae_hits_first():
    """BUY rechazado y precio cae a -SL_dist antes de tocar TP_target → CORRECTLY_BLOCKED."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=False,
    )
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.5, close=99.6),
        _candle(t0 + timedelta(minutes=10), high=100.5, low=99.7, close=100.4),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "CORRECTLY_BLOCKED"


def test_classify_executed_buy_with_positive_pnl_as_good_buy():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(pnl_pct=Decimal("1.2"))
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.95, close=100.05),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_BUY"


def test_classify_executed_buy_with_negative_pnl_as_bad_buy():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(pnl_pct=Decimal("-0.5"))
    candles = [
        _candle(t0 + timedelta(minutes=1), high=100.1, low=99.5, close=99.6),
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "BAD_BUY"


def test_classify_executed_buy_without_associated_trade_is_unknown():
    """Decisión ejecutada pero sin trade asociado todavía (race) → UNKNOWN."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=True,
    )
    candles = [_candle(t0 + timedelta(minutes=1), high=100.1, low=99.95, close=100.05)]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "UNKNOWN"


def _make_decision(*, input: dict, output: dict, ts=None, executed=False):
    """Helper for tests — minimal Decision-like object without DB."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=uuid4(),
        ts=ts or datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        input=input,
        output=output,
        executed=executed,
        trade_id=None,
    )
