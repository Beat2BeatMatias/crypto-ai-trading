import pytest

from execution.exchange_adapter import (
    BalanceView,
    ExchangeAdapter,
    FuturesAdapter,
    OpenResult,
    SpotAdapter,
    build_adapter,
)
from shared.schemas import Direction


def test_dataclasses_exist():
    o = OpenResult(filled_qty=0.01, avg_price=100.0, order_id="1")
    assert o.filled_qty == 0.01
    b = BalanceView(available=100.0, total=100.0, position_qty=0.0)
    assert b.available == 100.0


def test_adapter_is_protocol():
    assert hasattr(ExchangeAdapter, "open_position")


class _FakeSpot:
    options = {"defaultType": "spot"}
    markets = {"BTC/USDT": {"limits": {"cost": {"min": 5.0}}}}

    async def create_market_order(self, symbol, side, amount, params=None):
        return {"id": "o1", "filled": 0.001, "average": 100000.0}

    async def fetch_balance(self):
        return {"USDT": {"free": 500.0, "total": 500.0}, "BTC": {"free": 0.0}}


@pytest.mark.asyncio
async def test_spot_adapter_open_long_uses_quote_order_qty():
    a = SpotAdapter(client=_FakeSpot())
    res = await a.open_position(
        symbol="BTC/USDT",
        direction=Direction.LONG,
        notional_usdt=50.0,
        price=100000.0,
    )
    assert res.order_id == "o1"
    assert res.avg_price == 100000.0


@pytest.mark.asyncio
async def test_spot_min_notional_from_markets():
    a = SpotAdapter(client=_FakeSpot())
    assert a.min_notional("BTC/USDT") == 5.0


class _FakeFut:
    options = {"defaultType": "future"}
    markets = {"BTC/USDT:USDT": {"limits": {"cost": {"min": 100.0}}}}
    calls: list[tuple]

    def __init__(self):
        self.calls = []
        self.last: dict = {}
        self.orders: list[dict] = []

    def amount_to_precision(self, symbol, amount):
        return round(amount, 3)

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        entry = dict(symbol=symbol, type=type_, side=side, amount=amount, params=params or {})
        self.last = entry
        self.orders.append(entry)
        self.calls.append(("create_order", type_, side))
        return {"id": f"id{len(self.calls)}", "filled": amount, "average": 100000.0}

    async def set_leverage(self, lev, symbol):
        self.calls.append(("lev", lev, symbol))

    async def set_margin_mode(self, mode, symbol):
        self.calls.append(("margin", mode, symbol))

    async def fetch_balance(self):
        return {"USDT": {"free": 300.0, "total": 320.0}}

    async def fetch_positions(self, symbols=None):
        return [{
            "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.002,
            "entryPrice": 100000.0, "liquidationPrice": 150000.0, "leverage": 1,
        }]

    async def fetch_funding_rate(self, symbol):
        return {"fundingRate": 0.0001}


def test_futures_client_uses_future_default_type(monkeypatch):
    import exchange as ex_mod

    captured: dict = {}

    def _fake_build(*, default_type="spot"):
        captured["default_type"] = default_type
        return _FakeFut()

    monkeypatch.setattr(ex_mod, "build_binance_client", _fake_build)
    a = FuturesAdapter()
    client = a.build_client()
    assert captured["default_type"] == "future"
    assert client.options.get("defaultType") == "future"


@pytest.mark.asyncio
async def test_futures_open_short_is_market_sell_rounded():
    fake = _FakeFut()
    a = FuturesAdapter(client=fake)
    res = await a.open_position(
        symbol="BTC/USDT:USDT", direction=Direction.SHORT,
        notional_usdt=200.0, price=100000.0,
    )
    assert fake.last["side"] == "sell"
    assert fake.last["type"] == "market"
    assert res.filled_qty == 0.002


@pytest.mark.asyncio
async def test_futures_close_short_is_reduceonly_buy():
    fake = _FakeFut()
    a = FuturesAdapter(client=fake)
    await a.close_position(
        symbol="BTC/USDT:USDT", direction=Direction.SHORT,
        qty=0.002, close_reason="decisor_sell",
    )
    assert fake.last["side"] == "buy"
    assert fake.last["params"].get("reduceOnly") is True


@pytest.mark.asyncio
async def test_futures_brackets_for_short_are_buy_reduceonly():
    fake = _FakeFut()
    a = FuturesAdapter(client=fake)
    res = await a.place_brackets(
        symbol="BTC/USDT:USDT", direction=Direction.SHORT,
        qty=0.002, stop_loss=102000.0, take_profit=96000.0,
    )
    types = {o["type"] for o in fake.orders}
    assert types == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert all(o["side"] == "buy" for o in fake.orders)
    assert all(o["params"].get("reduceOnly") is True for o in fake.orders)
    assert res.order_id_sl and res.order_id_tp


@pytest.mark.asyncio
async def test_futures_setup_and_views():
    fake = _FakeFut()
    a = FuturesAdapter(client=fake)
    await a.setup_symbol("BTC/USDT:USDT", leverage=1, margin_mode="isolated")
    assert ("lev", 1, "BTC/USDT:USDT") in fake.calls
    assert ("margin", "isolated", "BTC/USDT:USDT") in fake.calls
    bal = await a.fetch_balance()
    assert bal.available == 300.0
    pos = await a.fetch_positions()
    assert pos[0].direction == Direction.SHORT
    assert pos[0].liquidation_price == 150000.0
    assert await a.fetch_funding_rate("BTC/USDT:USDT") == 0.0001


def test_build_adapter_selects_product():
    assert build_adapter("spot").product == "spot"
    assert build_adapter("futures").product == "futures"
