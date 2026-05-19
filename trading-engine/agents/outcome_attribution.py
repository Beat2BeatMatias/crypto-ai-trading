"""Counterfactual outcome attribution for decisor decisions.

Pure module: no DB queries, no commits, no clocks outside the `now` parameter.
Tested in trading-engine/tests/test_outcome_attribution.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
) -> DecisionAttribution:
    """Classify a decisor decision against the OHLCV evolution after `decision.ts`.

    Pure: deterministic given the same inputs. Caller provides `now` (no `datetime.now()`).
    Returns `UNKNOWN` if essential inputs are missing in `decision.input`.
    """
    inputs = _extract_decision_inputs(decision)
    if inputs is None:
        return DecisionAttribution(
            decision_id=decision.id,
            horizon_min=horizon_min,
            matured=False,
            forward_return_pct=None,
            mfe_pct=None,
            mae_pct=None,
            time_to_mfe_min=None,
            time_to_mae_min=None,
            sl_dist_pct=None,
            tp_target_pct=None,
            classification="UNKNOWN",
            computed_at=now,
        )
    raise NotImplementedError("classification not yet implemented")


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
