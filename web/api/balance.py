from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Position, BalanceSnapshot

router = APIRouter()


class BalanceOut(BaseModel):
    # USDT libre (disponible para operar)
    usdt: float
    # USDT reservado en órdenes activas (OCO, SL, TP, etc.)
    usdt_locked: float
    # USDT total = libre + reservado
    usdt_total: float

    # BTC libre en exchange (no comprometido en órdenes)
    btc_exchange: float
    # BTC reservado en órdenes activas (OCO, SL, TP, etc.)
    btc_locked: float
    # BTC total en exchange = libre + reservado
    btc_exchange_total: float

    # BTC en posiciones abiertas (registrado en la BD local)
    btc_in_positions: float

    open_positions: int
    balance_ts: datetime | None
    balance_source: str | None
    realized_pnl_today: float
    margin_balance: float | None = None
    available_margin: float | None = None


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

    usdt_free   = float(snap.usdt)        if snap else 0.0
    usdt_locked = float(snap.usdt_locked) if snap else 0.0
    btc_free    = float(snap.btc)         if snap else 0.0
    btc_locked  = float(snap.btc_locked)  if snap else 0.0

    margin_balance = float(snap.margin_balance) if snap and snap.margin_balance is not None else None
    available_margin = float(snap.available_margin) if snap and snap.available_margin is not None else None

    return BalanceOut(
        usdt=usdt_free,
        usdt_locked=usdt_locked,
        usdt_total=usdt_free + usdt_locked,
        btc_exchange=btc_free,
        btc_locked=btc_locked,
        btc_exchange_total=btc_free + btc_locked,
        btc_in_positions=btc_in_positions,
        open_positions=len(open_pos),
        balance_ts=snap.ts if snap else None,
        balance_source=snap.source if snap else None,
        realized_pnl_today=0.0,
        margin_balance=margin_balance,
        available_margin=available_margin,
    )
