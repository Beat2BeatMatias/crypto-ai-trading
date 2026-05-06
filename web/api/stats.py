from __future__ import annotations
from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade, Decision, Position

router = APIRouter()


class DailyStatsOut(BaseModel):
    # Trades
    trades_open: int
    trades_closed: int
    trades_won: int
    trades_lost: int
    # P&L
    pnl_realized: float
    pnl_unrealized: float
    fees_total: float
    # Decisiones
    decisions_total: int
    decisions_buy: int
    decisions_sell: int
    decisions_hold: int
    decisions_executed: int
    decisions_blocked: int


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


@router.get("/stats/daily", response_model=DailyStatsOut)
async def daily_stats(session: Annotated[AsyncSession, Depends(_session)]):
    today = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    trades = (await session.execute(
        select(Trade).where(Trade.ts_open >= today)
    )).scalars().all()

    decisions = (await session.execute(
        select(Decision).where(Decision.ts >= today, Decision.agent == "decisor")
    )).scalars().all()

    open_positions = (await session.execute(
        select(Position).where(Position.status == "open")
    )).scalars().all()

    closed = [t for t in trades if t.status == "closed"]
    won = [t for t in closed if t.pnl_usdt and float(t.pnl_usdt) > 0]
    lost = [t for t in closed if t.pnl_usdt and float(t.pnl_usdt) < 0]

    pnl_realized = sum(float(t.pnl_usdt or 0) for t in closed)
    pnl_unrealized = sum(float(p.unrealized_pnl or 0) for p in open_positions)
    fees_total = sum(float(t.fees_usdt or 0) for t in trades)

    buys = [d for d in decisions if d.output.get("action") == "BUY"]
    sells = [d for d in decisions if d.output.get("action") == "SELL"]
    holds = [d for d in decisions if d.output.get("action") == "HOLD"]
    executed = [d for d in decisions if d.executed]
    blocked = [d for d in decisions if not d.executed and d.rejected_reason]

    return DailyStatsOut(
        trades_open=len([t for t in trades if t.status == "open"]),
        trades_closed=len(closed),
        trades_won=len(won),
        trades_lost=len(lost),
        pnl_realized=round(pnl_realized, 2),
        pnl_unrealized=round(pnl_unrealized, 2),
        fees_total=round(fees_total, 4),
        decisions_total=len(decisions),
        decisions_buy=len(buys),
        decisions_sell=len(sells),
        decisions_hold=len(holds),
        decisions_executed=len(executed),
        decisions_blocked=len(blocked),
    )
