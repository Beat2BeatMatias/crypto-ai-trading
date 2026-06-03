"""Fetch USDT-M futures wallet balance via CCXT (for dashboard when engine snapshot lacks margin)."""
from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()


async def fetch_futures_margin_balance() -> dict[str, float] | None:
    """
    Returns available, total, locked USDT in the futures wallet, or None if keys missing / fetch fails.
    """
    api_key = os.environ.get("BINANCE_API_KEY", "").strip()
    api_secret = os.environ.get("BINANCE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return None

    testnet = os.environ.get("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")

    import ccxt.async_support as ccxt_async

    client: Any = ccxt_async.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future", "fetchCurrencies": False},
    })
    if testnet:
        client.set_sandbox_mode(True)

    try:
        bal = await client.fetch_balance()
        usdt = bal.get("USDT") or {}
        available = float(usdt.get("free") or 0.0)
        total = float(usdt.get("total") or 0.0)
        used = float(usdt.get("used") or 0.0)
        locked = max(0.0, used if used > 0 else total - available)
        return {
            "available_margin": available,
            "margin_balance": total,
            "margin_locked": locked,
        }
    except Exception as e:
        logger.warning("balance.futures_fetch_failed", error=str(e))
        return None
    finally:
        await client.close()
