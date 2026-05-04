from datetime import datetime
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Decision

router = APIRouter()

class DecisionOut(BaseModel):
    id: UUID
    ts: datetime
    agent: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    input: dict
    output: dict
    outcome: dict | None
    trade_id: UUID | None
    executed: bool
    rejected_reason: str | None

async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(session: Annotated[AsyncSession, Depends(_session)],
                          agent: str | None = Query(None),
                          executed: bool | None = Query(None),
                          limit: int = Query(100, le=500)):
    stmt = select(Decision).order_by(desc(Decision.ts)).limit(limit)
    if agent:
        stmt = stmt.where(Decision.agent == agent)
    if executed is not None:
        stmt = stmt.where(Decision.executed == executed)
    rows = (await session.execute(stmt)).scalars().all()
    return [DecisionOut.model_validate(r, from_attributes=True) for r in rows]
