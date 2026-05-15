from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from shared.config_store import ConfigStore, ConfigKey

router = APIRouter()


class KillSwitchBody(BaseModel):
    enabled: bool


class ModeBody(BaseModel):
    mode: str = Field(..., pattern="^(PAPER_TRADING|LIVE)$")
    confirmation: str


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


@router.post("/kill-switch")
async def toggle_kill_switch(body: KillSwitchBody,
                              session: Annotated[AsyncSession, Depends(_session)]):
    store = ConfigStore(session)
    try:
        await store.set(ConfigKey.KILL_SWITCH, "true" if body.enabled else "false", changed_by="user")
    except KeyError:
        raise HTTPException(404, "Config not seeded — apply migrations and seed defaults first")
    return {"ok": True, "kill_switch": body.enabled}


@router.post("/mode")
async def set_mode(body: ModeBody, session: Annotated[AsyncSession, Depends(_session)]):
    if body.mode == "LIVE" and body.confirmation != "CONFIRMO TRADING REAL":
        raise HTTPException(400, "LIVE mode requires confirmation phrase: CONFIRMO TRADING REAL")
    store = ConfigStore(session)
    try:
        await store.set(ConfigKey.MODE, body.mode, changed_by="user")
    except KeyError:
        raise HTTPException(404, "Config not seeded — apply migrations and seed defaults first")
    return {"ok": True, "mode": body.mode}


@router.post("/supervisor/run")
async def run_supervisor_now(session: Annotated[AsyncSession, Depends(_session)]):
    store = ConfigStore(session)
    try:
        await store.set(ConfigKey.SUPERVISOR_RUN_NOW, "true", changed_by="user")
    except KeyError:
        raise HTTPException(404, "Config not seeded — apply migrations and seed defaults first")
    return {"ok": True, "queued": True}


@router.post("/drawdown/reset")
async def reset_drawdown_peak(session: Annotated[AsyncSession, Depends(_session)]):
    """Establece el ancla de drawdown en el momento actual.

    A partir del próximo tick, el pico máximo de referencia se calcula solo
    considerando balance_snapshots posteriores a esta fecha, ignorando el
    historial anterior. No elimina datos.
    """
    store = ConfigStore(session)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await store.set(ConfigKey.DRAWDOWN_RESET_TS, now_iso, changed_by="user")
    except KeyError:
        raise HTTPException(404, "Config not seeded — apply migrations and seed defaults first")
    return {"ok": True, "reset_at": now_iso}
