from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from agents.outcome_attribution import (
    DecisionAttribution,
    attribute,
)


def test_decision_attribution_dataclass_is_frozen():
    attr = DecisionAttribution(
        decision_id=uuid4(),
        horizon_min=240,
        matured=False,
        forward_return_pct=None,
        mfe_pct=None,
        mae_pct=None,
        time_to_mfe_min=None,
        time_to_mae_min=None,
        sl_dist_pct=None,
        tp_target_pct=None,
        classification="UNKNOWN",
        computed_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        attr.classification = "GOOD_HOLD"  # type: ignore[misc]


def test_attribute_returns_unknown_when_decision_missing_inputs():
    """Una decisión sin price/atr_pct en su input se clasifica UNKNOWN."""
    decision = _make_decision(input={}, output={"action": "HOLD"})
    result = attribute(
        decision=decision,
        ohlcv_1m=[],
        associated_trade=None,
        horizon_min=240,
        now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert result.classification == "UNKNOWN"
    assert result.decision_id == decision.id


def _make_decision(*, input: dict, output: dict, ts=None, executed=False):
    """Helper for tests — minimal Decision-like object without DB."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=uuid4(),
        ts=ts or datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        input=input,
        output=output,
        executed=executed,
        trade_id=None,
    )
