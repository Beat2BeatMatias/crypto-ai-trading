from main import validate_futures_sizing


def test_validate_futures_sizing_unfeasible():
    ok, reason = validate_futures_sizing(
        available_margin=300.0,
        max_position_pct=0.10,
        leverage=1,
        min_notional=100.0,
    )
    assert ok is False
    assert "min_notional" in reason


def test_validate_futures_sizing_feasible():
    ok, reason = validate_futures_sizing(
        available_margin=1500.0,
        max_position_pct=0.10,
        leverage=1,
        min_notional=100.0,
    )
    assert ok is True
    assert reason == ""
