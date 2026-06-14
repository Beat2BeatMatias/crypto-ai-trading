"""Etiquetas direccionales [LONG]/[SHORT]/[AMBOS] en confluencias promovidas (Q–Z)."""
from __future__ import annotations

import re
from typing import Literal

from shared.schemas import DecisorAction, DecisorOutput, Direction, MarketRegime, direction_for_action

RegistryDirection = Literal["LONG", "SHORT", "BOTH"]

STATIC_LONG_CODES = frozenset("ABCDEFGH")
STATIC_SHORT_CODES = frozenset("IJKLMNOP")
STATIC_BOTH_CODES = frozenset({"F"})
CONFIDENCE_JUMP_OPPOSE_THRESHOLD = 0.15

_DIRECTION_TAG_RE = re.compile(
    r"\[(LONG|SHORT|AMBOS|BOTH)\]",
    re.IGNORECASE,
)

_DIRECTION_LABEL_ES: dict[RegistryDirection, str] = {
    "LONG": "solo BUY",
    "SHORT": "solo SHORT",
    "BOTH": "BUY y SHORT",
}


def parse_registry_direction(definition_md: str) -> RegistryDirection | None:
    """Primera etiqueta explícita en definition_md; AMBOS/BOTH o LONG+SHORT → BOTH."""
    tags = [m.group(1).upper() for m in _DIRECTION_TAG_RE.finditer(definition_md or "")]
    if not tags:
        return None
    if any(t in ("AMBOS", "BOTH") for t in tags):
        return "BOTH"
    has_long = "LONG" in tags
    has_short = "SHORT" in tags
    if has_long and has_short:
        return "BOTH"
    if has_short:
        return "SHORT"
    if has_long:
        return "LONG"
    return None


def direction_label_es(direction: RegistryDirection | None) -> str:
    if direction is None:
        return "sin etiqueta (inferir de la definición)"
    return _DIRECTION_LABEL_ES[direction]


def registry_direction_allows_action(
    direction: RegistryDirection | None,
    action: str,
    *,
    trading_product: str = "spot",
) -> bool:
    """True si la confluencia promovida puede citarse con esta action."""
    if direction is None:
        return True
    act = (action or "HOLD").upper()
    if act in ("HOLD", "SELL"):
        return True
    if act == "BUY":
        return direction in ("LONG", "BOTH")
    if act == "SHORT":
        if trading_product != "futures":
            return False
        return direction in ("SHORT", "BOTH")
    return True


def static_confluence_direction(code: str) -> RegistryDirection | None:
    if code in STATIC_BOTH_CODES:
        return "BOTH"
    if code in STATIC_LONG_CODES:
        return "LONG"
    if code in STATIC_SHORT_CODES:
        return "SHORT"
    return None


def confluence_effective_direction(
    code: str,
    registry_direction_by_code: dict[str, RegistryDirection] | None = None,
) -> RegistryDirection | None:
    static = static_confluence_direction(code)
    if static is not None:
        return static
    if registry_direction_by_code and code in registry_direction_by_code:
        return registry_direction_by_code[code]
    return None


def classify_confluences_by_direction(
    confluences: list[str],
    registry_direction_by_code: dict[str, RegistryDirection] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Clasifica códigos en alcistas, bajistas y neutrales (sin etiqueta / BOTH)."""
    long_codes: list[str] = []
    short_codes: list[str] = []
    neutral_codes: list[str] = []
    for code in confluences:
        direction = confluence_effective_direction(code, registry_direction_by_code)
        if direction == "LONG":
            long_codes.append(code)
        elif direction == "SHORT":
            short_codes.append(code)
        else:
            neutral_codes.append(code)
    return long_codes, short_codes, neutral_codes


def has_opposing_confluence_mix(
    confluences: list[str],
    registry_direction_by_code: dict[str, RegistryDirection] | None = None,
) -> bool:
    long_codes, short_codes, _ = classify_confluences_by_direction(
        confluences, registry_direction_by_code,
    )
    return bool(long_codes) and bool(short_codes)


def implied_signal_direction(
    decision: DecisorOutput,
    *,
    trading_product: str = "spot",
) -> Direction | None:
    direction = direction_for_action(decision.action)
    if direction is not None:
        return direction
    if decision.action != DecisorAction.HOLD:
        return None
    key = (
        decision.regime.value
        if isinstance(decision.regime, MarketRegime)
        else str(decision.regime)
    )
    if key == "TRENDING_DOWN" and trading_product == "futures":
        return Direction.SHORT
    if key == "TRENDING_UP":
        return Direction.LONG
    return None


def filter_confluences_for_direction(
    confluences: list[str],
    direction: Direction | None,
    registry_direction_by_code: dict[str, RegistryDirection] | None = None,
) -> tuple[list[str], list[str]]:
    if direction is None:
        return list(confluences), []
    kept: list[str] = []
    dropped: list[str] = []
    for code in confluences:
        eff = confluence_effective_direction(code, registry_direction_by_code)
        if eff is None or eff == "BOTH":
            kept.append(code)
        elif direction == Direction.LONG and eff == "LONG":
            kept.append(code)
        elif direction == Direction.SHORT and eff == "SHORT":
            kept.append(code)
        else:
            dropped.append(code)
    return kept, dropped


def resolve_opposing_confluences(
    confluences: list[str],
    *,
    direction: Direction | None,
    registry_direction_by_code: dict[str, RegistryDirection] | None = None,
) -> tuple[list[str], list[str]]:
    if not has_opposing_confluence_mix(confluences, registry_direction_by_code):
        return list(confluences), []
    return filter_confluences_for_direction(
        confluences, direction, registry_direction_by_code,
    )


def detect_confidence_jump_opposing_mix(
    *,
    current_confidence: float,
    previous_confidence: float,
    confluences: list[str],
    registry_direction_by_code: dict[str, RegistryDirection] | None = None,
    threshold: float = CONFIDENCE_JUMP_OPPOSE_THRESHOLD,
    inflated_confidence: float | None = None,
) -> dict[str, object] | None:
    if not has_opposing_confluence_mix(confluences, registry_direction_by_code):
        return None
    long_codes, short_codes, _ = classify_confluences_by_direction(
        confluences, registry_direction_by_code,
    )
    actual_delta = current_confidence - previous_confidence
    inflated_delta = (
        (inflated_confidence - previous_confidence)
        if inflated_confidence is not None
        else actual_delta
    )
    if actual_delta <= threshold and inflated_delta <= threshold:
        return None
    return {
        "alert": "confidence_jump_opposing_mix",
        "delta": round(actual_delta, 4),
        "inflated_delta": round(inflated_delta, 4),
        "threshold": threshold,
        "previous_confidence": round(previous_confidence, 4),
        "current_confidence": round(current_confidence, 4),
        "inflated_confidence": (
            round(inflated_confidence, 4) if inflated_confidence is not None else None
        ),
        "long_codes": long_codes,
        "short_codes": short_codes,
    }


def registry_direction_by_code(
    entries: list,
    *,
    static_codes: frozenset[str],
) -> dict[str, RegistryDirection]:
    """Mapa code → dirección para entradas promovidas activas (excluye catálogo fijo)."""
    out: dict[str, RegistryDirection] = {}
    for entry in entries:
        if not getattr(entry, "active", True):
            continue
        code = entry.code
        if code in static_codes:
            continue
        parsed = parse_registry_direction(entry.definition_md or "")
        if parsed is not None:
            out[code] = parsed
    return out
