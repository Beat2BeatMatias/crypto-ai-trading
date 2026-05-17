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

from typing import Literal

OperationalProfile = Literal["SCALPING", "HIBRIDO", "DAY_TRADING"]

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


def get_operational_profile(decisor_interval_min: int,
                            atr_timeframe: str) -> OperationalProfile:
    if decisor_interval_min <= 10 or atr_timeframe in ("1m", "5m"):
        return "SCALPING"
    if decisor_interval_min > 30 or atr_timeframe in ("1h", "4h"):
        return "DAY_TRADING"
    return "HIBRIDO"


def get_tf_priority_order(profile: OperationalProfile) -> list[str]:
    return _PROFILE_TF_ORDER[profile]


def get_profile_holding_range(profile: OperationalProfile) -> tuple[int, int]:
    return _PROFILE_HOLDING_RANGE[profile]


def get_profile_confluences(profile: OperationalProfile) -> list[str]:
    return _PROFILE_CONFLUENCES[profile]


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
