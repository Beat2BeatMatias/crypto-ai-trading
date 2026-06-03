from datetime import datetime
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Position, Trade
from shared.pnl import compute_pnl_usdt_directional, compute_pnl_pct_directional

router = APIRouter()

class PositionOut(BaseModel):
    id: UUID
    trade_id: UUID | None
    symbol: str
    quantity_btc: float
    entry_price: float
    current_price: float | None
    unrealized_pnl: float | None
    unrealized_pct: float | None
    status: str
    opened_at: datetime
    updated_at: datetime | None
    stop_loss: float | None = None
    take_profit: float | None = None
    sl_pnl_usdt: float | None = None
    sl_pnl_pct: float | None = None
    tp_pnl_usdt: float | None = None
    tp_pnl_pct: float | None = None
    position_side: str | None = "LONG"
    leverage: float | None = None
    liquidation_price: float | None = None

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

def _to_out(r: Position, trade: Trade | None) -> PositionOut:
    entry = float(r.entry_price)
    qty = float(r.quantity_btc)
    direction = (
        getattr(r, "position_side", None)
        or (getattr(trade, "position_side", None) if trade else None)
        or "LONG"
    )
    sl = float(trade.stop_loss) if trade and trade.stop_loss else None
    tp = float(trade.take_profit) if trade and trade.take_profit else None

    return PositionOut(
        id=r.id,
        trade_id=r.trade_id,
        symbol=r.symbol,
        quantity_btc=qty,
        entry_price=entry,
        current_price=float(r.current_price) if r.current_price else None,
        unrealized_pnl=float(r.unrealized_pnl) if r.unrealized_pnl else None,
        unrealized_pct=float(r.unrealized_pct) if r.unrealized_pct else None,
        status=r.status,
        opened_at=r.opened_at,
        updated_at=r.updated_at,
        stop_loss=sl,
        take_profit=tp,
        sl_pnl_usdt=compute_pnl_usdt_directional(
            entry=entry, quantity=qty, exit_price=sl, direction=direction,
        ),
        sl_pnl_pct=compute_pnl_pct_directional(entry=entry, exit_price=sl, direction=direction),
        tp_pnl_usdt=compute_pnl_usdt_directional(
            entry=entry, quantity=qty, exit_price=tp, direction=direction,
        ),
        tp_pnl_pct=compute_pnl_pct_directional(entry=entry, exit_price=tp, direction=direction),
        position_side=direction,
        leverage=float(r.leverage) if getattr(r, "leverage", None) else None,
        liquidation_price=float(r.liquidation_price) if getattr(r, "liquidation_price", None) else None,
    )

@router.get("/positions", response_model=list[PositionOut])
async def list_positions(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (await session.execute(select(Position).where(Position.status == "open"))).scalars().all()
    trade_ids = [r.trade_id for r in rows if r.trade_id is not None]
    trades_by_id: dict[UUID, Trade] = {}
    if trade_ids:
        trades = (await session.execute(select(Trade).where(Trade.id.in_(trade_ids)))).scalars().all()
        trades_by_id = {t.id: t for t in trades}

    return [_to_out(r, trades_by_id.get(r.trade_id) if r.trade_id else None) for r in rows]
