"""Tests for post-mortem severity scoring and lesson schema."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.postmortem_schemas import LessonRaw, compute_severity_score


def test_compute_severity_bad_buy_uses_pnl():
    outcome = SimpleNamespace(
        classification="BAD_BUY",
        tp_target_pct=0.4,
        sl_dist_pct=0.3,
        mfe_pct=0.1,
        mae_pct=-0.5,
    )
    trade = SimpleNamespace(pnl_pct=-1.2)
    score = compute_severity_score(
        classification="BAD_BUY", outcome=outcome, trade=trade,
    )
    assert score == pytest.approx(0.6)


def test_compute_severity_missed_opportunity():
    outcome = SimpleNamespace(
        classification="MISSED_OPPORTUNITY",
        tp_target_pct=0.5,
        sl_dist_pct=0.3,
        mfe_pct=0.75,
        mae_pct=-0.1,
    )
    score = compute_severity_score(
        classification="MISSED_OPPORTUNITY", outcome=outcome, trade=None,
    )
    assert score == pytest.approx(1.0)


def test_lesson_raw_validates_minimal_payload():
    lesson = LessonRaw.model_validate({
        "classification": "BAD_BUY",
        "severity_score": 0.8,
        "summary": "Test summary",
        "decision_snapshot": {
            "regime_declared": "RANGE",
            "action": "BUY",
            "confidence": 0.7,
            "confluences_declared": ["H"],
            "reasoning_excerpt": "rebote",
        },
        "forward_evidence": {
            "mfe_pct": 0.1,
            "mae_pct": -0.4,
        },
    })
    assert lesson.root_cause_tag == ""
    assert lesson.hindsight_guardrails_passed is True
