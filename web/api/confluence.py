from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from shared.db.models import ConfluenceCandidate, ConfluenceRegistry

router = APIRouter()


class ConfluenceCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pattern_tag: str
    proposed_code: str | None
    title: str
    definition_md: str
    verify_spec: dict
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    source_decision_ids: list[UUID]
    status: str
    promoted_at: datetime | None
    reject_reason: str | None


class ConfluenceRegistryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    slug: str
    title: str
    definition_md: str
    verify_spec: dict
    active: bool
    promoted_from: UUID | None
    created_at: datetime
    deactivated_at: datetime | None


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


@router.get("/confluence/candidates", response_model=list[ConfluenceCandidateOut])
async def list_confluence_candidates(
    session: Annotated[AsyncSession, Depends(_session)],
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
):
    stmt = select(ConfluenceCandidate).order_by(
        desc(ConfluenceCandidate.occurrence_count),
        desc(ConfluenceCandidate.last_seen_at),
    ).limit(limit)
    if status:
        stmt = stmt.where(ConfluenceCandidate.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [ConfluenceCandidateOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/confluence/registry", response_model=list[ConfluenceRegistryOut])
async def list_confluence_registry(
    session: Annotated[AsyncSession, Depends(_session)],
    active_only: bool = Query(True),
):
    stmt = select(ConfluenceRegistry).order_by(ConfluenceRegistry.code.asc())
    if active_only:
        stmt = stmt.where(ConfluenceRegistry.active.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return [ConfluenceRegistryOut.model_validate(r, from_attributes=True) for r in rows]
