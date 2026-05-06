from __future__ import annotations
from typing import Annotated, Any
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Decision

router = APIRouter()


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s


@router.get("/config/suggestions")
async def get_config_suggestions(
    session: Annotated[AsyncSession, Depends(_session)],
) -> dict[str, Any] | None:
    row = (await session.execute(
        select(Decision)
        .where(Decision.agent == "supervisor", Decision.executed == True)
        .order_by(desc(Decision.ts))
        .limit(1)
    )).scalar_one_or_none()

    if row is None or "config_suggestions" not in row.output:
        return None

    return {
        "generated_at": row.ts.isoformat(),
        **row.output["config_suggestions"],
    }
