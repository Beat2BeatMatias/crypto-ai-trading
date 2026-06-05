"""Deterministic confidence_base from filtered confluences (A–H + active I–Z)."""
from __future__ import annotations

from typing import Any

from shared.confluence_direction import filter_confluences_for_direction
from shared.schemas import (
    DecisorAction,
    DecisorOutput,
    Direction,
    MarketRegime,
    direction_for_action,
)

_QUALITY_STRONG_CODES = frozenset({"F", "G"})
_ABSOLUTE_ADJ_MAX = 0.20


def clamp_subjective_adjustment(value: Any, max_adj: float) -> float:
    """Clamp LLM confidence_adjustment to ±max_adj (config subjective_adj_max)."""
    if value is None:
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    limit = max(0.0, min(_ABSOLUTE_ADJ_MAX, float(max_adj)))
    return max(-limit, min(limit, v))

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


def hold_signal_direction(
    regime: MarketRegime | str,
    trading_product: str,
) -> Direction | None:
    """Dirección de referencia para medir fuerza de señal en HOLD (no implica ejecutar)."""
    key = regime.value if isinstance(regime, MarketRegime) else str(regime)
    if key == "TRENDING_DOWN" and trading_product == "futures":
        return Direction.SHORT
    if key == "TRENDING_UP":
        return Direction.LONG
    return None


def regime_factor(
    regime: MarketRegime | str,
    calibration: dict[str, Any],
    *,
    direction: Direction | None = None,
    trading_product: str = "spot",
) -> float:
    cal = {**_DEFAULT_CALIBRATION, **{k: float(v) for k, v in calibration.items() if k in _DEFAULT_CALIBRATION}}
    key = regime.value if isinstance(regime, MarketRegime) else str(regime)
    if direction is None:
        if key == "HIGH_VOLATILITY":
            return cal["peso_regime_high_vol"]
        return cal["peso_regime_range"]
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
    trading_product: str = "spot",
    registry_direction_by_code: dict[str, str] | None = None,
    direction_filter: bool = True,
) -> tuple[float, dict[str, Any]]:
    cal = calibration or {}
    if direction_filter:
        counted, excluded = filter_confluences_for_direction(
            confluences, direction, registry_direction_by_code,
        )
    else:
        counted, excluded = list(confluences), []
    count = effective_confluence_count(counted)
    qf = quality_factor(counted)
    rf = regime_factor(regime, cal, direction=direction, trading_product=trading_product)
    base_n = _conf_base_table_value(count, cal)
    base = max(0.0, min(1.0, base_n * qf * rf))
    meta: dict[str, Any] = {
        "confluence_count": count,
        "confluences_counted": list(counted),
        "confluence_count_raw": len(confluences),
        "extended_confluence_weight": 1.0,
        "quality_factor": qf,
        "regime_factor": rf,
        "conf_base_table_value": base_n,
        "confidence_base_computed": base,
    }
    if excluded:
        meta["confluences_excluded_direction"] = list(excluded)
    return base, meta


def apply_server_confidence(
    decision: DecisorOutput,
    *,
    calibration: dict[str, Any] | None = None,
    confluences_dropped: list[str] | None = None,
    trading_product: str = "spot",
    registry_direction_by_code: dict[str, str] | None = None,
) -> tuple[DecisorOutput, dict[str, Any]]:
    direction = direction_for_action(decision.action)
    if direction is None and decision.action == DecisorAction.HOLD:
        direction = hold_signal_direction(decision.regime, trading_product)
    base, meta = compute_confidence_base(
        decision.confluences,
        decision.regime,
        calibration,
        direction=direction,
        trading_product=trading_product,
        registry_direction_by_code=registry_direction_by_code,
    )
    if decision.action == DecisorAction.HOLD and direction is not None:
        meta["hold_signal_direction"] = direction.value
    if confluences_dropped:
        meta["confluences_dropped"] = list(confluences_dropped)
    if meta.get("confluences_excluded_direction"):
        inflated_base, _ = compute_confidence_base(
            decision.confluences,
            decision.regime,
            calibration,
            direction=direction,
            trading_product=trading_product,
            registry_direction_by_code=registry_direction_by_code,
            direction_filter=False,
        )
        meta["confidence_base_inflated"] = inflated_base
    payload = decision.model_dump()
    max_adj = float((calibration or {}).get("subjective_adj_max", 0.10))
    adj = clamp_subjective_adjustment(decision.confidence_adjustment, max_adj)
    payload["confidence_base"] = base
    payload["confidence_llm_factor"] = decision.confidence_llm_factor
    payload["confidence_adjustment"] = adj
    updated = DecisorOutput.model_validate(payload)
    meta["confidence_llm_factor"] = updated.confidence_llm_factor
    meta["confidence_adjustment"] = updated.confidence_adjustment
    meta["subjective_adj_max"] = max_adj
    meta["confidence"] = updated.confidence
    return updated, meta
