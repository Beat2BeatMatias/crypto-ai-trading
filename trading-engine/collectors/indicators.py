"""Technical indicator computation using pandas only — no external TA library.

All formulas are standard implementations:
- RSI (Wilder smoothing via EWM com=13)
- MACD (EMA 12/26/9)
- EMA 20/50/200
- Bollinger Bands (SMA20 ± 2σ)
- ATR (Wilder smoothing)
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _last_or_none(series: pd.Series | None) -> float | None:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)


def compute_indicators(df: pd.DataFrame, *, timeframe: str) -> dict[str, Any]:
    """Compute the latest indicator values from an OHLCV DataFrame.

    Returns a flat dict. Missing values (series shorter than window) are None.
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # RSI — Wilder smoothing (EWM with com=13 ≈ 1/14 smoothing)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    # EMAs
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    # Bollinger Bands (20-period, 2σ)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std(ddof=1)
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20

    # ATR — Wilder smoothing
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(com=13, min_periods=14).mean()

    last_close = _last_or_none(close)
    bb_upper_val = _last_or_none(bb_upper)
    bb_lower_val = _last_or_none(bb_lower)

    bb_pct = None
    if (last_close is not None and bb_upper_val is not None
            and bb_lower_val is not None and bb_upper_val != bb_lower_val):
        bb_pct = (last_close - bb_lower_val) / (bb_upper_val - bb_lower_val) * 100.0

    return {
        "timeframe": timeframe,
        "rsi": _last_or_none(rsi),
        "macd": _last_or_none(macd),
        "macd_signal": _last_or_none(macd_signal),
        "macd_hist": _last_or_none(macd_hist),
        "ema20": _last_or_none(ema20),
        "ema50": _last_or_none(ema50),
        "ema200": _last_or_none(ema200),
        "bb_upper": bb_upper_val,
        "bb_middle": _last_or_none(sma20),
        "bb_lower": bb_lower_val,
        "bb_pct": bb_pct,
        "atr": _last_or_none(atr),
        "volume_avg_20": _last_or_none(df["volume"].rolling(20).mean()),
        "last_close": last_close,
    }
