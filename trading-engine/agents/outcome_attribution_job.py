"""Background job that attributes outcomes to recent decisor decisions.

Runs hourly (configurable). Idempotent: upserts on `decision_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Callable, ContextManager

import structlog
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Decision, DecisionOutcome, Ohlcv, Trade
from agents.outcome_attribution import attribute

logger = structlog.get_logger()

_WINDOW_HOURS = 25
_BUFFER_MIN = 15


async def _fetch_candidates(session: AsyncSession, *, now: datetime) -> list[Decision]:
    """Decisor decisions in (now-25h, now-15min) with no outcome or with PENDING."""
    since = now - timedelta(hours=_WINDOW_HOURS)
    upto = now - timedelta(minutes=_BUFFER_MIN)
    stmt = (
        select(Decision)
        .outerjoin(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(
            Decision.agent == "decisor",
            Decision.ts >= since,
            Decision.ts <= upto,
            or_(
                DecisionOutcome.decision_id.is_(None),
                DecisionOutcome.classification == "PENDING",
            ),
        )
        .order_by(Decision.ts.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _fetch_ohlcv_1m(
    session: AsyncSession, *, ts_from: datetime, ts_to: datetime,
) -> list[Ohlcv]:
    """Velas 1m en bloque para cubrir todas las ventanas del tick."""
    stmt = (
        select(Ohlcv)
        .where(Ohlcv.timeframe == "1m", Ohlcv.time >= ts_from, Ohlcv.time <= ts_to)
        .order_by(Ohlcv.time.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _upsert_outcome(session: AsyncSession, attr) -> None:
    """Dialect-agnostic UPSERT on (decision_id) PK.

    Postgres uses ON CONFLICT; SQLite (tests) uses delete + insert (simpler, transactional).
    """
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "postgresql"
    payload = dict(
        decision_id=attr.decision_id,
        horizon_min=attr.horizon_min,
        matured=attr.matured,
        forward_return_pct=attr.forward_return_pct,
        mfe_pct=attr.mfe_pct,
        mae_pct=attr.mae_pct,
        time_to_mfe_min=attr.time_to_mfe_min,
        time_to_mae_min=attr.time_to_mae_min,
        sl_dist_pct=attr.sl_dist_pct,
        tp_target_pct=attr.tp_target_pct,
        classification=attr.classification,
        computed_at=attr.computed_at,
    )
    if dialect == "postgresql":
        stmt = pg_insert(DecisionOutcome).values(**payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["decision_id"],
            set_={k: v for k, v in payload.items() if k != "decision_id"},
        )
        await session.execute(stmt)
    else:
        from sqlalchemy import delete
        await session.execute(
            delete(DecisionOutcome).where(DecisionOutcome.decision_id == attr.decision_id)
        )
        session.add(DecisionOutcome(**payload))
