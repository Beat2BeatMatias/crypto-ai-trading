"""Wrappers over pandas-ta producing a flat dict of the latest indicator values."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pandas_ta as ta


def _last_or_none(series: pd.Series | None) -> float | None:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)


def compute_indicators(df: pd.DataFrame, *, timeframe: str) -> dict[str, Any]:
    """Compute the latest indicator values from an OHLCV DataFrame.

    Returns a flat dict. Missing values (series shorter than window) are None,
    not NaN, so the caller can safely check ``if val is not None``.
    """
    rsi = ta.rsi(df["close"], length=14)
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    ema20 = ta.ema(df["close"], length=20)
    ema50 = ta.ema(df["close"], length=50)
    ema200 = ta.ema(df["close"], length=200)
    bb = ta.bbands(df["close"], length=20, std=2)
    atr = ta.atr(df["high"], df["low"], df["close"], length=14)

    last_close = _last_or_none(df["close"])

    # Column names vary across pandas-ta releases (e.g. "BBU_20_2.0" vs "BBU_20_2.0_2.0"),
    # so we look up by prefix to stay version-agnostic.
    def _bb_col(prefix: str) -> pd.Series | None:
        if bb is None:
            return None
        matching = [c for c in bb.columns if c.startswith(prefix)]
        return bb[matching[0]] if matching else None

    bb_upper = _last_or_none(_bb_col("BBU_"))
    bb_lower = _last_or_none(_bb_col("BBL_"))

    bb_pct = None
    if (
        last_close is not None
        and bb_upper is not None
        and bb_lower is not None
        and bb_upper != bb_lower
    ):
        bb_pct = (last_close - bb_lower) / (bb_upper - bb_lower) * 100.0

    return {
        "timeframe": timeframe,
        "rsi": _last_or_none(rsi),
        "macd": _last_or_none(macd_df["MACD_12_26_9"]) if macd_df is not None else None,
        "macd_signal": _last_or_none(macd_df["MACDs_12_26_9"]) if macd_df is not None else None,
        "macd_hist": _last_or_none(macd_df["MACDh_12_26_9"]) if macd_df is not None else None,
        "ema20": _last_or_none(ema20),
        "ema50": _last_or_none(ema50),
        "ema200": _last_or_none(ema200),
        "bb_upper": bb_upper,
        "bb_middle": _last_or_none(_bb_col("BBM_")),
        "bb_lower": bb_lower,
        "bb_pct": bb_pct,
        "atr": _last_or_none(atr),
        "volume_avg_20": _last_or_none(df["volume"].rolling(20).mean()),
        "last_close": last_close,
    }
