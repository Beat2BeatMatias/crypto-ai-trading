import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from position_payload import build_position_payload, resolve_position_direction
from shared.db.models import Position, Trade


def test_resolve_short_from_trade_when_position_side_missing():
    pos = Position(
        id=uuid.uuid4(),
        symbol="BTC/USDT:USDT",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("80000"),
        status="open",
        opened_at=datetime.now(tz=timezone.utc),
    )
    trade = Trade(
        id=uuid.uuid4(),
        ts_open=datetime.now(tz=timezone.utc),
        side="SELL",
        position_side="SHORT",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("80000"),
        status="open",
        leverage=Decimal("3"),
    )
    assert resolve_position_direction(pos, trade) == "SHORT"


def test_build_position_payload_short_pnl_signs():
    pos = Position(
        id=uuid.uuid4(),
        symbol="BTC/USDT:USDT",
        quantity_btc=Decimal("0.003"),
        entry_price=Decimal("63116.2"),
        current_price=Decimal("63184.1"),
        unrealized_pnl=Decimal("-0.2037"),
        unrealized_pct=Decimal("-0.1076"),
        position_side="SHORT",
        status="open",
        opened_at=datetime.now(tz=timezone.utc),
    )
    trade = Trade(
        id=uuid.uuid4(),
        ts_open=datetime.now(tz=timezone.utc),
        side="SELL",
        position_side="SHORT",
        quantity_btc=Decimal("0.003"),
        entry_price=Decimal("63116.2"),
        stop_loss=Decimal("63602"),
        take_profit=Decimal("61355"),
        status="open",
        leverage=Decimal("3"),
    )
    payload = build_position_payload(pos, trade)
    assert payload["position_side"] == "SHORT"
    assert payload["leverage"] == 3.0
    assert payload["sl_pnl_usdt"] == pytest.approx(-1.4574, rel=1e-3)
    assert payload["tp_pnl_usdt"] == pytest.approx(5.2836, rel=1e-3)
