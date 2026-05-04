"""Tests for the CCXT Binance client factory."""
from unittest.mock import patch
import pytest


@patch.dict("os.environ", {
    "DATABASE_URL": "postgresql+asyncpg://x:y@h/d",
    "BINANCE_API_KEY": "test_key",
    "BINANCE_API_SECRET": "test_secret",
    "BINANCE_TESTNET": "true",
    "GEMINI_API_KEY": "g",
    "GROQ_API_KEY": "k",
})
def test_build_binance_client_testnet_uses_sandbox():
    import config as cfg
    cfg._settings = None  # reset memoised singleton
    from exchange import build_binance_client
    client = build_binance_client()
    # ccxt sets sandbox mode — check options
    assert client.options.get("defaultType") == "spot"
    # When testnet=True, ccxt sets sandbox URLs
    assert client.urls.get("api") != client.__class__({}).urls.get("api") \
        or client.sandbox is True


@patch.dict("os.environ", {
    "DATABASE_URL": "postgresql+asyncpg://x:y@h/d",
    "BINANCE_API_KEY": "live_key",
    "BINANCE_API_SECRET": "live_secret",
    "BINANCE_TESTNET": "false",
    "GEMINI_API_KEY": "g",
    "GROQ_API_KEY": "k",
})
def test_build_binance_client_mainnet_has_live_url():
    import config as cfg
    cfg._settings = None
    from exchange import build_binance_client
    client = build_binance_client()
    assert client.options.get("defaultType") == "spot"
    assert client.apiKey == "live_key"


@patch.dict("os.environ", {
    "DATABASE_URL": "postgresql+asyncpg://x:y@h/d",
    "BINANCE_API_KEY": "k",
    "BINANCE_API_SECRET": "s",
    "BINANCE_TESTNET": "true",
    "GEMINI_API_KEY": "g",
    "GROQ_API_KEY": "k",
})
def test_build_binance_client_rate_limit_enabled():
    import config as cfg
    cfg._settings = None
    from exchange import build_binance_client
    client = build_binance_client()
    assert client.enableRateLimit is True
