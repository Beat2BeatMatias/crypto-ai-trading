"""Tests for shared.notional_sizing — R11 geometry and leverage."""
import pytest

from shared.notional_sizing import (
    entry_notional_usdt,
    min_position_size_pct_for_exit_legs,
    r11_infeasible_reason,
)
from shared.schemas import Direction


def test_entry_notional_uses_leverage_on_futures():
    n = entry_notional_usdt(
        margin=250.0,
        position_size_pct=0.2,
        leverage=3.0,
        trading_product="futures",
    )
    assert n == pytest.approx(150.0)


def test_fd91182d_short_tp_passes_with_leverage_3x():
    # GIVEN mismo escenario que decisión fd91182d con leverage en notional
    margin = 250.0
    price = 63204.9
    tp = 61314.7
    sl = 64150.0
    min_n = 50.0
    pct = 0.2
    notional = entry_notional_usdt(
        margin=margin, position_size_pct=pct, leverage=3.0, trading_product="futures",
    )
    qty = notional / price
    assert notional == pytest.approx(150.0)
    assert qty * tp >= min_n


def test_fd91182d_short_tp_infeasible_at_1x_max_20pct():
    reason = r11_infeasible_reason(
        margin=250.0,
        max_position_pct=0.2,
        min_notional_usdt=50.0,
        leverage=1.0,
        trading_product="futures",
        price=63204.9,
        direction=Direction.SHORT,
        stop_loss=64150.0,
        take_profit=61314.7,
    )
    assert reason is not None
    assert "min position_size_pct" in reason


def test_min_pct_exit_short_tp_floor():
    floor = min_position_size_pct_for_exit_legs(
        direction=Direction.SHORT,
        price=63204.9,
        stop_loss=64150.0,
        take_profit=61314.7,
        margin=250.0,
        min_notional_usdt=50.0,
        leverage=1.0,
        trading_product="futures",
    )
    assert floor == pytest.approx(0.20616556877877573, rel=1e-6)
