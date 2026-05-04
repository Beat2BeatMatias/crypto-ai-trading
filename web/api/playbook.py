from datetime import datetime
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import PlaybookVersion

router = APIRouter()

class PlaybookOut(BaseModel):
    id: UUID
    version: int
    ts_generated: datetime
    content: str
    model: str | None
    trades_analyzed: int | None
    win_rate: float | None
    active: bool

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/playbook/active", response_model=PlaybookOut | None)
async def active(session: Annotated[AsyncSession, Depends(_session)]):
    row = (await session.execute(select(PlaybookVersion).where(PlaybookVersion.active.is_(True)))).scalar_one_or_none()
    if not row:
        return None
    return PlaybookOut(id=row.id, version=row.version, ts_generated=row.ts_generated,
                       content=row.content, model=row.model, trades_analyzed=row.trades_analyzed,
                       win_rate=float(row.win_rate) if row.win_rate else None, active=row.active)

@router.get("/playbook/history", response_model=list[PlaybookOut])
async def history(session: Annotated[AsyncSession, Depends(_session)]):
    rows = (await session.execute(select(PlaybookVersion).order_by(desc(PlaybookVersion.version)))).scalars().all()
    return [PlaybookOut(id=r.id, version=r.version, ts_generated=r.ts_generated, content=r.content,
                        model=r.model, trades_analyzed=r.trades_analyzed,
                        win_rate=float(r.win_rate) if r.win_rate else None, active=r.active) for r in rows]

@router.post("/playbook/{version}/activate")
async def activate(version: int, session: Annotated[AsyncSession, Depends(_session)]):
    target = (await session.execute(select(PlaybookVersion).where(PlaybookVersion.version == version))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"version {version} not found")
    await session.execute(update(PlaybookVersion).values(active=False))
    target.active = True
    await session.commit()
    return {"ok": True, "version": version}
