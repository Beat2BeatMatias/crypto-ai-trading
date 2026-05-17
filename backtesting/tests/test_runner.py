"""Unit tests for the backtesting runner — synthetic OHLCV, no network calls."""
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from runner import add_indicators, run_baseline, signal_buy


def make_uptrend(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = np.linspace(60_000, 70_000, n) + rng.normal(0, 100, size=n)
    high = close * 1.002
    low = close * 0.998
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": rng.uniform(50, 200, size=n)},
        index=pd.date_range("2026-04-01", periods=n, freq="5min", tz="UTC"),
    )


def test_add_indicators_returns_required_columns():
    df = add_indicators(make_uptrend())
    for col in ["rsi", "macd", "ema20", "ema50", "ema200", "atr", "volume_avg"]:
        assert col in df.columns, f"missing column: {col}"


def test_run_baseline_on_synthetic_uptrend():
    df = add_indicators(make_uptrend())
    res = run_baseline(df, sl_atr_mult=1.0, rr=2.0)
    # With 400 candles may have 0 or more trades
    assert res.n_trades >= 0
    assert res.n_wins + res.n_losses == res.n_trades
    if res.n_trades > 0:
        assert -100 <= res.total_pnl_pct <= 200
        assert res.max_drawdown_pct <= 0  # max_drawdown is negative


def test_zero_trades_on_flat_market():
    n = 400
    flat = pd.DataFrame(
        {"open": [60_000.0] * n, "high": [60_000.0] * n,
         "low": [60_000.0] * n, "close": [60_000.0] * n,
         "volume": [100.0] * n},
        index=pd.date_range("2026-04-01", periods=n, freq="5min", tz="UTC"),
    )
    df = add_indicators(flat)
    res = run_baseline(df)
    assert res.n_trades == 0
    assert res.win_rate == 0.0
    assert res.sharpe == 0.0


def test_signal_buy_requires_min_confluences():
    """signal_buy debe retornar False cuando menos de 3 confluencias blandas se cumplen."""
    # GIVEN: filtros duros OK, pero solo 1 de 4 confluencias blandas cumplida (RSI)
    row = pd.Series({
        "ema20": 66000.0, "ema50": 65000.0, "ema200": 60000.0,  # EMAs apiladas ✓
        "open": 65900.0, "close": 65500.0,   # vela bajista, dentro del pullback zone ✓
        "ema200_slope_pct": 0.5,             # slope EMA200 positivo ✓
        "rsi": 50.0,                         # ✓ en rango 38-68 (1 confluencia)
        "macd_hist": -0.5,                   # ✗ histograma negativo
        "volume": 100.0, "volume_avg": 200.0,  # ✗ sin exceso de volumen
    })
    # WHEN / THEN: solo 1 de 4 confluencias → False
    assert signal_buy(row) is False
