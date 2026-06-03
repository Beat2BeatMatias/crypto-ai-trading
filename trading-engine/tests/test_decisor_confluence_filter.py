"""Tests for decisor confluence code filtering (catálogo fijo A–J + registry K–Z)."""
from agents.decisor import _filter_confluence_codes


def test_filter_accepts_static_catalog_a_through_j():
    result = _filter_confluence_codes(["A", "B", "I", "J"], frozenset())
    assert result == ["A", "B", "I", "J"]


def test_filter_accepts_static_and_promoted_registry_codes():
    active = frozenset({"K", "L"})
    result = _filter_confluence_codes(["B", "I", "K", "X", "Z"], active)
    assert result == ["B", "I", "K"]


def test_filter_drops_codes_outside_catalog_and_inactive_registry():
    result = _filter_confluence_codes(["B", "K", "X", "Z"], frozenset())
    assert result == ["B"]
