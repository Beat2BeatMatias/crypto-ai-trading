"""Tests for lesson normalizer routes."""
from __future__ import annotations

from agents.lesson_normalizer import format_block_k_lessons, normalize
from agents.postmortem_schemas import (
    ConfluenceAnalysis,
    DecisionSnapshot,
    ForwardEvidence,
    LessonRaw,
    ProposedPattern,
)


def _base_lesson(**overrides) -> LessonRaw:
    data = {
        "classification": "BAD_BUY",
        "severity_score": 0.8,
        "summary": "BUY en RANGE sin volumen.",
        "decision_snapshot": {
            "regime_declared": "RANGE",
            "action": "BUY",
            "confidence": 0.7,
            "confluences_declared": ["H", "A"],
            "reasoning_excerpt": "rebote",
        },
        "forward_evidence": {"mfe_pct": 0.1, "mae_pct": -0.4},
    }
    data.update(overrides)
    return LessonRaw.model_validate(data)


def test_normalize_remap_when_maps_to_existing():
    lesson = _base_lesson(
        confluence_analysis={"misapplied_codes": ["H"], "notes": "H mal aplicada"},
        proposed_pattern={
            "tag": "range_rsi",
            "maps_to_existing": "H",
            "definition_hint": "",
        },
    )
    result = normalize(lesson, decision_ts="2026-05-23T14:30Z")
    assert result.route == "remap"
    assert result.remap is not None
    assert "H" in result.remap.misapplied_confluences


def test_normalize_remap_when_misapplied_without_new_pattern():
    lesson = _base_lesson(
        confluence_analysis={"misapplied_codes": ["A", "H"], "notes": "corrección"},
        proposed_pattern=None,
    )
    result = normalize(lesson, decision_ts="2026-05-23T14:30Z")
    assert result.route == "remap"
    assert result.dedupe_key.startswith("remap:")


def test_normalize_candidate_when_verify_spec_buildable():
    lesson = _base_lesson(
        confluence_analysis={"misapplied_codes": []},
        proposed_pattern={
            "tag": "vol_div_range",
            "title": "Volumen bajo en RANGE",
            "definition_hint": "RANGE + vol_ratio bajo",
            "maps_to_existing": None,
        },
    )
    result = normalize(
        lesson,
        decision_ts="2026-05-23T14:30Z",
        decision_input={"volume_ratio": 0.5, "rsi_15m": 32},
    )
    assert result.route == "candidate"
    assert result.candidate is not None
    assert result.candidate.verify_spec.get("all")


def test_normalize_guidance_fallback():
    lesson = _base_lesson(
        confluence_analysis={"misapplied_codes": []},
        proposed_pattern=None,
        summary="Priorizar TF 15m sobre 1m en HIBRIDO.",
    )
    result = normalize(lesson, decision_ts="2026-05-23T14:30Z", decision_input={})
    assert result.route == "guidance"
    assert result.guidance is not None


def test_format_block_k_dedupes_and_limits():
    rows = [
        {"lesson_normalized": {
            "route": "remap", "confidence": 0.9,
            "block_k_line": "linea A", "dedupe_key": "k1",
        }},
        {"lesson_normalized": {
            "route": "remap", "confidence": 0.5,
            "block_k_line": "linea A dup", "dedupe_key": "k1",
        }},
        {"lesson_normalized": {
            "route": "guidance", "confidence": 0.8,
            "block_k_line": "linea B", "dedupe_key": "k2",
        }},
        {"lesson_normalized": {
            "route": "candidate", "confidence": 0.99,
            "block_k_line": "no visible", "dedupe_key": "k3",
        }},
    ]
    text = format_block_k_lessons(rows, max_lines=5)
    assert "linea A" in text
    assert "linea A dup" not in text
    assert "linea B" in text
    assert "no visible" not in text
