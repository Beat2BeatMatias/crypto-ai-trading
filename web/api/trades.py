from datetime import datetime
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade

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

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/trades", response_model=list[TradeOut])
async def list_trades(session: Annotated[AsyncSession, Depends(_session)],
                      status: str | None = Query(None), limit: int = Query(100, le=500)):
    stmt = select(Trade).order_by(desc(Trade.ts_open)).limit(limit)
    if status:
        stmt = stmt.where(Trade.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [TradeOut(id=r.id, decision_id=r.decision_id, ts_open=r.ts_open, ts_close=r.ts_close,
                     side=r.side, quantity_btc=float(r.quantity_btc), entry_price=float(r.entry_price),
                     exit_price=float(r.exit_price) if r.exit_price else None,
                     pnl_usdt=float(r.pnl_usdt) if r.pnl_usdt else None,
                     pnl_pct=float(r.pnl_pct) if r.pnl_pct else None,
                     status=r.status, stop_loss=float(r.stop_loss) if r.stop_loss else None,
                     take_profit=float(r.take_profit) if r.take_profit else None,
                     close_reason=r.close_reason, fees_usdt=float(r.fees_usdt) if r.fees_usdt else None)
            for r in rows]
