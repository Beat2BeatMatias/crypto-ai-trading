"""Tests for shared.confidence_calibration."""
from shared.confidence_calibration import compute_calibration, is_positive_outcome


def test_is_positive_outcome():
    assert is_positive_outcome("GOOD_BUY")
    assert not is_positive_outcome("MISSED_OPPORTUNITY")


def test_calibration_discriminates_when_high_bucket_wins_more():
    samples = (
        [(0.55, "GOOD_HOLD")] * 6
        + [(0.55, "MISSED_OPPORTUNITY")] * 4
        + [(0.85, "GOOD_BUY")] * 8
        + [(0.85, "BAD_BUY")] * 2
    )
    report = compute_calibration(list(samples), window_hours=168, min_per_bucket=5, min_total=15)
    assert report.sample_size == 20
    assert report.discriminates is True
    assert report.brier_score is not None


def test_calibration_insufficient_sample():
    report = compute_calibration([(0.7, "GOOD_HOLD")] * 5, window_hours=168, min_total=20)
    assert "insuficiente" in report.recommendation.lower()


def test_calibration_no_discrimination_flat():
    samples = [(0.55, "GOOD_HOLD"), (0.85, "BAD_BUY")] * 6
    report = compute_calibration(samples, window_hours=168, min_per_bucket=5, min_total=10)
    assert report.discriminates is False
