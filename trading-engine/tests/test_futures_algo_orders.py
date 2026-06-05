import pytest

from execution.futures_algo_orders import algo_order_id, cancel_conditional_algo_order, place_conditional_algo_order


class _FakeClient:
    markets = {"BTC/USDT:USDT": {"id": "BTCUSDT"}}

    def __init__(self):
        self.post_requests: list[dict] = []
        self.delete_requests: list[dict] = []

    async def load_markets(self):
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def amount_to_precision(self, symbol, amount):
        return amount

    def price_to_precision(self, symbol, price):
        return price

    async def request(self, path, api, method, params):
        if method == "POST":
            self.post_requests.append(params)
            return {"algoId": 42}
        self.delete_requests.append(params)
        return {}

@pytest.mark.asyncio
async def test_place_conditional_algo_order_via_request_fallback():
    client = _FakeClient()
    oid = await place_conditional_algo_order(
        client,
        symbol="BTC/USDT:USDT",
        side="buy",
        order_type="STOP_MARKET",
        quantity=0.01,
        trigger_price=99000.0,
    )
    assert oid == "42"
    assert client.post_requests[0]["algoType"] == "CONDITIONAL"
    assert client.post_requests[0]["type"] == "STOP_MARKET"
    assert client.post_requests[0]["triggerPrice"] == 99000.0


@pytest.mark.asyncio
async def test_cancel_conditional_algo_order_via_request_fallback():
    client = _FakeClient()
    await cancel_conditional_algo_order(client, symbol="BTC/USDT:USDT", algo_id="42")
    assert client.delete_requests[0]["algoId"] == "42"


def test_algo_order_id_from_nested_info():
    assert algo_order_id({"info": {"algoId": 99}}) == "99"
    assert algo_order_id({"algoId": 100}) == "100"
