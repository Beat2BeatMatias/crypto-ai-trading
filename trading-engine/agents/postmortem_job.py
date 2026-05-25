"""Background job: LLM post-mortem for negative decision outcomes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager

import structlog
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.llm_client import LLMClient, LLMProvider
from agents.confluence_registry import fetch_promoted_pattern_tags, upsert_candidate
from agents.lesson_normalizer import normalize
from agents.postmortem_agent import PostMortemAgent, provider_from_config
from agents.postmortem_schemas import (
    POSTMORTEM_ELIGIBLE_CLASSIFICATIONS,
    compute_severity_score,
)
from shared.db.models import Decision, DecisionOutcome, Trade

logger = structlog.get_logger()

_MAX_POSTMORTEM_ATTEMPTS = 3
_DEFAULT_WINDOW_HOURS = 25


def _attempt_count(outcome: DecisionOutcome) -> int:
    raw = outcome.lesson_raw
    if isinstance(raw, dict):
        meta = raw.get("_meta")
        if isinstance(meta, dict):
            try:
                return int(meta.get("attempts", 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "rate_limit" in msg
        or "resource_exhausted" in msg
        or "timeout" in msg
        or "timed out" in msg
    )


async def _fetch_candidates(
    session: AsyncSession,
    *,
    now: datetime,
    window_hours: int = _DEFAULT_WINDOW_HOURS,
) -> list[tuple[Decision, DecisionOutcome]]:
    since = now - timedelta(hours=window_hours)
    stmt = (
        select(Decision, DecisionOutcome)
        .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(
            Decision.agent == "decisor",
            Decision.ts >= since,
            DecisionOutcome.classification.in_(POSTMORTEM_ELIGIBLE_CLASSIFICATIONS),
            DecisionOutcome.matured.is_(True),
            or_(
                DecisionOutcome.postmortem_status.is_(None),
                DecisionOutcome.postmortem_status == "failed",
            ),
        )
        .order_by(DecisionOutcome.computed_at.asc())
    )
    rows = list((await session.execute(stmt)).all())
    return [
        (d, o) for d, o in rows
        if o.postmortem_status != "failed" or _attempt_count(o) < _MAX_POSTMORTEM_ATTEMPTS
    ]


async def _load_trade(session: AsyncSession, trade_id) -> Trade | None:
    if trade_id is None:
        return None
    return (await session.execute(
        select(Trade).where(Trade.id == trade_id)
    )).scalar_one_or_none()


def _rank_candidates(
    rows: list[tuple[Decision, DecisionOutcome, Trade | None]],
) -> list[tuple[Decision, DecisionOutcome, Trade | None, float]]:
    scored = []
    for decision, outcome, trade in rows:
        score = compute_severity_score(
            classification=outcome.classification,
            outcome=outcome,
            trade=trade,
        )
        scored.append((decision, outcome, trade, score))
    scored.sort(key=lambda x: x[3], reverse=True)
    return scored


def _record_failure(outcome: DecisionOutcome, *, error: str, now: datetime) -> None:
    attempts = _attempt_count(outcome) + 1
    meta: dict[str, Any] = {"attempts": attempts, "last_error": error[:500]}
    if attempts >= _MAX_POSTMORTEM_ATTEMPTS:
        outcome.postmortem_status = "failed"
    else:
        outcome.postmortem_status = None
    outcome.lesson_raw = {"_meta": meta}
    outcome.lesson_normalized = None
    outcome.postmortem_at = now


async def outcome_postmortem_tick(
    *,
    session_factory: Callable[[], ContextManager[AsyncSession]],
    llm: LLMClient,
    max_per_tick: int = 5,
    provider_name: str = "gemini-2.5-flash",
    fallback_providers: list[LLMProvider] | None = None,
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    now_fn: Callable[[], datetime] | None = None,
) -> None:
    now = (now_fn or _utcnow)()
    agent = PostMortemAgent(
        llm=llm,
        provider=provider_from_config(provider_name),
        fallbacks=fallback_providers or [],
    )

    async with session_factory() as session:
        raw_rows = await _fetch_candidates(session, now=now, window_hours=window_hours)
        if not raw_rows:
            logger.info("postmortem.job.no_candidates")
            return

        enriched: list[tuple[Decision, DecisionOutcome, Trade | None]] = []
        for decision, outcome in raw_rows:
            trade = await _load_trade(session, decision.trade_id)
            enriched.append((decision, outcome, trade))

        ranked = _rank_candidates(enriched)[:max_per_tick]
        processed = 0
        promoted_tags = await fetch_promoted_pattern_tags(session)

        for decision, outcome, trade, severity in ranked:
            try:
                lesson = await agent.analyze(
                    decision=decision,
                    outcome=outcome,
                    trade=trade,
                    severity_score=severity,
                )
                normalized = normalize(
                    lesson,
                    decision_ts=decision.ts.strftime("%Y-%m-%dT%H:%MZ"),
                    decision_input=decision.input or {},
                    promoted_pattern_tags=promoted_tags,
                )
                outcome.postmortem_status = "completed"
                outcome.lesson_raw = lesson.model_dump()
                outcome.lesson_normalized = normalized.model_dump()
                outcome.postmortem_at = now
                if normalized.route == "candidate":
                    await upsert_candidate(
                        session,
                        normalized=normalized,
                        decision_id=decision.id,
                        now=now,
                    )
            except ValidationError as e:
                logger.warning(
                    "postmortem.job.validation_failed",
                    decision_id=str(decision.id),
                    error=str(e),
                )
                _record_failure(outcome, error=str(e), now=now)
            except Exception as e:
                log_fn = logger.warning if _is_transient_error(e) else logger.error
                log_fn(
                    "postmortem.job.failed",
                    decision_id=str(decision.id),
                    error=str(e),
                    transient=_is_transient_error(e),
                )
                _record_failure(outcome, error=str(e), now=now)

            session.add(outcome)
            processed += 1

        await session.commit()
        logger.info("postmortem.job.completed", processed=processed, candidates=len(raw_rows))


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
