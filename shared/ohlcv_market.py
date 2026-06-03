"""Mercado OHLCV alineado con trading_product (spot | futures)."""


def ohlcv_market_for_product(trading_product: str) -> str:
    return "futures" if trading_product == "futures" else "spot"


def chart_label_for_product(trading_product: str) -> str:
    return "BTC/USDT Perp" if trading_product == "futures" else "BTC/USDT"


def chart_symbol_for_product(trading_product: str) -> str:
    return "BTC/USDT:USDT" if trading_product == "futures" else "BTC/USDT"
