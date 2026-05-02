"""CCXT async Binance Spot client factory."""
from __future__ import annotations

import ccxt.async_support as ccxt_async

from config import get_settings


def build_binance_client() -> ccxt_async.binance:
    """Return a configured async CCXT Binance client.

    Uses testnet when BINANCE_TESTNET=true (default in .env.example).
    Always uses spot defaultType and rate limiting.
    """
    s = get_settings()
    client = ccxt_async.binance({
        "apiKey": s.binance_api_key,
        "secret": s.binance_api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot", "fetchCurrencies": False},
    })
    if s.binance_testnet:
        client.set_sandbox_mode(True)
    return client
