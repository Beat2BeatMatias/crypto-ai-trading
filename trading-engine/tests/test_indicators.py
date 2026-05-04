"""Tests for pandas-ta wrappers."""
import numpy as np
import pandas as pd
import pytest

from collectors.indicators import compute_indicators


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(seed=42)
    n = 300
    base = 60_000.0
    returns = rng.normal(0, 0.002, size=n)
    close = base * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(rng.normal(0, 0.0015, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, size=n)))
    open_ = np.concatenate([[base], close[:-1]])
    volume = rng.uniform(50, 200, size=n)
    idx = pd.date_range("2026-04-01", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_compute_indicators_returns_all_required_keys(synthetic_ohlcv):
    out = compute_indicators(synthetic_ohlcv, timeframe="5m")
    required = {
        "rsi", "macd", "macd_signal", "macd_hist",
        "ema20", "ema50", "ema200",
        "bb_upper", "bb_middle", "bb_lower", "bb_pct",
        "atr", "volume_avg_20",
    }
    assert required <= set(out.keys())


def test_rsi_in_range(synthetic_ohlcv):
    out = compute_indicators(synthetic_ohlcv, timeframe="5m")
    assert 0 <= out["rsi"] <= 100


def test_ema_ordering_for_uptrend():
    n = 300
    close = np.linspace(50_000, 70_000, n)
    df = pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0),
    }, index=pd.date_range("2026-04-01", periods=n, freq="1min", tz="UTC"))
    out = compute_indicators(df, timeframe="1h")
    assert out["ema20"] >= out["ema50"] >= out["ema200"]


def test_handles_short_series_gracefully():
    short = pd.DataFrame({
        "open": [1.0, 2.0, 3.0], "high": [1.0, 2.0, 3.0],
        "low": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0],
        "volume": [1.0, 1.0, 1.0],
    }, index=pd.date_range("2026-04-01", periods=3, freq="1min", tz="UTC"))
    out = compute_indicators(short, timeframe="5m")
    assert "rsi" in out  # should not raise
