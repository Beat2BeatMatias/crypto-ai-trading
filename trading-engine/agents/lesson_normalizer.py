"""Normalize raw post-mortem lessons into decisor-ready routes."""
from __future__ import annotations

from typing import Any

from agents.postmortem_schemas import (
    CandidatePayload,
    GuidancePayload,
    LessonNormalized,
    LessonRaw,
    RemapPayload,
)

_BLOCK_K_ROUTES = frozenset({"remap", "guidance"})

_KNOWN_CTX_KEYS = frozenset({
    "price", "rsi_15m", "rsi_5m", "rsi_1h", "hist_15m", "hist_1h",
    "volume_ratio", "block_a_profile", "pct_24h", "imbalance",
    "block_f_cross_tf", "volatility_label", "macd_15m", "adx_15m",
})


def normalize(
    lesson: LessonRaw,
    *,
    decision_ts: str,
    decision_input: dict[str, Any] | None = None,
    promoted_pattern_tags: frozenset[str] | None = None,
) -> LessonNormalized:
    """Classify a raw lesson into remap | candidate | guidance."""
    inp = decision_input or {}
    promoted = promoted_pattern_tags or frozenset()
    pattern_tag = _pattern_tag(lesson)
    misapplied = list(lesson.confluence_analysis.misapplied_codes)
    proposed = lesson.proposed_pattern

    if proposed and proposed.maps_to_existing:
        return _build_remap(lesson, decision_ts, pattern_tag, misapplied, confidence=0.9)

    if misapplied and proposed is None:
        return _build_remap(lesson, decision_ts, pattern_tag, misapplied, confidence=0.85)

    if proposed and pattern_tag in promoted:
        return _build_remap(
            lesson, decision_ts, pattern_tag, misapplied,
            correction=f"Patrón ya promovido ({pattern_tag}): {lesson.summary}",
            confidence=0.8,
        )

    if proposed and _can_build_verify_spec(proposed, inp):
        return _build_candidate(lesson, decision_ts, pattern_tag, proposed, inp)

    return _build_guidance(lesson, decision_ts, pattern_tag)


def format_block_k_lessons(
    rows: list[dict[str, Any]],
    *,
    max_lines: int = 5,
) -> str:
    """Dedupe by dedupe_key, keep highest confidence, emit up to max_lines."""
    if not rows:
        return "  (sin lecciones recientes de post-mortem.)"

    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        norm = row.get("lesson_normalized") or {}
        if norm.get("route") not in _BLOCK_K_ROUTES:
            continue
        key = norm.get("dedupe_key") or norm.get("block_k_line", "")
        conf = float(norm.get("confidence") or 0)
        prev = by_key.get(key)
        if prev is None or conf > float(prev.get("confidence") or 0):
            by_key[key] = norm

    ordered = sorted(by_key.values(), key=lambda x: float(x.get("confidence") or 0), reverse=True)
    lines = [f"  {item['block_k_line']}" for item in ordered[:max_lines] if item.get("block_k_line")]
    if not lines:
        return "  (sin lecciones recientes de post-mortem.)"
    return "\n".join(lines)


def _pattern_tag(lesson: LessonRaw) -> str:
    if lesson.proposed_pattern and lesson.proposed_pattern.tag:
        return lesson.proposed_pattern.tag
    return lesson.root_cause_tag or "unknown"


def _build_remap(
    lesson: LessonRaw,
    decision_ts: str,
    pattern_tag: str,
    misapplied: list[str],
    *,
    correction: str | None = None,
    confidence: float,
) -> LessonNormalized:
    text = correction or _remap_correction(lesson)
    codes = ",".join(sorted(misapplied)) if misapplied else "none"
    return LessonNormalized(
        route="remap",
        pattern_tag=pattern_tag,
        confidence=confidence,
        remap=RemapPayload(
            misapplied_confluences=misapplied,
            correction=text,
            maps_to_existing_only=True,
        ),
        block_k_line=f"[{decision_ts}] {lesson.classification}: {text[:200]}",
        dedupe_key=f"remap:{pattern_tag}:{codes}",
    )


def _remap_correction(lesson: LessonRaw) -> str:
    if lesson.confluence_analysis.notes:
        return lesson.confluence_analysis.notes
    if lesson.would_change and lesson.would_change.rationale:
        return lesson.would_change.rationale
    return lesson.summary


def _build_candidate(
    lesson: LessonRaw,
    decision_ts: str,
    pattern_tag: str,
    proposed: Any,
    inp: dict[str, Any],
) -> LessonNormalized:
    definition = proposed.definition_hint or proposed.title or lesson.summary
    verify_spec = _verify_spec_from_input(proposed.tag, inp)
    title = proposed.title or pattern_tag.replace("_", " ").title()
    return LessonNormalized(
        route="candidate",
        pattern_tag=pattern_tag,
        confidence=0.75,
        candidate=CandidatePayload(
            title=title,
            definition_md=definition,
            verify_spec=verify_spec,
            proposed_code_letter="I",
        ),
        block_k_line=f"[candidate {decision_ts}] {title}: {definition[:120]}",
        dedupe_key=f"candidate:{pattern_tag}",
    )


def _build_guidance(
    lesson: LessonRaw,
    decision_ts: str,
    pattern_tag: str,
) -> LessonNormalized:
    profile = lesson.decision_snapshot.regime_declared or ""
    message = lesson.summary
    if lesson.misread_indicators:
        first = lesson.misread_indicators[0]
        message = first.correct_interpretation or message
    return LessonNormalized(
        route="guidance",
        pattern_tag=pattern_tag,
        confidence=0.7,
        guidance=GuidancePayload(
            type="interpretation",
            message=message,
            applies_when={"regime": profile} if profile else {},
        ),
        block_k_line=f"[guidance] {message[:200]}",
        dedupe_key=f"guidance:{pattern_tag}",
    )


def _can_build_verify_spec(proposed: Any, inp: dict[str, Any]) -> bool:
    if not proposed.definition_hint and not proposed.title:
        return False
    keys = _input_keys_matching_ctx(inp)
    return len(keys) >= 1


def _verify_spec_from_input(pattern_tag: str, inp: dict[str, Any]) -> dict[str, Any]:
    keys = _input_keys_matching_ctx(inp)
    rules = [{"ctx": k, "exists": True} for k in keys[:4]]
    return {"all": rules, "pattern_tag": pattern_tag}


def _input_keys_matching_ctx(inp: dict[str, Any]) -> list[str]:
    return [k for k in inp if k in _KNOWN_CTX_KEYS]
