"""Re-normalize completed post-mortem lessons from stored lesson_raw (no LLM)."""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone

import structlog
from pydantic import ValidationError
from sqlalchemy import select

from agents.confluence_registry import fetch_promoted_pattern_tags, upsert_candidate
from agents.lesson_normalizer import normalize
from agents.postmortem_schemas import LessonRaw, coerce_lesson_raw
from config import get_settings
from shared.db.base import create_engine_from_url, create_session_factory
from shared.db.models import Decision, DecisionOutcome

logger = structlog.get_logger()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decision-ids",
        help="Comma-separated decision UUIDs to reprocess (default: all completed).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without committing.",
    )
    return parser.parse_args()


async def _run(*, decision_ids: list[uuid.UUID] | None, dry_run: bool) -> int:
    settings = get_settings()
    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)
    now = datetime.now(tz=timezone.utc)
    updated = 0
    skipped = 0
    errors = 0

    async with session_factory() as session:
        stmt = (
            select(Decision, DecisionOutcome)
            .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
            .where(
                DecisionOutcome.postmortem_status == "completed",
                DecisionOutcome.lesson_raw.isnot(None),
            )
            .order_by(Decision.ts.desc())
        )
        if decision_ids:
            stmt = stmt.where(DecisionOutcome.decision_id.in_(decision_ids))

        rows = list((await session.execute(stmt)).all())
        if not rows:
            logger.info("reprocess.no_rows")
            return 0

        promoted_tags = await fetch_promoted_pattern_tags(session)

        for decision, outcome in rows:
            raw = outcome.lesson_raw or {}
            if not isinstance(raw, dict) or "classification" not in raw:
                logger.warning(
                    "reprocess.skip_no_lesson",
                    decision_id=str(decision.id),
                )
                skipped += 1
                continue

            try:
                lesson = LessonRaw.model_validate(coerce_lesson_raw(raw))
                normalized = normalize(
                    lesson,
                    decision_ts=decision.ts.strftime("%Y-%m-%dT%H:%MZ"),
                    decision_input=decision.input or {},
                    promoted_pattern_tags=promoted_tags,
                )
            except ValidationError as exc:
                logger.error(
                    "reprocess.validation_failed",
                    decision_id=str(decision.id),
                    error=str(exc),
                )
                errors += 1
                continue

            old = outcome.lesson_normalized or {}
            new = normalized.model_dump()
            old_line = old.get("block_k_line", "")
            new_line = new.get("block_k_line", "")
            extra = new.get("block_k_lines") or []

            logger.info(
                "reprocess.updated",
                decision_id=str(decision.id),
                classification=outcome.classification,
                route=new.get("route"),
                old_len=len(old_line),
                new_len=len(new_line),
                extra_lines=len(extra),
                dry_run=dry_run,
            )

            if not dry_run:
                outcome.lesson_normalized = new
                outcome.postmortem_at = now
                session.add(outcome)
                if normalized.route == "candidate":
                    await upsert_candidate(
                        session,
                        normalized=normalized,
                        decision_id=decision.id,
                        now=now,
                    )
            updated += 1

        if not dry_run:
            await session.commit()

    logger.info(
        "reprocess.done",
        updated=updated,
        skipped=skipped,
        errors=errors,
        dry_run=dry_run,
    )
    return 1 if errors else 0


def main() -> None:
    args = _parse_args()
    ids = None
    if args.decision_ids:
        ids = [uuid.UUID(x.strip()) for x in args.decision_ids.split(",") if x.strip()]
    raise SystemExit(asyncio.run(_run(decision_ids=ids, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
