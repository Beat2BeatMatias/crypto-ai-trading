from __future__ import annotations
import asyncio
import os
from datetime import datetime, timezone
import httpx
import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, desc
from shared.db.models import Decision, Position, Trade, PlaybookVersion, ConfigEntry
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
    now = datetime.now(tz=timezone.utc)
    last_decision_ts = now
    last_trade_open_ts = now
    last_trade_close_ts = now
    last_playbook_ts = now
    last_kill_switch: bool | None = None
    try:
        while True:
            await asyncio.sleep(2)
            async with factory() as s:
                # --- decisions ---
                new_decisions = (await s.execute(
                    select(Decision).where(Decision.ts > last_decision_ts)
                    .order_by(desc(Decision.ts)).limit(10)
                )).scalars().all()
                if new_decisions:
                    last_decision_ts = max(d.ts for d in new_decisions)
                    for d in reversed(new_decisions):
                        await manager.broadcast("decision", {
                            "id": str(d.id), "ts": d.ts.isoformat(),
                            "agent": d.agent, "action": d.output.get("action"),
                            "confidence": d.output.get("confidence"),
                            "reasoning": d.output.get("reasoning", ""),
                        })

                # --- positions ---
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

                # --- trade_opened ---
                new_open_trades = (await s.execute(
                    select(Trade).where(
                        Trade.ts_open > last_trade_open_ts,
                        Trade.status == "open",
                    ).order_by(Trade.ts_open)
                )).scalars().all()
                for t in new_open_trades:
                    await manager.broadcast("trade_opened", {
                        "id": str(t.id),
                        "ts_open": t.ts_open.isoformat(),
                        "side": t.side,
                        "quantity_btc": float(t.quantity_btc),
                        "entry_price": float(t.entry_price),
                        "stop_loss": float(t.stop_loss) if t.stop_loss else None,
                        "take_profit": float(t.take_profit) if t.take_profit else None,
                    })
                if new_open_trades:
                    last_trade_open_ts = max(t.ts_open for t in new_open_trades)

                # --- trade_closed ---
                new_closed_trades = (await s.execute(
                    select(Trade).where(
                        Trade.ts_close != None,  # noqa: E711
                        Trade.ts_close > last_trade_close_ts,
                        Trade.status == "closed",
                    ).order_by(Trade.ts_close)
                )).scalars().all()
                for t in new_closed_trades:
                    await manager.broadcast("trade_closed", {
                        "id": str(t.id),
                        "ts_open": t.ts_open.isoformat(),
                        "ts_close": t.ts_close.isoformat(),
                        "side": t.side,
                        "entry_price": float(t.entry_price),
                        "exit_price": float(t.exit_price) if t.exit_price else None,
                        "pnl_usdt": float(t.pnl_usdt) if t.pnl_usdt else None,
                        "pnl_pct": float(t.pnl_pct) if t.pnl_pct else None,
                        "close_reason": t.close_reason,
                    })
                if new_closed_trades:
                    last_trade_close_ts = max(t.ts_close for t in new_closed_trades)

                # --- playbook_updated ---
                new_playbooks = (await s.execute(
                    select(PlaybookVersion).where(
                        PlaybookVersion.ts_generated > last_playbook_ts,
                    ).order_by(PlaybookVersion.ts_generated)
                )).scalars().all()
                for pb in new_playbooks:
                    await manager.broadcast("playbook_updated", {
                        "version": pb.version,
                        "ts_generated": pb.ts_generated.isoformat(),
                        "model": pb.model,
                        "active": pb.active,
                        "trades_analyzed": pb.trades_analyzed,
                        "win_rate": float(pb.win_rate) if pb.win_rate else None,
                    })
                if new_playbooks:
                    last_playbook_ts = max(pb.ts_generated for pb in new_playbooks)

                # --- kill_switch_triggered ---
                ks_row = await s.get(ConfigEntry, "kill_switch")
                if ks_row is not None:
                    current_ks = ks_row.value.lower() in ("true", "1")
                    if last_kill_switch is not None and current_ks != last_kill_switch:
                        await manager.broadcast("kill_switch_triggered", {
                            "enabled": current_ks,
                            "ts": datetime.now(tz=timezone.utc).isoformat(),
                        })
                    last_kill_switch = current_ks

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.error("ws.error", error=str(e))
        manager.disconnect(ws)
