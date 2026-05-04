from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from shared.db.models import Decision, Position
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
                positions = (await s.execute(
                    select(Position).where(Position.status == "open")
                )).scalars().all()
                await manager.broadcast("positions", [
                    {"id": str(p.id), "qty": float(p.quantity_btc),
                     "entry": float(p.entry_price),
                     "pnl": float(p.unrealized_pnl) if p.unrealized_pnl else None}
                    for p in positions
                ])
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error("ws.error", error=str(e))
        manager.disconnect(ws)
