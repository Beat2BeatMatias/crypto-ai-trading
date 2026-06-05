from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from execution.futures_algo_orders import cancel_conditional_algo_order, place_conditional_algo_order
from shared.schemas import Direction


@dataclass(frozen=True)
class OpenResult:
    filled_qty: float
    avg_price: float
    order_id: str


@dataclass(frozen=True)
class CloseResult:
    filled_qty: float
    avg_price: float
    order_id: str


@dataclass(frozen=True)
class BracketResult:
    order_id_sl: str | None
    order_id_tp: str | None


@dataclass(frozen=True)
class BalanceView:
    available: float
    total: float
    position_qty: float


@dataclass(frozen=True)
class PositionView:
    symbol: str
    direction: Direction | None
    qty: float
    entry_price: float
    liquidation_price: float | None
    leverage: float


@runtime_checkable
class ExchangeAdapter(Protocol):
    product: str

    def build_client(self) -> Any: ...

    async def setup_symbol(self, symbol: str, *, leverage: int, margin_mode: str) -> None: ...

    async def open_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        notional_usdt: float,
        price: float,
    ) -> OpenResult: ...

    async def close_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        qty: float,
        close_reason: str,
    ) -> CloseResult: ...

    async def place_brackets(
        self,
        *,
        symbol: str,
        direction: Direction,
        qty: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> BracketResult: ...

    async def fetch_balance(self) -> BalanceView: ...

    async def fetch_positions(self) -> list[PositionView]: ...

    async def fetch_funding_rate(self, symbol: str) -> float: ...

    def min_notional(self, symbol: str) -> float: ...


class SpotAdapter:
    product = "spot"

    def __init__(self, client: Any | None = None):
        self._client = client

    def build_client(self) -> Any:
        if self._client is None:
            from exchange import build_binance_client

            self._client = build_binance_client()
        return self._client

    async def setup_symbol(self, symbol: str, *, leverage: int, margin_mode: str) -> None:
        return None

    async def open_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        notional_usdt: float,
        price: float,
    ) -> OpenResult:
        if direction != Direction.LONG:
            raise ValueError("SpotAdapter only supports LONG (BUY)")
        client = self.build_client()
        order = await client.create_market_order(
            symbol, "buy", None, params={"quoteOrderQty": notional_usdt},
        )
        return OpenResult(
            filled_qty=float(order.get("filled") or 0.0),
            avg_price=float(order.get("average") or price),
            order_id=str(order["id"]),
        )

    async def close_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        qty: float,
        close_reason: str,
    ) -> CloseResult:
        client = self.build_client()
        order = await client.create_market_order(symbol, "sell", qty)
        return CloseResult(
            filled_qty=float(order.get("filled") or qty),
            avg_price=float(order.get("average") or 0.0),
            order_id=str(order["id"]),
        )

    async def place_brackets(
        self,
        *,
        symbol: str,
        direction: Direction,
        qty: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> BracketResult:
        raise NotImplementedError("Spot brackets delegated to Executor until Phase 5 refactor")

    async def fetch_balance(self) -> BalanceView:
        client = self.build_client()
        bal = await client.fetch_balance()
        usdt = bal.get("USDT", {})
        btc = bal.get("BTC", {})
        return BalanceView(
            available=float(usdt.get("free") or 0.0),
            total=float(usdt.get("total") or 0.0),
            position_qty=float(btc.get("free") or 0.0),
        )

    async def fetch_positions(self) -> list[PositionView]:
        return []

    async def fetch_funding_rate(self, symbol: str) -> float:
        return 0.0

    def min_notional(self, symbol: str) -> float:
        client = self.build_client()
        try:
            return float(client.markets[symbol]["limits"]["cost"]["min"])
        except (KeyError, TypeError, AttributeError):
            return 5.0


class FuturesAdapter:
    product = "futures"

    def __init__(self, client: Any | None = None):
        self._client = client

    def build_client(self) -> Any:
        if self._client is None:
            from exchange import build_binance_client

            self._client = build_binance_client(default_type="future")
        return self._client

    async def setup_symbol(self, symbol: str, *, leverage: int, margin_mode: str) -> None:
        client = self.build_client()
        try:
            await client.set_margin_mode(margin_mode, symbol)
        except Exception:
            pass
        await client.set_leverage(leverage, symbol)

    async def open_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        notional_usdt: float,
        price: float,
    ) -> OpenResult:
        client = self.build_client()
        side = "buy" if direction == Direction.LONG else "sell"
        qty = float(client.amount_to_precision(symbol, notional_usdt / price))
        order = await client.create_order(symbol, "market", side, qty)
        return OpenResult(
            filled_qty=float(order.get("filled") or qty),
            avg_price=float(order.get("average") or price),
            order_id=str(order["id"]),
        )

    async def close_position(
        self,
        *,
        symbol: str,
        direction: Direction,
        qty: float,
        close_reason: str,
    ) -> CloseResult:
        client = self.build_client()
        side = "sell" if direction == Direction.LONG else "buy"
        qty_prec = float(client.amount_to_precision(symbol, qty))
        order = await client.create_order(
            symbol, "market", side, qty_prec, None, {"reduceOnly": True},
        )
        return CloseResult(
            filled_qty=float(order.get("filled") or qty_prec),
            avg_price=float(order.get("average") or 0.0),
            order_id=str(order["id"]),
        )

    async def place_brackets(
        self,
        *,
        symbol: str,
        direction: Direction,
        qty: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> BracketResult:
        client = self.build_client()
        close_side = "sell" if direction == Direction.LONG else "buy"
        qty_prec = float(client.amount_to_precision(symbol, qty))
        sl_id = tp_id = None
        if stop_loss is not None:
            sl_id = await place_conditional_algo_order(
                client,
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=qty_prec,
                trigger_price=stop_loss,
            )
        if take_profit is not None:
            tp_id = await place_conditional_algo_order(
                client,
                symbol=symbol,
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=qty_prec,
                trigger_price=take_profit,
            )
        return BracketResult(order_id_sl=sl_id, order_id_tp=tp_id)

    async def cancel_bracket_order(self, symbol: str, order_id: str) -> None:
        await cancel_conditional_algo_order(self.build_client(), symbol=symbol, algo_id=order_id)

    async def fetch_balance(self) -> BalanceView:
        client = self.build_client()
        bal = await client.fetch_balance()
        usdt = bal.get("USDT", {})
        return BalanceView(
            available=float(usdt.get("free") or 0.0),
            total=float(usdt.get("total") or 0.0),
            position_qty=0.0,
        )

    async def fetch_positions(self) -> list[PositionView]:
        client = self.build_client()
        raw = await client.fetch_positions([])
        out: list[PositionView] = []
        for p in raw:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0.0:
                continue
            side = (p.get("side") or "").lower()
            direction = Direction.LONG if side == "long" else Direction.SHORT
            liq = p.get("liquidationPrice")
            out.append(PositionView(
                symbol=p.get("symbol", ""),
                direction=direction,
                qty=contracts,
                entry_price=float(p.get("entryPrice") or 0.0),
                liquidation_price=float(liq) if liq else None,
                leverage=float(p.get("leverage") or 1.0),
            ))
        return out

    async def fetch_funding_rate(self, symbol: str) -> float:
        client = self.build_client()
        fr = await client.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate") or 0.0)

    def min_notional(self, symbol: str) -> float:
        client = self.build_client()
        try:
            return float(client.markets[symbol]["limits"]["cost"]["min"])
        except (KeyError, TypeError, AttributeError):
            return 100.0


def build_adapter(product: str) -> SpotAdapter | FuturesAdapter:
    if product == "futures":
        return FuturesAdapter()
    return SpotAdapter()
