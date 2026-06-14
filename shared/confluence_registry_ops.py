"""Operaciones de confluence registry compartidas entre web y trading-engine."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import ConfluenceCandidate, ConfluenceRegistry

STATIC_CONFLUENCE_CODES = frozenset("ABCDEFGHIJKLMNOP")
# Letras para confluencias promovidas (Q–Z). I–P son bajistas fijas en futuros.
EXTENDED_LETTERS = [
    chr(c) for c in range(ord("Q"), ord("Z") + 1)
    if chr(c) not in STATIC_CONFLUENCE_CODES
]
LETTER_RECYCLE_DAYS = 30

KNOWN_CTX_KEYS = frozenset({
    "price", "rsi_15m", "rsi_5m", "rsi_1h", "hist_15m", "hist_1h",
    "volume_ratio", "block_a_profile", "pct_24h", "imbalance",
    "block_f_cross_tf", "volatility_label", "macd_15m", "adx_15m",
    "bb_pct_5m", "bb_pct_1m",
})


class ConfluenceOpsError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def verify_spec_testable(spec: dict[str, Any]) -> bool:
    if not spec:
        return False
    rules = list(spec.get("all") or []) + list(spec.get("any") or [])
    if not rules:
        return False
    for rule in rules:
        key = rule.get("ctx")
        if not key or key not in KNOWN_CTX_KEYS:
            return False
    return True


def slug_from_tag(pattern_tag: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", pattern_tag.lower()).strip("_")
    return slug[:64] or "pattern"


def playbook_conflict(playbook: str, title: str, definition_md: str) -> bool:
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


async def next_available_letter(session: AsyncSession, *, now: datetime) -> str | None:
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
        if letter in STATIC_CONFLUENCE_CODES:
            continue
        if letter not in reserved:
            return letter
    return None


async def active_registry_count(session: AsyncSession) -> int:
    return (await session.execute(
        select(func.count()).select_from(ConfluenceRegistry).where(
            ConfluenceRegistry.active.is_(True),
        )
    )).scalar_one()


async def promote_candidate_row(
    session: AsyncSession,
    candidate: ConfluenceCandidate,
    *,
    max_active: int,
    playbook_content: str = "",
    now: datetime | None = None,
) -> ConfluenceRegistry:
    now = now or datetime.now(tz=timezone.utc)

    if candidate.status != "open":
        raise ConfluenceOpsError("invalid_status", f"Candidato en estado '{candidate.status}'")

    if not verify_spec_testable(candidate.verify_spec):
        raise ConfluenceOpsError("invalid_verify_spec", "verify_spec incompleto o no testeable")

    if await active_registry_count(session) >= max_active:
        raise ConfluenceOpsError("max_active_reached", f"Máximo de {max_active} confluencias activas")

    if playbook_conflict(playbook_content, candidate.title, candidate.definition_md):
        raise ConfluenceOpsError("playbook_conflict", "Conflicto con reglas [STRICT] del playbook")

    code = await next_available_letter(session, now=now)
    if code is None:
        raise ConfluenceOpsError("no_letters", "No hay letras Q–Z disponibles")

    slug = slug_from_tag(candidate.pattern_tag)
    existing_slug = (await session.execute(
        select(ConfluenceRegistry).where(ConfluenceRegistry.slug == slug)
    )).scalar_one_or_none()
    if existing_slug and existing_slug.active:
        raise ConfluenceOpsError("already_promoted", f"Patrón ya promovido como '{existing_slug.code}'")

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
    await session.flush()
    return registry_row


async def promote_candidate_by_id(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    max_active: int = 5,
    playbook_content: str = "",
) -> ConfluenceRegistry:
    candidate = await session.get(ConfluenceCandidate, candidate_id)
    if candidate is None:
        raise ConfluenceOpsError("not_found", "Candidato no encontrado")
    return await promote_candidate_row(
        session,
        candidate,
        max_active=max_active,
        playbook_content=playbook_content,
    )


async def reject_candidate_by_id(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    reason: str,
) -> ConfluenceCandidate:
    candidate = await session.get(ConfluenceCandidate, candidate_id)
    if candidate is None:
        raise ConfluenceOpsError("not_found", "Candidato no encontrado")
    if candidate.status != "open":
        raise ConfluenceOpsError("invalid_status", f"Candidato en estado '{candidate.status}'")
    candidate.status = "rejected"
    candidate.reject_reason = reason.strip() or "rechazado por operador"
    session.add(candidate)
    await session.flush()
    return candidate


async def deactivate_registry_code(
    session: AsyncSession,
    code: str,
    *,
    now: datetime | None = None,
) -> ConfluenceRegistry:
    now = now or datetime.now(tz=timezone.utc)
    if code in STATIC_CONFLUENCE_CODES:
        raise ConfluenceOpsError("static_code", "No se puede desactivar el catálogo fijo A–P")
    row = await session.get(ConfluenceRegistry, code)
    if row is None or not row.active:
        raise ConfluenceOpsError("not_found", "Confluencia no encontrada o ya inactiva")
    row.active = False
    row.deactivated_at = now
    session.add(row)
    await session.flush()
    return row
