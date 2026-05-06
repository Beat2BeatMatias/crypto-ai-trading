from __future__ import annotations
from typing import Any
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade
from execution.executor import Executor

logger = structlog.get_logger()


class OrderTracker:
    """Detecta fills de SL/TP en Binance y cierra el trade en la BD sin emitir órdenes."""

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

        for trade in open_trades:
            if trade.close_requested:
                logger.info("order_tracker.manual_close_requested", trade_id=str(trade.id))
                try:
                    await self.executor.execute_sell(
                        trade_id=trade.id, decision_id=None, close_reason="manual_close",
                    )
                except Exception as e:
                    logger.error("order_tracker.manual_close_failed", trade_id=str(trade.id), error=str(e))

        try:
            # Obtener fills de venta desde Binance (los 50 más recientes)
            fills = await self.exchange.fetch_my_trades(self.symbol, limit=50)
        except Exception as e:
            logger.warning("order_tracker.fetch_trades_failed", error=str(e))
            return

        sell_fills = [f for f in fills if f.get("side") == "sell"]
        if not sell_fills:
            return

        for trade in open_trades:
            qty = float(trade.quantity_btc)
            entry_ts = trade.ts_open.timestamp() * 1000  # ms epoch

            # Buscar fill de venta posterior a la apertura del trade cuya cantidad coincida (±2%)
            matched = None
            for fill in sell_fills:
                fill_ts = fill.get("timestamp") or 0
                if fill_ts < entry_ts:
                    continue
                fill_qty = float(fill.get("amount") or 0)
                if qty > 0 and abs(fill_qty - qty) / qty < 0.02:
                    matched = fill
                    break

            if matched is None:
                continue

            fill_price = float(matched.get("price") or 0)
            fill_fee = float((matched.get("fee") or {}).get("cost") or 0)
            if fill_price == 0:
                continue

            sl = float(trade.stop_loss or 0)
            tp = float(trade.take_profit or 0)
            if sl > 0 and fill_price <= sl * 1.002:
                reason = "sl_triggered"
            elif tp > 0 and fill_price >= tp * 0.998:
                reason = "tp_triggered"
            else:
                reason = "bracket_fill"

            logger.info("order_tracker.bracket_detected",
                        trade_id=str(trade.id), reason=reason, fill_price=fill_price)
            await self.executor.record_bracket_fill(
                trade_id=trade.id, fill_price=fill_price,
                fill_fee=fill_fee, close_reason=reason,
            )
