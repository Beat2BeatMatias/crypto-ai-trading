from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Position

router = APIRouter()

class BalanceOut(BaseModel):
    btc_held: float
    open_positions: int
    realized_pnl_today: float

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/balance", response_model=BalanceOut)
async def get_balance(session: Annotated[AsyncSession, Depends(_session)]):
    open_pos = (await session.execute(select(Position).where(Position.status == "open"))).scalars().all()
    return BalanceOut(btc_held=sum(float(p.quantity_btc) for p in open_pos),
                      open_positions=len(open_pos), realized_pnl_today=0.0)
