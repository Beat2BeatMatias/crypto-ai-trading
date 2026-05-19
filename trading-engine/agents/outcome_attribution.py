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
    "BLOCKED_GOOD_TRADE", "CORRECTLY_BLOCKED",
    "MISSED_OPPORTUNITY", "GOOD_HOLD",
    "GOOD_SELL", "BAD_SELL",
]


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

    sl_dist_pct = inputs["sl_atr_mult"] * inputs["atr_pct_t"]
    tp_target_pct = inputs["min_rr_ratio"] * sl_dist_pct

    mfe, mae, t_mfe, t_mae = _compute_mfe_mae(
        price_t=inputs["price_t"], candles=ohlcv_1m, ts0=decision.ts,
    )

    matured = now >= decision.ts + _minutes(horizon_min)
    forward_return_pct = _forward_return(inputs["price_t"], ohlcv_1m, decision.ts, horizon_min)

    classification = _classify(
        decision=decision, mfe=mfe, mae=mae, t_mfe=t_mfe, t_mae=t_mae,
        sl_dist_pct=sl_dist_pct, tp_target_pct=tp_target_pct,
        matured=matured, associated_trade=associated_trade,
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


_OHLCV_MISSING_THRESHOLD_PCT = 30.0


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
) -> Classification:
    action = (decision.output or {}).get("action")
    if mfe is None or mae is None:
        return "UNKNOWN"
    mfe_hits_first = (
        mae > -sl_dist_pct
        or (t_mfe is not None and t_mae is not None and t_mfe < t_mae)
        or (t_mfe is not None and t_mae is None)
    )
    if action == "BUY" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return "GOOD_BUY" if pnl > 0 else "BAD_BUY"
    if action == "SELL" and decision.executed:
        if associated_trade is None or getattr(associated_trade, "pnl_pct", None) is None:
            return "UNKNOWN"
        try:
            pnl = float(associated_trade.pnl_pct)
        except (TypeError, ValueError):
            return "UNKNOWN"
        return "GOOD_SELL" if pnl > 0 else "BAD_SELL"
    if action == "HOLD":
        if not matured and mfe < tp_target_pct and mae > -sl_dist_pct:
            return "PENDING"
        if mfe >= tp_target_pct and mae > -sl_dist_pct and mfe_hits_first:
            return "MISSED_OPPORTUNITY"
        return "GOOD_HOLD"
    if action == "BUY" and not decision.executed:
        if not matured and mfe < tp_target_pct and mae > -sl_dist_pct:
            return "PENDING"
        if mfe >= tp_target_pct and mae > -sl_dist_pct and mfe_hits_first:
            return "BLOCKED_GOOD_TRADE"
        return "CORRECTLY_BLOCKED"
    if not matured:
        return "PENDING"
    return "UNKNOWN"


def _compute_mfe_mae(
    *,
    price_t: float,
    candles: list[Any],
    ts0: datetime,
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
        minute_offset = int((c.time - ts0).total_seconds() // 60)
        if high_pct > mfe:
            mfe = high_pct
            t_mfe = minute_offset
        if low_pct < mae:
            mae = low_pct
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
