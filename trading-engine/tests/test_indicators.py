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


def test_atr_winsorization_filters_flash_crash_candles():
    # GIVEN a series of normal candles followed by a testnet-style flash crash
    # (low drops from ~82k to ~68k → TR ≈ 14 000 vs normal ~300)
    n = 50
    price = 82_000.0
    closes = np.full(n, price)
    highs  = closes + 200.0
    lows   = closes - 200.0

    # Inject two flash-crash candles at positions 30 and 40
    lows[30] = 68_000.0   # TR ≈ 14 000
    lows[40] = 68_000.0

    df_crash = pd.DataFrame({
        "open":   closes, "high": highs, "low": lows,
        "close":  closes, "volume": np.full(n, 100.0),
    }, index=pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC"))

    # GIVEN a clean series with no outliers
    df_clean = pd.DataFrame({
        "open":   closes, "high": highs, "low": np.full(n, price - 200.0),
        "close":  closes, "volume": np.full(n, 100.0),
    }, index=pd.date_range("2026-04-01", periods=n, freq="15min", tz="UTC"))

    # WHEN indicators are computed for both
    out_crash = compute_indicators(df_crash, timeframe="15m")
    out_clean = compute_indicators(df_clean, timeframe="15m")

    # THEN the flash-crash ATR must not be more than 3x the clean ATR
    # (without winsorization it would be ~6-8x)
    assert out_crash["atr"] is not None
    assert out_clean["atr"] is not None
    ratio = out_crash["atr"] / out_clean["atr"]
    assert ratio < 3.0, f"ATR inflated {ratio:.1f}x by flash-crash candles — winsorization not working"


def test_compute_indicators_includes_volume_current():
    import numpy as np
    import pandas as pd
    from collectors.indicators import compute_indicators

    np.random.seed(42)
    n = 30
    closes = 95000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": closes - 50,
        "high": closes + 100,
        "low": closes - 100,
        "close": closes,
        "volume": np.full(n, 5.0),
    })
    df.loc[df.index[-1], "volume"] = 7.5  # override last candle

    result = compute_indicators(df, timeframe="5m")

    assert result["volume_current"] == pytest.approx(7.5)
    assert result["volume_avg_20"] is not None
