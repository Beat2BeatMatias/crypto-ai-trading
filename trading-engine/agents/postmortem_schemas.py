"""Pydantic schemas and helpers for decision post-mortem lessons."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


POSTMORTEM_ELIGIBLE_CLASSIFICATIONS = frozenset({
    "BAD_BUY", "BAD_SHORT", "BAD_SELL", "MISSED_OPPORTUNITY", "BLOCKED_GOOD_TRADE",
})

PostmortemStatus = Literal["completed", "skipped", "failed"]


class MisreadIndicator(BaseModel):
    indicator_key: str
    value_at_decision: float | str | None = None
    decisor_interpretation: str = ""
    correct_interpretation: str = ""
    evidence_from_input: bool = True


class IgnoredSignal(BaseModel):
    indicator_key: str
    value_at_decision: float | str | None = None
    why_relevant: str = ""


class DecisionSnapshot(BaseModel):
    regime_declared: str | None = None
    action: str | None = None
    confidence: float | None = None
    confluences_declared: list[str] = Field(default_factory=list)
    reasoning_excerpt: str = ""


class ForwardEvidence(BaseModel):
    mfe_pct: float | None = None
    mae_pct: float | None = None
    forward_return_pct: float | None = None
    time_to_mae_min: int | None = None
    time_to_mfe_min: int | None = None


class ConfluenceAnalysis(BaseModel):
    misapplied_codes: list[str] = Field(default_factory=list)
    should_have_used: list[str] = Field(default_factory=list)
    notes: str = ""


class ProposedPattern(BaseModel):
    tag: str
    title: str = ""
    definition_hint: str = ""
    maps_to_existing: str | None = None


class WouldChange(BaseModel):
    action: str | None = None
    rationale: str = ""


class RemapPayload(BaseModel):
    misapplied_confluences: list[str] = Field(default_factory=list)
    correction: str = ""
    maps_to_existing_only: bool = True


class CandidatePayload(BaseModel):
    title: str
    definition_md: str
    verify_spec: dict[str, Any] = Field(default_factory=dict)
    proposed_code_letter: str = "I"


class GuidancePayload(BaseModel):
    type: str = "general"
    message: str
    applies_when: dict[str, Any] = Field(default_factory=dict)


class LessonNormalized(BaseModel):
    version: int = 1
    route: Literal["remap", "candidate", "guidance"]
    pattern_tag: str
    confidence: float = Field(ge=0.0, le=1.0)
    remap: RemapPayload | None = None
    candidate: CandidatePayload | None = None
    guidance: GuidancePayload | None = None
    block_k_line: str
    block_k_lines: list[str] = Field(default_factory=list)
    dedupe_key: str


class LessonRaw(BaseModel):
    version: int = 1
    classification: str
    severity_score: float = Field(ge=0.0, le=1.0)
    root_cause_tag: str = ""
    summary: str = Field(max_length=2000)
    decision_snapshot: DecisionSnapshot
    forward_evidence: ForwardEvidence
    misread_indicators: list[MisreadIndicator] = Field(default_factory=list)
    ignored_signals: list[IgnoredSignal] = Field(default_factory=list)
    confluence_analysis: ConfluenceAnalysis = Field(default_factory=ConfluenceAnalysis)
    proposed_pattern: ProposedPattern | None = None
    would_change: WouldChange | None = None
    hindsight_guardrails_passed: bool = True


def compute_severity_score(
    *,
    classification: str,
    outcome: Any,
    trade: Any | None,
) -> float:
    """Deterministic priority score in [0, 1] for post-mortem queue ordering."""
    tp = _f(getattr(outcome, "tp_target_pct", None))
    sl = _f(getattr(outcome, "sl_dist_pct", None))
    mfe = _f(getattr(outcome, "mfe_pct", None))
    mae = _f(getattr(outcome, "mae_pct", None))

    if classification in ("BAD_BUY", "BAD_SHORT") and trade is not None:
        pnl = _f(getattr(trade, "pnl_pct", None))
        if pnl is not None:
            return min(1.0, abs(pnl) / 2.0)
        if mae is not None and sl and sl > 0:
            return min(1.0, abs(mae) / sl)

    if classification == "MISSED_OPPORTUNITY":
        if mfe is not None and tp and tp > 0:
            return min(1.0, mfe / tp)

    if classification == "BLOCKED_GOOD_TRADE":
        if mfe is not None and tp and tp > 0:
            return min(1.0, (mfe / tp) * 0.7)

    if classification == "BAD_SELL" and trade is not None:
        pnl = _f(getattr(trade, "pnl_pct", None))
        if pnl is not None and tp and tp > 0:
            return min(1.0, abs(pnl) / tp)

    if mfe is not None and tp and tp > 0:
        return min(1.0, mfe / tp)
    return 0.5


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_object_list(
    items: Any,
    *,
    kind: Literal["misread", "ignored"],
) -> list[dict[str, Any]]:
    if not items:
        return []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            key = item.strip()
            if kind == "misread":
                out.append({
                    "indicator_key": key,
                    "decisor_interpretation": key,
                    "correct_interpretation": "",
                    "evidence_from_input": True,
                })
            else:
                out.append({
                    "indicator_key": key,
                    "why_relevant": key,
                })
        elif isinstance(item, dict):
            row = dict(item)
            if "indicator_key" not in row:
                for alt in ("key", "indicator", "name"):
                    if alt in row:
                        row["indicator_key"] = str(row[alt])
                        break
            if "indicator_key" not in row:
                continue
            out.append(row)
    return out


def _coerce_proposed_pattern(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    row = dict(value)
    tag = row.get("tag")
    if not tag:
        maps_to = row.get("maps_to_existing")
        if maps_to:
            row["tag"] = f"remap_{str(maps_to).lower()}"
        elif row.get("name"):
            row["tag"] = str(row["name"]).lower().replace(" ", "_")[:64]
        else:
            row["tag"] = "unspecified_pattern"
    if not row.get("title"):
        row["title"] = str(row.get("definition_hint") or row["tag"])[:128]
    return row


def coerce_lesson_raw(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize common LLM JSON shape drift before Pydantic validation."""
    out = dict(data)
    out["misread_indicators"] = _coerce_object_list(
        out.get("misread_indicators"), kind="misread",
    )
    out["ignored_signals"] = _coerce_object_list(
        out.get("ignored_signals"), kind="ignored",
    )
    if "proposed_pattern" in out:
        out["proposed_pattern"] = _coerce_proposed_pattern(out.get("proposed_pattern"))
    return out
