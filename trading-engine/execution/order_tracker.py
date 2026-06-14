from __future__ import annotations
import uuid
from collections import defaultdict
from typing import Any
import structlog
from execution.futures_algo_orders import cancel_conditional_algo_order
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Trade, Ohlcv
from execution.executor import Executor

logger = structlog.get_logger()


class OrderTracker:
    """Detecta fills de SL/TP en Binance y cierra el trade en la BD sin emitir órdenes.

    Mecanismos de detección (en orden de prioridad):
    1. Match por order_id: si el trade tiene order_id_sl/order_id_tp guardados en BD,
       se busca el fill con ese ID exacto (inmune a coincidencias de cantidad).
    2. Match por cantidad agregada: agrupa sub-fills del mismo order_id de Binance y
       compara la cantidad total con la del trade (±2%). Cubre fills partidos en lotes.
    3. Software SL guardian: si el precio actual o el low de la última vela están por
       debajo del stop_loss, emite un market sell de emergencia.
    """

    def __init__(self, exchange: Any, session: AsyncSession, executor: Executor,
                 *, symbol: str):
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
                    await self._cancel_bracket_orders(trade)
                    await self._close_open_trade(
                        trade, decision_id=None, close_reason="manual_close",
                    )
                except Exception as e:
                    error_str = str(e)
                    if "-1013" in error_str and "NOTIONAL" in error_str:
                        logger.warning(
                            "order_tracker.manual_close_notional_fallback",
                            trade_id=str(trade.id),
                            error=error_str,
                        )
                        try:
                            market_price = await self._fetch_current_price()
                            if market_price:
                                await self.executor.force_close_trade(
                                    trade_id=trade.id,
                                    market_price=market_price,
                                    close_reason="force_closed_notional",
                                )
                            else:
                                logger.error(
                                    "order_tracker.manual_close_notional_price_unavailable",
                                    trade_id=str(trade.id),
                                )
                        except Exception as fe:
                            logger.error("order_tracker.force_close_failed",
                                         trade_id=str(trade.id), error=str(fe))
                    elif "-2022" in error_str:
                        logger.warning(
                            "order_tracker.manual_close_position_gone_fallback",
                            trade_id=str(trade.id),
                            error=error_str,
                        )
                        price = await self._fetch_current_price()
                        if price:
                            await self.executor.force_close_trade(
                                trade_id=trade.id, market_price=price,
                                close_reason="force_closed_position_gone",
                            )
                    else:
                        logger.error("order_tracker.manual_close_failed",
                                     trade_id=str(trade.id), error=error_str)

        current_price = await self._fetch_current_price()
        last_candle_low = await self._fetch_last_candle_low()
        last_candle_high = await self._fetch_last_candle_high()

        try:
            fills = await self.exchange.fetch_my_trades(self.symbol, limit=50)
        except Exception as e:
            logger.warning("order_tracker.fetch_trades_failed", error=str(e))
            fills = []

        aggregated_sell = self._aggregate_fills_by_order([f for f in fills if f.get("side") == "sell"])
        aggregated_buy = self._aggregate_fills_by_order([f for f in fills if f.get("side") == "buy"])

        # Re-fetch open trades: el loop de close_requested puede haber cerrado alguno
        open_trades = (await self.session.execute(
            select(Trade).where(Trade.status == "open")
        )).scalars().all()

        for trade in open_trades:
            qty = float(trade.quantity_btc)
            entry_ts = trade.ts_open.timestamp() * 1000  # ms epoch
            pos_side = getattr(trade, "position_side", "LONG") or "LONG"
            aggregated = aggregated_buy if pos_side == "SHORT" else aggregated_sell
            known_bracket_ids = {
                oid for oid in (trade.order_id_sl, trade.order_id_tp) if oid
            }

            # --- Mecanismo 1: match exacto por order_id_sl / order_id_tp ---
            matched_agg = None
            match_method = None
            if known_bracket_ids:
                for oid, agg in aggregated.items():
                    if oid in known_bracket_ids and agg["timestamp"] >= entry_ts:
                        matched_agg = agg
                        match_method = "order_id"
                        logger.info("order_tracker.bracket_matched_by_id",
                                    trade_id=str(trade.id), order_id=oid,
                                    fill_qty=agg["amount"])
                        break

            # --- Mecanismo 2: match por cantidad agregada (fallback) ---
            if matched_agg is None:
                for oid, agg in aggregated.items():
                    if agg["timestamp"] < entry_ts:
                        continue
                    if qty > 0 and abs(agg["amount"] - qty) / qty < 0.02:
                        matched_agg = agg
                        match_method = "qty_approx"
                        logger.info("order_tracker.bracket_matched_by_qty",
                                    trade_id=str(trade.id), order_id=oid,
                                    fill_qty=agg["amount"], trade_qty=qty)
                        break

            if matched_agg is not None:
                fill_price = matched_agg["price"]
                fill_fee = matched_agg["fee"]
                if fill_price > 0:
                    sl = float(trade.stop_loss or 0)
                    tp = float(trade.take_profit or 0)
                    if pos_side == "SHORT":
                        if sl > 0 and fill_price >= sl * 0.998:
                            reason = "sl_triggered"
                        elif tp > 0 and fill_price <= tp * 1.002:
                            reason = "tp_triggered"
                        else:
                            reason = "bracket_fill"
                    else:
                        if sl > 0 and fill_price <= sl * 1.002:
                            reason = "sl_triggered"
                        elif tp > 0 and fill_price >= tp * 0.998:
                            reason = "tp_triggered"
                        else:
                            reason = "bracket_fill"
                    logger.info("order_tracker.bracket_detected",
                                trade_id=str(trade.id), reason=reason,
                                fill_price=fill_price, match_method=match_method)
                    await self.executor.record_bracket_fill(
                        trade_id=trade.id, fill_price=fill_price,
                        fill_fee=fill_fee, close_reason=reason,
                    )
                    continue

            # --- Mecanismo 3: software SL guardian ---
            # Dispara si el precio actual (ticker puntual) O el low de la última vela
            # están por debajo del stop_loss. El ticker puntual puede no capturar
            # el mínimo intravela, por lo que el low de la vela cierra esa brecha.
            sl = float(trade.stop_loss or 0)
            if pos_side == "SHORT":
                sl_breached = sl > 0 and (
                    (current_price is not None and current_price > sl)
                    or (last_candle_high is not None and last_candle_high > sl)
                )
                trigger_price = (
                    current_price
                    if (current_price is not None and current_price > sl)
                    else last_candle_high
                )
            else:
                sl_breached = sl > 0 and (
                    (current_price is not None and current_price < sl)
                    or (last_candle_low is not None and last_candle_low < sl)
                )
                trigger_price = (
                    current_price
                    if (current_price is not None and current_price < sl)
                    else last_candle_low
                )
            if sl_breached:
                logger.warning(
                    "order_tracker.sl_guardian_triggered",
                    trade_id=str(trade.id),
                    stop_loss=sl,
                    current_price=current_price,
                    last_candle_low=last_candle_low,
                    trigger_price=trigger_price,
                )
                try:
                    await self._cancel_bracket_orders(trade)
                    await self._close_open_trade(
                        trade, decision_id=None, close_reason="sl_triggered",
                    )
                except Exception as e:
                    error_str = str(e)
                    if "-1013" in error_str and "NOTIONAL" in error_str:
                        logger.warning(
                            "order_tracker.sl_guardian_notional_fallback",
                            trade_id=str(trade.id),
                            error=error_str,
                        )
                        try:
                            price = current_price or await self._fetch_current_price()
                            if price:
                                await self.executor.force_close_trade(
                                    trade_id=trade.id,
                                    market_price=price,
                                    close_reason="force_closed_notional",
                                )
                            else:
                                logger.error(
                                    "order_tracker.sl_guardian_notional_price_unavailable",
                                    trade_id=str(trade.id),
                                )
                        except Exception as fe:
                            logger.error("order_tracker.sl_guardian_force_close_failed",
                                         trade_id=str(trade.id), error=str(fe))
                    elif "-2022" in error_str:
                        logger.warning(
                            "order_tracker.sl_guardian_position_gone_fallback",
                            trade_id=str(trade.id),
                            error=error_str,
                        )
                        price = current_price or await self._fetch_current_price()
                        if price:
                            await self.executor.force_close_trade(
                                trade_id=trade.id, market_price=price,
                                close_reason="force_closed_position_gone",
                            )
                    else:
                        logger.error("order_tracker.sl_guardian_failed",
                                     trade_id=str(trade.id), error=error_str)
                continue

            # --- Mecanismo 4: software TP guardian ---
            # Actúa cuando el bracket de TP no fue colocado en Binance (order_id_tp == None),
            # lo cual ocurre cuando Binance rechaza la orden LIMIT por saldo insuficiente
            # (el BTC ya está reservado por la orden SL activa).
            # En ese caso, monitorea el precio actual y cierra con market sell al alcanzar el TP.
            tp = float(trade.take_profit or 0)
            tp_guardian_active = not trade.order_id_tp and tp > 0
            if pos_side == "SHORT":
                tp_reached = (
                    tp_guardian_active
                    and current_price is not None
                    and current_price <= tp
                )
            else:
                tp_reached = (
                    tp_guardian_active
                    and current_price is not None
                    and current_price >= tp
                )
            if tp_reached:
                logger.warning(
                    "order_tracker.tp_guardian_triggered",
                    trade_id=str(trade.id),
                    take_profit=tp,
                    current_price=current_price,
                )
                try:
                    await self._close_open_trade(
                        trade, decision_id=None, close_reason="tp_triggered",
                    )
                except Exception as e:
                    error_str = str(e)
                    if "-1013" in error_str and "NOTIONAL" in error_str:
                        logger.warning(
                            "order_tracker.tp_guardian_notional_fallback",
                            trade_id=str(trade.id),
                            error=error_str,
                        )
                        try:
                            price = current_price or await self._fetch_current_price()
                            if price:
                                await self.executor.force_close_trade(
                                    trade_id=trade.id,
                                    market_price=price,
                                    close_reason="force_closed_notional",
                                )
                            else:
                                logger.error(
                                    "order_tracker.tp_guardian_notional_price_unavailable",
                                    trade_id=str(trade.id),
                                )
                        except Exception as fe:
                            logger.error("order_tracker.tp_guardian_force_close_failed",
                                         trade_id=str(trade.id), error=str(fe))
                    elif "-2022" in error_str:
                        logger.warning(
                            "order_tracker.tp_guardian_position_gone_fallback",
                            trade_id=str(trade.id),
                            error=error_str,
                        )
                        price = current_price or await self._fetch_current_price()
                        if price:
                            await self.executor.force_close_trade(
                                trade_id=trade.id, market_price=price,
                                close_reason="force_closed_position_gone",
                            )


    @staticmethod
    def _aggregate_fills_by_order(fills: list[dict]) -> dict[str, dict]:
        aggregated: dict[str, dict] = defaultdict(
            lambda: {"amount": 0.0, "price": 0.0, "fee": 0.0, "timestamp": 0}
        )
        for fill in fills:
            oid = str(fill.get("order") or fill.get("id") or "")
            if not oid:
                continue
            agg = aggregated[oid]
            fill_qty = float(fill.get("amount") or 0)
            fill_price = float(fill.get("price") or 0)
            agg["amount"] += fill_qty
            if agg["amount"] > 0:
                prev_qty = agg["amount"] - fill_qty
                agg["price"] = (agg["price"] * prev_qty + fill_price * fill_qty) / agg["amount"]
            agg["fee"] += float((fill.get("fee") or {}).get("cost") or 0)
            agg["timestamp"] = max(agg["timestamp"], fill.get("timestamp") or 0)
        return aggregated

    async def _close_open_trade(
        self,
        trade: Trade,
        *,
        decision_id: uuid.UUID | None,
        close_reason: str,
    ) -> None:
        await self.executor.execute_close(
            trade_id=trade.id, decision_id=decision_id, close_reason=close_reason,
        )

    async def _fetch_current_price(self) -> float | None:
        """Obtiene el precio actual del mercado para el guardian de SL."""
        try:
            ticker = await self.exchange.fetch_ticker(self.symbol)
            return float(ticker.get("last") or ticker.get("close") or 0) or None
        except Exception as e:
            logger.warning("order_tracker.price_fetch_failed", error=str(e))
            return None

    async def _fetch_last_candle_low(self) -> float | None:
        """Retorna el low de la última vela 1m almacenada en BD.

        Complementa al ticker puntual: si el precio tocó el SL intravela
        pero ya rebotó al momento del poll, el ticker no lo detecta pero
        el low de la vela sí. Usa 1m para máxima resolución temporal.
        """
        try:
            row = (await self.session.execute(
                select(Ohlcv)
                .where(Ohlcv.timeframe == "1m")
                .order_by(Ohlcv.time.desc())
                .limit(1)
            )).scalar_one_or_none()
            return float(row.low) if row else None
        except Exception as e:
            logger.warning("order_tracker.candle_low_fetch_failed", error=str(e))
            return None

    async def _fetch_last_candle_high(self) -> float | None:
        try:
            row = (await self.session.execute(
                select(Ohlcv)
                .where(Ohlcv.timeframe == "1m")
                .order_by(Ohlcv.time.desc())
                .limit(1)
            )).scalar_one_or_none()
            return float(row.high) if row else None
        except Exception as e:
            logger.warning("order_tracker.candle_high_fetch_failed", error=str(e))
            return None

    async def _cancel_bracket_orders(self, trade: Trade) -> None:
        """Cancela las órdenes SL/TP pendientes en Binance antes del market sell.

        Si las órdenes ya se ejecutaron o no existen, ignora el error silenciosamente.
        """
        for order_id in (trade.order_id_sl, trade.order_id_tp):
            if not order_id:
                continue
            cancelled = False
            try:
                await self.exchange.cancel_order(
                    order_id, self.symbol, params={"trigger": True},
                )
                cancelled = True
            except Exception:
                try:
                    await cancel_conditional_algo_order(
                        self.exchange, symbol=self.symbol, algo_id=order_id,
                    )
                    cancelled = True
                except Exception:
                    pass
            if cancelled:
                logger.info("order_tracker.bracket_order_cancelled",
                            trade_id=str(trade.id), order_id=order_id)
