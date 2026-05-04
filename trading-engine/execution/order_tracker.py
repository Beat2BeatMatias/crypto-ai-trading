from __future__ import annotations
from typing import Any
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade
from execution.executor import Executor

logger = structlog.get_logger()

class OrderTracker:
    def __init__(self, exchange: Any, session: AsyncSession, executor: Executor, *, symbol: str):
        self.exchange = exchange
        self.session = session
        self.executor = executor
        self.symbol = symbol

    async def poll_once(self) -> None:
        open_trades = (await self.session.execute(
            select(Trade).where(Trade.status == "open")
        )).scalars().all()
        if not open_trades:
            return
        try:
            fills = await self.exchange.fetch_my_trades(self.symbol, limit=20)
        except Exception as e:
            logger.warning("order_tracker.fetch_trades_failed", error=str(e))
            return
        for trade in open_trades:
            for fill in fills:
                if fill.get("side") != "sell":
                    continue
                if abs(float(fill.get("amount", 0)) - float(trade.quantity_btc)) / float(trade.quantity_btc) < 0.01:
                    await self.executor.execute_sell(
                        trade_id=trade.id, decision_id=None, close_reason="bracket_fill",
                    )
                    break
