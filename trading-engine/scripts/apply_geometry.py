"""Aplica la geometría de riesgo elegida por el sweep a la DB.

Idempotente: escribe vía ConfigStore.set (queda en config_history).

Usage (desde el dir trading-engine/):
    python -m scripts.apply_geometry --sl 1.5 --rr 2.5 --max-pos 0.05
"""
from __future__ import annotations

import argparse
import asyncio
import os

from shared.config_store import ConfigKey, ConfigStore
from shared.db.base import create_engine_from_url, create_session_factory

CHANGED_BY = "p0-geometry"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sl",
        type=float,
        required=True,
        help="SL ATR multiplier (min). Ej: 1.5",
    )
    parser.add_argument(
        "--rr",
        type=float,
        required=True,
        help="Min reward/risk ratio. Ej: 2.5",
    )
    parser.add_argument(
        "--sl-max",
        type=float,
        default=2.5,
        help="SL ATR max multiplier (default: 2.5)",
    )
    parser.add_argument(
        "--max-pos",
        type=float,
        default=0.05,
        help="Max position size as fraction of capital (default: 0.05 = 5%%)",
    )
    return parser.parse_args()


def _get_database_url() -> str:
    """Resolve DATABASE_URL from environment or fall back to config.py settings.

    Importing get_settings() requires all API keys to be present, which is
    unnecessary for a DB-only script. We read DATABASE_URL from the environment
    directly and only fall back to config.py when all keys are available.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        from config import get_settings  # noqa: PLC0415
        return get_settings().database_url
    except Exception as exc:
        raise SystemExit(
            "DATABASE_URL not set and config.py could not be loaded: "
            f"{exc}\n\n"
            "Set DATABASE_URL before running, e.g.:\n"
            "  DATABASE_URL=postgresql+asyncpg://trader@localhost:5532/crypto_ai_trading "
            "python -m scripts.apply_geometry --sl 1.5 --rr 2.5 --max-pos 0.05"
        ) from exc


async def _run(sl: float, rr: float, sl_max: float, max_pos: float) -> None:
    database_url = _get_database_url()
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)

    # Invariante: default_rr >= min_rr
    default_rr = max(rr, 2.5)

    params = {
        ConfigKey.SL_ATR_MULTIPLIER: str(sl),
        ConfigKey.MIN_RR_RATIO: str(rr),
        ConfigKey.SL_ATR_MAX_MULTIPLIER: str(sl_max),
        ConfigKey.DEFAULT_RR_RATIO: str(default_rr),
        ConfigKey.MAX_POSITION_PCT: str(max_pos),
    }

    print(f"Aplicando geometría de riesgo (changed_by={CHANGED_BY!r}):")
    for key, value in params.items():
        print(f"  {key.value} = {value}")

    async with session_factory() as session:
        store = ConfigStore(session)
        for key, value in params.items():
            await store.set(key, value, changed_by=CHANGED_BY)

    print("\nValores escritos. Leyendo de vuelta desde la DB:")
    async with session_factory() as session:
        store = ConfigStore(session)
        for key in params:
            value = await store.get(key)
            print(f"  {key.value} = {value!r}")

    await engine.dispose()
    print("\nListo. Geometría aplicada con éxito.")


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(
        sl=args.sl,
        rr=args.rr,
        sl_max=args.sl_max,
        max_pos=args.max_pos,
    ))


if __name__ == "__main__":
    main()
