"""Tests for decisor LLM temperature, self-consistency, and risk sizing config keys."""
from __future__ import annotations

from shared.config_store import ConfigKey, DEFAULTS


def test_decisor_llm_keys_in_enum():
    assert ConfigKey.DECISOR_LLM_TEMPERATURE.value == "decisor_llm_temperature"
    assert ConfigKey.DECISOR_SELF_CONSISTENCY_N.value == "decisor_self_consistency_n"
    assert ConfigKey.RISK_PER_TRADE_PCT.value == "risk_per_trade_pct"


def test_decisor_llm_keys_have_defaults():
    cases = [
        (ConfigKey.DECISOR_LLM_TEMPERATURE, "0.1", "float"),
        (ConfigKey.DECISOR_SELF_CONSISTENCY_N, "0", "int"),
        (ConfigKey.RISK_PER_TRADE_PCT, "0.005", "float"),
    ]
    for key, expected_value, expected_type in cases:
        assert key in DEFAULTS
        d = DEFAULTS[key]
        assert d.value == expected_value
        assert d.value_type == expected_type
