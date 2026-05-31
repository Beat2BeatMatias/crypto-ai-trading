"""Smoke test del sweep de geometría sobre datos sintéticos."""
from __future__ import annotations
import numpy as np
import pandas as pd
from runner import add_indicators
from sweep import run_sweep

def _synthetic_ohlcv(n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0003, 0.01, n)
    close = 80000 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = rng.uniform(10, 100, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )

def test_run_sweep_returns_row_per_grid_combo():
    df = add_indicators(_synthetic_ohlcv())
    rows = run_sweep(df, sl_grid=[0.5, 1.0], rr_grid=[1.5, 2.0], fee=0.001)
    assert len(rows) == 4  # 2 x 2
    for r in rows:
        assert "sl_atr_mult" in r and "rr" in r
        assert "total_pnl_pct" in r and "profit_factor" in r and "win_rate" in r

def test_run_sweep_is_sorted_by_pnl_desc():
    df = add_indicators(_synthetic_ohlcv())
    rows = run_sweep(df, sl_grid=[0.5, 1.0, 1.5], rr_grid=[1.5, 2.5], fee=0.001)
    pnls = [r["total_pnl_pct"] for r in rows]
    assert pnls == sorted(pnls, reverse=True)
