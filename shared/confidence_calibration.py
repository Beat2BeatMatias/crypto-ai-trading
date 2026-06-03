"""Confidence calibration metrics from decisions + outcome classifications."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_POSITIVE_OUTCOMES = frozenset({
    "GOOD_BUY", "GOOD_SHORT", "GOOD_HOLD", "GOOD_SELL", "CORRECTLY_BLOCKED",
})

_CONF_BUCKETS: list[tuple[str, float, float]] = [
    ("<0.50", 0.0, 0.50),
    ("0.50–0.59", 0.50, 0.60),
    ("0.60–0.69", 0.60, 0.70),
    ("0.70–0.79", 0.70, 0.80),
    ("0.80–0.89", 0.80, 0.90),
    ("≥0.90", 0.90, 1.01),
]


@dataclass(frozen=True)
class CalibrationBucket:
    range: str
    count: int
    success_count: int
    success_rate: float | None
    avg_confidence: float | None


@dataclass(frozen=True)
class CalibrationReport:
    window_hours: int
    sample_size: int
    buckets: list[CalibrationBucket]
    brier_score: float | None
    expected_calibration_error: float | None
    discriminates: bool | None
    low_bucket_success_rate: float | None
    high_bucket_success_rate: float | None
    recommendation: str


def is_positive_outcome(classification: str) -> bool:
    return classification in _POSITIVE_OUTCOMES


def compute_calibration(
    samples: list[tuple[float, str]],
    *,
    window_hours: int,
    min_per_bucket: int = 5,
    min_total: int = 20,
    discrimination_gap: float = 0.05,
) -> CalibrationReport:
    """Build calibration report from (confidence, classification) pairs."""
    if not samples:
        return CalibrationReport(
            window_hours=window_hours,
            sample_size=0,
            buckets=[],
            brier_score=None,
            expected_calibration_error=None,
            discriminates=None,
            low_bucket_success_rate=None,
            high_bucket_success_rate=None,
            recommendation="Sin muestras con outcome maduro en la ventana.",
        )

    bucket_data: dict[str, list[tuple[float, int]]] = {label: [] for label, _, _ in _CONF_BUCKETS}
    brier_terms: list[float] = []

    for conf, classification in samples:
        conf = max(0.0, min(1.0, float(conf)))
        y = 1.0 if is_positive_outcome(classification) else 0.0
        brier_terms.append((conf - y) ** 2)
        for label, lo, hi in _CONF_BUCKETS:
            if lo <= conf < hi:
                bucket_data[label].append((conf, int(y)))
                break

    buckets: list[CalibrationBucket] = []
    ece_terms: list[float] = []
    n = len(samples)

    for label, lo, hi in _CONF_BUCKETS:
        rows = bucket_data[label]
        cnt = len(rows)
        if cnt == 0:
            buckets.append(CalibrationBucket(
                range=label, count=0, success_count=0,
                success_rate=None, avg_confidence=None,
            ))
            continue
        successes = sum(y for _, y in rows)
        avg_conf = sum(c for c, _ in rows) / cnt
        rate = successes / cnt
        buckets.append(CalibrationBucket(
            range=label,
            count=cnt,
            success_count=successes,
            success_rate=round(rate, 3),
            avg_confidence=round(avg_conf, 3),
        ))
        ece_terms.append(abs(avg_conf - rate) * (cnt / n))

    brier = round(sum(brier_terms) / n, 4) if brier_terms else None
    ece = round(sum(ece_terms), 4) if ece_terms else None

    populated = [b for b in buckets if b.count >= min_per_bucket and b.success_rate is not None]
    low_sr = populated[0].success_rate if populated else None
    high_sr = populated[-1].success_rate if populated else None
    discriminates: bool | None = None
    if len(populated) >= 2 and low_sr is not None and high_sr is not None:
        discriminates = (high_sr - low_sr) >= discrimination_gap

    recommendation = _recommendation(
        sample_size=n,
        min_total=min_total,
        discriminates=discriminates,
        brier=brier,
        ece=ece,
    )

    return CalibrationReport(
        window_hours=window_hours,
        sample_size=n,
        buckets=buckets,
        brier_score=brier,
        expected_calibration_error=ece,
        discriminates=discriminates,
        low_bucket_success_rate=low_sr,
        high_bucket_success_rate=high_sr,
        recommendation=recommendation,
    )


def _recommendation(
    *,
    sample_size: int,
    min_total: int,
    discriminates: bool | None,
    brier: float | None,
    ece: float | None,
) -> str:
    if sample_size < min_total:
        return (
            f"Muestra insuficiente ({sample_size}<{min_total}). "
            "Acumular más ciclos antes de confiar en la confianza para sizing."
        )
    if discriminates is False:
        return (
            "La confianza no discrimina outcomes (buckets altos ≈ bajos). "
            "Mantener sizing por riesgo fijo; no escalar por confidence hasta recalibrar."
        )
    if discriminates is True:
        return (
            "La confianza discrimina outcomes en la ventana. "
            "Opcional: revisar umbrales conf_threshold_* o considerar sizing moderado por bucket."
        )
    if brier is not None and ece is not None:
        return f"Calibración parcial (Brier={brier}, ECE={ece}). Seguir monitoreando."
    return "Sin conclusión de calibración."
