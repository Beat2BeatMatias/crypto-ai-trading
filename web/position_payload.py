"""Serialización consistente de posiciones abiertas (REST + WebSocket)."""
from __future__ import annotations

from shared.db.models import Position, Trade
from shared.pnl import compute_pnl_pct_directional, compute_pnl_usdt_directional


def resolve_position_direction(position: Position, trade: Trade | None) -> str:
    ps = getattr(position, "position_side", None)
    if ps in ("LONG", "SHORT"):
        return ps
    if trade is not None:
        ts = getattr(trade, "position_side", None)
        if ts in ("LONG", "SHORT"):
            return ts
        if trade.side == "SELL":
            return "SHORT"
    return "LONG"


def resolve_leverage(position: Position, trade: Trade | None) -> float | None:
    if trade is not None and trade.leverage is not None:
        return float(trade.leverage)
    lev = getattr(position, "leverage", None)
    return float(lev) if lev is not None else None


def resolve_liquidation_price(position: Position, trade: Trade | None) -> float | None:
    liq = getattr(position, "liquidation_price", None)
    if liq is not None:
        return float(liq)
    if trade is not None and getattr(trade, "liquidation_price", None) is not None:
        return float(trade.liquidation_price)
    return None


def build_position_payload(position: Position, trade: Trade | None) -> dict:
    entry = float(position.entry_price)
    qty = float(position.quantity_btc)
    direction = resolve_position_direction(position, trade)
    sl = float(trade.stop_loss) if trade and trade.stop_loss else None
    tp = float(trade.take_profit) if trade and trade.take_profit else None

    return {
        "id": str(position.id),
        "trade_id": str(position.trade_id) if position.trade_id else None,
        "symbol": position.symbol,
        "quantity_btc": qty,
        "entry_price": entry,
        "current_price": float(position.current_price) if position.current_price else None,
        "unrealized_pnl": float(position.unrealized_pnl) if position.unrealized_pnl else None,
        "unrealized_pct": float(position.unrealized_pct) if position.unrealized_pct else None,
        "status": position.status,
        "opened_at": position.opened_at.isoformat() if position.opened_at else None,
        "updated_at": position.updated_at.isoformat() if position.updated_at else None,
        "stop_loss": sl,
        "take_profit": tp,
        "order_id_sl": trade.order_id_sl if trade else None,
        "order_id_tp": trade.order_id_tp if trade else None,
        "position_side": direction,
        "leverage": resolve_leverage(position, trade),
        "liquidation_price": resolve_liquidation_price(position, trade),
        "sl_pnl_usdt": compute_pnl_usdt_directional(
            entry=entry, quantity=qty, exit_price=sl, direction=direction,
        ),
        "sl_pnl_pct": compute_pnl_pct_directional(
            entry=entry, exit_price=sl, direction=direction,
        ),
        "tp_pnl_usdt": compute_pnl_usdt_directional(
            entry=entry, quantity=qty, exit_price=tp, direction=direction,
        ),
        "tp_pnl_pct": compute_pnl_pct_directional(
            entry=entry, exit_price=tp, direction=direction,
        ),
    }
