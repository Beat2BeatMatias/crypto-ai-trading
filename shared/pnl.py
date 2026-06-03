from __future__ import annotations


def compute_pnl_usdt(
    *,
    entry: float,
    quantity: float,
    exit_price: float | None,
    side: str = "BUY",
) -> float | None:
    if exit_price is None or entry <= 0 or quantity <= 0:
        return None
    if side.upper() == "BUY":
        return round((exit_price - entry) * quantity, 4)
    return round((entry - exit_price) * quantity, 4)


def compute_pnl_pct(
    *,
    entry: float,
    exit_price: float | None,
    side: str = "BUY",
) -> float | None:
    if exit_price is None or entry <= 0:
        return None
    if side.upper() == "BUY":
        return round((exit_price - entry) / entry * 100, 4)
    return round((entry - exit_price) / entry * 100, 4)


def compute_pnl_usdt_directional(
    *,
    entry: float,
    quantity: float,
    exit_price: float | None,
    direction: str,
) -> float | None:
    side = "BUY" if direction.upper() == "LONG" else "SELL"
    return compute_pnl_usdt(
        entry=entry, quantity=quantity, exit_price=exit_price, side=side,
    )


def compute_pnl_pct_directional(
    *,
    entry: float,
    exit_price: float | None,
    direction: str,
) -> float | None:
    side = "BUY" if direction.upper() == "LONG" else "SELL"
    return compute_pnl_pct(entry=entry, exit_price=exit_price, side=side)
