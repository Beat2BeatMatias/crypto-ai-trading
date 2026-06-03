"""Deterministic confidence_base from filtered confluences (A–H + active I–Z)."""
from __future__ import annotations

from typing import Any

from shared.schemas import DecisorOutput, Direction, MarketRegime, direction_for_action

_QUALITY_STRONG_CODES = frozenset({"F", "G"})

_DEFAULT_CALIBRATION: dict[str, float] = {
    "conf_base_0": 0.40,
    "conf_base_1": 0.55,
    "conf_base_2": 0.70,
    "conf_base_3": 0.85,
    "conf_base_4plus": 1.00,
    "peso_regime_range": 0.85,
    "peso_regime_high_vol": 0.75,
}


def effective_confluence_count(confluences: list[str]) -> int:
    """Count post-filter confluences (A–H + active I–Z, each weight 1.0)."""
    return len(confluences)


def quality_factor(confluences: list[str]) -> float:
    codes = set(confluences)
    return 1.0 if codes & _QUALITY_STRONG_CODES else 0.85


def regime_factor(
    regime: MarketRegime | str,
    calibration: dict[str, Any],
    *,
    direction: Direction | None = None,
) -> float:
    cal = {**_DEFAULT_CALIBRATION, **{k: float(v) for k, v in calibration.items() if k in _DEFAULT_CALIBRATION}}
    key = regime.value if isinstance(regime, MarketRegime) else str(regime)
    direction = direction or Direction.LONG
    if direction == Direction.LONG:
        if key == "TRENDING_UP":
            return 1.0
        if key == "TRENDING_DOWN":
            return 0.0
    else:
        if key == "TRENDING_DOWN":
            return 1.0
        if key == "TRENDING_UP":
            return 0.0
    if key == "RANGE":
        return cal["peso_regime_range"]
    if key == "HIGH_VOLATILITY":
        return cal["peso_regime_high_vol"]
    if key == "NEUTRAL":
        return cal["peso_regime_range"]
    return cal["peso_regime_range"]


def _conf_base_table_value(count: int, calibration: dict[str, Any]) -> float:
    cal = {**_DEFAULT_CALIBRATION, **{k: float(v) for k, v in calibration.items() if k.startswith("conf_base_") or k.startswith("peso_")}}
    table = (
        cal["conf_base_0"],
        cal["conf_base_1"],
        cal["conf_base_2"],
        cal["conf_base_3"],
        cal["conf_base_4plus"],
    )
    idx = min(max(count, 0), 4)
    return table[idx]


def compute_confidence_base(
    confluences: list[str],
    regime: MarketRegime | str,
    calibration: dict[str, Any] | None = None,
    *,
    direction: Direction | None = None,
) -> tuple[float, dict[str, Any]]:
    cal = calibration or {}
    count = effective_confluence_count(confluences)
    qf = quality_factor(confluences)
    rf = regime_factor(regime, cal, direction=direction)
    base_n = _conf_base_table_value(count, cal)
    base = max(0.0, min(1.0, base_n * qf * rf))
    meta: dict[str, Any] = {
        "confluence_count": count,
        "confluences_counted": list(confluences),
        "extended_confluence_weight": 1.0,
        "quality_factor": qf,
        "regime_factor": rf,
        "conf_base_table_value": base_n,
        "confidence_base_computed": base,
    }
    return base, meta


def apply_server_confidence(
    decision: DecisorOutput,
    *,
    calibration: dict[str, Any] | None = None,
    confluences_dropped: list[str] | None = None,
) -> tuple[DecisorOutput, dict[str, Any]]:
    direction = direction_for_action(decision.action)
    base, meta = compute_confidence_base(
        decision.confluences, decision.regime, calibration, direction=direction,
    )
    if confluences_dropped:
        meta["confluences_dropped"] = list(confluences_dropped)
    payload = decision.model_dump()
    payload["confidence_base"] = base
    payload["confidence_adjustment"] = decision.confidence_adjustment
    updated = DecisorOutput.model_validate(payload)
    meta["confidence_adjustment"] = updated.confidence_adjustment
    meta["confidence"] = updated.confidence
    return updated, meta
