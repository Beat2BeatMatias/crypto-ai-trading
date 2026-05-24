from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from shared.config_store import ConfigKey, ConfigStore
from shared.confluence_registry_ops import (
    ConfluenceOpsError,
    deactivate_registry_code,
    promote_candidate_by_id,
    reject_candidate_by_id,
    verify_spec_testable,
)
from shared.db.models import ConfluenceCandidate, ConfluenceRegistry, PlaybookVersion

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
    source_decision_ids: list[str]
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


class RejectCandidateIn(BaseModel):
    reason: str = Field(default="", max_length=500)


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


async def _active_playbook_content(session: AsyncSession) -> str:
    row = (await session.execute(
        select(PlaybookVersion).where(PlaybookVersion.active.is_(True))
    )).scalar_one_or_none()
    return row.content if row else ""


async def _max_active(session: AsyncSession) -> int:
    store = ConfigStore(session)
    try:
        return int(await store.get_typed(ConfigKey.CONFLUENCE_REGISTRY_MAX_ACTIVE))
    except KeyError:
        return 5


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


@router.post("/confluence/candidates/{candidate_id}/promote", response_model=ConfluenceRegistryOut)
async def promote_confluence_candidate(
    candidate_id: UUID,
    session: Annotated[AsyncSession, Depends(_session)],
):
    playbook = await _active_playbook_content(session)
    max_active = await _max_active(session)
    try:
        row = await promote_candidate_by_id(
            session,
            candidate_id,
            max_active=max_active,
            playbook_content=playbook,
        )
        await session.commit()
    except ConfluenceOpsError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message}) from e
    return ConfluenceRegistryOut.model_validate(row, from_attributes=True)


@router.post("/confluence/candidates/{candidate_id}/reject", response_model=ConfluenceCandidateOut)
async def reject_confluence_candidate(
    candidate_id: UUID,
    body: RejectCandidateIn,
    session: Annotated[AsyncSession, Depends(_session)],
):
    try:
        row = await reject_candidate_by_id(session, candidate_id, reason=body.reason)
        await session.commit()
    except ConfluenceOpsError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message}) from e
    return ConfluenceCandidateOut.model_validate(row, from_attributes=True)


@router.post("/confluence/registry/{code}/deactivate", response_model=ConfluenceRegistryOut)
async def deactivate_confluence_registry_entry(
    code: str,
    session: Annotated[AsyncSession, Depends(_session)],
):
    if len(code) != 1:
        raise HTTPException(status_code=400, detail="code debe ser una sola letra")
    try:
        row = await deactivate_registry_code(session, code.upper())
        await session.commit()
    except ConfluenceOpsError as e:
        raise HTTPException(status_code=400, detail={"code": e.code, "message": e.message}) from e
    return ConfluenceRegistryOut.model_validate(row, from_attributes=True)
