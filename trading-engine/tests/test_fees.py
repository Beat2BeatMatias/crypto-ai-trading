"""Tests del fee round-trip efectivo (piso en paper)."""
from __future__ import annotations
from risk.fees import effective_roundtrip_fee_pct

def test_effective_fee_uses_real_when_above_floor():
    # taker 0.075% -> round-trip 0.15% > floor 0.10% -> usa el real
    assert effective_roundtrip_fee_pct(taker_fee=0.00075, floor_pct=0.10) == 0.15

def test_effective_fee_applies_floor_when_real_is_zero():
    # testnet: taker 0 -> round-trip 0 -> usa el floor
    assert effective_roundtrip_fee_pct(taker_fee=0.0, floor_pct=0.20) == 0.20

def test_effective_fee_floor_zero_means_real_only():
    assert effective_roundtrip_fee_pct(taker_fee=0.0, floor_pct=0.0) == 0.0
