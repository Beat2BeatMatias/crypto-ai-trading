"""Pydantic schemas and helpers for decision post-mortem lessons."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


POSTMORTEM_ELIGIBLE_CLASSIFICATIONS = frozenset({
    "BAD_BUY", "BAD_SELL", "MISSED_OPPORTUNITY", "BLOCKED_GOOD_TRADE",
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

    if classification == "BAD_BUY" and trade is not None:
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
