"""Notional mínimo Binance: entrada, patas SL/TP y apalancamiento en futuros."""
from __future__ import annotations

from shared.schemas import DecisorOutput, Direction, direction_for_action

SL_LIMIT_SLIPPAGE = 0.9985

# Peor caso bootstrap: TP/SL ~3 % del precio de entrada (short TP o long SL).
DEFAULT_MIN_EXIT_LEG_PRICE_RATIO = 0.97


def effective_notional_leverage(*, trading_product: str, leverage: float) -> float:
    if str(trading_product).lower() == "futures" and leverage > 0:
        return float(leverage)
    return 1.0


def entry_notional_usdt(
    *,
    margin: float,
    position_size_pct: float,
    leverage: float,
    trading_product: str = "spot",
) -> float:
    lev = effective_notional_leverage(trading_product=trading_product, leverage=leverage)
    return margin * position_size_pct * lev


def estimate_qty_btc(
    *,
    margin: float,
    position_size_pct: float,
    price: float,
    leverage: float,
    trading_product: str = "spot",
) -> float:
    if price <= 0:
        return 0.0
    return entry_notional_usdt(
        margin=margin,
        position_size_pct=position_size_pct,
        leverage=leverage,
        trading_product=trading_product,
    ) / price


def min_position_size_pct_for_lot(
    *,
    min_qty_base: float,
    price: float,
    margin: float,
    leverage: float,
    trading_product: str = "spot",
) -> float:
    lev = effective_notional_leverage(trading_product=trading_product, leverage=leverage)
    if margin <= 0 or lev <= 0 or price <= 0:
        return 1.0
    return min_qty_base * price / (margin * lev)


def sl_exit_notional_usdt(*, qty_btc: float, stop_loss: float, direction: Direction) -> float:
    if direction == Direction.LONG:
        return qty_btc * stop_loss * SL_LIMIT_SLIPPAGE
    return qty_btc * stop_loss


def tp_exit_notional_usdt(*, qty_btc: float, take_profit: float) -> float:
    return qty_btc * take_profit


def min_position_size_pct_for_entry(
    *,
    min_notional_usdt: float,
    margin: float,
    leverage: float,
    trading_product: str = "spot",
) -> float:
    lev = effective_notional_leverage(trading_product=trading_product, leverage=leverage)
    if margin <= 0 or lev <= 0:
        return 1.0
    return min_notional_usdt / (margin * lev)


def min_position_size_pct_for_exit_legs(
    *,
    direction: Direction,
    price: float,
    stop_loss: float | None,
    take_profit: float | None,
    margin: float,
    min_notional_usdt: float,
    min_qty_base: float = 0.0,
    leverage: float,
    trading_product: str = "spot",
) -> float:
    """Mínimo position_size_pct para que entrada y patas SL/TP cumplan min_notional y min_qty."""
    floors = [
        min_position_size_pct_for_entry(
            min_notional_usdt=min_notional_usdt,
            margin=margin,
            leverage=leverage,
            trading_product=trading_product,
        ),
    ]
    if min_qty_base > 0:
        floors.append(
            min_position_size_pct_for_lot(
                min_qty_base=min_qty_base,
                price=price,
                margin=margin,
                leverage=leverage,
                trading_product=trading_product,
            ),
        )
    lev = effective_notional_leverage(trading_product=trading_product, leverage=leverage)
    if margin <= 0 or price <= 0 or lev <= 0:
        return max(floors)

    denom = margin * lev
    if direction == Direction.LONG and stop_loss is not None and stop_loss > 0:
        sl_price = stop_loss * SL_LIMIT_SLIPPAGE
        floors.append(min_notional_usdt * price / (denom * sl_price))
    if direction == Direction.SHORT and stop_loss is not None and stop_loss > 0:
        floors.append(min_notional_usdt * price / (denom * stop_loss))
    if take_profit is not None and take_profit > 0:
        floors.append(min_notional_usdt * price / (denom * take_profit))
    return max(floors)


def min_position_size_pct_for_decision(
    decision: DecisorOutput,
    *,
    margin: float,
    price: float,
    min_notional_usdt: float,
    min_qty_base: float = 0.0,
    leverage: float,
    trading_product: str = "spot",
) -> float:
    direction = direction_for_action(decision.action)
    if direction is None:
        return 0.0
    return min_position_size_pct_for_exit_legs(
        direction=direction,
        price=price,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        margin=margin,
        min_notional_usdt=min_notional_usdt,
        min_qty_base=min_qty_base,
        leverage=leverage,
        trading_product=trading_product,
    )


def r11_infeasible_reason(
    *,
    margin: float,
    max_position_pct: float,
    min_notional_usdt: float,
    min_qty_base: float = 0.0,
    leverage: float,
    trading_product: str,
    price: float,
    direction: Direction,
    stop_loss: float | None,
    take_profit: float | None,
) -> str | None:
    min_pct = min_position_size_pct_for_exit_legs(
        direction=direction,
        price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        margin=margin,
        min_notional_usdt=min_notional_usdt,
        min_qty_base=min_qty_base,
        leverage=leverage,
        trading_product=trading_product,
    )
    if min_pct <= max_position_pct + 1e-9:
        return None
    lev = effective_notional_leverage(trading_product=trading_product, leverage=leverage)
    return (
        f"min position_size_pct {min_pct:.4f} > max {max_position_pct:.4f} "
        f"(margen {margin:.2f} USDT, leverage {lev:.0f}x, min_notional {min_notional_usdt:.2f}; "
        f"subí margen, max_position_pct o acercá TP/SL)"
    )
