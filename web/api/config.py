from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import ConfigEntry, ConfigHistory
from shared.config_store import ConfigStore, ConfigKey

router = APIRouter()


class ConfigEntryOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None
    updated_at: datetime | None
    last_changed_by: str | None


class ConfigUpdate(BaseModel):
    value: str


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


_INTERNAL_KEYS = {"supervisor_run_now"}


@router.get("/config", response_model=list[ConfigEntryOut])
async def list_config(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (await session.execute(select(ConfigEntry).order_by(ConfigEntry.key))).scalars().all()

    history_rows = (await session.execute(
        select(ConfigHistory).order_by(ConfigHistory.ts.desc())
    )).scalars().all()
    last_changed_by: dict[str, str] = {}
    for h in history_rows:
        if h.key not in last_changed_by:
            last_changed_by[h.key] = h.changed_by

    result = []
    for r in rows:
        if r.key in _INTERNAL_KEYS:
            continue
        result.append(ConfigEntryOut(
            key=r.key,
            value=r.value,
            value_type=r.value_type,
            description=r.description,
            updated_at=r.updated_at,
            last_changed_by=last_changed_by.get(r.key),
        ))
    return result


@router.put("/config/{key}")
async def update_config(key: str, body: ConfigUpdate,
                        session: Annotated[AsyncSession, Depends(_session)]):
    try:
        config_key = ConfigKey(key)
    except ValueError:
        raise HTTPException(400, f"unknown config key: {key}")
    store = ConfigStore(session)
    try:
        await store.set(config_key, body.value, changed_by="user")
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "key": key, "value": body.value}
