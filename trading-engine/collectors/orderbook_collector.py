"""OrderBookCollector: maintains the latest order book snapshot in memory.

The watch loop subscribes to Binance WS via ccxt.pro. For unit tests,
inject self._book directly to bypass the WS connection.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class OrderBookSnapshot:
    spread: float
    spread_pct: float
    bid_total_btc: float
    ask_total_btc: float
    imbalance: float
    bid_wall_price: float
    bid_wall_size: float
    bid_wall_distance_pct: float
    ask_wall_price: float
    ask_wall_size: float
    ask_wall_distance_pct: float
    top_bid: float
    top_ask: float


class OrderBookCollector:
    """In-memory order book from a CCXT.pro WS feed."""

    def __init__(self, symbol: str, exchange: Any | None = None):
        self.symbol = symbol
        self.exchange = exchange
        self._book: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.exchange is None:
            raise RuntimeError("Exchange not set; cannot start WS feed")
        self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _watch_loop(self) -> None:
        consecutive_errors = 0
        while True:
            try:
                book = await self.exchange.watch_order_book(self.symbol, limit=20)
                self._book = book
                consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                # Primer error: warning. Errores persistentes: debug para evitar spam.
                if consecutive_errors == 1:
                    logger.warning("orderbook.watch.error", error=str(e))
                else:
                    logger.debug("orderbook.watch.error", error=str(e),
                                 consecutive=consecutive_errors)
                # Backoff exponencial con techo de 60 s (errores permanentes como
                # "not supported" no necesitan reintentos agresivos)
                backoff = min(2 * (2 ** min(consecutive_errors - 1, 4)), 60)
                await asyncio.sleep(backoff)

    def snapshot(self, levels: int = 10) -> OrderBookSnapshot | None:
        """Return derived metrics from the latest book, or None if no book yet."""
        if self._book is None:
            return None
        bids = self._book.get("bids", [])[:levels]
        asks = self._book.get("asks", [])[:levels]
        if not bids or not asks:
            return None

        top_bid = float(bids[0][0])
        top_ask = float(asks[0][0])
        mid = (top_bid + top_ask) / 2
        spread = top_ask - top_bid
        spread_pct = spread / mid * 100 if mid > 0 else 0.0

        bid_total = sum(float(lvl[1]) for lvl in bids)
        ask_total = sum(float(lvl[1]) for lvl in asks)
        imbalance = bid_total / ask_total if ask_total > 0 else float("inf")

        bid_wall = max(bids, key=lambda lvl: float(lvl[1]))
        ask_wall = max(asks, key=lambda lvl: float(lvl[1]))

        return OrderBookSnapshot(
            spread=spread,
            spread_pct=spread_pct,
            bid_total_btc=bid_total,
            ask_total_btc=ask_total,
            imbalance=imbalance,
            bid_wall_price=float(bid_wall[0]),
            bid_wall_size=float(bid_wall[1]),
            bid_wall_distance_pct=(top_bid - float(bid_wall[0])) / top_bid * 100,
            ask_wall_price=float(ask_wall[0]),
            ask_wall_size=float(ask_wall[1]),
            ask_wall_distance_pct=(float(ask_wall[0]) - top_ask) / top_ask * 100,
            top_bid=top_bid,
            top_ask=top_ask,
        )
