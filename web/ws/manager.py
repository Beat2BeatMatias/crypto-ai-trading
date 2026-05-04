from __future__ import annotations
import json
from typing import Any
import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


class WSManager:
    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("ws.connected", clients=len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("ws.disconnected", clients=len(self._clients))

    async def broadcast(self, event: str, data: Any) -> None:
        msg = json.dumps({"event": event, "data": data}, default=str)
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


manager = WSManager()
