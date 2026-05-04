"""Tests for OrderBookCollector — in-memory snapshot + derived stats."""
import pytest
from collectors.orderbook_collector import OrderBookCollector, OrderBookSnapshot


def make_book() -> dict:
    return {
        "bids": [[60_000.0, 0.5], [59_990.0, 1.2], [59_980.0, 0.8],
                 [59_970.0, 0.3], [59_960.0, 0.4], [59_950.0, 30.0],
                 [59_940.0, 0.2], [59_930.0, 0.1], [59_920.0, 0.5],
                 [59_910.0, 0.3]],
        "asks": [[60_010.0, 0.4], [60_020.0, 0.6], [60_030.0, 0.7],
                 [60_040.0, 0.5], [60_050.0, 25.0],
                 [60_060.0, 0.3], [60_070.0, 0.4], [60_080.0, 0.2],
                 [60_090.0, 0.6], [60_100.0, 0.4]],
    }


def test_snapshot_computes_basic_metrics():
    col = OrderBookCollector(symbol="BTC/USDT")
    col._book = make_book()
    snap = col.snapshot(levels=10)
    assert snap.spread > 0
    assert snap.spread_pct > 0
    assert snap.bid_total_btc > 0
    assert snap.ask_total_btc > 0
    assert snap.imbalance > 0


def test_snapshot_detects_walls():
    col = OrderBookCollector(symbol="BTC/USDT")
    col._book = make_book()
    snap = col.snapshot(levels=10)
    assert snap.bid_wall_size == 30.0
    assert snap.bid_wall_price == 59_950.0
    assert snap.ask_wall_size == 25.0
    assert snap.ask_wall_price == 60_050.0


def test_snapshot_with_no_book_returns_none():
    col = OrderBookCollector(symbol="BTC/USDT")
    assert col.snapshot(levels=10) is None


def test_imbalance_balanced_book():
    col = OrderBookCollector(symbol="BTC/USDT")
    col._book = {
        "bids": [[60_000.0, 1.0]] * 10,
        "asks": [[60_010.0, 1.0]] * 10,
    }
    snap = col.snapshot(levels=10)
    assert 0.95 <= snap.imbalance <= 1.05
