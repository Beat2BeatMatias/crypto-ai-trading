"""Background job: LLM post-mortem for negative decision outcomes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, ContextManager

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.llm_client import LLMClient
from agents.confluence_registry import fetch_promoted_pattern_tags, upsert_candidate
from agents.lesson_normalizer import normalize
from agents.postmortem_agent import PostMortemAgent, provider_from_config
from agents.postmortem_schemas import (
    POSTMORTEM_ELIGIBLE_CLASSIFICATIONS,
    compute_severity_score,
)
from shared.db.models import Decision, DecisionOutcome, Trade

logger = structlog.get_logger()


async def _fetch_candidates(session: AsyncSession) -> list[tuple[Decision, DecisionOutcome]]:
    stmt = (
        select(Decision, DecisionOutcome)
        .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(
            Decision.agent == "decisor",
            DecisionOutcome.classification.in_(POSTMORTEM_ELIGIBLE_CLASSIFICATIONS),
            DecisionOutcome.matured.is_(True),
            DecisionOutcome.postmortem_status.is_(None),
        )
        .order_by(DecisionOutcome.computed_at.asc())
    )
    return list((await session.execute(stmt)).all())


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


async def outcome_postmortem_tick(
    *,
    session_factory: Callable[[], ContextManager[AsyncSession]],
    llm: LLMClient,
    max_per_tick: int = 5,
    provider_name: str = "gemini-2.5-flash",
    now_fn: Callable[[], datetime] | None = None,
) -> None:
    now = (now_fn or _utcnow)()
    agent = PostMortemAgent(llm=llm, provider=provider_from_config(provider_name))

    async with session_factory() as session:
        raw_rows = await _fetch_candidates(session)
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
            except Exception as e:
                logger.error(
                    "postmortem.job.failed",
                    decision_id=str(decision.id),
                    error=str(e),
                )
                outcome.postmortem_status = "failed"
                outcome.postmortem_at = now

            session.add(outcome)
            processed += 1

        await session.commit()
        logger.info("postmortem.job.completed", processed=processed, candidates=len(raw_rows))


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
