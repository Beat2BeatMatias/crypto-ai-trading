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


def test_remap_splits_long_text_by_misapplied_code():
    long_notes = (
        "La confluencia 'H' (RANGE_SUPPORT_TOUCH) fue declarada, pero el precio "
        "($77,161.66) no estaba tocando el soporte de rango (BB_lower_1h en $76,247). "
        "La confluencia 'B' (MACD_BULLISH_CROSS) fue declarada sin cruce real en 15m."
    )
    lesson = _base_lesson(
        classification="MISSED_OPPORTUNITY",
        confluence_analysis={"misapplied_codes": ["H", "B"], "notes": long_notes},
        proposed_pattern=None,
    )
    result = normalize(lesson, decision_ts="2026-05-25T04:36Z")
    assert result.route == "remap"
    assert "MACD_BULLISH_CROSS" in result.block_k_line or any(
        "MACD_BULLISH_CROSS" in line for line in result.block_k_lines
    )
    assert "RANGE_SUPPORT_TOUCH" in result.block_k_line
    all_lines = [result.block_k_line, *result.block_k_lines]
    rendered = format_block_k_lessons(
        [{"lesson_normalized": result.model_dump()}],
        max_lines=5,
    )
    assert "MACD_BULLISH_CROSS" in rendered
    assert "RANGE_SUPPORT_TOUCH" in rendered
    assert len(all_lines) >= 2


def test_remap_single_code_not_truncated_at_200():
    text = "x" * 350
    lesson = _base_lesson(
        confluence_analysis={"misapplied_codes": ["H"], "notes": text},
        proposed_pattern=None,
    )
    result = normalize(lesson, decision_ts="2026-05-25T04:36Z")
    assert len(result.block_k_line) > 200
    assert "…" not in result.block_k_line


def test_format_block_k_respects_max_lines_with_split_lesson():
    rows = [{
        "lesson_normalized": {
            "route": "remap",
            "confidence": 0.9,
            "block_k_line": "linea 1",
            "block_k_lines": ["linea 2", "linea 3"],
            "dedupe_key": "k1",
        },
    }]
    text = format_block_k_lessons(rows, max_lines=2)
    assert "linea 1" in text
    assert "linea 2" in text
    assert "linea 3" not in text
