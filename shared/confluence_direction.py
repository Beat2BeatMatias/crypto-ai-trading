"""Etiquetas direccionales [LONG]/[SHORT]/[AMBOS] en confluencias promovidas (K–Z)."""
from __future__ import annotations

import re
from typing import Literal

RegistryDirection = Literal["LONG", "SHORT", "BOTH"]

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
