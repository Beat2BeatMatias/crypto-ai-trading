"""Tests for ContextBuilder — assembles the decisor input context dict."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime,
    Numeric, Text, event, ForeignKey,
)
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from shared.db.models import Indicators, Ohlcv, Position, Decision, DecisionOutcome
from agents.context_builder import ContextBuilder
from collectors.orderbook_collector import OrderBookSnapshot, DepthLevel


# ---------------------------------------------------------------------------
# SQLite-compatible schema (no JSONB, no PostgreSQL server_defaults)
# ---------------------------------------------------------------------------

_sqlite_metadata = MetaData()

_ohlcv_table = Table(
    "ohlcv", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("timeframe", String(4), primary_key=True),
    Column("open", Numeric(18, 8)),
    Column("high", Numeric(18, 8)),
    Column("low", Numeric(18, 8)),
    Column("close", Numeric(18, 8)),
    Column("volume", Numeric(24, 8)),
)

_indicators_table = Table(
    "indicators", _sqlite_metadata,
    Column("time", DateTime, primary_key=True),
    Column("data", JSON, nullable=False),
)

_positions_table = Table(
    "positions", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("trade_id", String(36), nullable=True),
    Column("symbol", String(20), nullable=False, default="BTC/USDT"),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("current_price", Numeric(18, 8)),
    Column("unrealized_pnl", Numeric(18, 4)),
    Column("unrealized_pct", Numeric(8, 4)),
    Column("status", String(10), default="open"),
    Column("opened_at", DateTime, nullable=False),
    Column("updated_at", DateTime),
)

_decisions_table = Table(
    "decisions", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("ts", DateTime, nullable=False),
    Column("agent", String(20), nullable=False),
    Column("model", String(50), nullable=False),
    Column("tokens_in", Integer),
    Column("tokens_out", Integer),
    Column("latency_ms", Integer),
    Column("input", JSON, nullable=False),
    Column("output", JSON, nullable=False),
    Column("outcome", JSON),
    Column("trade_id", String(36), nullable=True),
    Column("executed", Boolean, default=False),
    Column("rejected_reason", String(200)),
)

# Trades table stub — mantener en sync con migrations 001-007.
_trades_table = Table(
    "trades", _sqlite_metadata,
    Column("id", String(36), primary_key=True),
    Column("decision_id", String(36), nullable=True),
    Column("ts_open", DateTime, nullable=False),
    Column("ts_close", DateTime),
    Column("side", String(4), nullable=False),
    Column("quantity_btc", Numeric(18, 8), nullable=False),
    Column("entry_price", Numeric(18, 8), nullable=False),
    Column("exit_price", Numeric(18, 8)),
    Column("pnl_usdt", Numeric(18, 4)),
    Column("pnl_pct", Numeric(8, 4)),
    Column("status", String(12), nullable=False),
    Column("stop_loss", Numeric(18, 8)),
    Column("take_profit", Numeric(18, 8)),
    Column("close_reason", String(20)),
    Column("order_id_open", String(50)),
    Column("order_id_close", String(50)),
    Column("order_id_sl", String(50)),   # migration 007
    Column("order_id_tp", String(50)),   # migration 007
    Column("fees_usdt", Numeric(18, 4)),
    Column("close_requested", Boolean, default=False),  # migration 002
)

_decision_outcomes_table = Table(
    "decision_outcomes", _sqlite_metadata,
    Column("decision_id", String(36), ForeignKey("decisions.id"), primary_key=True),
    Column("horizon_min", Integer, nullable=False),
    Column("matured", Boolean, nullable=False),
    Column("forward_return_pct", Numeric(10, 5)),
    Column("mfe_pct", Numeric(10, 5)),
    Column("mae_pct", Numeric(10, 5)),
    Column("time_to_mfe_min", Integer),
    Column("time_to_mae_min", Integer),
    Column("sl_dist_pct", Numeric(10, 5)),
    Column("tp_target_pct", Numeric(10, 5)),
    Column("classification", String(32), nullable=False),
    Column("computed_at", DateTime, nullable=False),
    Column("postmortem_status", String(16)),
    Column("lesson_raw", JSON),
    Column("lesson_normalized", JSON),
    Column("postmortem_at", DateTime),
)


# ---------------------------------------------------------------------------
# ORM event hooks — generate PKs / timestamps at Python level for SQLite
# ---------------------------------------------------------------------------

def _before_insert_indicators(mapper, connection, target):
    if target.time is None:
        target.time = datetime.now(timezone.utc)


def _before_insert_position(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.opened_at is None:
        target.opened_at = datetime.now(timezone.utc)


def _before_insert_decision(mapper, connection, target):
    if target.id is None:
        target.id = uuid.uuid4()
    if target.ts is None:
        target.ts = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def session():
    """Fresh in-memory SQLite session seeded with one Indicators row and OHLCV history."""
    event.listen(Indicators, "before_insert", _before_insert_indicators)
    event.listen(Position, "before_insert", _before_insert_position)
    event.listen(Decision, "before_insert", _before_insert_decision)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        # Seed one Indicators row with 1h timeframe data
        sess.add(Indicators(
            time=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            data={
                "1h": {
                    "last_close": 95000.0,
                    "rsi": 55.0,
                    "ema20": 94000.0,
                    "ema50": 93000.0,
                    "ema200": 90000.0,
                    "macd": 10.0,
                    "macd_signal": 8.0,
                    "macd_hist": 2.0,
                    "bb_upper": 96000.0,
                    "bb_lower": 92000.0,
                    "atr": 500.0,
                },
                "5m": {
                    "last_close": 94900.0,
                    "rsi": 48.0,
                    "bb_pct": 0.55,
                },
            },
        ))

        # Seed OHLCV 1h rows: 170 candles ending at 2025-01-01 11:00 UTC
        # (the current price comes from Indicators, not OHLCV)
        base_price = Decimal("94000.00")
        for i in range(170):
            candle_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
            # i=0 is oldest (169h ago), i=169 is 1h ago relative to "current" candle time
            candle_time = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc) - timedelta(hours=(169 - i))
            sess.add(Ohlcv(
                time=candle_time,
                timeframe="1h",
                open=base_price,
                high=base_price + Decimal("100"),
                low=base_price - Decimal("100"),
                close=base_price + Decimal(str(i)),  # incremental so each is distinct
                volume=Decimal("10.0"),
            ))

        await sess.commit()
        yield sess

    event.remove(Indicators, "before_insert", _before_insert_indicators)
    event.remove(Position, "before_insert", _before_insert_position)
    event.remove(Decision, "before_insert", _before_insert_decision)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper to call build() with sensible defaults
# ---------------------------------------------------------------------------

async def _build(session: AsyncSession, **overrides) -> dict:
    builder = ContextBuilder(session, symbol="BTC/USDT")
    defaults = dict(
        orderbook=None,
        usdt_balance=1000.0,
        btc_held=0.0,
        playbook_content="# Playbook v0",
        max_simultaneous_trades=2,
        daily_stop_pct=0.02,
        decisor_interval_min=5,
        mode="PAPER_TRADING",
        taker_fee_pct=0.001,
        maker_fee_pct=0.001,
        current_drawdown_pct=0.0,
    )
    defaults.update(overrides)
    return await builder.build(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_returns_all_required_keys(session: AsyncSession):
    # GIVEN a fresh DB with one Indicators row

    # WHEN building the context
    ctx = await _build(session)

    # THEN all required keys are present
    required_keys = [
        "timestamp_utc", "price", "rsi_5m", "rsi_1h",
        "spread", "imbalance", "open_positions_count",
        "playbook", "mode", "taker_fee_pct", "roundtrip_fee_pct",
    ]
    for key in required_keys:
        assert key in ctx, f"Missing key: {key}"

    # Spot-check some values
    assert ctx["price"] == 95000.0
    assert ctx["rsi_1h"] == 55.0
    assert ctx["rsi_5m"] == 48.0
    assert ctx["mode"] == "PAPER_TRADING"
    assert ctx["playbook"] == "# Playbook v0"
    assert ctx["spread"] == 0          # no orderbook
    assert ctx["imbalance"] is None    # no orderbook → sin datos reales
    assert ctx["open_positions_count"] == 0


@pytest.mark.asyncio
async def test_open_positions_count_reflects_db(session: AsyncSession):
    # GIVEN one open Position row in the DB
    session.add(Position(
        symbol="BTC/USDT",
        quantity_btc=Decimal("0.001"),
        entry_price=Decimal("95000.00"),
        status="open",
        opened_at=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    ))
    await session.commit()

    # WHEN building the context
    ctx = await _build(session)

    # THEN open_positions_count equals 1
    assert ctx["open_positions_count"] == 1


@pytest.mark.asyncio
async def test_roundtrip_fee_pct_equals_taker_times_two(session: AsyncSession):
    # GIVEN a taker fee of 0.1 % (0.001 as a fraction)

    # WHEN building the context
    ctx = await _build(session, taker_fee_pct=0.001)

    # THEN roundtrip_fee_pct is taker * 2 expressed as a percentage
    assert ctx["taker_fee_pct"] == pytest.approx(0.1)       # 0.001 * 100
    assert ctx["roundtrip_fee_pct"] == pytest.approx(0.2)   # 0.001 * 2 * 100


@pytest.mark.asyncio
async def test_atr_timeframe_key_in_context(session: AsyncSession):
    # GIVEN atr_timeframe="5m"
    ctx = await _build(session, atr_timeframe="5m")

    # THEN explicit atr_timeframe key is present
    assert "atr_timeframe" in ctx
    assert ctx["atr_timeframe"] == "5m"


@pytest.mark.asyncio
async def test_current_drawdown_pct_passed_through(session: AsyncSession):
    # GIVEN current_drawdown_pct=-0.05
    ctx = await _build(session, current_drawdown_pct=-0.05)

    # THEN it is present in the context
    assert ctx["current_drawdown_pct"] == pytest.approx(-0.05)


@pytest.mark.asyncio
async def test_volume_keys_default_to_zero_when_no_data(session: AsyncSession):
    # GIVEN no volume data in indicators (fixture only has 1h data, no volume_current)
    ctx = await _build(session, atr_timeframe="1h")

    # THEN volume keys are present with 0.0 defaults
    assert "volume_current" in ctx
    assert "volume_avg20" in ctx
    assert "volume_ratio" in ctx
    assert ctx["volume_current"] == pytest.approx(0.0)
    assert ctx["volume_avg20"] == pytest.approx(0.0)
    assert ctx["volume_ratio"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_new_config_v2_keys_present_with_defaults(session: AsyncSession):
    # GIVEN no calibration overrides
    ctx = await _build(session)

    # THEN all 6 new config vars are present with their defaults
    assert ctx["min_fees_to_tp_ratio"] == pytest.approx(3.0)
    assert ctx["min_confluences_buy"] == 2
    assert ctx["cooldown_after_sell_min"] == 15
    assert ctx["subjective_adj_max"] == pytest.approx(0.10)
    assert ctx["expected_holding_max_min"] == 240
    assert ctx["confluence_weak_factor"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Bloque G — _format_last_decisions con warnings del CoherenceChecker
# ---------------------------------------------------------------------------

class _FakeDecision:
    """Stub mínimo de Decision para testear _format_last_decisions."""
    def __init__(self, action: str, confidence: float,
                 warnings: list[dict] | None = None,
                 two_pass: bool = False,
                 close_reason: str | None = None):
        self.ts = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
        self.output = {
            "action": action,
            "confidence": confidence,
            "coherence_warnings": warnings or [],
            "two_pass_triggered": two_pass,
        }
        self.outcome = {"close_reason": close_reason} if close_reason else None


def test_format_last_decisions_shows_warning_details():
    # GIVEN una decisión pasada con 2 coherence warnings
    decision = _FakeDecision(
        action="BUY",
        confidence=0.72,
        warnings=[
            {"rule_id": "C1", "message": "RSI(15m)=55, no oversold", "severity": "warning", "evidence": {}},
            {"rule_id": "C3", "message": "TRENDING_UP sin ADX fuerte (ADX=14)", "severity": "warning", "evidence": {}},
        ],
        two_pass=True,
    )

    # WHEN formateando el bloque G
    result = ContextBuilder._format_last_decisions([decision])

    # THEN el detalle de cada warning aparece en el texto
    assert "⚠" in result
    assert "C1" in result
    assert "RSI(15m)=55" in result
    assert "C3" in result
    assert "TRENDING_UP sin ADX" in result
    assert "[two-pass]" in result


def test_format_last_decisions_no_warnings_shows_no_warning_block():
    # GIVEN una decisión pasada sin warnings
    decision = _FakeDecision(action="HOLD", confidence=0.5)

    # WHEN formateando el bloque G
    result = ContextBuilder._format_last_decisions([decision])

    # THEN no hay bloque de warnings en la salida
    assert "⚠" not in result
    assert "[two-pass]" not in result


def test_format_last_decisions_limits_warnings_to_four():
    # GIVEN una decisión con 6 warnings (más que el límite de 4)
    warnings = [
        {"rule_id": f"C{i}", "message": f"warning {i}", "severity": "warning", "evidence": {}}
        for i in range(1, 7)
    ]
    decision = _FakeDecision(action="BUY", confidence=0.6, warnings=warnings)

    # WHEN formateando
    result = ContextBuilder._format_last_decisions([decision])

    # THEN se muestran sólo 4 y aparece el mensaje "y 2 más"
    assert "y 2 más" in result


def test_format_last_decisions_empty_list_returns_no_prev_message():
    # GIVEN lista vacía de decisiones
    result = ContextBuilder._format_last_decisions([])

    # THEN devuelve el mensaje de sin decisiones
    assert "Sin decisiones previas" in result


# ---------------------------------------------------------------------------
# pct_1h / pct_4h / pct_24h / pct_7d — calculados desde OHLCV
# ---------------------------------------------------------------------------

def test_pct_change_returns_zero_when_not_enough_rows():
    # GIVEN menos filas que el offset requerido
    rows = []

    # WHEN calculando con offset=1
    result = ContextBuilder._pct_change(rows, offset=1, current_price=100.0)

    # THEN devuelve 0.0
    assert result == 0.0


def test_pct_change_calculates_correctly():
    # GIVEN 3 filas con precios de cierre conocidos
    class _Row:
        def __init__(self, close):
            self.close = Decimal(str(close))

    rows = [_Row(100), _Row(105), _Row(110)]  # oldest to newest
    # offset=1: compara price con rows[-2].close = 105
    # offset=2: compara price con rows[-3].close = 100

    # WHEN calculando pct_1h (offset=1)
    result_1 = ContextBuilder._pct_change(rows, offset=1, current_price=110.0)
    # THEN (110 - 105) / 105 * 100 = 4.7619...
    assert result_1 == pytest.approx((110 - 105) / 105 * 100, rel=1e-3)

    # WHEN calculando pct_2 (offset=2)
    result_2 = ContextBuilder._pct_change(rows, offset=2, current_price=110.0)
    # THEN (110 - 100) / 100 * 100 = 10.0
    assert result_2 == pytest.approx(10.0, rel=1e-3)


@pytest.mark.asyncio
async def test_pct_fields_not_zero_when_ohlcv_exists(session: AsyncSession):
    # GIVEN una sesión con 170 velas OHLCV sembradas en el fixture
    # (precio actual = 95000 desde Indicators, cierre 1h atrás = 94000 + 169 = 94169)

    # WHEN construyendo el context
    ctx = await _build(session)

    # THEN pct_1h, pct_4h, pct_24h son distintos de 0.0
    assert ctx["pct_1h"] != 0.0, "pct_1h sigue siendo 0, no se calculó"
    assert ctx["pct_4h"] != 0.0, "pct_4h sigue siendo 0, no se calculó"
    assert ctx["pct_24h"] != 0.0, "pct_24h sigue siendo 0, no se calculó"


@pytest.mark.asyncio
async def test_pct_1h_value_is_correct(session: AsyncSession):
    # GIVEN fixture con 170 velas; candle[-2] (1h atrás) tiene close = 94000 + 168 = 94168
    # precio actual = 95000
    # pct_1h = (95000 - 94168) / 94168 * 100

    # WHEN construyendo el context
    ctx = await _build(session)

    expected_close_1h_ago = Decimal("94000") + Decimal("168")
    expected_pct = float(
        (Decimal("95000") - expected_close_1h_ago) / expected_close_1h_ago * 100
    )

    # THEN pct_1h coincide con el valor esperado (tolerancia 0.01%)
    assert ctx["pct_1h"] == pytest.approx(expected_pct, rel=1e-3)


@pytest.mark.asyncio
async def test_pct_fields_zero_when_no_ohlcv(session: AsyncSession):
    # GIVEN una sesión sin filas OHLCV (borramos las sembradas)
    from sqlalchemy import text
    await session.execute(text("DELETE FROM ohlcv"))
    await session.commit()

    # WHEN construyendo el context
    ctx = await _build(session)

    # THEN todos los pct vuelven a 0.0
    assert ctx["pct_1h"] == 0.0
    assert ctx["pct_4h"] == 0.0
    assert ctx["pct_24h"] == 0.0
    assert ctx["pct_7d"] == 0.0


# ---------------------------------------------------------------------------
# high_24h / low_24h — calculados desde OHLCV real
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high_low_24h_from_ohlcv(session: AsyncSession):
    # GIVEN fixture con 170 velas 1h (close = 94000+i, high = close+100, low = close-100)
    # Las últimas 24 velas tienen: high_max = 94000+169+100 = 94269, low_min = 94000+146-100 = 94046

    # WHEN construyendo el context
    ctx = await _build(session)

    # En el fixture: high = base_price + 100 = 94000 + 100 = 94100 (fijo para todas las velas)
    #               low  = base_price - 100 = 94000 - 100 = 93900 (fijo para todas las velas)
    expected_high = float(Decimal("94000") + Decimal("100"))  # 94100
    expected_low  = float(Decimal("94000") - Decimal("100"))  # 93900

    assert ctx["high_24h"] == pytest.approx(expected_high, rel=1e-4)
    assert ctx["low_24h"]  == pytest.approx(expected_low, rel=1e-4)
    # Sanity: no pueden ser price*1.02 ni price*0.98 (price=95000)
    assert ctx["high_24h"] != pytest.approx(95000.0 * 1.02, rel=1e-3)
    assert ctx["low_24h"]  != pytest.approx(95000.0 * 0.98, rel=1e-3)


@pytest.mark.asyncio
async def test_high_low_24h_fallback_to_price_when_no_ohlcv(session: AsyncSession):
    # GIVEN sin datos OHLCV
    from sqlalchemy import text
    await session.execute(text("DELETE FROM ohlcv"))
    await session.commit()

    # WHEN construyendo el context
    ctx = await _build(session)

    # THEN high_24h y low_24h son iguales al precio actual (fallback seguro)
    assert ctx["high_24h"] == pytest.approx(95000.0)
    assert ctx["low_24h"]  == pytest.approx(95000.0)


# ---------------------------------------------------------------------------
# dist_support_pct / dist_resistance_pct — distancias reales
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dist_support_and_resistance_calculated(session: AsyncSession):
    # GIVEN indicadores con ema50_1h=93000, ema200_1h=90000, price=95000

    # WHEN construyendo el context
    ctx = await _build(session)

    # THEN dist_support_pct = (ema50_1h - price) / price * 100 = (93000 - 95000) / 95000 * 100
    expected_support_dist = (93000 - 95000) / 95000 * 100
    expected_resist_dist  = (90000 - 95000) / 95000 * 100

    assert ctx["dist_support_pct"]    == pytest.approx(expected_support_dist, rel=1e-3)
    assert ctx["dist_resistance_pct"] == pytest.approx(expected_resist_dist, rel=1e-3)
    assert ctx["dist_support_pct"]    != 0
    assert ctx["dist_resistance_pct"] != 0


@pytest.mark.asyncio
async def test_support_resistance_1h_use_ema_levels(session: AsyncSession):
    # GIVEN ema50_1h=93000 y ema200_1h=90000 en el fixture

    # WHEN construyendo el context
    ctx = await _build(session)

    # THEN support_1h = ema50_1h (no ema50 * 0.99)
    assert ctx["support_1h"]    == pytest.approx(93000.0)
    # THEN resistance_1h = ema200_1h (no ema50 * 1.01)
    assert ctx["resistance_1h"] == pytest.approx(90000.0)


# ---------------------------------------------------------------------------
# imbalance — None cuando no hay orderbook
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_imbalance_none_when_no_orderbook(session: AsyncSession):
    # GIVEN sin orderbook

    # WHEN construyendo el context (orderbook=None por defecto en _build)
    ctx = await _build(session)

    # THEN imbalance es None y label es "n/d"
    assert ctx["imbalance"] is None
    assert ctx["imbalance_label"] == "n/d"


# ---------------------------------------------------------------------------
# last_decision_ago — tiempo transcurrido real
# ---------------------------------------------------------------------------

def test_format_time_ago_seconds():
    # GIVEN un timestamp de hace 45 segundos
    ts = datetime.now(tz=timezone.utc) - timedelta(seconds=45)

    # WHEN formateando
    result = ContextBuilder._format_time_ago(ts)

    # THEN devuelve formato "Xs ago"
    assert "s ago" in result
    assert "min" not in result


def test_format_time_ago_minutes():
    # GIVEN un timestamp de hace 45 minutos (menos de 1h, para que no entre en el branch de horas)
    ts = datetime.now(tz=timezone.utc) - timedelta(minutes=45)

    # WHEN formateando
    result = ContextBuilder._format_time_ago(ts)

    # THEN devuelve "45min ago"
    assert "45min ago" in result


def test_format_time_ago_hours():
    # GIVEN un timestamp de hace 2h 30min
    ts = datetime.now(tz=timezone.utc) - timedelta(hours=2, minutes=30)

    # WHEN formateando
    result = ContextBuilder._format_time_ago(ts)

    # THEN devuelve "2h 30min ago"
    assert "2h 30min ago" in result


@pytest.mark.asyncio
async def test_last_decision_ago_not_na_when_decisions_exist(session: AsyncSession):
    # GIVEN una decisión reciente en la DB
    session.add(Decision(
        ts=datetime.now(tz=timezone.utc) - timedelta(minutes=15),
        agent="decisor",
        model="gpt-4o",
        tokens_in=100,
        tokens_out=50,
        latency_ms=800,
        input={},
        output={"action": "HOLD", "confidence": 0.5},
    ))
    await session.commit()

    # WHEN construyendo el context
    ctx = await _build(session)

    # THEN last_decision_ago no es "n/a"
    assert ctx["last_decision_ago"] != "n/a"
    assert "min ago" in ctx["last_decision_ago"] or "s ago" in ctx["last_decision_ago"]


# ---------------------------------------------------------------------------
# precio — single source of truth: orderbook.top_ask cuando está disponible
# ---------------------------------------------------------------------------

def _make_snapshot(top_ask: float, top_bid: float | None = None) -> OrderBookSnapshot:
    """Construye un OrderBookSnapshot mínimo para tests."""
    bid = top_bid if top_bid is not None else top_ask - 1.0
    mid = (bid + top_ask) / 2
    depth = DepthLevel(price_pct=0.1, bid_btc=1.0, bid_usdt=mid, ask_btc=1.0, ask_usdt=mid)
    return OrderBookSnapshot(
        spread=top_ask - bid,
        spread_pct=(top_ask - bid) / mid * 100,
        bid_total_btc=10.0,
        ask_total_btc=10.0,
        imbalance=1.0,
        bid_wall_price=bid - 10,
        bid_wall_size=5.0,
        bid_wall_distance_pct=0.1,
        ask_wall_price=top_ask + 10,
        ask_wall_size=5.0,
        ask_wall_distance_pct=0.1,
        top_bid=bid,
        top_ask=top_ask,
        depth_01pct=depth,
        depth_025pct=depth,
        depth_05pct=depth,
        depth_1pct=depth,
        mid_impact_pct=None,
    )


@pytest.mark.asyncio
async def test_price_uses_orderbook_top_ask_when_available(session: AsyncSession):
    # GIVEN un orderbook con top_ask distinto al last_close del indicador (95000)
    ob = _make_snapshot(top_ask=96500.0)

    # WHEN construyendo el context con ese orderbook
    ctx = await _build(session, orderbook=ob)

    # THEN ctx["price"] es el top_ask del orderbook, no el last_close stale
    assert ctx["price"] == pytest.approx(96500.0), (
        "El precio en el context debe coincidir con ob.top_ask "
        "para que LLM y RiskGate usen la misma referencia"
    )


@pytest.mark.asyncio
async def test_price_falls_back_to_last_close_when_no_orderbook(session: AsyncSession):
    # GIVEN sin orderbook (indicador tiene last_close=95000 en el fixture)

    # WHEN construyendo el context sin orderbook
    ctx = await _build(session, orderbook=None)

    # THEN ctx["price"] es el last_close del indicador 1h
    assert ctx["price"] == pytest.approx(95000.0), (
        "Sin orderbook el precio debe venir del last_close del indicador como fallback"
    )


@pytest.mark.asyncio
async def test_block_k_lessons_injected_from_decision_outcomes(session: AsyncSession):
    now = datetime.now(tz=timezone.utc)
    decision = Decision(
        ts=now - timedelta(hours=2),
        agent="decisor",
        model="test",
        input={"price": 100.0},
        output={"action": "BUY"},
        executed=True,
    )
    session.add(decision)
    await session.commit()
    session.add(DecisionOutcome(
        decision_id=decision.id,
        horizon_min=240,
        matured=True,
        classification="BAD_BUY",
        computed_at=now - timedelta(hours=1),
        postmortem_status="completed",
        lesson_normalized={
            "route": "remap",
            "confidence": 0.9,
            "block_k_line": "[2026-05-23T14:30Z] BAD_BUY: no operar rebote RSI sin volumen.",
            "dedupe_key": "remap:test:H",
        },
    ))
    await session.commit()

    ctx = await _build(session)

    assert "no operar rebote RSI sin volumen" in ctx["block_k_lessons"]
    assert "ninguna confluencia promovida" in ctx["confluence_registry_block"]
