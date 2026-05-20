from __future__ import annotations
from collections import defaultdict
from typing import Any
import structlog
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
                    await self.executor.execute_sell(
                        trade_id=trade.id, decision_id=None, close_reason="manual_close",
                    )
                except Exception as e:
                    logger.error("order_tracker.manual_close_failed",
                                 trade_id=str(trade.id), error=str(e))

        current_price = await self._fetch_current_price()
        last_candle_low = await self._fetch_last_candle_low()

        try:
            fills = await self.exchange.fetch_my_trades(self.symbol, limit=50)
        except Exception as e:
            logger.warning("order_tracker.fetch_trades_failed", error=str(e))
            fills = []

        sell_fills = [f for f in fills if f.get("side") == "sell"]

        # Agrupar sub-fills por order_id para detectar fills parciales de Binance.
        # Binance puede partir una orden en múltiples registros con el mismo order_id
        # pero cantidades menores. Sumar todos los sub-fills del mismo order para
        # comparar contra la cantidad total del trade.
        aggregated: dict[str, dict] = defaultdict(
            lambda: {"amount": 0.0, "price": 0.0, "fee": 0.0, "timestamp": 0}
        )
        for fill in sell_fills:
            oid = str(fill.get("order") or fill.get("id") or "")
            if not oid:
                continue
            agg = aggregated[oid]
            fill_qty = float(fill.get("amount") or 0)
            fill_price = float(fill.get("price") or 0)
            agg["amount"] += fill_qty
            # precio promedio ponderado por cantidad
            if agg["amount"] > 0:
                prev_qty = agg["amount"] - fill_qty
                agg["price"] = (agg["price"] * prev_qty + fill_price * fill_qty) / agg["amount"]
            agg["fee"] += float((fill.get("fee") or {}).get("cost") or 0)
            agg["timestamp"] = max(agg["timestamp"], fill.get("timestamp") or 0)

        # Re-fetch open trades: el loop de close_requested puede haber cerrado alguno
        open_trades = (await self.session.execute(
            select(Trade).where(Trade.status == "open")
        )).scalars().all()

        for trade in open_trades:
            qty = float(trade.quantity_btc)
            entry_ts = trade.ts_open.timestamp() * 1000  # ms epoch
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
            sl_breached = sl > 0 and (
                (current_price is not None and current_price < sl)
                or (last_candle_low is not None and last_candle_low < sl)
            )
            if sl_breached:
                trigger_price = (
                    current_price
                    if (current_price is not None and current_price < sl)
                    else last_candle_low
                )
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
                    await self.executor.execute_sell(
                        trade_id=trade.id, decision_id=None, close_reason="sl_triggered",
                    )
                except Exception as e:
                    logger.error("order_tracker.sl_guardian_failed",
                                 trade_id=str(trade.id), error=str(e))

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

    async def _cancel_bracket_orders(self, trade: Trade) -> None:
        """Cancela las órdenes SL/TP pendientes en Binance antes del market sell.

        Si las órdenes ya se ejecutaron o no existen, ignora el error silenciosamente.
        """
        for order_id in (trade.order_id_sl, trade.order_id_tp):
            if not order_id:
                continue
            try:
                await self.exchange.cancel_order(order_id, self.symbol)
                logger.info("order_tracker.bracket_order_cancelled",
                            trade_id=str(trade.id), order_id=order_id)
            except Exception:
                pass
