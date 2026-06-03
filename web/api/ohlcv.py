from datetime import datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Ohlcv
from shared.ohlcv_market import ohlcv_market_for_product

router = APIRouter()

ALLOWED_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h")
Timeframe = Literal["1m", "5m", "15m", "1h", "4h"]


class CandleOut(BaseModel):
    time: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


async def _config_trading_product(session: AsyncSession) -> str:
    from sqlalchemy import text

    row = (await session.execute(
        text("SELECT value FROM config WHERE key = 'trading_product'"),
    )).first()
    product = row.value if row else "spot"
    return product if product in ("spot", "futures") else "spot"


@router.get("/ohlcv", response_model=list[CandleOut])
async def list_ohlcv(
    session: Annotated[AsyncSession, Depends(_session)],
    timeframe: Timeframe = Query("5m"),
    limit: int = Query(300, ge=1, le=1000),
    market: Literal["spot", "futures"] | None = Query(
        None, description="Por defecto usa trading_product de config",
    ),
):
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe inválido: {timeframe}")

    trading_product = await _config_trading_product(session)
    effective_market = market or ohlcv_market_for_product(trading_product)

    stmt = (
        select(Ohlcv)
        .where(Ohlcv.timeframe == timeframe, Ohlcv.market == effective_market)
        .order_by(desc(Ohlcv.time))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()

    candles = [
        CandleOut(
            time=r.time,
            open=float(r.open) if r.open is not None else None,
            high=float(r.high) if r.high is not None else None,
            low=float(r.low) if r.low is not None else None,
            close=float(r.close) if r.close is not None else None,
            volume=float(r.volume) if r.volume is not None else None,
        )
        for r in rows
    ]
    candles.reverse()
    return candles
