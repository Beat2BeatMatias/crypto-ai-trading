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
from shared.notional_sizing import entry_notional_usdt
from shared.schemas import DecisorOutput, Direction
from shared.pnl import compute_pnl_pct_directional, compute_pnl_usdt_directional

logger = structlog.get_logger()

# Binance Spot: STOP_LOSS_LIMIT requiere un precio límite ligeramente por debajo
# del stop price para garantizar el fill ante pequeños gaps de precio.
_SL_LIMIT_SLIPPAGE = 0.9985  # 0.15% por debajo del stop price
_SL_BRACKET_RETRIES = 2
_SL_BRACKET_RETRY_DELAY_SEC = 2.0

# OCO (One-Cancels-the-Other): coloca SL y TP en una sola llamada a Binance.
# Cuando uno se ejecuta, Binance cancela automáticamente el otro, resolviendo
# el problema de saldo insuficiente que ocurre al colocarlos por separado
# (el SL reserva el BTC y Binance rechaza el TP con "insufficient balance").
_OCO_RETRIES = 2
_OCO_RETRY_DELAY_SEC = 2.0


class Executor:
    def __init__(
        self,
        exchange: Any,
        session: AsyncSession,
        *,
        symbol: str,
        adapter: Any | None = None,
    ):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol
        self._adapter = adapter

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

        # Colocar brackets SL/TP via OCO (One-Cancels-the-Other).
        # La OCO coloca SL y TP en una sola llamada: cuando uno se ejecuta,
        # Binance cancela el otro automáticamente. Esto elimina el problema de
        # "insufficient balance" que ocurre al colocarlos por separado (el SL
        # reserva el BTC y Binance rechaza el TP con saldo insuficiente).
        #
        # Fallback en cascada si algún precio no está disponible:
        #   1. OCO (SL + TP en un request) — preferido cuando ambos precios existen
        #   2. Solo SL STOP_LOSS_LIMIT — cuando no hay TP en la decisión
        #   3. Solo TP LIMIT — cuando no hay SL en la decisión (poco común)
        #   4. Sin bracket — SL Guardian y TP Guardian del OrderTracker cubren el trade
        sl_order_id: str | None = None
        tp_order_id: str | None = None

        has_sl = decision.stop_loss is not None
        has_tp = decision.take_profit is not None

        if has_sl and has_tp:
            sl_order_id, tp_order_id = await self._place_oco_bracket(
                filled_qty=filled_qty,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                trade_id=trade.id,
            )
        elif has_sl:
            sl_order_id = await self._place_sl_bracket(
                filled_qty=filled_qty,
                stop_loss=decision.stop_loss,
                trade_id=trade.id,
            )
        elif has_tp:
            tp_order_id = await self._place_tp_bracket(
                filled_qty=filled_qty,
                take_profit=decision.take_profit,
                trade_id=trade.id,
            )

        # Persistir los IDs de brackets obtenidos (incluso si solo uno fue exitoso)
        if sl_order_id is not None or tp_order_id is not None:
            await self.session.refresh(trade)
            trade.order_id_sl = sl_order_id
            trade.order_id_tp = tp_order_id
            await self.session.commit()

        await self.session.refresh(trade)
        logger.info("executor.buy_executed", trade_id=str(trade.id), price=avg_price,
                    sl_placed=(sl_order_id is not None), tp_placed=(tp_order_id is not None))
        return trade

    async def _place_oco_bracket(
        self, *, filled_qty: float, stop_loss: float, take_profit: float,
        trade_id: uuid.UUID,
    ) -> tuple[str | None, str | None]:
        """Coloca una orden OCO (SL + TP simultáneos) con retry.

        Binance Spot usa el nuevo endpoint POST /api/v3/orderList/oco, mapeado en
        CCXT como exchange.privatePostOrderListOco(). El endpoint deprecated
        POST /api/v3/order/oco ya no acepta el tipo "OCO" para BTC/USDT.

        Parámetros del orderList OCO para el lado SELL:
          - aboveType      → LIMIT_MAKER  (orden TP, ejecuta si precio sube)
          - abovePrice     → take_profit
          - belowType      → STOP_LOSS_LIMIT (orden SL, ejecuta si precio baja)
          - belowStopPrice → stop_loss (precio de activación)
          - belowPrice     → sl_limit_price (precio límite, ligeramente por debajo)

        Si la OCO falla (error transitorio), hace fallback colocando SL solo.
        El TP Guardian del OrderTracker cubre el TP mientras no haya OCO activa.
        """
        sl_limit_price = round(stop_loss * _SL_LIMIT_SLIPPAGE, 2)
        # Binance rechaza notación científica (ej. "8e-05"); usar Decimal para
        # obtener siempre representación decimal completa (ej. "0.00008").
        qty_str = format(Decimal(str(filled_qty)), 'f')

        for attempt in range(1, _OCO_RETRIES + 1):
            try:
                oco = await self.exchange.privatePostOrderListOco({
                    "symbol": self.symbol.replace("/", ""),
                    "side": "SELL",
                    "quantity": qty_str,
                    "aboveType": "LIMIT_MAKER",
                    "abovePrice": str(take_profit),
                    "belowType": "STOP_LOSS_LIMIT",
                    "belowStopPrice": str(stop_loss),
                    "belowPrice": str(sl_limit_price),
                    "belowTimeInForce": "GTC",
                })
                # Binance retorna orderListId + lista "orderReports" con las dos órdenes hijas
                order_reports = oco.get("orderReports") or oco.get("orders") or []
                sl_id: str | None = None
                tp_id: str | None = None
                for o in order_reports:
                    otype = (o.get("type") or "").upper()
                    oid = str(o["orderId"]) if o.get("orderId") else None
                    if otype in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                        sl_id = oid
                    elif otype in ("LIMIT_MAKER", "LIMIT"):
                        tp_id = oid
                # Fallback: si la respuesta no viene con orderReports parseables, usar el listId
                if not sl_id and not tp_id:
                    root_id = str(oco.get("orderListId") or oco.get("id") or "")
                    sl_id = root_id or None
                    tp_id = root_id or None
                logger.info("executor.oco_bracket_placed",
                            trade_id=str(trade_id), sl_id=sl_id, tp_id=tp_id,
                            stop_price=stop_loss, tp_price=take_profit, attempt=attempt)
                return sl_id, tp_id
            except Exception as e:
                logger.warning("executor.oco_bracket_attempt_failed",
                               error=str(e), trade_id=str(trade_id), attempt=attempt)
                if attempt < _OCO_RETRIES:
                    await asyncio.sleep(_OCO_RETRY_DELAY_SEC)

        # Fallback: OCO agotó reintentos → colocar solo el SL
        logger.warning("executor.oco_failed_falling_back_to_sl_only",
                       trade_id=str(trade_id), stop_price=stop_loss, tp_price=take_profit)
        sl_id = await self._place_sl_bracket(
            filled_qty=filled_qty, stop_loss=stop_loss, trade_id=trade_id,
        )
        return sl_id, None

    async def _place_sl_bracket(
        self, *, filled_qty: float, stop_loss: float, trade_id: uuid.UUID,
    ) -> str | None:
        """Coloca solo la orden STOP_LOSS_LIMIT con retry."""
        sl_limit_price = round(stop_loss * _SL_LIMIT_SLIPPAGE, 2)
        for attempt in range(1, _SL_BRACKET_RETRIES + 1):
            try:
                sl_order = await self.exchange.create_order(
                    self.symbol, "STOP_LOSS_LIMIT", "sell", filled_qty,
                    price=sl_limit_price,
                    params={"stopPrice": stop_loss, "timeInForce": "GTC"},
                )
                sl_id = str(sl_order.get("id")) if sl_order.get("id") else None
                logger.info("executor.sl_bracket_placed",
                            order_id=sl_id, stop_price=stop_loss,
                            limit_price=sl_limit_price, attempt=attempt)
                return sl_id
            except Exception as e:
                logger.warning("executor.sl_bracket_attempt_failed",
                               error=str(e), trade_id=str(trade_id), attempt=attempt)
                if attempt < _SL_BRACKET_RETRIES:
                    await asyncio.sleep(_SL_BRACKET_RETRY_DELAY_SEC)
        logger.error("executor.sl_bracket_failed_all_retries",
                     trade_id=str(trade_id), stop_price=stop_loss,
                     retries=_SL_BRACKET_RETRIES)
        return None

    async def _place_tp_bracket(
        self, *, filled_qty: float, take_profit: float, trade_id: uuid.UUID,
    ) -> str | None:
        """Coloca solo la orden LIMIT de TP (sin SL asociado)."""
        try:
            tp_order = await self.exchange.create_order(
                self.symbol, "LIMIT", "sell", filled_qty, price=take_profit,
                params={"timeInForce": "GTC"},
            )
            tp_id = str(tp_order.get("id")) if tp_order.get("id") else None
            logger.info("executor.tp_bracket_placed",
                        order_id=tp_id, price=take_profit)
            return tp_id
        except Exception as e:
            logger.warning("executor.tp_bracket_failed",
                           error=str(e), trade_id=str(trade_id))
            return None

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
        pos_side = getattr(trade, "position_side", "LONG") or "LONG"
        gross_pnl = compute_pnl_usdt_directional(
            entry=float(trade.entry_price),
            quantity=float(trade.quantity_btc),
            exit_price=avg_price,
            direction=pos_side,
        ) or 0.0
        prior_fees = float(trade.fees_usdt or 0)
        trade.fees_usdt = Decimal(str(prior_fees + fee))
        trade.pnl_usdt = Decimal(str(gross_pnl - prior_fees - fee))
        pnl_pct = compute_pnl_pct_directional(
            entry=float(trade.entry_price), exit_price=avg_price, direction=pos_side,
        )
        trade.pnl_pct = Decimal(str(pnl_pct)) if pnl_pct is not None else None
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
                d.trade_id = trade.id
        await self.session.commit()
        await self.session.refresh(trade)
        logger.info("executor.sell_executed", trade_id=str(trade.id))
        return trade

    async def force_close_trade(self, *, trade_id: uuid.UUID, market_price: float,
                                close_reason: str) -> Trade:
        """Cierra un trade en BD sin emitir ninguna orden al exchange.

        Usado cuando el exchange rechaza la orden por motivos estructurales
        (ej. NOTIONAL insuficiente) y el trade no puede cerrarse de forma normal.
        El PnL se calcula con el precio de mercado actual como precio de salida.
        No se registra order_id_close ya que no hubo orden real.
        """
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise RuntimeError(f"Trade {trade_id} not open")
        trade.exit_price = None   # sin orden real — el BTC permanece en la cuenta
        trade.ts_close = datetime.now(tz=timezone.utc)
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.pnl_usdt = None     # no aplica: no hubo venta en el exchange
        trade.pnl_pct = None
        pos = (await self.session.execute(
            select(Position).where(Position.trade_id == trade.id)
        )).scalar_one_or_none()
        if pos:
            pos.status = "closed"
            pos.updated_at = datetime.now(tz=timezone.utc)
        await self.session.commit()
        await self.session.refresh(trade)
        logger.warning(
            "executor.force_close",
            trade_id=str(trade.id),
            close_reason=close_reason,
            market_price=market_price,
        )
        return trade

    async def record_bracket_fill(self, *, trade_id: uuid.UUID, fill_price: float,
                                   fill_fee: float, close_reason: str) -> None:
        """Registra el cierre de un trade cuyo SL/TP ya fue ejecutado por Binance.
        No emite ninguna orden al exchange — solo actualiza la BD."""
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            return
        prior_fees = float(trade.fees_usdt or 0)
        pos_side = getattr(trade, "position_side", "LONG") or "LONG"
        gross_pnl = compute_pnl_usdt_directional(
            entry=float(trade.entry_price),
            quantity=float(trade.quantity_btc),
            exit_price=fill_price,
            direction=pos_side,
        ) or 0.0
        trade.exit_price = Decimal(str(fill_price))
        trade.ts_close = datetime.now(tz=timezone.utc)
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.fees_usdt = Decimal(str(prior_fees + fill_fee))
        trade.pnl_usdt = Decimal(str(gross_pnl - prior_fees - fill_fee))
        pnl_pct = compute_pnl_pct_directional(
            entry=float(trade.entry_price), exit_price=fill_price, direction=pos_side,
        )
        trade.pnl_pct = Decimal(str(pnl_pct)) if pnl_pct is not None else None
        pos = (await self.session.execute(
            select(Position).where(Position.trade_id == trade.id)
        )).scalar_one_or_none()
        if pos:
            pos.status = "closed"
            pos.updated_at = datetime.now(tz=timezone.utc)
        await self.session.commit()
        logger.info("executor.bracket_fill_recorded", trade_id=str(trade_id),
                    reason=close_reason, price=fill_price, pnl=float(trade.pnl_usdt))

    async def execute_open(
        self,
        *,
        direction: Direction,
        decision: DecisorOutput,
        decision_id: uuid.UUID,
        available_margin: float,
        price: float,
        leverage: float = 1.0,
        trading_product: str = "futures",
    ) -> Trade:
        if self._adapter is None:
            if direction != Direction.LONG:
                raise ValueError("SHORT open requires ExchangeAdapter")
            return await self.execute_buy(
                decision=decision, decision_id=decision_id, usdt_balance=available_margin,
            )

        notional = entry_notional_usdt(
            margin=available_margin,
            position_size_pct=decision.position_size_pct,
            leverage=leverage,
            trading_product=trading_product,
        )
        res = await self._adapter.open_position(
            symbol=self.symbol, direction=direction, notional_usdt=notional, price=price,
        )
        if res.avg_price == 0 or res.filled_qty == 0:
            raise RuntimeError(f"open zero fill: {res}")

        order_side = "BUY" if direction == Direction.LONG else "SELL"
        trade = Trade(
            decision_id=decision_id,
            ts_open=datetime.now(tz=timezone.utc),
            side=order_side,
            position_side=direction.value,
            quantity_btc=Decimal(str(res.filled_qty)),
            entry_price=Decimal(str(res.avg_price)),
            status="open",
            stop_loss=Decimal(str(decision.stop_loss)) if decision.stop_loss else None,
            take_profit=Decimal(str(decision.take_profit)) if decision.take_profit else None,
            order_id_open=res.order_id,
            leverage=Decimal(str(leverage)),
            margin_mode="isolated",
        )
        self.session.add(trade)
        await self.session.flush()
        self.session.add(Position(
            trade_id=trade.id,
            symbol=self.symbol,
            quantity_btc=trade.quantity_btc,
            entry_price=trade.entry_price,
            position_side=direction.value,
            leverage=trade.leverage,
            status="open",
            opened_at=trade.ts_open,
        ))
        d = await self.session.get(Decision, decision_id)
        if d is not None:
            d.executed = True
            d.trade_id = trade.id
        await self.session.commit()

        try:
            brackets = await self._adapter.place_brackets(
                symbol=self.symbol,
                direction=direction,
                qty=res.filled_qty,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
            )
            if brackets.order_id_sl or brackets.order_id_tp:
                await self.session.refresh(trade)
                trade.order_id_sl = brackets.order_id_sl
                trade.order_id_tp = brackets.order_id_tp
                await self.session.commit()
        except Exception as e:
            logger.error(
                "executor.brackets_failed",
                trade_id=str(trade.id),
                direction=direction.value,
                error=str(e),
            )
            d = await self.session.get(Decision, decision_id)
            if d is not None:
                d.rejected_reason = f"brackets_failed: {str(e)[:160]}"
                await self.session.commit()
        await self.session.refresh(trade)
        logger.info(
            "executor.open_executed",
            trade_id=str(trade.id),
            direction=direction.value,
            price=res.avg_price,
        )
        return trade

    async def execute_close(
        self,
        *,
        trade_id: uuid.UUID,
        decision_id: uuid.UUID | None,
        close_reason: str,
    ) -> Trade:
        trade = await self.session.get(Trade, trade_id)
        if trade is None or trade.status != "open":
            raise RuntimeError(f"Trade {trade_id} not open")

        pos_side = getattr(trade, "position_side", "LONG") or "LONG"
        direction = Direction(pos_side)

        if self._adapter is not None:
            res = await self._adapter.close_position(
                symbol=self.symbol,
                direction=direction,
                qty=float(trade.quantity_btc),
                close_reason=close_reason,
            )
            exit_price = res.avg_price
            order_id = res.order_id
        else:
            if direction == Direction.SHORT:
                raise ValueError("SHORT close requires ExchangeAdapter")
            return await self.execute_sell(
                trade_id=trade_id, decision_id=decision_id, close_reason=close_reason,
            )

        fee = 0.0
        trade.exit_price = Decimal(str(exit_price))
        trade.ts_close = datetime.now(tz=timezone.utc)
        trade.status = "closed"
        trade.close_reason = close_reason
        trade.order_id_close = order_id
        gross_pnl = compute_pnl_usdt_directional(
            entry=float(trade.entry_price),
            quantity=float(trade.quantity_btc),
            exit_price=exit_price,
            direction=pos_side,
        ) or 0.0
        prior_fees = float(trade.fees_usdt or 0)
        trade.fees_usdt = Decimal(str(prior_fees + fee))
        trade.pnl_usdt = Decimal(str(gross_pnl - prior_fees - fee))
        pnl_pct = compute_pnl_pct_directional(
            entry=float(trade.entry_price), exit_price=exit_price, direction=pos_side,
        )
        trade.pnl_pct = Decimal(str(pnl_pct)) if pnl_pct is not None else None
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
                d.trade_id = trade.id
        await self.session.commit()
        await self.session.refresh(trade)
        logger.info("executor.close_executed", trade_id=str(trade.id), reason=close_reason)
        return trade
