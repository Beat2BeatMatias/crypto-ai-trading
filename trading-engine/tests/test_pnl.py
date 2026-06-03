from shared.pnl import compute_pnl_usdt_directional, compute_pnl_pct_directional


def test_pnl_short_profit_when_price_drops():
    pnl = compute_pnl_usdt_directional(
        entry=100_000.0, quantity=0.01, exit_price=96_000.0, direction="SHORT",
    )
    assert pnl == 40.0


def test_pnl_long_profit_when_price_rises():
    pnl = compute_pnl_usdt_directional(
        entry=100_000.0, quantity=0.01, exit_price=104_000.0, direction="LONG",
    )
    assert pnl == 40.0


def test_pnl_pct_directional_short():
    pct = compute_pnl_pct_directional(entry=100_000.0, exit_price=96_000.0, direction="SHORT")
    assert pct == 4.0
