"""Runtime configuration store: read/write key-value config from Postgres."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import ConfigEntry, ConfigHistory


class ConfigKey(str, Enum):
    MODE = "mode"
    MAX_POSITION_PCT = "max_position_pct"
    MAX_SIMULTANEOUS_TRADES = "max_simultaneous_trades"
    DAILY_STOP_PCT = "daily_stop_pct"
    MAX_DRAWDOWN_PCT = "max_drawdown_pct"
    MAX_SLIPPAGE_PCT = "max_slippage_pct"
    DEFAULT_RR_RATIO = "default_rr_ratio"
    DECISOR_INTERVAL_MIN = "decisor_interval_min"
    SUPERVISOR_CRON = "supervisor_cron"
    DECISOR_PROVIDER = "decisor_provider"
    SUPERVISOR_PROVIDER = "supervisor_provider"
    FALLBACK_PROVIDERS = "fallback_providers"
    SUPERVISOR_FALLBACK_PROVIDERS = "supervisor_fallback_providers"
    LLM_MAX_RETRIES = "llm_max_retries"
    LLM_TIMEOUT_SEC = "llm_timeout_sec"
    ORDERBOOK_LEVELS = "orderbook_levels"
    KILL_SWITCH = "kill_switch"
    SUPERVISOR_RUN_NOW = "supervisor_run_now"
    ATR_TIMEFRAME = "atr_timeframe"
    MIN_RR_RATIO = "min_rr_ratio"
    SL_ATR_MULTIPLIER = "sl_atr_multiplier"


@dataclass(frozen=True)
class _Default:
    value: str
    value_type: str
    description: str


DEFAULTS: dict[ConfigKey, _Default] = {
    ConfigKey.MODE: _Default("PAPER_TRADING", "string", "PAPER_TRADING or LIVE"),
    ConfigKey.MAX_POSITION_PCT: _Default("0.10", "float", "Max % capital per trade"),
    ConfigKey.MAX_SIMULTANEOUS_TRADES: _Default("2", "int", "Max concurrent open positions"),
    ConfigKey.DAILY_STOP_PCT: _Default("-0.03", "float", "Daily P&L stop"),
    ConfigKey.MAX_DRAWDOWN_PCT: _Default("-0.10", "float", "Total drawdown limit"),
    ConfigKey.MAX_SLIPPAGE_PCT: _Default("0.003", "float", "Max acceptable slippage"),
    ConfigKey.DEFAULT_RR_RATIO: _Default("2.0", "float", "Default take-profit ratio"),
    ConfigKey.DECISOR_INTERVAL_MIN: _Default("5", "int", "Decisor frequency in minutes"),
    ConfigKey.SUPERVISOR_CRON: _Default("0 0 * * *", "string", "Supervisor schedule (UTC)"),
    ConfigKey.DECISOR_PROVIDER: _Default(
        "groq-llama-3.3-70b", "string",
        "Primary LLM for decisor. Options: groq-llama-3.3-70b | groq-compound-beta | gemini-2.5-flash",
    ),
    ConfigKey.SUPERVISOR_PROVIDER: _Default(
        "gemini-2.5-pro", "string",
        "LLM for supervisor. Options: gemini-2.5-pro | groq-llama-3.3-70b | groq-compound-beta",
    ),
    ConfigKey.FALLBACK_PROVIDERS: _Default(
        "gemini-2.5-flash,groq-llama-4-scout,groq-gpt-oss-120b,groq-qwen3-32b,groq-llama-3.1-8b",
        "string",
        "Cascada de fallback para decisor (CSV ordenado). Opciones: gemini-2.5-flash | groq-llama-3.3-70b | groq-compound-beta | groq-compound-mini | groq-llama-4-scout | groq-gpt-oss-120b | groq-gpt-oss-20b | groq-qwen3-32b | groq-llama-3.1-8b",
    ),
    ConfigKey.SUPERVISOR_FALLBACK_PROVIDERS: _Default(
        "groq-llama-3.3-70b,groq-llama-4-scout,groq-gpt-oss-120b,gemini-2.5-flash",
        "string",
        "Cascada de fallback para supervisor (CSV ordenado). Mismas opciones que fallback_providers",
    ),
    ConfigKey.LLM_MAX_RETRIES: _Default("3", "int", "Retries on LLM failure"),
    ConfigKey.LLM_TIMEOUT_SEC: _Default("30", "int", "LLM call timeout"),
    ConfigKey.ORDERBOOK_LEVELS: _Default("10", "int", "Order book depth in context"),
    ConfigKey.KILL_SWITCH: _Default("false", "bool", "Emergency stop"),
    ConfigKey.SUPERVISOR_RUN_NOW: _Default("false", "bool", "internal: manual supervisor trigger"),
    ConfigKey.ATR_TIMEFRAME: _Default(
        "15m", "string",
        "Timeframe del ATR usado como referencia para SL/TP. Opciones: 5m | 15m | 1h",
    ),
    ConfigKey.MIN_RR_RATIO: _Default(
        "1.3", "float",
        "R:R mínimo requerido para aprobar un BUY (reward / risk). Ej: 1.3 = ganar 1.3x lo que se arriesga",
    ),
    ConfigKey.SL_ATR_MULTIPLIER: _Default(
        "0.3", "float",
        "Multiplicador del ATR para SL mínimo. Ej: 0.3 → SL debe estar al menos 0.3×ATR abajo del precio",
    ),
}


class ConfigStore:
    """Async helper around the config and config_history tables."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed_defaults(self) -> None:
        """Insert default rows for any missing key. Idempotent."""
        existing = {
            r.key for r in (await self.session.execute(select(ConfigEntry))).scalars().all()
        }
        for key, default in DEFAULTS.items():
            if key.value in existing:
                continue
            self.session.add(
                ConfigEntry(
                    key=key.value,
                    value=default.value,
                    value_type=default.value_type,
                    description=default.description,
                    updated_at=datetime.now(tz=timezone.utc),
                )
            )
        await self.session.commit()

    async def get(self, key: ConfigKey) -> str:
        row = await self.session.get(ConfigEntry, key.value)
        if row is None:
            raise KeyError(f"Config key not found: {key.value}")
        return row.value

    async def get_typed(self, key: ConfigKey) -> Any:
        """Return value cast to the type recorded in value_type."""
        row = await self.session.get(ConfigEntry, key.value)
        if row is None:
            raise KeyError(f"Config key not found: {key.value}")
        return _cast(row.value, row.value_type)

    async def set(self, key: ConfigKey, value: str, *, changed_by: str = "system") -> None:
        row = await self.session.get(ConfigEntry, key.value)
        if row is None:
            raise KeyError(f"Cannot set unknown key: {key.value}")
        old_value = row.value
        row.value = value
        row.updated_at = datetime.now(tz=timezone.utc)
        self.session.add(
            ConfigHistory(
                id=uuid.uuid4(),
                ts=datetime.now(tz=timezone.utc),
                key=key.value,
                old_value=old_value,
                new_value=value,
                changed_by=changed_by,
            )
        )
        await self.session.commit()


def _cast(value: str, value_type: str) -> Any:
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return value.lower() in ("true", "1", "yes")
    if value_type == "json":
        return json.loads(value)
    return value
