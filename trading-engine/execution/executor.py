from __future__ import annotations
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade, Position, Decision
from shared.schemas import DecisorOutput

logger = structlog.get_logger()

class Executor:
    def __init__(self, exchange: Any, session: AsyncSession, *, symbol: str):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol

    async def execute_buy(self, *, decision: DecisorOutput, decision_id: uuid.UUID,
                           usdt_balance: float) -> Trade:
        usdt_to_spend = usdt_balance * decision.position_size_pct
        order = await self.exchange.create_market_order(
            self.symbol, "buy", None, params={"quoteOrderQty": usdt_to_spend},
        )
        avg_price = float(order.get("average") or 0.0)
        filled_qty = float(order.get("filled") or 0.0)
        fee = float((order.get("fee") or {}).get("cost") or 0.0)
        if avg_price == 0 or filled_qty == 0:
            raise RuntimeError(f"Buy order zero fill: {order}")
        if decision.stop_loss is not None:
            await self.exchange.create_order(
                self.symbol, "STOP_LOSS_LIMIT", "sell", filled_qty,
                price=decision.stop_loss * 0.999,
                params={"stopPrice": decision.stop_loss},
            )
        if decision.take_profit is not None:
            await self.exchange.create_order(
                self.symbol, "LIMIT", "sell", filled_qty, price=decision.take_profit,
            )
        trade = Trade(
            decision_id=decision_id, ts_open=datetime.now(tz=timezone.utc),
            side="BUY", quantity_btc=Decimal(str(filled_qty)),
            entry_price=Decimal(str(avg_price)), status="open",
            stop_loss=Decimal(str(decision.stop_loss)) if decision.stop_loss else None,
            take_profit=Decimal(str(decision.take_profit)) if decision.take_profit else None,
            order_id_open=str(order.get("id")), fees_usdt=Decimal(str(fee)),
        )
        self.session.add(trade)
        await self.session.flush()
        self.session.add(Position(
            trade_id=trade.id, symbol=self.symbol,
            quantity_btc=trade.quantity_btc, entry_price=trade.entry_price,
            status="open", opened_at=trade.ts_open,
        ))
        d = await self.session.get(Decision, decision_id)
        if d is not None:
            d.executed = True
            d.trade_id = trade.id
        await self.session.commit()
        await self.session.refresh(trade)
        logger.info("executor.buy_executed", trade_id=str(trade.id), price=avg_price)
        return trade

    async def execute_sell(self, *, trade_id: uuid.UUID, decision_id: uuid.UUID | None,
                            close_reason: str) -> Trade:
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise RuntimeError(f"Trade {trade_id} not open")
        order = await self.exchange.create_market_order(
            self.symbol, "sell", float(trade.quantity_btc),
        )
        avg_price = float(order.get("average") or 0.0)
        fee = float((order.get("fee") or {}).get("cost") or 0.0)
        if avg_price == 0:
            raise RuntimeError(f"Sell order zero fill: {order}")
        trade.exit_price = Decimal(str(avg_price))
        trade.ts_close = datetime.now(tz=timezone.utc)
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.order_id_close = str(order.get("id"))
        gross_pnl = float(trade.exit_price - trade.entry_price) * float(trade.quantity_btc)
        prior_fees = float(trade.fees_usdt or 0)
        trade.fees_usdt = Decimal(str(prior_fees + fee))
        trade.pnl_usdt = Decimal(str(gross_pnl - prior_fees - fee))
        trade.pnl_pct = Decimal(str(
            (avg_price - float(trade.entry_price)) / float(trade.entry_price) * 100
        ))
        pos = (await self.session.execute(
            select(Position).where(Position.trade_id == trade.id)
        )).scalar_one_or_none()
        if pos:
            pos.status = "closed"
            pos.updated_at = datetime.now(tz=timezone.utc)
        if decision_id:
            d = await self.session.get(Decision, decision_id)
            if d:
                d.executed = True
        await self.session.commit()
        await self.session.refresh(trade)
        logger.info("executor.sell_executed", trade_id=str(trade.id))
        return trade

    async def record_bracket_fill(self, *, trade_id: uuid.UUID, fill_price: float,
                                   fill_fee: float, close_reason: str) -> None:
        """Registra el cierre de un trade cuyo SL/TP ya fue ejecutado por Binance.
        No emite ninguna orden al exchange — solo actualiza la BD."""
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            return
        prior_fees = float(trade.fees_usdt or 0)
        gross_pnl = (fill_price - float(trade.entry_price)) * float(trade.quantity_btc)
        trade.exit_price = Decimal(str(fill_price))
        trade.ts_close = datetime.now(tz=timezone.utc)
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.fees_usdt = Decimal(str(prior_fees + fill_fee))
        trade.pnl_usdt = Decimal(str(gross_pnl - prior_fees - fill_fee))
        trade.pnl_pct = Decimal(str(
            (fill_price - float(trade.entry_price)) / float(trade.entry_price) * 100
        ))
        pos = (await self.session.execute(
            select(Position).where(Position.trade_id == trade.id)
        )).scalar_one_or_none()
        if pos:
            pos.status = "closed"
            pos.updated_at = datetime.now(tz=timezone.utc)
        await self.session.commit()
        logger.info("executor.bracket_fill_recorded", trade_id=str(trade_id),
                    reason=close_reason, price=fill_price, pnl=float(trade.pnl_usdt))
