"""Fee round-trip efectivo: aplica un piso para que R10 valide en testnet."""
from __future__ import annotations


def effective_roundtrip_fee_pct(*, taker_fee: float, floor_pct: float) -> float:
    """Round-trip fee en puntos porcentuales.

    taker_fee: fracción por lado (0.001 = 0.1%).
    floor_pct: piso del round-trip en puntos % (0.20 = 0.20%).
    Devuelve max(round-trip real, piso).
    """
    real_roundtrip_pct = taker_fee * 2 * 100
    return max(real_roundtrip_pct, floor_pct)
