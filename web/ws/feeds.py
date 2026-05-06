from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from shared.db.models import Decision, Ohlcv, Position
from ws.manager import manager

logger = structlog.get_logger()
router = APIRouter()


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
                latest_ohlcv = (await s.execute(
                    select(Ohlcv).where(Ohlcv.timeframe == "1m")
                    .order_by(desc(Ohlcv.time)).limit(1)
                )).scalar_one_or_none()
                if latest_ohlcv is None:
                    latest_ohlcv = (await s.execute(
                        select(Ohlcv).order_by(desc(Ohlcv.time)).limit(1)
                    )).scalar_one_or_none()
                await manager.broadcast("ticker", {
                    "symbol": "BTC/USDT",
                    "price": float(latest_ohlcv.close) if latest_ohlcv and latest_ohlcv.close else None,
                    "ts": latest_ohlcv.time.isoformat() if latest_ohlcv else None,
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
