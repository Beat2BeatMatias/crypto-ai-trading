"""Technical indicator computation using pandas only — no external TA library.

All formulas are standard implementations:
- RSI (Wilder smoothing via EWM com=13)
- MACD (EMA 12/26/9)
- EMA 9/20/50/200
- Bollinger Bands (SMA20 ± 2σ)
- ATR (Wilder smoothing, TR winsorized)
- ADX (Wilder, 14)
- Stochastic Oscillator (14, 3, 3) — %K, %D
- VWAP intraday (daily UTC reset) + bands (1σ, 2σ)
- Wick and body ratios (last 3 candles)
- Volume delta approximation (Lee-Ready sign)
- OBV slope (20-period linear regression slope)
- Price structure: HH/HL/LH/LL over last 20 candles
- ATR percentile vs. available history (up to 30 days)
"""
from __future__ import annotations

import math
from datetime import timezone
from typing import Any

import numpy as np
import pandas as pd


def _last_or_none(series: pd.Series | None) -> float | None:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)


def _safe_float(val: Any) -> float | None:
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_atr_series(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder ATR(14) with TR winsorized at 3× rolling median. Shared by ATR and ADX."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr_median = tr.rolling(20, min_periods=5).median()
    tr_capped = tr.clip(upper=(tr_median * 3).where(tr_median.notna(), tr))
    return tr_capped.ewm(com=13, min_periods=14).mean()


def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (ADX, +DI, -DI) series using Wilder smoothing."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    # Zero out when the other DM is larger
    mask = plus_dm >= minus_dm
    plus_dm = plus_dm.where(mask, 0.0)
    minus_dm = minus_dm.where(~mask, 0.0)

    atr14 = _compute_atr_series(high, low, close)

    com = period - 1
    plus_di = 100 * plus_dm.ewm(com=com, min_periods=period).mean() / atr14.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(com=com, min_periods=period).mean() / atr14.replace(0, float("nan"))

    dx_denom = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / dx_denom
    adx = dx.ewm(com=com, min_periods=period).mean()

    return adx, plus_di, minus_di


def _compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                        k_period: int = 14, d_period: int = 3,
                        smooth_k: int = 3) -> tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator: smoothed %K and %D."""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    denom = (highest_high - lowest_low).replace(0, float("nan"))
    raw_k = 100 * (close - lowest_low) / denom
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(d_period).mean()
    return k, d


def _compute_vwap(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Intraday VWAP with daily UTC reset, plus ±1σ and ±2σ bands.

    Expects 'timestamp' column (ms epoch int or datetime-like) or datetime index.
    Falls back to full-series VWAP if timestamps are unavailable.
    Returns (vwap, band_1sigma_upper, band_2sigma_upper).
    Lower bands are symmetric: vwap - (upper - vwap).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]

    # Determine the UTC day for each row so we can group and reset
    try:
        if "timestamp" in df.columns:
            ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        elif isinstance(df.index, pd.DatetimeIndex):
            ts = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
        else:
            ts = None
    except Exception:
        ts = None

    if ts is not None:
        day_key = ts.date  # DatetimeIndex exposes .date directly (no .dt accessor)
        cum_pv = (typical * vol).groupby(day_key).cumsum()
        cum_v = vol.groupby(day_key).cumsum()
        vwap = cum_pv / cum_v.replace(0, float("nan"))

        # Rolling σ of typical price since day start, same groupby
        deviation_sq = (typical - vwap) ** 2
        cum_dev_sq = deviation_sq.groupby(day_key).cumsum()
        # cumulative variance (biased) — safe for intraday band
        with np.errstate(invalid="ignore"):
            sigma = (cum_dev_sq / cum_v.replace(0, float("nan"))).apply(
                lambda x: math.sqrt(x) if (x is not None and not math.isnan(x) and x >= 0) else float("nan")
            )
    else:
        cum_pv = (typical * vol).cumsum()
        cum_v = vol.cumsum()
        vwap = cum_pv / cum_v.replace(0, float("nan"))
        variance = ((typical - vwap) ** 2).expanding().mean()
        sigma = variance.apply(lambda x: math.sqrt(x) if x >= 0 else float("nan"))

    band_upper_1 = vwap + sigma
    band_upper_2 = vwap + 2 * sigma
    return vwap, band_upper_1, band_upper_2


def _compute_wick_ratios(high: pd.Series, low: pd.Series,
                         open_: pd.Series, close: pd.Series,
                         n: int = 3) -> list[dict[str, float | None]]:
    """
    Wick and body metrics for the last n candles.

    upper_wick_ratio: upper wick / candle range (0–1, higher = more rejection above)
    lower_wick_ratio: lower wick / candle range (0–1, higher = more rejection below)
    body_ratio:       body / range  (0–1, higher = more conviction)
    """
    result = []
    tail = min(n, len(high))
    for i in range(-tail, 0):
        h, l, o, c = float(high.iloc[i]), float(low.iloc[i]), float(open_.iloc[i]), float(close.iloc[i])
        rng = h - l
        if rng < 1e-10:
            result.append({"upper_wick_ratio": None, "lower_wick_ratio": None, "body_ratio": None})
            continue
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        body = abs(c - o)
        result.append({
            "upper_wick_ratio": _safe_float(upper_wick / rng),
            "lower_wick_ratio": _safe_float(lower_wick / rng),
            "body_ratio": _safe_float(body / rng),
        })
    return result


def _compute_volume_delta(open_: pd.Series, close: pd.Series,
                          volume: pd.Series, n: int = 20) -> pd.Series:
    """
    Approximate taker buy/sell delta using candle direction (Lee-Ready sign).
    Positive = net buying pressure, negative = net selling.
    """
    sign = pd.Series(
        data=[(1.0 if c > o else -1.0 if c < o else 0.0)
              for o, c in zip(open_, close)],
        index=volume.index,
    )
    return sign * volume


def _compute_obv_slope(close: pd.Series, volume: pd.Series,
                       window: int = 20) -> float | None:
    """
    OBV (On Balance Volume) linear-regression slope over `window` candles,
    normalized by average OBV to give a dimensionless trend indicator.
    Positive = OBV rising (accumulation), negative = OBV falling (distribution).
    """
    obv = pd.Series(0.0, index=close.index)
    direction = close.diff().apply(lambda d: 1.0 if d > 0 else (-1.0 if d < 0 else 0.0))
    obv = (direction * volume).cumsum()

    if len(obv) < window:
        return None

    y = obv.iloc[-window:].values.astype(float)
    if np.any(~np.isfinite(y)):
        return None

    x = np.arange(window, dtype=float)
    # Linear regression slope via covariance formula
    x_mean, y_mean = x.mean(), y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom < 1e-10:
        return None
    slope = ((x - x_mean) * (y - y_mean)).sum() / denom

    avg_obv = abs(y_mean) if abs(y_mean) > 1e-10 else 1.0
    return _safe_float(slope / avg_obv)


def _compute_price_structure(high: pd.Series, low: pd.Series,
                              n: int = 20) -> str:
    """
    Classify price structure over last n candles as:
    'uptrend'       — predominant Higher Highs + Higher Lows
    'downtrend'     — predominant Lower Highs + Lower Lows
    'consolidation' — mixed signals
    """
    if len(high) < max(n, 4):
        return "consolidation"

    highs = high.iloc[-n:].values
    lows = low.iloc[-n:].values

    hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    lh = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])

    bull_score = hh + hl
    bear_score = lh + ll

    if bull_score > bear_score * 1.4:
        return "uptrend"
    if bear_score > bull_score * 1.4:
        return "downtrend"
    return "consolidation"


def _compute_atr_percentile(atr_series: pd.Series) -> float | None:
    """
    Percentile (0–100) of the current ATR value within all available history.
    Returns None if fewer than 20 data points.
    """
    values = atr_series.dropna().values
    if len(values) < 20:
        return None
    current = values[-1]
    pct = float((values < current).sum()) / len(values) * 100
    return _safe_float(pct)


def compute_pivot_points(prev_high: float, prev_low: float,
                         prev_close: float) -> dict[str, float]:
    """
    Classical daily pivot points from the previous day's H/L/C.
    Returns PP, R1/R2/R3, S1/S2/S3.
    """
    pp = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pp - prev_low
    s1 = 2 * pp - prev_high
    r2 = pp + (prev_high - prev_low)
    s2 = pp - (prev_high - prev_low)
    r3 = prev_high + 2 * (pp - prev_low)
    s3 = prev_low - 2 * (prev_high - pp)
    return {
        "pp": round(pp, 2),
        "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
        "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2),
    }


def compute_indicators(df: pd.DataFrame, *, timeframe: str) -> dict[str, Any]:
    """Compute the latest indicator values from an OHLCV DataFrame.

    Returns a flat dict. Missing values (series shorter than window) are None.
    Expects columns: open, high, low, close, volume.
    Optional column 'timestamp' (ms epoch int) enables intraday VWAP daily reset.
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    volume = df["volume"]

    # ------------------------------------------------------------------
    # RSI — Wilder smoothing (EWM com=13 ≈ 1/14)
    # ------------------------------------------------------------------
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, min_periods=14).mean()
    avg_loss = loss.ewm(com=13, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    # ------------------------------------------------------------------
    # MACD (12/26/9)
    # ------------------------------------------------------------------
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    # ------------------------------------------------------------------
    # EMAs
    # ------------------------------------------------------------------
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    # ------------------------------------------------------------------
    # Bollinger Bands (20-period, 2σ)
    # ------------------------------------------------------------------
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std(ddof=1)
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20

    # ------------------------------------------------------------------
    # ATR — Wilder + TR winsorization (shared helper)
    # ------------------------------------------------------------------
    atr = _compute_atr_series(high, low, close)

    # ------------------------------------------------------------------
    # ADX (14)
    # ------------------------------------------------------------------
    adx, plus_di, minus_di = _compute_adx(high, low, close, period=14)

    # ------------------------------------------------------------------
    # Stochastic (14, 3, 3)
    # ------------------------------------------------------------------
    stoch_k, stoch_d = _compute_stochastic(high, low, close)

    # ------------------------------------------------------------------
    # VWAP intraday + bands
    # ------------------------------------------------------------------
    vwap, vwap_upper_1, vwap_upper_2 = _compute_vwap(df)

    # ------------------------------------------------------------------
    # Wick / body ratios (last 3 candles)
    # ------------------------------------------------------------------
    wick_ratios = _compute_wick_ratios(high, low, open_, close, n=3)

    # ------------------------------------------------------------------
    # Volume delta (Lee-Ready approximation)
    # ------------------------------------------------------------------
    vol_delta_series = _compute_volume_delta(open_, close, volume)
    vol_delta_20 = _last_or_none(vol_delta_series.rolling(20).sum())

    # ------------------------------------------------------------------
    # OBV slope (20-period)
    # ------------------------------------------------------------------
    obv_slope = _compute_obv_slope(close, volume, window=20)

    # ------------------------------------------------------------------
    # Price structure (HH/HL analysis, last 20 candles)
    # ------------------------------------------------------------------
    structure = _compute_price_structure(high, low, n=20)

    # ------------------------------------------------------------------
    # ATR percentile vs. all available history in df
    # ------------------------------------------------------------------
    atr_percentile = _compute_atr_percentile(atr)

    # ------------------------------------------------------------------
    # Derived scalars
    # ------------------------------------------------------------------
    last_close = _last_or_none(close)
    bb_upper_val = _last_or_none(bb_upper)
    bb_lower_val = _last_or_none(bb_lower)

    bb_pct = None
    if (last_close is not None and bb_upper_val is not None
            and bb_lower_val is not None and bb_upper_val != bb_lower_val):
        bb_pct = (last_close - bb_lower_val) / (bb_upper_val - bb_lower_val) * 100.0

    vwap_val = _last_or_none(vwap)
    vwap_dev_pct = None
    if vwap_val and last_close and vwap_val > 0:
        vwap_dev_pct = (last_close - vwap_val) / vwap_val * 100

    result = {
        "timeframe": timeframe,
        # Core
        "last_close": last_close,
        # RSI
        "rsi": _last_or_none(rsi),
        # MACD
        "macd": _last_or_none(macd),
        "macd_signal": _last_or_none(macd_signal),
        "macd_hist": _last_or_none(macd_hist),
        # EMAs
        "ema9": _last_or_none(ema9),
        "ema20": _last_or_none(ema20),
        "ema50": _last_or_none(ema50),
        "ema200": _last_or_none(ema200),
        # Bollinger
        "bb_upper": bb_upper_val,
        "bb_middle": _last_or_none(sma20),
        "bb_lower": bb_lower_val,
        "bb_pct": bb_pct,
        # ATR
        "atr": _last_or_none(atr),
        "atr_percentile": atr_percentile,
        # ADX
        "adx": _last_or_none(adx),
        "plus_di": _last_or_none(plus_di),
        "minus_di": _last_or_none(minus_di),
        # Stochastic
        "stoch_k": _last_or_none(stoch_k),
        "stoch_d": _last_or_none(stoch_d),
        # VWAP
        "vwap": vwap_val,
        "vwap_upper_1": _last_or_none(vwap_upper_1),
        "vwap_upper_2": _last_or_none(vwap_upper_2),
        "vwap_lower_1": (_safe_float(2 * vwap_val - _last_or_none(vwap_upper_1))
                         if vwap_val and _last_or_none(vwap_upper_1) else None),
        "vwap_lower_2": (_safe_float(2 * vwap_val - _last_or_none(vwap_upper_2))
                         if vwap_val and _last_or_none(vwap_upper_2) else None),
        "vwap_dev_pct": vwap_dev_pct,
        # Volume
        "volume_current": _last_or_none(volume),
        "volume_avg_20": _last_or_none(volume.rolling(20).mean()),
        "volume_delta_20": vol_delta_20,
        # OBV
        "obv_slope": obv_slope,
        # Structure
        "structure": structure,
        # Wick / body (last 3 candles, index 0 = oldest of the 3)
        "wick_upper_c0": wick_ratios[0]["upper_wick_ratio"] if len(wick_ratios) > 0 else None,
        "wick_lower_c0": wick_ratios[0]["lower_wick_ratio"] if len(wick_ratios) > 0 else None,
        "body_ratio_c0": wick_ratios[0]["body_ratio"] if len(wick_ratios) > 0 else None,
        "wick_upper_c1": wick_ratios[1]["upper_wick_ratio"] if len(wick_ratios) > 1 else None,
        "wick_lower_c1": wick_ratios[1]["lower_wick_ratio"] if len(wick_ratios) > 1 else None,
        "body_ratio_c1": wick_ratios[1]["body_ratio"] if len(wick_ratios) > 1 else None,
        "wick_upper_c2": wick_ratios[2]["upper_wick_ratio"] if len(wick_ratios) > 2 else None,
        "wick_lower_c2": wick_ratios[2]["lower_wick_ratio"] if len(wick_ratios) > 2 else None,
        "body_ratio_c2": wick_ratios[2]["body_ratio"] if len(wick_ratios) > 2 else None,
    }
    return result
