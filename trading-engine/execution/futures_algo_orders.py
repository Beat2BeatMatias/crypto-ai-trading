"""Binance USDT-M conditional orders via Algo Order API (POST /fapi/v1/algoOrder)."""
from __future__ import annotations

from typing import Any


def algo_order_id(response: dict[str, Any]) -> str:
    info = response.get("info") if isinstance(response.get("info"), dict) else {}
    raw = (
        response.get("algoId")
        or info.get("algoId")
        or response.get("id")
        or response.get("clientAlgoId")
    )
    return str(raw) if raw is not None else ""


async def place_conditional_algo_order(
    client: Any,
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    trigger_price: float,
    working_type: str = "MARK_PRICE",
) -> str:
    """Place STOP_MARKET / TAKE_PROFIT_MARKET reduce-only via Algo API. Returns algoId."""
    await client.load_markets()
    market = client.market(symbol)
    request = {
        "algoType": "CONDITIONAL",
        "symbol": market["id"],
        "side": side.upper(),
        "type": order_type,
        "quantity": client.amount_to_precision(symbol, quantity),
        "triggerPrice": client.price_to_precision(symbol, trigger_price),
        "reduceOnly": "true",
        "workingType": working_type,
    }
    if hasattr(client, "fapiPrivatePostAlgoOrder"):
        response = await client.fapiPrivatePostAlgoOrder(request)
    else:
        response = await client.request("algoOrder", "fapiPrivate", "POST", request)
    order_id = algo_order_id(response)
    if not order_id:
        raise RuntimeError(f"Algo order sin algoId en respuesta: {response!r}")
    return order_id


async def cancel_conditional_algo_order(client: Any, *, symbol: str, algo_id: str) -> None:
    await client.load_markets()
    market = client.market(symbol)
    request = {"symbol": market["id"], "algoId": algo_id}
    if hasattr(client, "fapiPrivateDeleteAlgoOrder"):
        await client.fapiPrivateDeleteAlgoOrder(request)
    else:
        await client.request("algoOrder", "fapiPrivate", "DELETE", request)
