"""Smoke tests that all ORM models exist with the right columns."""
import pytest
from sqlalchemy import inspect

from shared.db.models import (
    Base, Ohlcv, Indicators, Decision, Trade, Position,
    PlaybookVersion, ConfigEntry, ConfigHistory, DailyStats, FeeSnapshot,
)


def _columns(model) -> set[str]:
    return {c.name for c in inspect(model).columns}


def test_ohlcv_columns():
    cols = _columns(Ohlcv)
    assert {"time", "timeframe", "open", "high", "low", "close", "volume"} <= cols


def test_indicators_columns():
    cols = _columns(Indicators)
    assert {"time", "data"} <= cols


def test_decision_columns():
    cols = _columns(Decision)
    assert {
        "id", "ts", "agent", "model", "tokens_in", "tokens_out", "latency_ms",
        "input", "output", "outcome", "trade_id", "executed", "rejected_reason",
    } <= cols


def test_trade_columns():
    cols = _columns(Trade)
    assert {
        "id", "decision_id", "ts_open", "ts_close", "side", "quantity_btc",
        "entry_price", "exit_price", "pnl_usdt", "pnl_pct", "status",
        "stop_loss", "take_profit", "close_reason", "order_id_open",
        "order_id_close", "fees_usdt",
    } <= cols


def test_position_columns():
    cols = _columns(Position)
    assert {
        "id", "trade_id", "symbol", "quantity_btc", "entry_price",
        "current_price", "unrealized_pnl", "unrealized_pct",
        "status", "opened_at", "updated_at",
    } <= cols


def test_playbook_version_columns():
    cols = _columns(PlaybookVersion)
    assert {
        "id", "version", "ts_generated", "content", "model",
        "trades_analyzed", "win_rate", "pnl_summary", "active",
    } <= cols


def test_config_entry_columns():
    cols = _columns(ConfigEntry)
    assert {"key", "value", "value_type", "description", "updated_at"} <= cols


def test_config_history_columns():
    cols = _columns(ConfigHistory)
    assert {"id", "ts", "key", "old_value", "new_value", "changed_by"} <= cols


def test_daily_stats_columns():
    cols = _columns(DailyStats)
    assert {
        "date", "decisions_total", "trades_executed", "wins", "losses",
        "pnl_usdt", "pnl_pct", "max_drawdown", "breakdown",
    } <= cols


def test_fee_snapshot_columns():
    cols = _columns(FeeSnapshot)
    assert {"id", "ts", "symbol", "maker_fee", "taker_fee", "raw"} <= cols


def test_all_tables_use_same_metadata():
    """All models must register on the shared Base.metadata."""
    table_names = {t.name for t in Base.metadata.sorted_tables}
    expected = {
        "ohlcv", "indicators", "decisions", "trades", "positions",
        "playbook_versions", "config", "config_history", "daily_stats",
        "fee_snapshots",
    }
    assert expected <= table_names
