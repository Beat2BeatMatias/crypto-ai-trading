"""Background job that attributes outcomes to recent decisor decisions.

Runs hourly (configurable). Idempotent: upserts on `decision_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Callable, ContextManager

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


async def outcome_attribution_tick(
    *,
    session_factory: Callable[[], "ContextManager[AsyncSession]"],
    horizon_min: int = 240,
    now_fn: Callable[[], datetime] | None = None,
) -> None:
    """Tick called by the scheduler. Idempotent."""
    now = (now_fn or _utcnow)()
    async with session_factory() as session:
        candidates = await _fetch_candidates(session, now=now)
        if not candidates:
            logger.info("outcome_attribution.job.no_candidates")
            return
        ohlcv = await _fetch_ohlcv_1m(
            session,
            ts_from=min(c.ts for c in candidates),
            ts_to=now,
        )
        ohlcv_by_minute = _index_ohlcv_by_minute(ohlcv)
        processed = 0
        for d in candidates:
            window = _slice_window(
                ohlcv_by_minute, ts_from=d.ts, ts_to=d.ts + timedelta(minutes=horizon_min),
            )
            trade = (await _load_trade(session, d.trade_id)) if d.trade_id else None
            attr = attribute(
                decision=d, ohlcv_1m=window, associated_trade=trade,
                horizon_min=horizon_min, now=now,
            )
            await _upsert_outcome(session, attr)
            processed += 1
        await session.commit()
        logger.info("outcome_attribution.job.completed", processed=processed)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _index_ohlcv_by_minute(rows: Iterable[Ohlcv]) -> dict[datetime, Ohlcv]:
    return {_truncate_to_minute(r.time): r for r in rows}


def _truncate_to_minute(t: datetime) -> datetime:
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t.replace(second=0, microsecond=0)


def _slice_window(
    index_by_min: dict[datetime, Ohlcv], *, ts_from: datetime, ts_to: datetime,
) -> list[Any]:
    start = _truncate_to_minute(ts_from)
    end = _truncate_to_minute(ts_to)
    out: list[Any] = []
    cursor = start + timedelta(minutes=1)
    while cursor <= end:
        row = index_by_min.get(cursor)
        if row is not None:
            out.append(_with_aware_time(row))
        cursor += timedelta(minutes=1)
    return out


def _with_aware_time(o: Ohlcv) -> Any:
    """Return a thin proxy that guarantees `time` is UTC-aware.

    In production `Ohlcv.time` is TIMESTAMPTZ → always aware. In tests using SQLite
    the value may come back naive, which would break datetime arithmetic against the
    aware `Decision.ts`. The wrapper keeps `attribute()` simple and tz-agnostic.
    """
    t = o.time
    if t.tzinfo is not None:
        return o
    return SimpleNamespace(
        time=t.replace(tzinfo=timezone.utc),
        open=o.open, high=o.high, low=o.low, close=o.close,
        volume=getattr(o, "volume", None),
    )


async def _load_trade(session: AsyncSession, trade_id) -> "Trade | None":
    return (await session.execute(
        select(Trade).where(Trade.id == trade_id)
    )).scalar_one_or_none()
