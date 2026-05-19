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
