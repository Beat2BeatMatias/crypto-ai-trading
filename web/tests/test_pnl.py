import pytest
from shared.pnl import compute_pnl_usdt, compute_pnl_pct


def test_compute_pnl_usdt_buy_profit():
    assert compute_pnl_usdt(entry=80000.0, quantity=0.001, exit_price=80500.0) == pytest.approx(0.5, rel=1e-4)


def test_compute_pnl_usdt_buy_loss():
    assert compute_pnl_usdt(entry=80000.0, quantity=0.001, exit_price=79500.0) == pytest.approx(-0.5, rel=1e-4)


def test_compute_pnl_pct_buy():
    assert compute_pnl_pct(entry=80000.0, exit_price=80400.0) == pytest.approx(0.5, rel=1e-4)


def test_compute_pnl_returns_none_without_exit_price():
    assert compute_pnl_usdt(entry=80000.0, quantity=0.001, exit_price=None) is None
    assert compute_pnl_pct(entry=80000.0, exit_price=None) is None
