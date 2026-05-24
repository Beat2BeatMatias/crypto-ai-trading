"""Tests for decisor confluence code filtering with extended registry."""
from agents.decisor import _filter_confluence_codes


def test_filter_accepts_static_and_registry_codes():
    active = frozenset({"I", "J"})
    result = _filter_confluence_codes(["B", "I", "X", "Z"], active)
    assert result == ["B", "I"]


def test_filter_without_registry_only_static():
    result = _filter_confluence_codes(["B", "I"], frozenset())
    assert result == ["B"]
