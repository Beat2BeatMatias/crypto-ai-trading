from __future__ import annotations
import asyncio
import os
from datetime import datetime, timezone
import httpx
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from shared.db.models import Decision, Position
from ws.manager import manager

logger = structlog.get_logger()
router = APIRouter()

_TESTNET = os.environ.get("BINANCE_TESTNET", "").lower() == "true"
_PRICE_URL = (
    "https://testnet.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"
    if _TESTNET
    else "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
)


async def ticker_broadcaster() -> None:
    """Background task: fetches live BTC price from Binance public REST every 5s
    and broadcasts to all connected WebSocket clients."""
    async with httpx.AsyncClient(timeout=4) as client:
        while True:
            try:
                resp = await client.get(_PRICE_URL)
                if resp.status_code == 200:
                    price = float(resp.json()["price"])
                    await manager.broadcast("ticker", {
                        "symbol": "BTC/USDT",
                        "price": price,
                        "ts": datetime.now(tz=timezone.utc).isoformat(),
                    })
                else:
                    logger.warning("ws.ticker_http_error", status=resp.status_code)
            except Exception as e:
                logger.warning("ws.ticker_fetch_failed", error=str(e))
            await asyncio.sleep(5)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    factory = ws.app.state.session_factory
    last_decision_ts = datetime.now(tz=timezone.utc)
    try:
        while True:
            await asyncio.sleep(2)
            async with factory() as s:
                new = (await s.execute(
                    select(Decision).where(Decision.ts > last_decision_ts)
                    .order_by(desc(Decision.ts)).limit(10)
                )).scalars().all()
                if new:
                    last_decision_ts = max(d.ts for d in new)
                    for d in reversed(new):
                        await manager.broadcast("decision", {
                            "id": str(d.id), "ts": d.ts.isoformat(),
                            "agent": d.agent, "action": d.output.get("action"),
                            "confidence": d.output.get("confidence"),
                            "reasoning": d.output.get("reasoning", ""),
                        })
                positions = (await s.execute(
                    select(Position).where(Position.status == "open")
                )).scalars().all()
                await manager.broadcast("positions", [
                    {
                        "id": str(p.id),
                        "trade_id": str(p.trade_id) if p.trade_id else None,
                        "symbol": p.symbol,
                        "quantity_btc": float(p.quantity_btc),
                        "entry_price": float(p.entry_price),
                        "current_price": float(p.current_price) if p.current_price else None,
                        "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl else None,
                        "unrealized_pct": float(p.unrealized_pct) if p.unrealized_pct else None,
                        "status": p.status,
                        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                    }
                    for p in positions
                ])
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error("ws.error", error=str(e))
        manager.disconnect(ws)
