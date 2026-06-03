from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Position, BalanceSnapshot

router = APIRouter()


class FuturesBalanceOut(BaseModel):
    available_margin: float | None
    margin_balance: float | None
    margin_locked: float | None
    source: str  # live | snapshot | unavailable
    fetched_at: datetime | None = None


class BalanceOut(BaseModel):
    usdt: float
    usdt_locked: float
    usdt_total: float
    btc_exchange: float
    btc_locked: float
    btc_exchange_total: float
    btc_in_positions: float
    open_positions: int
    balance_ts: datetime | None
    balance_source: str | None
    realized_pnl_today: float
    margin_balance: float | None = None
    available_margin: float | None = None
    futures: FuturesBalanceOut | None = None


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


async def _config_trading_product(session: AsyncSession) -> str:
    row = (await session.execute(
        text("SELECT value FROM config WHERE key = 'trading_product'")
    )).first()
    product = row.value if row else "spot"
    return product if product in ("spot", "futures") else "spot"


def _futures_from_snapshot(snap: BalanceSnapshot | None) -> FuturesBalanceOut | None:
    if snap is None or snap.margin_balance is None:
        return None
    total = float(snap.margin_balance)
    available = float(snap.available_margin) if snap.available_margin is not None else total
    locked = max(0.0, total - available)
    return FuturesBalanceOut(
        available_margin=available,
        margin_balance=total,
        margin_locked=locked,
        source="snapshot",
        fetched_at=snap.ts,
    )


@router.get("/balance", response_model=BalanceOut)
async def get_balance(
    request: Request,
    session: Annotated[AsyncSession, Depends(_session)],
):
    open_pos = (await session.execute(
        select(Position).where(Position.status == "open")
    )).scalars().all()
    btc_in_positions = sum(float(p.quantity_btc) for p in open_pos)

    snap = (await session.execute(
        select(BalanceSnapshot).order_by(desc(BalanceSnapshot.ts)).limit(1)
    )).scalar_one_or_none()

    usdt_free = float(snap.usdt) if snap else 0.0
    usdt_locked = float(snap.usdt_locked) if snap else 0.0
    btc_free = float(snap.btc) if snap else 0.0
    btc_locked = float(snap.btc_locked) if snap else 0.0

    margin_balance = float(snap.margin_balance) if snap and snap.margin_balance is not None else None
    available_margin = (
        float(snap.available_margin) if snap and snap.available_margin is not None else None
    )

    trading_product = await _config_trading_product(session)
    futures_out: FuturesBalanceOut | None = None

    if trading_product == "futures":
        from binance_futures_balance import fetch_futures_margin_balance

        live = await fetch_futures_margin_balance()
        if live is not None:
            futures_out = FuturesBalanceOut(
                available_margin=live["available_margin"],
                margin_balance=live["margin_balance"],
                margin_locked=live["margin_locked"],
                source="live",
                fetched_at=datetime.now(timezone.utc),
            )
        else:
            futures_out = _futures_from_snapshot(snap)
            if futures_out is None:
                futures_out = FuturesBalanceOut(
                    available_margin=None,
                    margin_balance=None,
                    margin_locked=None,
                    source="unavailable",
                    fetched_at=None,
                )

        # En futuros el snapshot guarda margen en usdt/margin_*; no mezclar wallet spot.
        if futures_out.available_margin is not None:
            usdt_free = futures_out.available_margin
            usdt_locked = float(futures_out.margin_locked or 0.0)
            margin_balance = futures_out.margin_balance
            available_margin = futures_out.available_margin
        elif margin_balance is not None:
            usdt_free = available_margin if available_margin is not None else float(margin_balance)
            usdt_locked = max(0.0, float(margin_balance) - usdt_free)
        btc_free = 0.0
        btc_locked = 0.0

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
        futures=futures_out,
    )
