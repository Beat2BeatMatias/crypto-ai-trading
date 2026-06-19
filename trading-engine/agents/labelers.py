"""
Labelers: deterministic interpretative labels computed from raw indicator values.

These labels give the LLM a pre-digested interpretation of each indicator so it
can focus its reasoning on confluence and context rather than recalculating
trivial thresholds. Raw numeric values are always included alongside the labels.

Profiles
--------
SCALPING   — decisor_interval_min ≤ 10 and atr_timeframe in {1m, 5m}
HIBRIDO    — decisor_interval_min in 15-30 or atr_timeframe == 15m
DAY_TRADING — decisor_interval_min > 30 or atr_timeframe in {1h, 4h}
"""
from __future__ import annotations

from typing import Any, Literal

OperationalProfile = Literal["SCALPING", "HIBRIDO", "DAY_TRADING"]

CANONICAL_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h")
_TF_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "10m": 10, "15m": 15, "1h": 60, "4h": 240,
}
STRUCTURAL_TIMEFRAME = "1h"

# Ordered timeframes by priority for each profile (highest priority first)
_PROFILE_TF_ORDER: dict[OperationalProfile, list[str]] = {
    "SCALPING":    ["1m", "5m", "15m", "1h", "4h"],
    "HIBRIDO":     ["15m", "1h", "5m", "4h", "1m"],
    "DAY_TRADING": ["1h", "4h", "15m", "5m", "1m"],
}

# Holding range in minutes per profile (for coherence checks)
_PROFILE_HOLDING_RANGE: dict[OperationalProfile, tuple[int, int]] = {
    "SCALPING":    (10, 60),
    "HIBRIDO":     (30, 180),
    "DAY_TRADING": (60, 480),
}

# Confluences most relevant per profile (informational — not exclusive)
_PROFILE_CONFLUENCES: dict[OperationalProfile, list[str]] = {
    "SCALPING":    ["A", "D", "E"],
    "HIBRIDO":     ["B", "C", "H"],
    "DAY_TRADING": ["B", "C", "G"],
}

_PROFILE_CONFLUENCES_FUTURES_SHORT: dict[OperationalProfile, list[str]] = {
    "SCALPING":    ["I", "D", "F"],
    "HIBRIDO":     ["I", "J", "C"],
    "DAY_TRADING": ["I", "J", "G"],
}


def get_operational_profile(decisor_interval_min: int,
                            atr_timeframe: str) -> OperationalProfile:
    if decisor_interval_min <= 15 or atr_timeframe in ("1m", "5m"):
        return "SCALPING"
    if decisor_interval_min > 30 or atr_timeframe in ("1h", "4h"):
        return "DAY_TRADING"
    return "HIBRIDO"


def get_tf_priority_order(profile: OperationalProfile) -> list[str]:
    return _PROFILE_TF_ORDER[profile]


def _available_from_indicators(ind: dict[str, Any]) -> frozenset[str]:
    return frozenset(tf for tf in CANONICAL_TIMEFRAMES if (ind.get(tf) or {}))


def operational_atr_from_indicators(
    ind: dict[str, Any],
    *,
    atr_timeframe: str,
    decisor_interval_min: int,
) -> tuple[str, float]:
    """Resuelve TF operativo y valor ATR (misma cadena de fallback que ContextBuilder)."""
    profile = get_operational_profile(decisor_interval_min, atr_timeframe)
    available = _available_from_indicators(ind)
    operational_tf = normalize_operational_timeframe(
        atr_timeframe, profile=profile, available=available,
    )
    for tf in (operational_tf, atr_timeframe, "15m", "5m", "1h"):
        raw = (ind.get(tf) or {}).get("atr")
        if raw is not None:
            return operational_tf, float(raw)
    return operational_tf, 300.0


def normalize_operational_timeframe(
    atr_timeframe: str,
    *,
    profile: OperationalProfile,
    available: frozenset[str] | None = None,
) -> str:
    """Mapea atr_timeframe de config al bucket canónico más cercano (p. ej. 10m → 5m/15m)."""
    available = available or frozenset(CANONICAL_TIMEFRAMES)
    tf = (atr_timeframe or "15m").lower().strip()
    if tf in available:
        return tf
    if tf == "10m":
        if profile == "SCALPING":
            return "5m" if "5m" in available else "15m"
        return "15m" if "15m" in available else "5m"
    minutes = _TF_MINUTES.get(tf)
    if minutes is None:
        return "15m" if "15m" in available else next(iter(sorted(available, key=lambda t: _TF_MINUTES[t])))
    ranked = sorted(available, key=lambda t: abs(_TF_MINUTES[t] - minutes))
    return ranked[0]


def confluence_verification_tfs(
    atr_timeframe: str,
    profile: OperationalProfile,
    *,
    available: frozenset[str] | None = None,
) -> tuple[str, str, str]:
    """
    TFs para catálogo A/B/I/J y CoherenceChecker C1/C2/C1P/C2P.
    primary: alineado a atr_timeframe; secondary: TF superior; structural: régimen (1h).
    """
    available = available or frozenset(CANONICAL_TIMEFRAMES)
    order = [tf for tf in get_tf_priority_order(profile) if tf in available]
    if not order:
        order = [tf for tf in CANONICAL_TIMEFRAMES if tf in available]
    primary = normalize_operational_timeframe(
        atr_timeframe, profile=profile, available=frozenset(order) or available,
    )
    if primary not in order:
        primary = order[0]

    ladder = [tf for tf in CANONICAL_TIMEFRAMES if tf in available]
    pidx = ladder.index(primary) if primary in ladder else 0
    secondary = ladder[min(pidx + 1, len(ladder) - 1)] if ladder else primary
    if secondary == primary and len(ladder) > 1:
        secondary = ladder[min(pidx + 1, len(ladder) - 1)]

    structural = STRUCTURAL_TIMEFRAME if STRUCTURAL_TIMEFRAME in available else ladder[-1]
    return primary, secondary, structural


def format_confluence_tf_hierarchy(
    *,
    atr_timeframe: str,
    atr_operational_tf: str,
    primary_tf: str,
    secondary_tf: str,
    structural_tf: str,
) -> str:
    """Texto para el prompt del Decisor — jerarquía alineada al perfil y ATR."""
    atr_note = (
        f" (config {atr_timeframe} → operativo {atr_operational_tf})"
        if atr_timeframe != atr_operational_tf
        else ""
    )
    return "\n".join([
        f"- TF confluencia primario{atr_note}: {primary_tf} (alineado a ATR de referencia)",
        f"- TF confluencia secundario: {secondary_tf}",
        f"- TF estructural (régimen / EMAs / C3): {structural_tf}",
        f"- Precedencia: {secondary_tf} > {primary_tf} para confirmar dirección; "
        f"{structural_tf} anula señales menores en conflicto fuerte.",
        f"- Entrada válida si {primary_tf} y {secondary_tf} coinciden en dirección.",
        f"- Declarar A/B/I/J solo con evidencia en {primary_tf} o {secondary_tf} "
        f"(no en TFs fuera de este par).",
        f"- RSI({structural_tf}) >70 cancela señales alcistas de TFs menores.",
    ])


def critical_indicator_keys(primary_tf: str, structural_tf: str) -> tuple[tuple[str, str], ...]:
    """Indicadores mínimos para early-exit (primary + estructura 1h)."""
    keys: list[tuple[str, str]] = [
        (primary_tf, "rsi"),
        (primary_tf, "macd"),
        (structural_tf, "rsi"),
        (structural_tf, "macd"),
        ("1h", "ema20"),
        ("1h", "ema50"),
        ("1h", "atr"),
    ]
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pair in keys:
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return tuple(out)


def get_profile_holding_range(profile: OperationalProfile) -> tuple[int, int]:
    return _PROFILE_HOLDING_RANGE[profile]


def get_profile_confluences(
    profile: OperationalProfile,
    *,
    trading_product: str = "spot",
) -> list[str]:
    long_codes = list(_PROFILE_CONFLUENCES[profile])
    if trading_product != "futures":
        return long_codes
    merged = long_codes + [
        c for c in _PROFILE_CONFLUENCES_FUTURES_SHORT[profile]
        if c not in long_codes
    ]
    return merged


def format_profile_confluences_prompt(
    profile: OperationalProfile,
    *,
    trading_product: str = "spot",
) -> str:
    long_part = ", ".join(_PROFILE_CONFLUENCES[profile])
    if trading_product != "futures":
        return long_part
    short_part = ", ".join(_PROFILE_CONFLUENCES_FUTURES_SHORT[profile])
    return f"BUY/LONG: {long_part} | SHORT (futuros): {short_part}"


# ---------------------------------------------------------------------------
# Individual labelers
# ---------------------------------------------------------------------------

def rsi_label(rsi: float | None) -> str:
    if rsi is None:
        return "n/d"
    if rsi < 30:
        return "oversold"
    if rsi < 45:
        return "weak_bear"
    if rsi < 55:
        return "neutral"
    if rsi < 70:
        return "weak_bull"
    return "overbought"


def macd_label(macd: float | None, signal: float | None,
               hist: float | None, prev_hist: float | None = None) -> str:
    """
    Classifies the MACD state. `prev_hist` is the histogram value from the
    prior candle; if provided enables 'cross' detection vs 'extending'.
    """
    if macd is None or signal is None or hist is None:
        return "n/d"
    bull = macd > signal
    if bull:
        if prev_hist is not None:
            if prev_hist < 0 <= hist:
                return "bullish_cross"
            if hist > prev_hist:
                return "bullish_extending"
            return "bullish_weakening"
        return "bullish"
    else:
        if prev_hist is not None:
            if prev_hist > 0 >= hist:
                return "bearish_cross"
            if hist < prev_hist:
                return "bearish_extending"
            return "bearish_weakening"
        return "bearish"


def trend_label(ema20: float | None, ema50: float | None, ema200: float | None,
                price: float | None, adx: float | None) -> str:
    """
    Combines EMA alignment and ADX strength.
    strong_up / up / consolidation / down / strong_down
    """
    if price is None or ema20 is None or ema50 is None:
        return "n/d"

    ema_bull = price > ema20 > ema50
    ema_bear = price < ema20 < ema50
    if ema200 is not None:
        ema_bull = ema_bull and ema20 > ema200
        ema_bear = ema_bear and ema20 < ema200

    strong = adx is not None and adx > 25

    if ema_bull:
        return "strong_up" if strong else "up"
    if ema_bear:
        return "strong_down" if strong else "down"
    return "consolidation"


def volatility_label(atr_percentile: float | None) -> str:
    if atr_percentile is None:
        return "n/d"
    if atr_percentile < 30:
        return "low"
    if atr_percentile < 70:
        return "normal"
    if atr_percentile < 90:
        return "elevated"
    return "extreme"


def stoch_label(k: float | None, d: float | None) -> str:
    if k is None or d is None:
        return "n/d"
    if k < 20:
        return "oversold"
    if k > 80:
        return "overbought"
    if k > d:
        return "rising"
    return "falling"


def vwap_label(price: float | None, vwap: float | None,
               vwap_upper_1: float | None, vwap_lower_1: float | None) -> str:
    if price is None or vwap is None:
        return "n/d"
    if price > (vwap_upper_1 or vwap * 1.002):
        return "extended_above"
    if price > vwap:
        return "above"
    if price < (vwap_lower_1 or vwap * 0.998):
        return "extended_below"
    return "below"


def structure_label(structure: str | None) -> str:
    return structure or "n/d"


def imbalance_label(imbalance: float | None) -> str:
    if imbalance is None:
        return "n/d"
    if imbalance > 1.5:
        return "strong_buy_pressure"
    if imbalance > 1.2:
        return "buy_pressure"
    if imbalance < 0.67:
        return "strong_sell_pressure"
    if imbalance < 0.8:
        return "sell_pressure"
    return "balanced"
