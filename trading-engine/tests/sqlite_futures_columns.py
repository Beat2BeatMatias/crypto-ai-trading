"""Columnas SQLite extra para alinear fixtures de test con migración 016 (futures)."""
from sqlalchemy import Column, Numeric, String

FUTURES_TRADE_COLUMNS = [
    Column("position_side", String(5), default="LONG"),
    Column("leverage", Numeric(5, 2), default=1),
    Column("liquidation_price", Numeric(18, 8)),
    Column("margin_mode", String(10), default="isolated"),
    Column("funding_paid_usdt", Numeric(18, 4)),
]

FUTURES_POSITION_COLUMNS = [
    Column("position_side", String(5), default="LONG"),
    Column("leverage", Numeric(5, 2), default=1),
    Column("liquidation_price", Numeric(18, 8)),
]

FUTURES_BALANCE_COLUMNS = [
    Column("margin_balance", Numeric(18, 4)),
    Column("available_margin", Numeric(18, 4)),
]
