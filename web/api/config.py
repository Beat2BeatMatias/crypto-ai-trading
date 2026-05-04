from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import ConfigEntry
from shared.config_store import ConfigStore, ConfigKey

router = APIRouter()


class ConfigEntryOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str | None


class ConfigUpdate(BaseModel):
    value: str


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


@router.get("/config", response_model=list[ConfigEntryOut])
async def list_config(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (await session.execute(select(ConfigEntry).order_by(ConfigEntry.key))).scalars().all()
    return [ConfigEntryOut.model_validate(r, from_attributes=True) for r in rows]


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
