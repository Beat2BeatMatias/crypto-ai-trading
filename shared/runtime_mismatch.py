"""Clasificación de desajuste config futuros vs runtime spot."""
from __future__ import annotations

from enum import Enum


class RuntimeMismatchReason(str, Enum):
    API_PERMISSIONS = "api_permissions"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    RESTART_REQUIRED = "restart_required"
    UNKNOWN = "unknown"


_API_MARKERS = (
    "futures_setup_failed",
    "-2015",
    "invalid api-key",
    "invalid api key",
    "permissions for action",
    "permission",
    "ip whitelist",
    "not authorized",
)

_SIZING_MARKERS = (
    "futures_sizing",
    "sizing_unfeasible",
    "min_notional",
)


def classify_downgrade_reason(stored: str) -> RuntimeMismatchReason | None:
    """Infer reason from engine-persisted downgrade text (empty → None)."""
    if not (stored or "").strip():
        return None
    low = stored.lower()
    if any(m in low for m in _API_MARKERS):
        return RuntimeMismatchReason.API_PERMISSIONS
    if any(m in low for m in _SIZING_MARKERS):
        return RuntimeMismatchReason.INSUFFICIENT_MARGIN
    return None


def diagnose_from_live_margin(
    *,
    available_margin: float,
    max_position_pct: float,
    max_leverage: int,
    min_notional_estimate: float = 100.0,
) -> RuntimeMismatchReason:
    max_trade = available_margin * max_position_pct * max_leverage
    if max_trade < min_notional_estimate:
        return RuntimeMismatchReason.INSUFFICIENT_MARGIN
    return RuntimeMismatchReason.RESTART_REQUIRED


def mismatch_detail_es(
    reason: RuntimeMismatchReason,
    *,
    stored_reason: str = "",
    available_margin: float | None = None,
    max_trade_notional: float | None = None,
) -> str:
    if reason == RuntimeMismatchReason.API_PERMISSIONS:
        return (
            "Binance rechazó la API de futuros (clave, IP o permiso «Futures»). "
            "Revisá la API key en Binance y reiniciá trading-engine."
        )
    if reason == RuntimeMismatchReason.INSUFFICIENT_MARGIN:
        if available_margin is not None and max_trade_notional is not None:
            return (
                f"Margen futuros disponible ~{available_margin:.0f} USDT; "
                f"notional máx. estimado ~{max_trade_notional:.0f} USDT "
                f"por debajo del mínimo del par. Depositá USDT en Futuros USDT-M "
                f"y reiniciá trading-engine."
            )
        if stored_reason:
            return f"{stored_reason} Depositá margen en Futuros USDT-M y reiniciá trading-engine."
        return (
            "Margen insuficiente para el mínimo de orden del par. "
            "Depositá USDT en Futuros USDT-M y reiniciá trading-engine."
        )
    if reason == RuntimeMismatchReason.RESTART_REQUIRED:
        return (
            "La config está en futuros y el margen/API parecen OK, pero el engine "
            "sigue en spot. Reiniciá trading-engine: docker-compose restart trading-engine"
        )
    return (
        "Config en futuros pero el engine opera en spot. "
        "Revisá logs del engine y reiniciá trading-engine."
    )
