"""Tests for supervisor config window / auto-apply keys."""
from shared.config_store import ConfigKey, DEFAULTS


def test_supervisor_config_keys_defaults():
    assert ConfigKey.SUPERVISOR_CONFIG_WINDOW_HOURS in DEFAULTS
    assert DEFAULTS[ConfigKey.SUPERVISOR_CONFIG_WINDOW_HOURS].value == "168"
    assert DEFAULTS[ConfigKey.SUPERVISOR_CONFIG_AUTO_APPLY].value == "false"
    assert DEFAULTS[ConfigKey.SUPERVISOR_CONFIG_MIN_EVALUATED].value == "30"
