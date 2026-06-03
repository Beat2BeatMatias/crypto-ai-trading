"""Tests for operational profile and confluence TF alignment helpers."""
from __future__ import annotations

import pytest

from agents.labelers import (
    CANONICAL_TIMEFRAMES,
    confluence_verification_tfs,
    critical_indicator_keys,
    format_confluence_tf_hierarchy,
    normalize_operational_timeframe,
    operational_atr_from_indicators,
)


def test_normalize_10m_scalping_prefers_5m():
    assert normalize_operational_timeframe(
        "10m", profile="SCALPING", available=frozenset(CANONICAL_TIMEFRAMES),
    ) == "5m"


def test_normalize_10m_hibrido_prefers_15m():
    assert normalize_operational_timeframe(
        "10m", profile="HIBRIDO", available=frozenset(CANONICAL_TIMEFRAMES),
    ) == "15m"


def test_confluence_verification_tfs_scalping_5m():
    primary, secondary, structural = confluence_verification_tfs(
        "5m", "SCALPING",
    )
    assert primary == "5m"
    assert secondary == "15m"
    assert structural == "1h"


def test_confluence_verification_tfs_hibrido_15m():
    primary, secondary, structural = confluence_verification_tfs(
        "15m", "HIBRIDO",
    )
    assert primary == "15m"
    assert secondary == "1h"
    assert structural == "1h"


def test_confluence_verification_tfs_day_trading_1h():
    primary, secondary, structural = confluence_verification_tfs(
        "1h", "DAY_TRADING",
    )
    assert primary == "1h"
    assert secondary == "4h"
    assert structural == "1h"


def test_critical_indicator_keys_dedupes_when_primary_is_1h():
    keys = critical_indicator_keys("1h", "1h")
    assert ("1h", "rsi") in keys
    assert ("1h", "macd") in keys
    assert keys.count(("1h", "rsi")) == 1


def test_format_confluence_tf_hierarchy_notes_10m_mapping():
    text = format_confluence_tf_hierarchy(
        atr_timeframe="10m",
        atr_operational_tf="5m",
        primary_tf="5m",
        secondary_tf="15m",
        structural_tf="1h",
    )
    assert "10m" in text
    assert "5m" in text
    assert "15m" in text


def test_operational_atr_from_indicators_10m_scalping_uses_5m():
    ind = {"5m": {"atr": 188.0}, "15m": {"atr": 289.0}}
    tf, val = operational_atr_from_indicators(
        ind, atr_timeframe="10m", decisor_interval_min=5,
    )
    assert tf == "5m"
    assert val == 188.0


def test_operational_atr_from_indicators_missing_5m_bucket_uses_15m():
    ind = {"15m": {"atr": 289.0}}
    tf, val = operational_atr_from_indicators(
        ind, atr_timeframe="10m", decisor_interval_min=5,
    )
    assert tf == "15m"
    assert val == 289.0
