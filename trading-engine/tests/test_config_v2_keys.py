"""Tests for the 6 new decisor-v2 ConfigKey entries."""
from __future__ import annotations
import pytest
from shared.config_store import ConfigKey, DEFAULTS


def test_new_keys_present_in_enum():
    assert ConfigKey.MIN_FEES_TO_TP_RATIO.value == "min_fees_to_tp_ratio"
    assert ConfigKey.MIN_CONFLUENCES_BUY.value == "min_confluences_buy"
    assert ConfigKey.MIN_CONFLUENCES_SHORT.value == "min_confluences_short"
    assert ConfigKey.COOLDOWN_AFTER_SELL_MIN.value == "cooldown_after_sell_min"
    assert ConfigKey.SUBJECTIVE_ADJ_MAX.value == "subjective_adj_max"
    assert ConfigKey.EXPECTED_HOLDING_MAX_MIN.value == "expected_holding_max_min"
    assert ConfigKey.CONFLUENCE_WEAK_FACTOR.value == "confluence_weak_factor"


def test_conf_threshold_short_keys_present():
    assert ConfigKey.CONF_THRESHOLD_SHORT_TRENDING_DOWN.value == "conf_threshold_short_trending_down"
    assert ConfigKey.CONF_THRESHOLD_SHORT_RANGE.value == "conf_threshold_short_range"
    assert ConfigKey.CONF_THRESHOLD_SHORT_HIGH_VOL.value == "conf_threshold_short_high_vol"


def test_new_keys_have_defaults():
    cases = [
        (ConfigKey.MIN_FEES_TO_TP_RATIO, "3.0", "float"),
        (ConfigKey.MIN_CONFLUENCES_BUY, "2", "int"),
        (ConfigKey.MIN_CONFLUENCES_SHORT, "2", "int"),
        (ConfigKey.CONF_THRESHOLD_SHORT_TRENDING_DOWN, "0.60", "float"),
        (ConfigKey.CONF_THRESHOLD_SHORT_RANGE, "0.70", "float"),
        (ConfigKey.CONF_THRESHOLD_SHORT_HIGH_VOL, "0.80", "float"),
        (ConfigKey.COOLDOWN_AFTER_SELL_MIN, "15", "int"),
        (ConfigKey.SUBJECTIVE_ADJ_MAX, "0.10", "float"),
        (ConfigKey.EXPECTED_HOLDING_MAX_MIN, "240", "int"),
        (ConfigKey.CONFLUENCE_WEAK_FACTOR, "0.5", "float"),
    ]
    for key, expected_value, expected_type in cases:
        assert key in DEFAULTS, f"Missing default for {key}"
        d = DEFAULTS[key]
        assert d.value == expected_value, f"{key}: expected value {expected_value!r}, got {d.value!r}"
        assert d.value_type == expected_type, f"{key}: expected type {expected_type!r}, got {d.value_type!r}"
