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


class SupervisorRunOut(BaseModel):
    """Proyección ligera de una ejecución del Supervisor para el frontend."""
    ts: datetime
    ratified: bool
    ratify_reason: str | None
    force_regen_reason: str | None
    mode: str
    new_version: int | None
    playbook_age_days: int | None
    playbook_win_rate_baseline: float | None


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


@router.get("/supervisor/runs", response_model=list[SupervisorRunOut])
async def list_supervisor_runs(
    session: Annotated[AsyncSession, Depends(_session)],
    limit: int = Query(30, le=200),
):
    """Historial de ejecuciones del Supervisor (ratificaciones + regeneraciones).

    Proyección ligera de `decisions` con `agent="supervisor"`, extrayendo
    los campos de ratificación del JSONB `output` para que el frontend no
    tenga que parsear el payload completo.
    """
    rows = (await session.execute(
        select(Decision)
        .where(Decision.agent == "supervisor")
        .order_by(desc(Decision.ts))
        .limit(limit)
    )).scalars().all()

    result: list[SupervisorRunOut] = []
    for r in rows:
        out = r.output or {}
        result.append(SupervisorRunOut(
            ts=r.ts,
            ratified=bool(out.get("ratified", False)),
            ratify_reason=out.get("ratify_reason"),
            force_regen_reason=out.get("force_regen_reason"),
            mode=str(out.get("mode") or "normal"),
            new_version=out.get("new_version"),
            playbook_age_days=out.get("playbook_age_days"),
            playbook_win_rate_baseline=out.get("playbook_win_rate_baseline"),
        ))
    return result
