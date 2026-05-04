from datetime import datetime
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Position

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

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/positions", response_model=list[PositionOut])
async def list_positions(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (await session.execute(select(Position).where(Position.status == "open"))).scalars().all()
    return [PositionOut(id=r.id, trade_id=r.trade_id, symbol=r.symbol,
                        quantity_btc=float(r.quantity_btc), entry_price=float(r.entry_price),
                        current_price=float(r.current_price) if r.current_price else None,
                        unrealized_pnl=float(r.unrealized_pnl) if r.unrealized_pnl else None,
                        unrealized_pct=float(r.unrealized_pct) if r.unrealized_pct else None,
                        status=r.status, opened_at=r.opened_at, updated_at=r.updated_at)
            for r in rows]
