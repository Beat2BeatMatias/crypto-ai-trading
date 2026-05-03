from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import FeeSnapshot

logger = structlog.get_logger()
REFRESH_INTERVAL = timedelta(hours=24)

class FeeManager:
    def __init__(self, exchange: Any, session: AsyncSession, *, symbol: str):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol
        self._maker: float | None = None
        self._taker: float | None = None
        self._last_refresh: datetime | None = None

    @property
    def maker(self) -> float:
        if self._maker is None:
            raise RuntimeError("FeeManager not initialised — call refresh() first")
        return self._maker

    @property
    def taker(self) -> float:
        if self._taker is None:
            raise RuntimeError("FeeManager not initialised — call refresh() first")
        return self._taker

    @property
    def roundtrip_pct(self) -> float:
        return self.taker * 2

    async def refresh(self) -> None:
        try:
            data = await self.exchange.fetch_trading_fees()
            entry = data.get(self.symbol) or data.get(self.symbol.replace("/", "")) or {}
            maker = float(entry.get("maker", 0.001))
            taker = float(entry.get("taker", 0.001))
            self._maker = maker
            self._taker = taker
            self._last_refresh = datetime.now(tz=timezone.utc)
            self.session.add(FeeSnapshot(
                ts=datetime.now(tz=timezone.utc),
                symbol=self.symbol,
                maker_fee=Decimal(str(maker)),
                taker_fee=Decimal(str(taker)),
                raw=data,
            ))
            await self.session.commit()
            logger.info("fees.refreshed", maker=maker, taker=taker)
        except Exception as e:
            logger.warning("fees.refresh_failed", error=str(e))
            await self._load_last_snapshot()

    async def get_or_refresh(self) -> None:
        now = datetime.now(tz=timezone.utc)
        if self._last_refresh is None or (now - self._last_refresh) > REFRESH_INTERVAL or self._taker is None:
            await self.refresh()

    async def _load_last_snapshot(self) -> None:
        row = (await self.session.execute(
            select(FeeSnapshot).where(FeeSnapshot.symbol == self.symbol)
            .order_by(FeeSnapshot.ts.desc()).limit(1)
        )).scalar_one_or_none()
        if row is None:
            self._maker = 0.001
            self._taker = 0.001
        else:
            self._maker = float(row.maker_fee)
            self._taker = float(row.taker_fee)
        self._last_refresh = datetime.now(tz=timezone.utc)
