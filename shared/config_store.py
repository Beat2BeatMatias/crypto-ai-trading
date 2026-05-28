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
    # SL range
    SL_ATR_MAX_MULTIPLIER = "sl_atr_max_multiplier"
    # Confidence thresholds by regime
    CONF_THRESHOLD_TRENDING_UP = "conf_threshold_trending_up"
    CONF_THRESHOLD_RANGE = "conf_threshold_range"
    CONF_THRESHOLD_HIGH_VOL = "conf_threshold_high_vol"
    # RSI overbought filter
    RSI_OVERBOUGHT_1H = "rsi_overbought_1h"
    # Confidence formula — base confluencias table
    CONF_BASE_0 = "conf_base_0"
    CONF_BASE_1 = "conf_base_1"
    CONF_BASE_2 = "conf_base_2"
    CONF_BASE_3 = "conf_base_3"
    CONF_BASE_4PLUS = "conf_base_4plus"
    # Confidence formula — peso regime (guía para el LLM, no enforcement)
    PESO_REGIME_RANGE = "peso_regime_range"
    PESO_REGIME_HIGH_VOL = "peso_regime_high_vol"
    # Confidence formula — ajustes (guía para el LLM, no enforcement)
    ADJ_VOLUME_BOOST = "adj_volume_boost"
    ADJ_VOLUME_RATIO = "adj_volume_ratio"
    ADJ_SPREAD_PENALTY = "adj_spread_penalty"
    ADJ_SPREAD_THRESHOLD_PCT = "adj_spread_threshold_pct"
    # Decisor v2 — operational parameters
    MIN_FEES_TO_TP_RATIO = "min_fees_to_tp_ratio"
    MIN_CONFLUENCES_BUY = "min_confluences_buy"
    COOLDOWN_AFTER_SELL_MIN = "cooldown_after_sell_min"
    SUBJECTIVE_ADJ_MAX = "subjective_adj_max"
    EXPECTED_HOLDING_MAX_MIN = "expected_holding_max_min"
    CONFLUENCE_WEAK_FACTOR = "confluence_weak_factor"
    DRAWDOWN_RESET_TS = "drawdown_reset_ts"
    ENGINE_PAUSED = "engine_paused"
    ENGINE_PAUSE_REASON = "engine_pause_reason"
    # Supervisor — fase de ratificación del playbook (01-functional-spec §F5.bis.5)
    MAX_PLAYBOOK_AGE_DAYS = "max_playbook_age_days"
    PLAYBOOK_FORCE_REGEN_WR_DELTA_PCT = "playbook_force_regen_wr_delta_pct"
    # Decisor LLM-centric (02-technical-spec §2.6)
    MIN_POSITION_SIZE = "min_position_size"
    COHERENCE_STRICT_MODE = "coherence_strict_mode"
    TWO_PASS_ENABLED = "two_pass_enabled"
    # Outcome Attribution job
    OUTCOME_ATTRIBUTION_INTERVAL_MIN = "outcome_attribution_interval_min"
    OUTCOME_ATTRIBUTION_HORIZON_MIN = "outcome_attribution_horizon_min"
    OUTCOME_ATTRIBUTION_WINDOW_HOURS = "outcome_attribution_window_hours"
    OUTCOME_COVERAGE_THRESHOLD_PCT = "outcome_coverage_threshold_pct"
    POSTMORTEM_ENABLED = "postmortem_enabled"
    POSTMORTEM_MAX_PER_TICK = "postmortem_max_per_tick"
    POSTMORTEM_PROVIDER = "postmortem_provider"
    POSTMORTEM_FALLBACK_PROVIDERS = "postmortem_fallback_providers"
    POSTMORTEM_INTERVAL_MIN = "postmortem_interval_min"
    BLOCK_K_MAX_LINES = "block_k_max_lines"
    BLOCK_K_WINDOW_HOURS = "block_k_window_hours"
    CONFLUENCE_PROMOTION_MIN_OCCURRENCES = "confluence_promotion_min_occurrences"
    CONFLUENCE_PROMOTION_WINDOW_DAYS = "confluence_promotion_window_days"
    CONFLUENCE_REGISTRY_MAX_ACTIVE = "confluence_registry_max_active"
    LIVE_SINCE_TS = "live_since_ts"


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
        "Primary LLM for decisor (chat). Options: groq-llama-3.3-70b | groq-compound-beta | groq-qwen3-32b* | groq-llama-4-scout | groq-gpt-oss-120b | gemini-2.5-flash  (* soporta reasoning_effort)",
    ),
    ConfigKey.SUPERVISOR_PROVIDER: _Default(
        "gemini-2.5-pro", "string",
        "LLM for supervisor (chat). Options: gemini-2.5-pro | groq-llama-3.3-70b | groq-compound-beta | groq-qwen3-32b* | groq-llama-4-scout | groq-gpt-oss-120b  (* soporta reasoning_effort)",
    ),
    ConfigKey.FALLBACK_PROVIDERS: _Default(
        "gemini-2.5-flash,groq-llama-4-scout,groq-gpt-oss-120b,groq-qwen3-32b,groq-llama-3.1-8b",
        "string",
        "Cascada de fallback para decisor (CSV ordenado). Opciones: gemini-2.5-flash | groq-llama-3.3-70b | groq-compound-beta | groq-compound-mini | groq-llama-4-scout | groq-gpt-oss-120b | groq-gpt-oss-20b | groq-qwen3-32b* | groq-llama-3.1-8b  (* soporta reasoning_effort)",
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
    ConfigKey.ATR_TIMEFRAME: _Default("15m", "string", "ATR reference timeframe. Options: 5m | 15m | 1h"),
    ConfigKey.MIN_RR_RATIO: _Default("1.3", "float", "Min reward/risk ratio for BUY approval"),
    ConfigKey.SL_ATR_MULTIPLIER: _Default("0.3", "float", "Min SL distance as ATR multiplier"),
    ConfigKey.SL_ATR_MAX_MULTIPLIER: _Default("1.5", "float", "Max SL distance as ATR multiplier"),
    ConfigKey.CONF_THRESHOLD_TRENDING_UP: _Default(
        "0.60", "float",
        "Guía LLM: confidence mínima recomendada para BUY en TRENDING_UP. No es enforcement; el LLM decide autónomamente y puede desviarse con justificación.",
    ),
    ConfigKey.CONF_THRESHOLD_RANGE: _Default(
        "0.70", "float",
        "Guía LLM: confidence mínima recomendada para BUY en RANGE. No es enforcement; el LLM decide autónomamente.",
    ),
    ConfigKey.CONF_THRESHOLD_HIGH_VOL: _Default(
        "0.80", "float",
        "Guía LLM: confidence mínima recomendada para BUY en HIGH_VOLATILITY. No es enforcement; el LLM decide autónomamente.",
    ),
    ConfigKey.RSI_OVERBOUGHT_1H: _Default(
        "70", "int",
        "Guía LLM/Supervisor: RSI 1h considerado sobrecomprado. El Supervisor puede sugerirlo; el Decisor lo usa como referencia contextual, no como bloqueo.",
    ),
    # conf_base_N: guías de referencia para la fórmula de confidence que el LLM usa internamente.
    # Aparecen en el system prompt del Decisor como calibración sugerida.
    # El LLM declara su propia confidence; el sistema no la recalcula ni la fuerza.
    ConfigKey.CONF_BASE_0: _Default(
        "0.40", "float",
        "Guía LLM: confidence_base de referencia con 0 confluencias activas. El LLM calibra su propia confidence usando esta escala como guía.",
    ),
    ConfigKey.CONF_BASE_1: _Default(
        "0.55", "float",
        "Guía LLM: confidence_base de referencia con 1 confluencia activa.",
    ),
    ConfigKey.CONF_BASE_2: _Default(
        "0.70", "float",
        "Guía LLM: confidence_base de referencia con 2 confluencias activas.",
    ),
    ConfigKey.CONF_BASE_3: _Default(
        "0.85", "float",
        "Guía LLM: confidence_base de referencia con 3 confluencias activas.",
    ),
    ConfigKey.CONF_BASE_4PLUS: _Default(
        "1.00", "float",
        "Guía LLM: confidence_base de referencia con 4+ confluencias activas (cap).",
    ),
    ConfigKey.PESO_REGIME_RANGE: _Default(
        "0.85", "float",
        "Guía LLM: factor de confianza base en régimen RANGE (0=conservador, 1=full). El LLM lo usa como referencia en su cálculo de confidence.",
    ),
    ConfigKey.PESO_REGIME_HIGH_VOL: _Default(
        "0.75", "float",
        "Guía LLM: factor de confianza base en régimen HIGH_VOLATILITY. Generalmente menor que RANGE para reflejar mayor riesgo.",
    ),
    ConfigKey.ADJ_VOLUME_BOOST: _Default(
        "0.05", "float",
        "Guía LLM: boost sugerido de confidence cuando el volumen supera adj_volume_ratio × la media. El LLM puede aplicarlo como confidence_adjustment.",
    ),
    ConfigKey.ADJ_VOLUME_RATIO: _Default(
        "1.5", "float",
        "Guía LLM: múltiplo del volumen medio 5m que activa el boost de confianza. Referencia para el LLM.",
    ),
    ConfigKey.ADJ_SPREAD_PENALTY: _Default(
        "-0.05", "float",
        "Guía LLM: penalización sugerida de confidence cuando el spread supera adj_spread_threshold_pct. Referencia para el LLM.",
    ),
    ConfigKey.ADJ_SPREAD_THRESHOLD_PCT: _Default(
        "0.05", "float",
        "Umbral de spread (% del precio) que el LLM interpreta como señal de mayor riesgo. Usado también en el system prompt del Decisor.",
    ),
    ConfigKey.MIN_FEES_TO_TP_RATIO: _Default(
        "3.0", "float",
        "Min TP movement as multiple of round-trip fees for BUY approval (R10). Range 1.5–6.0.",
    ),
    ConfigKey.MIN_CONFLUENCES_BUY: _Default(
        "2", "int",
        "Guía LLM: número mínimo de confluencias recomendado para BUY. Inyectado en el system prompt como regla de calidad; el LLM lo considera pero tiene autonomía final. Rango 1–4.",
    ),
    ConfigKey.COOLDOWN_AFTER_SELL_MIN: _Default(
        "15", "int",
        "Guía LLM: minutos de cooldown recomendados tras un SELL antes de una nueva entrada BUY. Inyectado en el system prompt; el LLM lo respeta como norma de calidad. Rango 0–120.",
    ),
    ConfigKey.SUBJECTIVE_ADJ_MAX: _Default(
        "0.10", "float",
        "Límite del confidence_adjustment subjetivo que el LLM puede declarar (±). Enforced por Pydantic. Rango 0.00–0.20.",
    ),
    ConfigKey.EXPECTED_HOLDING_MAX_MIN: _Default(
        "240", "int",
        "Tiempo máximo de holding esperado en minutos. Usado por el Supervisor para detección de trades zombie y por el CoherenceChecker (C6). Rango 30–1440.",
    ),
    ConfigKey.CONFLUENCE_WEAK_FACTOR: _Default(
        "0.5", "float",
        "Guía LLM: multiplicador aplicado a una confluencia débil vs una sólida al calibrar confidence. Referencia inyectada en el contexto; el LLM decide el peso real.",
    ),
    ConfigKey.DRAWDOWN_RESET_TS: _Default(
        "", "string",
        "ISO UTC timestamp of last drawdown peak reset. Empty = use full history.",
    ),
    ConfigKey.ENGINE_PAUSED: _Default(
        "false", "bool",
        "internal: motor pausado por circuit breaker. Se resetea al iniciar el engine.",
    ),
    ConfigKey.ENGINE_PAUSE_REASON: _Default(
        "", "string",
        "internal: razón de la última pausa del circuit breaker.",
    ),
    ConfigKey.MAX_PLAYBOOK_AGE_DAYS: _Default(
        "7", "int",
        "Edad máxima (días) del playbook activo antes de forzar regeneración por el Supervisor. Rango 1–30.",
    ),
    ConfigKey.PLAYBOOK_FORCE_REGEN_WR_DELTA_PCT: _Default(
        "15", "float",
        "Diferencia absoluta de win rate (puntos %) vs. baseline del playbook activo que fuerza regeneración. Rango 1–50.",
    ),
    ConfigKey.MIN_POSITION_SIZE: _Default(
        "0.005", "float",
        "Tamaño mínimo de posición en BTC para ejecutar un trade (piso de sizing). El LLM no puede proponer menos.",
    ),
    ConfigKey.COHERENCE_STRICT_MODE: _Default(
        "false", "bool",
        "Si true, warnings críticos de CoherenceChecker (C1/C2/C3) bloquean la decisión forzando HOLD. Default false (warnings sólo informativos).",
    ),
    ConfigKey.TWO_PASS_ENABLED: _Default(
        "true", "bool",
        "Si true, cuando el CoherenceChecker detecta warnings en C1/C2/C3 se hace una segunda llamada al LLM para auto-revisión.",
    ),
    ConfigKey.OUTCOME_ATTRIBUTION_INTERVAL_MIN: _Default(
        "60", "int",
        "Cada cuántos minutos corre el job de outcome attribution. Rango 15–240.",
    ),
    ConfigKey.OUTCOME_ATTRIBUTION_HORIZON_MIN: _Default(
        "240", "int",
        "Horizonte de evaluación contrafactual en minutos. "
        "Define cuántas velas 1m se analizan después de cada decisión para calcular MFE/MAE. "
        "Debe ser mayor que el holding promedio esperado. Rango 60–1440.",
    ),
    ConfigKey.OUTCOME_ATTRIBUTION_WINDOW_HOURS: _Default(
        "25", "int",
        "Ventana compartida (horas) para outcome attribution y post-mortem: "
        "solo decisiones con ts dentro de este rango se (re)calculan o envían al LLM. Rango 12–72.",
    ),
    ConfigKey.OUTCOME_COVERAGE_THRESHOLD_PCT: _Default(
        "30", "int",
        "Porcentaje máximo de velas 1m faltantes en la ventana antes de clasificar como UNKNOWN. "
        "Con 30 (default), si más del 30 % de las velas están ausentes la clasificación es UNKNOWN. "
        "Rango 5–50.",
    ),
    ConfigKey.POSTMORTEM_ENABLED: _Default(
        "true", "bool",
        "Si true, el job de post-mortem analiza decisiones con outcome negativo vía LLM.",
    ),
    ConfigKey.POSTMORTEM_MAX_PER_TICK: _Default(
        "5", "int",
        "Máximo de post-mortems LLM por tick del job. Rango 1–20.",
    ),
    ConfigKey.POSTMORTEM_PROVIDER: _Default(
        "gemini-2.5-flash", "string",
        "Provider LLM primario para post-mortem (chat). Opciones: gemini-2.5-flash | gemini-2.5-pro | groq-llama-3.3-70b | groq-qwen3-32b* | groq-compound-beta | groq-llama-4-scout | groq-gpt-oss-120b  (* reasoning_effort)",
    ),
    ConfigKey.POSTMORTEM_FALLBACK_PROVIDERS: _Default(
        "groq-compound-mini,groq-llama-4-scout,groq-qwen3-32b,groq-gpt-oss-20b,groq-llama-3.1-8b",
        "string",
        "Cascada de fallback para post-mortem (CSV ordenado). Mismas opciones que fallback_providers.",
    ),
    ConfigKey.POSTMORTEM_INTERVAL_MIN: _Default(
        "60", "int",
        "Deprecated: post-mortem corre encadenado tras outcome attribution "
        "(mismo intervalo que outcome_attribution_interval_min). Se conserva por compatibilidad.",
    ),
    ConfigKey.BLOCK_K_MAX_LINES: _Default(
        "5", "int",
        "Máximo de líneas de lecciones post-mortem inyectadas en Bloque K del Decisor. Rango 1–10.",
    ),
    ConfigKey.BLOCK_K_WINDOW_HOURS: _Default(
        "72", "int",
        "Ventana horaria de lecciones post-mortem visibles en Bloque K. Rango 24–168.",
    ),
    ConfigKey.CONFLUENCE_PROMOTION_MIN_OCCURRENCES: _Default(
        "3", "int",
        "Ocurrencias mínimas de un pattern_tag para promover candidato a catálogo I–Z. Rango 2–10.",
    ),
    ConfigKey.CONFLUENCE_PROMOTION_WINDOW_DAYS: _Default(
        "7", "int",
        "Ventana en días para contar ocurrencias de candidatos a confluencia. Rango 3–30.",
    ),
    ConfigKey.CONFLUENCE_REGISTRY_MAX_ACTIVE: _Default(
        "5", "int",
        "Máximo de confluencias promovidas activas (I–Z) simultáneas. Rango 1–18.",
    ),
    ConfigKey.LIVE_SINCE_TS: _Default(
        "", "string",
        "ISO UTC timestamp when mode switched to LIVE. Used as default filter cutoff for trades/decisions.",
    ),
}


async def default_list_since(session: AsyncSession) -> datetime | None:
    """Return the LIVE cutoff timestamp when mode is LIVE, else None."""
    store = ConfigStore(session)
    try:
        if await store.get(ConfigKey.MODE) != "LIVE":
            return None
        raw = (await store.get(ConfigKey.LIVE_SINCE_TS)).strip()
        if not raw:
            return None
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except KeyError:
        return None


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
        return int(float(value))  # float() first handles "5.0" stored as string
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return value.lower() in ("true", "1", "yes")
    if value_type == "json":
        return json.loads(value)
    return value
