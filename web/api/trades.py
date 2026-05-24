from datetime import datetime
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade, Position
from shared.pnl import compute_pnl_usdt, compute_pnl_pct

router = APIRouter()

class TradeOut(BaseModel):
    id: UUID
    decision_id: UUID | None
    ts_open: datetime
    ts_close: datetime | None
    side: str
    quantity_btc: float
    entry_price: float
    exit_price: float | None
    pnl_usdt: float | None
    pnl_pct: float | None
    status: str
    stop_loss: float | None
    take_profit: float | None
    close_reason: str | None
    fees_usdt: float | None
    close_requested: bool
    order_id_open: str | None
    order_id_close: str | None
    order_id_sl: str | None
    order_id_tp: str | None
    current_price: float | None = None
    unrealized_pnl_usdt: float | None = None
    unrealized_pnl_pct: float | None = None
    sl_pnl_usdt: float | None = None
    sl_pnl_pct: float | None = None
    tp_pnl_usdt: float | None = None
    tp_pnl_pct: float | None = None

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

def _open_trade_pnl(r: Trade, current_price: float | None) -> dict[str, float | None]:
    entry = float(r.entry_price)
    qty = float(r.quantity_btc)
    side = r.side
    sl = float(r.stop_loss) if r.stop_loss else None
    tp = float(r.take_profit) if r.take_profit else None
    return {
        "current_price": current_price,
        "unrealized_pnl_usdt": compute_pnl_usdt(entry=entry, quantity=qty, exit_price=current_price, side=side),
        "unrealized_pnl_pct": compute_pnl_pct(entry=entry, exit_price=current_price, side=side),
        "sl_pnl_usdt": compute_pnl_usdt(entry=entry, quantity=qty, exit_price=sl, side=side),
        "sl_pnl_pct": compute_pnl_pct(entry=entry, exit_price=sl, side=side),
        "tp_pnl_usdt": compute_pnl_usdt(entry=entry, quantity=qty, exit_price=tp, side=side),
        "tp_pnl_pct": compute_pnl_pct(entry=entry, exit_price=tp, side=side),
    }

def _to_out(r: Trade, *, current_price: float | None = None) -> TradeOut:
    open_pnl: dict[str, float | None] = {}
    if r.status == "open":
        open_pnl = _open_trade_pnl(r, current_price)

    return TradeOut(
        id=r.id, decision_id=r.decision_id, ts_open=r.ts_open, ts_close=r.ts_close,
        side=r.side, quantity_btc=float(r.quantity_btc), entry_price=float(r.entry_price),
        exit_price=float(r.exit_price) if r.exit_price else None,
        pnl_usdt=float(r.pnl_usdt) if r.pnl_usdt else None,
        pnl_pct=float(r.pnl_pct) if r.pnl_pct else None,
        status=r.status, stop_loss=float(r.stop_loss) if r.stop_loss else None,
        take_profit=float(r.take_profit) if r.take_profit else None,
        close_reason=r.close_reason, fees_usdt=float(r.fees_usdt) if r.fees_usdt else None,
        close_requested=bool(r.close_requested),
        order_id_open=r.order_id_open,
        order_id_close=r.order_id_close,
        order_id_sl=r.order_id_sl,
        order_id_tp=r.order_id_tp,
        **open_pnl,
    )

@router.get("/trades", response_model=list[TradeOut])
async def list_trades(session: Annotated[AsyncSession, Depends(_session)],
                      status: str | None = Query(None), limit: int = Query(100, le=500)):
    stmt = select(Trade).order_by(desc(Trade.ts_open)).limit(limit)
    if status:
        stmt = stmt.where(Trade.status == status)
    rows = (await session.execute(stmt)).scalars().all()

    open_trade_ids = [r.id for r in rows if r.status == "open"]
    position_prices: dict[UUID, float | None] = {}
    if open_trade_ids:
        positions = (await session.execute(
            select(Position).where(
                Position.trade_id.in_(open_trade_ids),
                Position.status == "open",
            )
        )).scalars().all()
        for p in positions:
            if p.trade_id is not None:
                position_prices[p.trade_id] = float(p.current_price) if p.current_price else None

    return [_to_out(r, current_price=position_prices.get(r.id)) for r in rows]

@router.post("/trades/{trade_id}/close", response_model=TradeOut)
async def request_trade_close(trade_id: UUID, session: Annotated[AsyncSession, Depends(_session)]):
    trade = await session.get(Trade, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade no encontrado")
    if trade.status != "open":
        raise HTTPException(status_code=409, detail="El trade no está abierto")
    trade.close_requested = True
    await session.commit()
    await session.refresh(trade)

    current_price = None
    pos = (await session.execute(
        select(Position).where(Position.trade_id == trade.id, Position.status == "open")
    )).scalar_one_or_none()
    if pos and pos.current_price:
        current_price = float(pos.current_price)

    return _to_out(trade, current_price=current_price)
