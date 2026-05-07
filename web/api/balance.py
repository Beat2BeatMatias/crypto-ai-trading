from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Position, BalanceSnapshot

router = APIRouter()

class BalanceOut(BaseModel):
    usdt: float
    btc_exchange: float
    btc_in_positions: float
    open_positions: int
    balance_ts: datetime | None
    balance_source: str | None
    realized_pnl_today: float

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/balance", response_model=BalanceOut)
async def get_balance(session: Annotated[AsyncSession, Depends(_session)]):
    open_pos = (await session.execute(
        select(Position).where(Position.status == "open")
    )).scalars().all()
    btc_in_positions = sum(float(p.quantity_btc) for p in open_pos)

    snap = (await session.execute(
        select(BalanceSnapshot).order_by(desc(BalanceSnapshot.ts)).limit(1)
    )).scalar_one_or_none()

    return BalanceOut(
        usdt=float(snap.usdt) if snap else 0.0,
        btc_exchange=float(snap.btc) if snap else 0.0,
        btc_in_positions=btc_in_positions,
        open_positions=len(open_pos),
        balance_ts=snap.ts if snap else None,
        balance_source=snap.source if snap else None,
        realized_pnl_today=0.0,
    )
