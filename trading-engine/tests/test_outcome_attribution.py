from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agents.outcome_attribution import (
    DecisionAttribution,
    _classify,
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


def _dense_candles(t0, *, horizon_min, peaks):
    """Build dense per-minute candles for a window. `peaks` is a list of dicts
    {min, high, low, close?} overriding specific minutes; other minutes get neutral
    candles (high=low=close=100.0) that don't affect MFE/MAE around price_t=100.
    """
    peaks_by_min = {p["min"]: p for p in peaks}
    out = []
    for i in range(1, horizon_min + 1):
        if i in peaks_by_min:
            p = peaks_by_min[i]
            out.append(_candle(
                t0 + timedelta(minutes=i),
                high=p["high"], low=p["low"], close=p.get("close"),
            ))
        else:
            out.append(_candle(
                t0 + timedelta(minutes=i),
                high=100.0, low=100.0, close=100.0,
            ))
    return out


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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.05, "low": 99.95, "close": 100.05},
        {"min": 10, "high": 100.5, "low": 100.0, "close": 100.5},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.1, "low": 99.6, "close": 99.7},
        {"min": 5, "high": 100.5, "low": 99.9, "close": 100.4},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.3, "low": 99.95, "close": 100.2},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 2, "high": 100.5, "low": 99.95, "close": 100.4},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.1, "low": 99.5, "close": 99.6},
        {"min": 10, "high": 100.5, "low": 99.7, "close": 100.4},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.1, "low": 99.95, "close": 100.05},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.1, "low": 99.5, "close": 99.6},
    ])
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
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.1, "low": 99.95, "close": 100.05},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "UNKNOWN"


def test_classify_executed_sell_with_positive_trade_pnl_as_good_sell():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "SELL"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(
        pnl_pct=Decimal("0.8"),
        entry_price=Decimal("99.0"),
        stop_loss=Decimal("98.5"),
        take_profit=Decimal("101.0"),
    )
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 1, "high": 100.1, "low": 99.9, "close": 100.0},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_SELL"


def test_classify_executed_sell_loss_cut_better_than_waiting_for_sl():
    """SELL con PnL negativo pero mejor que el SL del bracket → GOOD_SELL."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 99.6, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "SELL"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(
        pnl_pct=Decimal("-0.4"),
        entry_price=Decimal("100.0"),
        stop_loss=Decimal("99.2"),
        take_profit=Decimal("101.0"),
    )
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 15, "high": 99.8, "low": 99.1, "close": 99.15},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "GOOD_SELL"


def test_classify_executed_sell_as_bad_when_tp_would_have_been_reached():
    """SELL prematuro: el precio hubiera alcanzado el TP si se hubiera holdeado."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 99.6, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "SELL"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(
        pnl_pct=Decimal("-0.4"),
        entry_price=Decimal("100.0"),
        stop_loss=Decimal("99.0"),
        take_profit=Decimal("102.0"),
    )
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 8, "high": 102.5, "low": 99.8, "close": 102.0},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "BAD_SELL"


def test_classify_pending_when_window_not_matured_and_no_resolution():
    """AC OA-05: ventana no madurada y todavía sin MFE >= TP ni MAE <= -SL."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = _dense_candles(t0, horizon_min=30, peaks=[
        {"min": 1, "high": 100.1, "low": 99.98, "close": 100.05},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(minutes=30),
    )
    assert result.classification == "PENDING"
    assert result.matured is False


def test_classify_hold_stays_pending_when_mfe_exceeds_tp_before_maturity():
    """No finalizar contrafactual antes de madurar: spike temprano → PENDING, no MISSED."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = _dense_candles(t0, horizon_min=60, peaks=[
        {"min": 10, "high": 100.5, "low": 99.95, "close": 100.5},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(minutes=60),
    )
    assert result.classification == "PENDING"
    assert result.matured is False
    assert result.mfe_pct == pytest.approx(0.5, abs=1e-3)


def test_classify_blocked_buy_uses_output_sl_tp_when_declared():
    """BUY bloqueado: umbrales del SL/TP declarados por el Decisor, no solo config."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={
            "action": "BUY",
            "stop_loss": 99.7,
            "take_profit": 100.25,
        },
        ts=t0,
        executed=False,
    )
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 5, "high": 100.3, "low": 99.95, "close": 100.25},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.tp_target_pct == pytest.approx(0.25, abs=1e-3)
    assert result.classification == "BLOCKED_GOOD_TRADE"


def test_classify_hold_uses_output_sl_tp_when_declared():
    """HOLD con SL/TP en output: contrafactual usa esos niveles, no solo config."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={
            "action": "HOLD",
            "stop_loss": 99.7,
            "take_profit": 100.25,
        },
        ts=t0,
    )
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 5, "high": 100.3, "low": 99.95, "close": 100.25},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.tp_target_pct == pytest.approx(0.25, abs=1e-3)
    assert result.sl_dist_pct == pytest.approx(0.3, abs=1e-3)
    assert result.classification == "MISSED_OPPORTUNITY"


def test_classify_blocked_buy_falls_back_to_config_without_output_levels():
    """Sin stop_loss/take_profit en output, config da TP=0.39% — MFE 0.3% no alcanza."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "BUY"},
        ts=t0,
        executed=False,
    )
    candles = _dense_candles(t0, horizon_min=240, peaks=[
        {"min": 5, "high": 100.3, "low": 99.95, "close": 100.25},
    ])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.tp_target_pct == pytest.approx(0.39, abs=1e-3)
    assert result.classification == "CORRECTLY_BLOCKED"


def test_classify_executed_sell_stays_pending_before_horizon_matures():
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 99.6, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "SELL"},
        ts=t0,
        executed=True,
    )
    trade = SimpleNamespace(
        pnl_pct=Decimal("-0.4"),
        entry_price=Decimal("100.0"),
        stop_loss=Decimal("99.2"),
        take_profit=Decimal("101.0"),
    )
    candles = _dense_candles(t0, horizon_min=30, peaks=[])
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=trade,
        horizon_min=240, now=t0 + timedelta(minutes=30),
    )
    assert result.classification == "PENDING"
    assert result.matured is False


def test_coverage_ok_returns_unknown_when_collector_partially_degraded():
    """Regression: if price_collector delivers only 20 candles in a 240-min window,
    `_coverage_ok` MUST trigger UNKNOWN (~91.6% missing). Previously a density gate
    bypassed this case.
    """
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = _dense_candles(t0, horizon_min=20, peaks=[])[:20]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "UNKNOWN"


def test_classify_unknown_when_ohlcv_coverage_below_threshold():
    """AC OA-06: si > 30% de la ventana no tiene velas, clasificamos UNKNOWN."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [
        _candle(t0 + timedelta(minutes=i), high=100.05, low=99.98, close=100.0)
        for i in range(1, 101)
    ]
    result = attribute(
        decision=decision, ohlcv_1m=candles, associated_trade=None,
        horizon_min=240, now=t0 + timedelta(hours=5),
    )
    assert result.classification == "UNKNOWN"


def test_attribute_is_deterministic_for_same_inputs():
    """AC OA-01: misma entrada produce mismo output (función pura)."""
    t0 = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    decision = _make_decision(
        input={"price": 100.0, "atr_ref_pct": 1.0,
               "sl_atr_multiplier": 0.3, "min_rr_ratio": 1.3},
        output={"action": "HOLD"},
        ts=t0,
    )
    candles = [_candle(t0 + timedelta(minutes=i), high=100.5, low=99.95, close=100.4)
               for i in range(1, 241)]
    now_fixed = t0 + timedelta(hours=5)
    r1 = attribute(decision=decision, ohlcv_1m=candles, associated_trade=None,
                   horizon_min=240, now=now_fixed)
    r2 = attribute(decision=decision, ohlcv_1m=candles, associated_trade=None,
                   horizon_min=240, now=now_fixed)
    assert r1 == r2


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


# ─────────────── P2-T1: threshold de fees en GOOD/BAD_BUY ───────────────

def _buy_decision_exec(ts, price: float = 100.0):
    from uuid import uuid4
    return SimpleNamespace(
        id=uuid4(), ts=ts,
        output={"action": "BUY", "stop_loss": price * 0.99, "take_profit": price * 1.02},
        executed=True, rejected_reason=None, trade_id=uuid4(),
    )

def _trade_pnl(pnl_pct: float):
    return SimpleNamespace(pnl_pct=Decimal(str(pnl_pct)))


def test_classify_buy_when_pnl_above_fee_threshold_should_be_good():
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = _dense_candles(t0, horizon_min=10, peaks=[])
    result = _classify(
        decision=_buy_decision_exec(t0), mfe=2.0, mae=-0.5,
        t_mfe=5, t_mae=8, sl_dist_pct=1.0, tp_target_pct=2.0,
        matured=True, associated_trade=_trade_pnl(0.30),
        candles=candles, horizon_min=10, ts0=t0,
        net_fee_threshold_pct=0.20,
    )
    assert result == "GOOD_BUY"


def test_classify_buy_when_pnl_positive_but_below_fee_threshold_should_be_bad():
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = _dense_candles(t0, horizon_min=10, peaks=[])
    result = _classify(
        decision=_buy_decision_exec(t0), mfe=2.0, mae=-0.5,
        t_mfe=5, t_mae=8, sl_dist_pct=1.0, tp_target_pct=2.0,
        matured=True, associated_trade=_trade_pnl(0.10),
        candles=candles, horizon_min=10, ts0=t0,
        net_fee_threshold_pct=0.20,
    )
    assert result == "BAD_BUY"


def test_classify_buy_default_threshold_zero_preserves_backward_compat():
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = _dense_candles(t0, horizon_min=10, peaks=[])
    result = _classify(
        decision=_buy_decision_exec(t0), mfe=2.0, mae=-0.5,
        t_mfe=5, t_mae=8, sl_dist_pct=1.0, tp_target_pct=2.0,
        matured=True, associated_trade=_trade_pnl(0.05),
        candles=candles, horizon_min=10, ts0=t0,
        # sin net_fee_threshold_pct → default 0.0
    )
    assert result == "GOOD_BUY"
