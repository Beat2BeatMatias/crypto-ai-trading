"""Confluence registry — candidates queue, promotion, verify_spec evaluation."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.postmortem_schemas import LessonNormalized
from shared.confluence_direction import (
    direction_label_es,
    parse_registry_direction,
    registry_direction_by_code as map_registry_directions,
)
from shared.confluence_registry_ops import (
    STATIC_CONFLUENCE_CODES,
    playbook_conflict,
    promote_candidate_row,
    verify_spec_testable,
)
from shared.db.models import ConfluenceCandidate, ConfluenceRegistry

logger = structlog.get_logger()

__all__ = [
    "STATIC_CONFLUENCE_CODES",
    "fetch_active_registry",
    "active_registry_codes",
    "render_registry_block",
    "registry_verify_specs",
    "registry_direction_by_code",
    "fetch_promoted_pattern_tags",
    "upsert_candidate",
    "promote_eligible_candidates",
    "verify_spec_testable",
    "evaluate_verify_spec",
]


async def fetch_active_registry(session: AsyncSession) -> list[ConfluenceRegistry]:
    stmt = (
        select(ConfluenceRegistry)
        .where(ConfluenceRegistry.active.is_(True))
        .order_by(ConfluenceRegistry.code.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def active_registry_codes(entries: list[ConfluenceRegistry]) -> frozenset[str]:
    """Códigos promovidos activos (K–Z). Excluye A–J del catálogo fijo."""
    return frozenset(
        e.code for e in entries
        if e.active and e.code not in STATIC_CONFLUENCE_CODES
    )


def render_registry_block(entries: list[ConfluenceRegistry]) -> str:
    promoted = [
        e for e in entries
        if e.active and e.code not in STATIC_CONFLUENCE_CODES
    ]
    if not promoted:
        return (
            "  (ninguna confluencia promovida activa; I/J del catálogo fijo = SHORT, "
            "no confundir con lecciones de post-mortem.)"
        )
    lines: list[str] = [
        "  Promovidas (K–Z). Catálogo fijo A–J (I=RSI overbought SHORT, J=MACD bearish) "
        "está en el system prompt:",
    ]
    for entry in sorted(promoted, key=lambda e: e.code):
        definition = (entry.definition_md or "").replace("\n", " ").strip()
        if len(definition) > 120:
            definition = definition[:117] + "..."
        direction = parse_registry_direction(entry.definition_md or "")
        dir_hint = f" [{direction_label_es(direction)}]" if direction else ""
        lines.append(f"{entry.code}. {entry.title}{dir_hint} — {definition}")
    return "\n".join(f"  {line}" for line in lines)


def registry_verify_specs(entries: list[ConfluenceRegistry]) -> dict[str, dict[str, Any]]:
    return {
        e.code: e.verify_spec
        for e in entries
        if e.active and e.code not in STATIC_CONFLUENCE_CODES
    }


def registry_direction_by_code(
    entries: list[ConfluenceRegistry],
) -> dict[str, str]:
    return map_registry_directions(entries, static_codes=STATIC_CONFLUENCE_CODES)


async def fetch_promoted_pattern_tags(session: AsyncSession) -> frozenset[str]:
    stmt = select(ConfluenceRegistry.slug).where(ConfluenceRegistry.active.is_(True))
    slugs = {row[0] for row in (await session.execute(stmt)).all()}
    stmt_tags = (
        select(ConfluenceCandidate.pattern_tag)
        .where(ConfluenceCandidate.status == "promoted")
    )
    tags = {row[0] for row in (await session.execute(stmt_tags)).all()}
    return frozenset(slugs | tags)


async def upsert_candidate(
    session: AsyncSession,
    *,
    normalized: LessonNormalized,
    decision_id: uuid.UUID,
    now: datetime,
) -> ConfluenceCandidate | None:
    if normalized.route != "candidate" or normalized.candidate is None:
        return None

    payload = normalized.candidate
    pattern_tag = normalized.pattern_tag
    existing = (await session.execute(
        select(ConfluenceCandidate).where(ConfluenceCandidate.pattern_tag == pattern_tag)
    )).scalar_one_or_none()

    if existing is not None:
        if existing.status != "open":
            return existing
        existing.occurrence_count += 1
        existing.last_seen_at = now
        if str(decision_id) not in existing.source_decision_ids:
            existing.source_decision_ids = [
                *existing.source_decision_ids,
                str(decision_id),
            ]
        existing.title = payload.title
        existing.definition_md = payload.definition_md
        existing.verify_spec = payload.verify_spec
        session.add(existing)
        return existing

    row = ConfluenceCandidate(
        id=uuid.uuid4(),
        pattern_tag=pattern_tag,
        title=payload.title,
        definition_md=payload.definition_md,
        verify_spec=payload.verify_spec,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        source_decision_ids=[str(decision_id)],
        status="open",
    )
    session.add(row)
    return row


async def promote_eligible_candidates(
    session: AsyncSession,
    *,
    min_occurrences: int = 3,
    window_days: int = 7,
    max_active: int = 5,
    playbook_content: str = "",
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(tz=timezone.utc)
    window_start = now - timedelta(days=window_days)

    from shared.confluence_registry_ops import active_registry_count

    active_count = await active_registry_count(session)
    if active_count >= max_active:
        logger.info("confluence.promotion.skipped_max_active", active=active_count, max_active=max_active)
        return []

    stmt = (
        select(ConfluenceCandidate)
        .where(
            ConfluenceCandidate.status == "open",
            ConfluenceCandidate.occurrence_count >= min_occurrences,
            ConfluenceCandidate.last_seen_at >= window_start,
        )
        .order_by(ConfluenceCandidate.occurrence_count.desc())
    )
    candidates = list((await session.execute(stmt)).scalars().all())
    promoted: list[dict[str, Any]] = []

    for candidate in candidates:
        if active_count + len(promoted) >= max_active:
            break
        if not verify_spec_testable(candidate.verify_spec):
            continue
        if playbook_conflict(playbook_content, candidate.title, candidate.definition_md):
            logger.info(
                "confluence.promotion.skipped_playbook_conflict",
                pattern_tag=candidate.pattern_tag,
            )
            continue
        try:
            registry_row = await promote_candidate_row(
                session,
                candidate,
                max_active=max_active,
                playbook_content=playbook_content,
                now=now,
            )
        except Exception as e:
            logger.warning(
                "confluence.promotion.candidate_failed",
                pattern_tag=candidate.pattern_tag,
                error=str(e),
            )
            continue
        promoted.append({
            "code": registry_row.code,
            "slug": registry_row.slug,
            "pattern_tag": candidate.pattern_tag,
            "title": candidate.title,
            "occurrence_count": candidate.occurrence_count,
        })
        logger.info(
            "confluence.promoted",
            code=registry_row.code,
            pattern_tag=candidate.pattern_tag,
            occurrences=candidate.occurrence_count,
        )

    return promoted


def evaluate_verify_spec(spec: dict[str, Any], ctx: dict[str, Any]) -> bool:
    if not spec:
        return True
    all_rules = spec.get("all") or []
    any_rules = spec.get("any") or []
    if all_rules and not all(_eval_rule(rule, ctx) for rule in all_rules):
        return False
    if any_rules and not any(_eval_rule(rule, ctx) for rule in any_rules):
        return False
    return True


def _eval_rule(rule: dict[str, Any], ctx: dict[str, Any]) -> bool:
    key = rule.get("ctx")
    if not key:
        return False
    val = ctx.get(key)
    if rule.get("exists"):
        return val is not None
    if val is None:
        return False
    try:
        if "eq" in rule:
            return val == rule["eq"]
        if "in" in rule:
            return val in rule["in"]
        if "not_in" in rule:
            return val not in rule["not_in"]
        num = float(val)
        if "lt" in rule:
            return num < float(rule["lt"])
        if "lte" in rule:
            return num <= float(rule["lte"])
        if "gt" in rule:
            return num > float(rule["gt"])
        if "gte" in rule:
            return num >= float(rule["gte"])
    except (TypeError, ValueError):
        return False
    return True
