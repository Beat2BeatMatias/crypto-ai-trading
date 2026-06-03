import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from shared.db.models import Trade, Position


async def _seed_open_position(session_factory):
    trade_id = uuid.uuid4()
    async with session_factory() as s:
        s.add(Trade(
            id=trade_id,
            ts_open=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            side="BUY",
            quantity_btc=Decimal("0.001"),
            entry_price=Decimal("80000.00"),
            stop_loss=Decimal("79000.00"),
            take_profit=Decimal("82000.00"),
            status="open",
        ))
        s.add(Position(
            id=uuid.uuid4(),
            trade_id=trade_id,
            symbol="BTC/USDT",
            quantity_btc=Decimal("0.001"),
            entry_price=Decimal("80000.00"),
            current_price=Decimal("80500.00"),
            unrealized_pnl=Decimal("0.5"),
            unrealized_pct=Decimal("0.625"),
            status="open",
            opened_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        ))
        await s.commit()


async def test_list_positions_includes_sl_tp_pnl(client, app_with_db):
    await _seed_open_position(app_with_db.state.session_factory)

    r = await client.get("/api/positions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["stop_loss"] == pytest.approx(79000.0)
    assert data[0]["take_profit"] == pytest.approx(82000.0)
    assert data[0]["sl_pnl_usdt"] == pytest.approx(-1.0, rel=1e-4)
    assert data[0]["tp_pnl_usdt"] == pytest.approx(2.0, rel=1e-4)


async def test_list_positions_includes_futures_fields(client, app_with_db):
    trade_id = uuid.uuid4()
    async with app_with_db.state.session_factory() as s:
        s.add(Trade(
            id=trade_id,
            ts_open=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            side="SELL",
            position_side="SHORT",
            quantity_btc=Decimal("0.001"),
            entry_price=Decimal("80000.00"),
            stop_loss=Decimal("81000.00"),
            take_profit=Decimal("78000.00"),
            status="open",
            leverage=Decimal("1"),
            liquidation_price=Decimal("92000.00"),
        ))
        s.add(Position(
            id=uuid.uuid4(),
            trade_id=trade_id,
            symbol="BTC/USDT:USDT",
            position_side="SHORT",
            quantity_btc=Decimal("0.001"),
            entry_price=Decimal("80000.00"),
            current_price=Decimal("79500.00"),
            leverage=Decimal("1"),
            liquidation_price=Decimal("92000.00"),
            status="open",
            opened_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        ))
        await s.commit()

    r = await client.get("/api/positions")
    assert r.status_code == 200
    data = r.json()[0]
    assert data["position_side"] == "SHORT"
    assert data["liquidation_price"] == pytest.approx(92000.0)
    assert data["sl_pnl_usdt"] == pytest.approx(-1.0, rel=1e-4)
    assert data["tp_pnl_usdt"] == pytest.approx(2.0, rel=1e-4)
