"""Confluence registry — candidates queue, promotion, verify_spec evaluation."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.postmortem_schemas import LessonNormalized
from shared.db.models import ConfluenceCandidate, ConfluenceRegistry

logger = structlog.get_logger()

STATIC_CONFLUENCE_CODES = frozenset("ABCDEFGH")
EXTENDED_LETTERS = [chr(c) for c in range(ord("I"), ord("Z") + 1)]
LETTER_RECYCLE_DAYS = 30

_KNOWN_CTX_KEYS = frozenset({
    "price", "rsi_15m", "rsi_5m", "rsi_1h", "hist_15m", "hist_1h",
    "volume_ratio", "block_a_profile", "pct_24h", "imbalance",
    "block_f_cross_tf", "volatility_label", "macd_15m", "adx_15m",
})


async def fetch_active_registry(session: AsyncSession) -> list[ConfluenceRegistry]:
    stmt = (
        select(ConfluenceRegistry)
        .where(ConfluenceRegistry.active.is_(True))
        .order_by(ConfluenceRegistry.code.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def active_registry_codes(entries: list[ConfluenceRegistry]) -> frozenset[str]:
    return frozenset(e.code for e in entries if e.active)


def render_registry_block(entries: list[ConfluenceRegistry]) -> str:
    if not entries:
        return "  (ninguna confluencia promovida activa.)"
    lines: list[str] = []
    for entry in sorted(entries, key=lambda e: e.code):
        definition = (entry.definition_md or "").replace("\n", " ").strip()
        if len(definition) > 120:
            definition = definition[:117] + "..."
        lines.append(f"{entry.code}. {entry.title} — {definition}")
    return "\n".join(f"  {line}" for line in lines)


def registry_verify_specs(entries: list[ConfluenceRegistry]) -> dict[str, dict[str, Any]]:
    return {e.code: e.verify_spec for e in entries if e.active}


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

    active_count = (await session.execute(
        select(func.count()).select_from(ConfluenceRegistry).where(ConfluenceRegistry.active.is_(True))
    )).scalar_one()
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
        if _playbook_conflict(playbook_content, candidate.title, candidate.definition_md):
            logger.info(
                "confluence.promotion.skipped_playbook_conflict",
                pattern_tag=candidate.pattern_tag,
            )
            continue

        code = await _next_available_letter(session, now=now)
        if code is None:
            logger.warning("confluence.promotion.no_letters_available")
            break

        slug = _slug_from_tag(candidate.pattern_tag)
        registry_row = ConfluenceRegistry(
            code=code,
            slug=slug,
            title=candidate.title,
            definition_md=candidate.definition_md,
            verify_spec=candidate.verify_spec,
            active=True,
            promoted_from=candidate.id,
            created_at=now,
        )
        candidate.status = "promoted"
        candidate.proposed_code = code
        candidate.promoted_at = now
        session.add(registry_row)
        session.add(candidate)
        promoted.append({
            "code": code,
            "slug": slug,
            "pattern_tag": candidate.pattern_tag,
            "title": candidate.title,
            "occurrence_count": candidate.occurrence_count,
        })
        logger.info(
            "confluence.promoted",
            code=code,
            pattern_tag=candidate.pattern_tag,
            occurrences=candidate.occurrence_count,
        )

    if promoted:
        await session.flush()
    return promoted


def verify_spec_testable(spec: dict[str, Any]) -> bool:
    if not spec:
        return False
    rules = list(spec.get("all") or []) + list(spec.get("any") or [])
    if not rules:
        return False
    for rule in rules:
        key = rule.get("ctx")
        if not key or key not in _KNOWN_CTX_KEYS:
            return False
    return True


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


async def _next_available_letter(session: AsyncSession, *, now: datetime) -> str | None:
    recycle_cutoff = now - timedelta(days=LETTER_RECYCLE_DAYS)
    rows = list((await session.execute(select(ConfluenceRegistry))).scalars().all())
    reserved: set[str] = set()
    for row in rows:
        if row.active:
            reserved.add(row.code)
            continue
        if row.deactivated_at is None or row.deactivated_at > recycle_cutoff:
            reserved.add(row.code)
    for letter in EXTENDED_LETTERS:
        if letter not in reserved:
            return letter
    return None


def _slug_from_tag(pattern_tag: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", pattern_tag.lower()).strip("_")
    return slug[:64] or "pattern"


def _playbook_conflict(playbook: str, title: str, definition_md: str) -> bool:
    if not playbook:
        return False
    strict_lines = [
        line.strip()
        for line in playbook.splitlines()
        if "[STRICT]" in line.upper()
    ]
    if not strict_lines:
        return False
    haystack = f"{title} {definition_md}".lower()
    for line in strict_lines:
        tokens = re.findall(r"[a-záéíóúñ]{5,}", line.lower())
        for token in tokens:
            if token in haystack and any(
                neg in line.lower() for neg in ("no ", "nunca", "prohibido", "evitar")
            ):
                return True
    return False
