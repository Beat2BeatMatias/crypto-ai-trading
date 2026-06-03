from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Position

logger = structlog.get_logger()

class PositionManager:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def count_open(self) -> int:
        return (await self.session.execute(
            select(func.count()).select_from(Position).where(Position.status == "open")
        )).scalar_one()

    async def list_open(self) -> list[Position]:
        return list((await self.session.execute(
            select(Position).where(Position.status == "open")
        )).scalars().all())

    async def refresh_unrealized(self, *, current_price: float) -> None:
        positions = await self.list_open()
        now = datetime.now(tz=timezone.utc)
        for p in positions:
            p.current_price = Decimal(str(current_price))
            entry = float(p.entry_price)
            qty = float(p.quantity_btc)
            side = getattr(p, "position_side", "LONG") or "LONG"
            if side == "LONG":
                pnl = (current_price - entry) * qty
                pct = (current_price - entry) / entry * 100 if entry > 0 else 0
            else:
                pnl = (entry - current_price) * qty
                pct = (entry - current_price) / entry * 100 if entry > 0 else 0
            p.unrealized_pnl = Decimal(str(round(pnl, 4)))
            p.unrealized_pct = Decimal(str(round(pct, 4)))
            p.updated_at = now
        await self.session.commit()
