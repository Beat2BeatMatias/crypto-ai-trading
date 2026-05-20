from __future__ import annotations
import asyncio
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

# Binance Spot: STOP_LOSS_LIMIT requiere un precio límite ligeramente por debajo
# del stop price para garantizar el fill ante pequeños gaps de precio.
_SL_LIMIT_SLIPPAGE = 0.9985  # 0.15% por debajo del stop price
_SL_BRACKET_RETRIES = 2
_SL_BRACKET_RETRY_DELAY_SEC = 2.0


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

        # Persistir Trade y Position ANTES de intentar colocar los brackets.
        # Esto garantiza que el BUY quede registrado en BD aunque los brackets fallen.
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

        # Colocar brackets SL/TP (best-effort con retry): si agotan los intentos,
        # el trade queda en BD con order_id_sl=NULL y el SL Guardian lo cubre.
        # Binance Spot usa STOP_LOSS_LIMIT (no STOP_MARKET, que es solo para Futures).
        sl_order_id: str | None = None
        tp_order_id: str | None = None

        if decision.stop_loss is not None:
            sl_limit_price = round(decision.stop_loss * _SL_LIMIT_SLIPPAGE, 2)
            for attempt in range(1, _SL_BRACKET_RETRIES + 1):
                try:
                    sl_order = await self.exchange.create_order(
                        self.symbol, "STOP_LOSS_LIMIT", "sell", filled_qty,
                        price=sl_limit_price,
                        params={"stopPrice": decision.stop_loss, "timeInForce": "GTC"},
                    )
                    sl_order_id = str(sl_order.get("id")) if sl_order.get("id") else None
                    logger.info("executor.sl_bracket_placed",
                                order_id=sl_order_id, stop_price=decision.stop_loss,
                                limit_price=sl_limit_price, attempt=attempt)
                    break
                except Exception as e:
                    logger.warning("executor.sl_bracket_attempt_failed",
                                   error=str(e), trade_id=str(trade.id), attempt=attempt)
                    if attempt < _SL_BRACKET_RETRIES:
                        await asyncio.sleep(_SL_BRACKET_RETRY_DELAY_SEC)
            else:
                logger.error("executor.sl_bracket_failed_all_retries",
                             trade_id=str(trade.id), stop_price=decision.stop_loss,
                             retries=_SL_BRACKET_RETRIES)

        if decision.take_profit is not None:
            try:
                tp_order = await self.exchange.create_order(
                    self.symbol, "LIMIT", "sell", filled_qty, price=decision.take_profit,
                    params={"timeInForce": "GTC"},
                )
                tp_order_id = str(tp_order.get("id")) if tp_order.get("id") else None
                logger.info("executor.tp_bracket_placed",
                            order_id=tp_order_id, price=decision.take_profit)
            except Exception as e:
                logger.warning("executor.tp_bracket_failed",
                               error=str(e), trade_id=str(trade.id))

        # Actualizar trade con los IDs de las órdenes bracket obtenidas
        if sl_order_id is not None or tp_order_id is not None:
            await self.session.refresh(trade)
            trade.order_id_sl = sl_order_id
            trade.order_id_tp = tp_order_id
            await self.session.commit()

        await self.session.refresh(trade)
        logger.info("executor.buy_executed", trade_id=str(trade.id), price=avg_price,
                    sl_placed=(sl_order_id is not None), tp_placed=(tp_order_id is not None))
        return trade

    async def execute_sell(self, *, trade_id: uuid.UUID, decision_id: uuid.UUID | None,
                            close_reason: str) -> Trade:
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise RuntimeError(f"Trade {trade_id} not open")

        # Verificar balance BTC disponible antes de emitir la orden.
        # Si el BTC fue consumido por otra orden (ej. bracket compartido entre trades),
        # usar la cantidad real disponible para evitar el error "insufficient balance".
        qty_to_sell = float(trade.quantity_btc)
        try:
            balance = await self.exchange.fetch_balance()
            btc_free = float((balance.get("free") or {}).get("BTC") or 0)
            if btc_free < qty_to_sell * 0.95:
                logger.warning(
                    "executor.sell_qty_adjusted_low_balance",
                    trade_id=str(trade_id),
                    expected_qty=qty_to_sell,
                    available_btc=btc_free,
                    close_reason=close_reason,
                )
                qty_to_sell = btc_free
            if qty_to_sell < 1e-6:
                raise RuntimeError(
                    f"BTC insuficiente para cerrar trade {trade_id}: "
                    f"disponible={btc_free}, esperado={float(trade.quantity_btc)}"
                )
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("executor.balance_check_failed_using_trade_qty",
                           trade_id=str(trade_id), error=str(e))

        order = await self.exchange.create_market_order(
            self.symbol, "sell", qty_to_sell,
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
