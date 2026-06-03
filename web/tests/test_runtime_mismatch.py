from shared.runtime_mismatch import (
    RuntimeMismatchReason,
    classify_downgrade_reason,
    diagnose_from_live_margin,
)


def test_classify_api_permissions_from_setup_failed():
    stored = "futures_setup_failed: binance -2015 Invalid API-key, IP, or permissions"
    assert classify_downgrade_reason(stored) == RuntimeMismatchReason.API_PERMISSIONS


def test_classify_insufficient_margin_from_sizing():
    stored = (
        "futures.sizing_unfeasible: max trade notional 30.00 < min_notional 100.00"
    )
    assert classify_downgrade_reason(stored) == RuntimeMismatchReason.INSUFFICIENT_MARGIN


def test_classify_empty_returns_none():
    assert classify_downgrade_reason("") is None


def test_diagnose_live_margin_insufficient():
    assert (
        diagnose_from_live_margin(
            available_margin=50.0,
            max_position_pct=0.2,
            max_leverage=3,
            min_notional_estimate=100.0,
        )
        == RuntimeMismatchReason.INSUFFICIENT_MARGIN
    )


def test_diagnose_live_margin_restart_required():
    assert (
        diagnose_from_live_margin(
            available_margin=250.0,
            max_position_pct=0.2,
            max_leverage=3,
            min_notional_estimate=100.0,
        )
        == RuntimeMismatchReason.RESTART_REQUIRED
    )
