"""Counterfactual outcome attribution for decisor decisions.

Pure module: no DB queries, no commits, no clocks outside the `now` parameter.
Tested in trading-engine/tests/test_outcome_attribution.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID


Classification = Literal[
    "PENDING", "UNKNOWN",
    "GOOD_BUY", "BAD_BUY",
    "GOOD_SHORT", "BAD_SHORT",
    "BLOCKED_GOOD_TRADE", "CORRECTLY_BLOCKED",
    "MISSED_OPPORTUNITY", "GOOD_HOLD",
    "GOOD_SELL", "BAD_SELL",
]

_OHLCV_MISSING_THRESHOLD_PCT = 30.0


@dataclass(frozen=True)
class DecisionAttribution:
    decision_id: UUID
    horizon_min: int
    matured: bool
    forward_return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    time_to_mfe_min: int | None
    time_to_mae_min: int | None
    sl_dist_pct: float | None
    tp_target_pct: float | None
    classification: Classification
    computed_at: datetime


def attribute(
    *,
    decision: Any,
    ohlcv_1m: list[Any],
    associated_trade: Any | None,
    horizon_min: int,
    now: datetime,
    coverage_threshold_pct: float = _OHLCV_MISSING_THRESHOLD_PCT,
    net_fee_threshold_pct: float = 0.0,
) -> DecisionAttribution:
    """Classify a decisor decision against the OHLCV evolution after `decision.ts`.

    Pure: deterministic given the same inputs. Caller provides `now` (no `datetime.now()`).
    Returns `UNKNOWN` if essential inputs are missing in `decision.input`.

    `coverage_threshold_pct` controls the maximum % of missing 1m candles before
    classifying UNKNOWN (default 30.0). Configurable via OUTCOME_COVERAGE_THRESHOLD_PCT.
    """
    inputs = _extract_decision_inputs(decision)
    if inputs is None:
        return _unknown(decision, horizon_min, now)

    if not _coverage_ok(ohlcv_1m, horizon_min, now, decision.ts, coverage_threshold_pct):
        return _unknown(decision, horizon_min, now)

    direction = _entry_direction(decision, associated_trade)
    sl_dist_pct, tp_target_pct = _resolve_risk_thresholds(decision, inputs, direction=direction)

    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(
        price_t=inputs["price_t"], candles=ohlcv_1m, ts0=decision.ts, direction=direction,
    )

    matured = now >= decision.ts + _minutes(horizon_min)
    forward_return_pct = _forward_return(inputs["price_t"], ohlcv_1m, decision.ts, horizon_min)

    classification = _classify(
        decision=decision, mfe=mfe, mae=mae, t_mfe=t_mfe, t_mae=t_mae,
        sl_dist_pct=sl_dist_pct, tp_target_pct=tp_target_pct,
        matured=matured, associated_trade=associated_trade,
        candles=ohlcv_1m, horizon_min=horizon_min, ts0=decision.ts,
        net_fee_threshold_pct=net_fee_threshold_pct,
        direction=direction,
    )

    return DecisionAttribution(
        decision_id=decision.id,
        horizon_min=horizon_min,
        matured=matured,
        forward_return_pct=forward_return_pct,
        mfe_pct=mfe,
        mae_pct=mae,
        time_to_mfe_min=t_mfe,
        time_to_mae_min=t_mae,
        sl_dist_pct=sl_dist_pct,
        tp_target_pct=tp_target_pct,
        classification=classification,
        computed_at=now,
    )


def _coverage_ok(
    candles: list[Any],
    horizon_min: int,
    now: datetime,
    ts0: datetime,
    threshold_pct: float = _OHLCV_MISSING_THRESHOLD_PCT,
) -> bool:
    """True if at least (100 - threshold_pct)% of the expected 1m slots are present.

    `expected` is the smaller of `horizon_min` and elapsed minutes since `ts0`. If the
    window hasn't elapsed yet (e.g., immature PENDING), we expect proportionally fewer
    candles. In production OHLCV is dense (1m candles every minute), so this check
    triggers UNKNOWN only when the price collector has real gaps.
    """
    expected = min(horizon_min, int((now - ts0).total_seconds() // 60))
    if expected <= 0:
        return True
    missing_pct = (expected - len(candles)) / expected * 100
    return missing_pct <= threshold_pct


def _unknown(decision: Any, horizon_min: int, now: datetime) -> DecisionAttribution:
    return DecisionAttribution(
        decision_id=decision.id,
        horizon_min=horizon_min, matured=False,
        forward_return_pct=None, mfe_pct=None, mae_pct=None,
        time_to_mfe_min=None, time_to_mae_min=None,
        sl_dist_pct=None, tp_target_pct=None,
        classification="UNKNOWN", computed_at=now,
    )


def _minutes(n: int) -> timedelta:
    return timedelta(minutes=n)


def _forward_return(price_t: float, candles: list[Any], ts0: datetime, horizon_min: int) -> float | None:
    if not candles:
        return None
    target_ts = ts0 + _minutes(horizon_min)
    last = candles[-1]
    for c in reversed(candles):
        if c.time <= target_ts:
            last = c
            break
    return (float(last.close) - price_t) / price_t * 100


def _classify(
    *,
    decision: Any,
    mfe: float | None,
    mae: float | None,
    t_mfe: int | None,
    t_mae: int | None,
    sl_dist_pct: float,
    tp_target_pct: float,
    matured: bool,
    associated_trade: Any | None,
    candles: list[Any],
    horizon_min: int,
    ts0: datetime,
    net_fee_threshold_pct: float = 0.0,
    direction: str = "LONG",
) -> Classification:
    action = (decision.output or {}).get("action")
    if mfe is None or mae is None:
        return "UNKNOWN"
    if action == "SHORT" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return "GOOD_SHORT" if pnl > net_fee_threshold_pct else "BAD_SHORT"
    if action == "BUY" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return "GOOD_BUY" if pnl > net_fee_threshold_pct else "BAD_BUY"
    if action == "SELL" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return _classify_executed_sell(
            actual_pnl=pnl,
            trade=associated_trade,
            candles=candles,
            matured=matured,
            horizon_min=horizon_min,
            ts0=ts0,
            sl_dist_pct=sl_dist_pct,
            direction=direction,
        )
    if action == "HOLD":
        if not matured:
            return "PENDING"
        if _bracket_tp_would_fill(decision, candles, sl_dist_pct, tp_target_pct, direction=direction):
            return "MISSED_OPPORTUNITY"
        return "GOOD_HOLD"
    if action == "SHORT" and not decision.executed:
        if not matured:
            return "PENDING"
        if _bracket_tp_would_fill(decision, candles, sl_dist_pct, tp_target_pct, direction=direction):
            return "BLOCKED_GOOD_TRADE"
        return "CORRECTLY_BLOCKED"
    if action == "BUY" and not decision.executed:
        if not matured:
            return "PENDING"
        if _bracket_tp_would_fill(decision, candles, sl_dist_pct, tp_target_pct, direction=direction):
            return "BLOCKED_GOOD_TRADE"
        return "CORRECTLY_BLOCKED"
    if not matured:
        return "PENDING"
    return "UNKNOWN"


def _entry_direction(decision: Any, associated_trade: Any | None) -> str:
    action = (decision.output or {}).get("action")
    if action == "SHORT":
        return "SHORT"
    if action == "BUY":
        return "LONG"
    if action == "SELL" and associated_trade is not None:
        return getattr(associated_trade, "position_side", None) or "LONG"
    return "LONG"


def _compute_mfe_mae(
    *,
    price_t: float,
    candles: list[Any],
    ts0: datetime,
    direction: str = "LONG",
) -> tuple[float | None, float | None, int | None, int | None]:
    """Iterate candles in order; track MFE/MAE and minute-offset when each was reached.

    Returns (mfe_pct, mae_pct, time_to_mfe_min, time_to_mae_min).
    All None if `candles` is empty.

    MFE is the maximum (high - price_t) / price_t across all candles.
    MAE is the minimum (low - price_t) / price_t (negative for drawdown).
    """
    if not candles or price_t <= 0:
        return None, None, None, None

    mfe = float("-inf")
    mae = float("inf")
    t_mfe: int | None = None
    t_mae: int | None = None

    for c in candles:
        high_pct = (float(c.high) - price_t) / price_t * 100
        low_pct = (float(c.low) - price_t) / price_t * 100
        if direction == "SHORT":
            favorable = -high_pct
            adverse = -low_pct
        else:
            favorable = high_pct
            adverse = low_pct
        minute_offset = int((c.time - ts0).total_seconds() // 60)
        if favorable > mfe:
            mfe = favorable
            t_mfe = minute_offset
        if adverse < mae:
            mae = adverse
            t_mae = minute_offset

    return mfe, mae, t_mfe, t_mae


def _extract_decision_inputs(decision: Any) -> dict[str, float] | None:
    """Return {price_t, atr_pct_t, sl_atr_mult, min_rr_ratio} or None if any required key is missing."""
    inp = decision.input or {}
    try:
        price = float(inp["price"])
        atr_pct = float(inp["atr_ref_pct"])
        sl_mult = float(inp["sl_atr_multiplier"])
        rr = float(inp["min_rr_ratio"])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or atr_pct <= 0:
        return None
    return {
        "price_t": price,
        "atr_pct_t": atr_pct,
        "sl_atr_mult": sl_mult,
        "min_rr_ratio": rr,
    }


def _resolve_risk_thresholds(
    decision: Any, inputs: dict[str, float], *, direction: str = "LONG",
) -> tuple[float, float]:
    """Return (sl_dist_pct, tp_target_pct) for contrafactual evaluation.

    If the decision output already declares stop_loss and take_profit, use those
    levels (the bracket the Decisor had in mind). Otherwise fall back to the config
    snapshot in decision.input (atr × sl_mult, min_rr).
    """
    config_sl = inputs["sl_atr_mult"] * inputs["atr_pct_t"]
    config_tp = inputs["min_rr_ratio"] * config_sl

    out = decision.output or {}
    sl_raw, tp_raw = out.get("stop_loss"), out.get("take_profit")
    if sl_raw is None or tp_raw is None:
        return config_sl, config_tp

    try:
        price = inputs["price_t"]
        sl = float(sl_raw)
        tp = float(tp_raw)
    except (TypeError, ValueError):
        return config_sl, config_tp

    if direction == "SHORT":
        if sl <= price or tp >= price:
            return config_sl, config_tp
        sl_dist = (sl - price) / price * 100
        tp_target = (price - tp) / price * 100
    else:
        if sl >= price or tp <= price:
            return config_sl, config_tp
        sl_dist = (price - sl) / price * 100
        tp_target = (tp - price) / price * 100
    if sl_dist <= 0 or tp_target <= 0:
        return config_sl, config_tp
    return sl_dist, tp_target


def _classify_executed_sell(
    *,
    actual_pnl: float,
    trade: Any,
    candles: list[Any],
    matured: bool,
    horizon_min: int,
    ts0: datetime,
    sl_dist_pct: float,
    direction: str = "LONG",
) -> Classification:
    """Classify an executed SELL against the forward path if the position had been held.

    GOOD_SELL: profitable exit, or loss smaller than waiting for the bracket SL.
    BAD_SELL: sold too early (TP would have been reached), or held would have done better.
    """
    if not matured:
        return "PENDING"

    if actual_pnl > 0:
        return "GOOD_SELL"

    entry = _optional_float(getattr(trade, "entry_price", None))
    stop_loss = _optional_float(getattr(trade, "stop_loss", None))
    take_profit = _optional_float(getattr(trade, "take_profit", None))

    if entry is not None and entry > 0 and stop_loss is None:
        if direction == "SHORT":
            stop_loss = entry * (1 + sl_dist_pct / 100)
        else:
            stop_loss = entry * (1 - sl_dist_pct / 100)

    sl_pnl_pct: float | None = None
    tp_pnl_pct: float | None = None
    if entry is not None and entry > 0:
        if stop_loss is not None:
            if direction == "SHORT":
                sl_pnl_pct = (entry - stop_loss) / entry * 100
            else:
                sl_pnl_pct = (stop_loss - entry) / entry * 100
        if take_profit is not None:
            if direction == "SHORT":
                tp_pnl_pct = (entry - take_profit) / entry * 100
            else:
                tp_pnl_pct = (take_profit - entry) / entry * 100

    bracket = _first_bracket_outcome(
        candles, stop_loss=stop_loss, take_profit=take_profit, direction=direction,
    )

    if bracket == "tp" and tp_pnl_pct is not None and actual_pnl < tp_pnl_pct:
        return "BAD_SELL"

    if bracket == "sl" and sl_pnl_pct is not None:
        return "GOOD_SELL" if actual_pnl > sl_pnl_pct else "BAD_SELL"

    if sl_pnl_pct is not None and actual_pnl > sl_pnl_pct:
        return "GOOD_SELL"

    hold_pnl = _forward_hold_pnl_pct(entry, candles, horizon_min, ts0, direction=direction)
    if hold_pnl is not None and hold_pnl > actual_pnl:
        return "BAD_SELL"

    if sl_pnl_pct is not None:
        return "GOOD_SELL" if actual_pnl > sl_pnl_pct else "BAD_SELL"

    return "BAD_SELL"


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bracket_tp_would_fill(
    decision: Any,
    candles: list[Any],
    sl_dist_pct: float,
    tp_target_pct: float,
    direction: str = "LONG",
) -> bool:
    """True if chronological path would hit TP before SL (realistic bracket), not MFE peak."""
    inp = decision.input or {}
    try:
        price = float(inp["price"])
    except (KeyError, TypeError, ValueError):
        return False
    if price <= 0:
        return False
    stop_loss, take_profit = _absolute_bracket_levels(
        decision, price_t=price, sl_dist_pct=sl_dist_pct, tp_target_pct=tp_target_pct,
        direction=direction,
    )
    return _first_bracket_outcome(
        candles, stop_loss=stop_loss, take_profit=take_profit, direction=direction,
    ) == "tp"


def _absolute_bracket_levels(
    decision: Any,
    *,
    price_t: float,
    sl_dist_pct: float,
    tp_target_pct: float,
    direction: str = "LONG",
) -> tuple[float, float]:
    out = decision.output or {}
    sl_raw, tp_raw = out.get("stop_loss"), out.get("take_profit")
    if sl_raw is not None and tp_raw is not None:
        try:
            sl, tp = float(sl_raw), float(tp_raw)
            if direction == "SHORT" and tp < price_t < sl:
                return sl, tp
            if direction != "SHORT" and sl < price_t < tp:
                return sl, tp
        except (TypeError, ValueError):
            pass
    if direction == "SHORT":
        stop_loss = price_t * (1 + sl_dist_pct / 100)
        take_profit = price_t * (1 - tp_target_pct / 100)
    else:
        stop_loss = price_t * (1 - sl_dist_pct / 100)
        take_profit = price_t * (1 + tp_target_pct / 100)
    return stop_loss, take_profit


def _first_bracket_outcome(
    candles: list[Any],
    *,
    stop_loss: float | None,
    take_profit: float | None,
    direction: str = "LONG",
) -> Literal["sl", "tp", "neither"]:
    """First bracket level touched on the forward path (chronological candle order)."""
    for c in candles:
        low = float(c.low)
        high = float(c.high)
        if direction == "SHORT":
            sl_hit = stop_loss is not None and high >= stop_loss
            tp_hit = take_profit is not None and low <= take_profit
        else:
            sl_hit = stop_loss is not None and low <= stop_loss
            tp_hit = take_profit is not None and high >= take_profit
        if sl_hit and tp_hit:
            return "sl"
        if sl_hit:
            return "sl"
        if tp_hit:
            return "tp"
    return "neither"


def _forward_hold_pnl_pct(
    entry: float | None,
    candles: list[Any],
    horizon_min: int,
    ts0: datetime,
    direction: str = "LONG",
) -> float | None:
    """PnL % if the position had been held until horizon close (from entry)."""
    if entry is None or entry <= 0 or not candles:
        return None
    target_ts = ts0 + _minutes(horizon_min)
    last_close: float | None = None
    for c in reversed(candles):
        if c.time <= target_ts:
            last_close = float(c.close)
            break
    if last_close is None:
        return None
    if direction == "SHORT":
        return (entry - last_close) / entry * 100
    return (last_close - entry) / entry * 100
