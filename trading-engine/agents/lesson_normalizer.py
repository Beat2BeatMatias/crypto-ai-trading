"""Normalize raw post-mortem lessons into decisor-ready routes."""
from __future__ import annotations

import re
from typing import Any

from agents.postmortem_schemas import (
    CandidatePayload,
    GuidancePayload,
    LessonNormalized,
    LessonRaw,
    RemapPayload,
)

_BLOCK_K_ROUTES = frozenset({"remap", "guidance"})
_BLOCK_K_LINE_MAX = 480

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
    lines_out: list[str] = []
    for item in ordered:
        for line in _block_k_lines_from_norm(item):
            if len(lines_out) >= max_lines:
                break
            lines_out.append(f"  {line}")
        if len(lines_out) >= max_lines:
            break
    if not lines_out:
        return "  (sin lecciones recientes de post-mortem.)"
    return "\n".join(lines_out)


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
    block_lines = _remap_block_k_lines(
        decision_ts, lesson.classification, text, misapplied,
    )
    return LessonNormalized(
        route="remap",
        pattern_tag=pattern_tag,
        confidence=confidence,
        remap=RemapPayload(
            misapplied_confluences=misapplied,
            correction=text,
            maps_to_existing_only=True,
        ),
        block_k_line=block_lines[0],
        block_k_lines=block_lines[1:] if len(block_lines) > 1 else [],
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
        block_k_line=f"[candidate {decision_ts}] {title}: {_clamp_text(definition, max_len=240)}",
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
        block_k_line=f"[guidance] {_clamp_text(message)}",
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


def _block_k_lines_from_norm(norm: dict[str, Any]) -> list[str]:
    primary = str(norm.get("block_k_line") or "")
    extra = [str(line) for line in (norm.get("block_k_lines") or []) if line]
    if extra:
        return ([primary] if primary else []) + extra
    if primary:
        return [primary]
    return []


def _clamp_text(text: str, *, max_len: int = _BLOCK_K_LINE_MAX) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[:max_len].rsplit(" ", 1)[0]
    return f"{cut}…"


def _remap_block_k_lines(
    decision_ts: str,
    classification: str,
    text: str,
    misapplied: list[str],
) -> list[str]:
    prefix = f"[{decision_ts}] {classification}:"
    if len(misapplied) <= 1:
        return [f"{prefix} {_clamp_text(text)}"]

    segments: list[str] = []
    for code in misapplied:
        segment = _extract_code_segment(text, code)
        if segment:
            segments.append(f"{prefix} {_clamp_text(segment)}")
        else:
            segments.append(
                f"{prefix} Confluencia '{code}' mal aplicada. {_clamp_text(text, max_len=240)}"
            )

    if segments:
        return segments
    return [f"{prefix} {_clamp_text(text)}"]


def _extract_code_segment(text: str, code: str) -> str | None:
    parts = re.split(r"(?<=[.;])\s+", text.strip())
    pattern = _code_mention_pattern(code)
    for part in parts:
        if pattern.search(part):
            return part.strip()
    return None


def _code_mention_pattern(code: str) -> re.Pattern[str]:
    c = re.escape(code)
    return re.compile(rf"(?:confluencia\s+['\"]{c}['\"]|['\"]{c}['\"])", re.I)
