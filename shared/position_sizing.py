"""Risk-based position sizing: constant capital at risk per trade."""
from __future__ import annotations

from typing import Any

from shared.notional_sizing import min_position_size_pct_for_decision
from shared.schemas import DecisorAction, DecisorOutput, Direction, direction_for_action


def apply_risk_based_sizing(
    decision: DecisorOutput,
    *,
    price: float,
    capital_total: float,
    usdt_available: float,
    risk_per_trade_pct: float,
    max_position_pct: float,
    min_position_size: float,
    min_position_size_pct_notional: float,
    min_notional_usdt: float = 5.0,
    leverage: float = 1.0,
    trading_product: str = "spot",
) -> tuple[DecisorOutput, dict[str, Any] | None]:
    """Derive position_size_pct from risk budget and SL distance.

    loss_if_sl ≈ usdt_available × position_size_pct × sl_distance_pct
    Target: capital_total × risk_per_trade_pct
    => position_size_pct = risk_per_trade_pct / sl_distance_pct (using capital_total in numerator
       is equivalent when sizing from available USDT for the next BUY).
    """
    direction = direction_for_action(decision.action)
    if direction is None:
        return decision, None
    if decision.stop_loss is None or price <= 0 or capital_total <= 0:
        return decision, None

    if direction == Direction.LONG:
        sl_distance_pct = (price - decision.stop_loss) / price
    else:
        sl_distance_pct = (decision.stop_loss - price) / price
    if sl_distance_pct <= 1e-9:
        return decision, None

    llm_pct = decision.position_size_pct
    raw_pct = risk_per_trade_pct / sl_distance_pct
    exit_floor_pct = min_position_size_pct_for_decision(
        decision,
        margin=usdt_available,
        price=price,
        min_notional_usdt=min_notional_usdt,
        leverage=leverage,
        trading_product=trading_product,
    )
    floor_pct = max(min_position_size, min_position_size_pct_notional, exit_floor_pct)
    capped_pct = min(raw_pct, max_position_pct)
    final_pct = max(capped_pct, floor_pct) if capped_pct >= floor_pct else capped_pct

    risk_at_sl_usdt = usdt_available * final_pct * sl_distance_pct if usdt_available > 0 else 0.0
    target_risk_usdt = capital_total * risk_per_trade_pct

    meta: dict[str, Any] = {
        "position_size_pct_llm": llm_pct,
        "position_size_pct_computed": final_pct,
        "risk_per_trade_pct": risk_per_trade_pct,
        "sl_distance_pct": round(sl_distance_pct, 6),
        "capital_base": round(capital_total, 2),
        "usdt_available": round(usdt_available, 2),
        "target_risk_usdt": round(target_risk_usdt, 4),
        "risk_at_sl_usdt": round(risk_at_sl_usdt, 4),
        "capped_by_max_position": raw_pct > max_position_pct,
        "min_position_size_pct_exit_floor": round(exit_floor_pct, 6),
        "notional_leverage": leverage if trading_product == "futures" else 1.0,
    }

    updated = decision.model_copy(update={"position_size_pct": final_pct})
    return updated, meta
