"""Trading engine entrypoint. Wires all components and starts the scheduler loop."""
from __future__ import annotations
import asyncio
import signal
import structlog
from shared.db.base import create_engine_from_url, create_session_factory
from shared.config_store import ConfigKey, ConfigStore
from config import get_settings
from exchange import build_binance_client
from collectors.price_collector import PriceCollector
from collectors.orderbook_collector import OrderBookCollector
from execution.fee_manager import FeeManager
from execution.executor import Executor
from execution.order_tracker import OrderTracker
from execution.position_manager import PositionManager
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager
from agents.decisor import Decisor
from agents.supervisor import Supervisor
from risk.risk_gate import RiskGate
from risk.circuit_breaker import CircuitBreaker
from scheduler import EngineScheduler

logger = structlog.get_logger()


async def _compute_risk_metrics(session, usdt_balance: float) -> tuple[float, float]:
    """Calcula daily_pnl y total_drawdown reales para las reglas R9 y drawdown del Risk Gate.

    Returns:
        daily_pnl_frac: P&L de trades cerrados hoy como fracción (ej. -0.03 = -3%).
        total_drawdown_frac: drawdown desde el pico histórico de balance (negativo o cero).
    """
    from datetime import date, datetime, timezone
    from sqlalchemy import select
    from shared.db.models import Trade as _Trade, BalanceSnapshot as _BalSnap

    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)

    trades_today = (await session.execute(
        select(_Trade).where(
            _Trade.ts_close >= today_start,
            _Trade.status == "closed",
        )
    )).scalars().all()

    daily_pnl_usdt = sum(float(t.pnl_usdt or 0) for t in trades_today)

    # Capital de referencia: último snapshot antes de medianoche UTC; si no existe, capital actual.
    start_snap = (await session.execute(
        select(_BalSnap)
        .where(_BalSnap.ts < today_start)
        .order_by(_BalSnap.ts.desc())
        .limit(1)
    )).scalar_one_or_none()
    ref_capital = float(start_snap.usdt) if start_snap else max(usdt_balance, 1.0)
    daily_pnl_frac = daily_pnl_usdt / ref_capital if ref_capital > 0 else 0.0

    # Drawdown total: balance actual vs pico histórico en balance_snapshots.
    peak_row = (await session.execute(
        select(_BalSnap.usdt).order_by(_BalSnap.usdt.desc()).limit(1)
    )).scalar_one_or_none()
    peak = float(peak_row) if peak_row else usdt_balance
    total_drawdown_frac = (usdt_balance - peak) / peak if peak > 0 else 0.0

    return daily_pnl_frac, total_drawdown_frac


def _parse_providers(csv: str) -> list[LLMProvider]:
    """Parsea CSV de provider IDs, ignorando valores inválidos."""
    result = []
    for token in csv.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            result.append(LLMProvider(token))
        except ValueError:
            logger.warning("main.invalid_provider_in_config", value=token)
    return result


async def run() -> None:
    settings = get_settings()
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ])

    engine_db = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine_db)

    try:
        from google import genai
        gemini_client = genai.Client(api_key=settings.gemini_api_key)
    except Exception:
        gemini_client = None
        logger.warning("gemini.client_init_failed")

    try:
        from groq import AsyncGroq
        groq_client = AsyncGroq(api_key=settings.groq_api_key)
    except Exception:
        groq_client = None
        logger.warning("groq.client_init_failed")

    llm = LLMClient(gemini_client=gemini_client, groq_client=groq_client)
    exchange = build_binance_client()

    # Bootstrap
    async with session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        await PromptManager(s).seed_playbook_v0()
        fee_mgr = FeeManager(exchange, s, symbol=settings.symbol)
        await fee_mgr.refresh()

    orderbook = OrderBookCollector(symbol=settings.symbol, exchange=exchange)
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)  # defaults; updated each tick from config
    sched = EngineScheduler()

    async def decisor_tick() -> None:
        if cb.engine_paused:
            logger.warning("engine.paused")
            return

        async with session_factory() as s:
            store = ConfigStore(s)
            if await store.get_typed(ConfigKey.SUPERVISOR_RUN_NOW):
                await store.set(ConfigKey.SUPERVISOR_RUN_NOW, "false", changed_by="system")
                logger.info("supervisor.manual_trigger")
                await supervisor_tick()

        async with session_factory() as s:
            store = ConfigStore(s)
            mode = await store.get(ConfigKey.MODE)
            kill = await store.get_typed(ConfigKey.KILL_SWITCH)
            max_pos = await store.get_typed(ConfigKey.MAX_POSITION_PCT)
            max_sim = await store.get_typed(ConfigKey.MAX_SIMULTANEOUS_TRADES)
            daily_stop = await store.get_typed(ConfigKey.DAILY_STOP_PCT)
            interval_min = await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN)
            decisor_provider = LLMProvider(await store.get(ConfigKey.DECISOR_PROVIDER))
            fallbacks = _parse_providers(await store.get(ConfigKey.FALLBACK_PROVIDERS))
            atr_timeframe = await store.get(ConfigKey.ATR_TIMEFRAME)
            min_rr_ratio = await store.get_typed(ConfigKey.MIN_RR_RATIO)
            sl_atr_multiplier = await store.get_typed(ConfigKey.SL_ATR_MULTIPLIER)
            max_drawdown_pct = await store.get_typed(ConfigKey.MAX_DRAWDOWN_PCT)
            max_slippage_pct = await store.get_typed(ConfigKey.MAX_SLIPPAGE_PCT)
            cb.update_thresholds(daily_stop_pct=daily_stop, max_drawdown_pct=max_drawdown_pct)
            calibration = {
                "sl_atr_max_multiplier": await store.get_typed(ConfigKey.SL_ATR_MAX_MULTIPLIER),
                "conf_threshold_trending_up": await store.get_typed(ConfigKey.CONF_THRESHOLD_TRENDING_UP),
                "conf_threshold_range": await store.get_typed(ConfigKey.CONF_THRESHOLD_RANGE),
                "conf_threshold_high_vol": await store.get_typed(ConfigKey.CONF_THRESHOLD_HIGH_VOL),
                "rsi_overbought_1h": await store.get_typed(ConfigKey.RSI_OVERBOUGHT_1H),
                "conf_base_0": await store.get_typed(ConfigKey.CONF_BASE_0),
                "conf_base_1": await store.get_typed(ConfigKey.CONF_BASE_1),
                "conf_base_2": await store.get_typed(ConfigKey.CONF_BASE_2),
                "conf_base_3": await store.get_typed(ConfigKey.CONF_BASE_3),
                "conf_base_4plus": await store.get_typed(ConfigKey.CONF_BASE_4PLUS),
                "peso_timeframe_partial": await store.get_typed(ConfigKey.PESO_TIMEFRAME_PARTIAL),
                "peso_timeframe_minimal": await store.get_typed(ConfigKey.PESO_TIMEFRAME_MINIMAL),
                "peso_regime_range": await store.get_typed(ConfigKey.PESO_REGIME_RANGE),
                "peso_regime_high_vol": await store.get_typed(ConfigKey.PESO_REGIME_HIGH_VOL),
                "adj_volume_boost": await store.get_typed(ConfigKey.ADJ_VOLUME_BOOST),
                "adj_volume_ratio": await store.get_typed(ConfigKey.ADJ_VOLUME_RATIO),
                "adj_antipattern_penalty": await store.get_typed(ConfigKey.ADJ_ANTIPATTERN_PENALTY),
                "adj_spread_penalty": await store.get_typed(ConfigKey.ADJ_SPREAD_PENALTY),
                "adj_spread_threshold_pct": await store.get_typed(ConfigKey.ADJ_SPREAD_THRESHOLD_PCT),
                "adj_orderbook_penalty": await store.get_typed(ConfigKey.ADJ_ORDERBOOK_PENALTY),
                "adj_orderbook_ratio": await store.get_typed(ConfigKey.ADJ_ORDERBOOK_RATIO),
                "factor_conf_60": await store.get_typed(ConfigKey.FACTOR_CONF_60),
                "factor_conf_70": await store.get_typed(ConfigKey.FACTOR_CONF_70),
                "factor_conf_80": await store.get_typed(ConfigKey.FACTOR_CONF_80),
                "factor_conf_90": await store.get_typed(ConfigKey.FACTOR_CONF_90),
                "factor_regime_non_trending": await store.get_typed(ConfigKey.FACTOR_REGIME_NON_TRENDING),
            }

            collector = PriceCollector(exchange, s, symbol=settings.symbol)
            try:
                for tf in ("1m", "5m", "15m", "1h", "4h"):
                    await collector.fetch_and_persist(timeframe=tf)
            except Exception as e:
                logger.warning("engine.ohlcv_fetch_failed_using_cached_data", error=str(e))
            await collector.compute_and_persist_indicators()

            fees = FeeManager(exchange, s, symbol=settings.symbol)
            await fees.get_or_refresh()

            try:
                balance = await exchange.fetch_balance()
                usdt = float(balance.get("free", {}).get("USDT", 0.0))
                btc = float(balance.get("free", {}).get("BTC", 0.0))
                cb.record_exchange_success()
                from shared.db.models import BalanceSnapshot
                s.add(BalanceSnapshot(usdt=usdt, btc=btc, source="binance"))
                await s.commit()
            except Exception as e:
                logger.warning("engine.balance_unavailable_using_db_fallback", error=str(e))
                # Exchange down: no USDT (prevents new BUYs), BTC from open positions in DB
                usdt = 0.0
                from sqlalchemy import select as _sel
                from shared.db.models import Position as _Pos
                _open = (await s.execute(
                    _sel(_Pos).where(_Pos.status == "open")
                )).scalars().all()
                btc = sum(float(p.quantity_btc) for p in _open)

            daily_pnl_frac, total_drawdown_frac = await _compute_risk_metrics(s, usdt)

            decisor = Decisor(session=s, llm=llm, symbol=settings.symbol,
                              provider=decisor_provider, fallbacks=fallbacks)
            ob_snap = orderbook.snapshot(levels=10)
            try:
                decision = await decisor.decide(
                    orderbook=ob_snap, usdt_balance=usdt, btc_held=btc,
                    max_position_pct=max_pos, max_simultaneous_trades=max_sim,
                    daily_stop_pct=daily_stop, decisor_interval_min=interval_min,
                    mode=mode, taker_fee=fees.taker, maker_fee=fees.maker,
                    atr_timeframe=atr_timeframe, min_rr_ratio=min_rr_ratio,
                    sl_atr_multiplier=sl_atr_multiplier, calibration=calibration,
                    current_drawdown_pct=total_drawdown_frac * 100,
                )
                cb.record_llm_success()
            except Exception as e:
                logger.error("engine.decisor_error", error=str(e))
                cb.record_llm_failure()
                return

            pm = PositionManager(s)
            open_count = await pm.count_open()

            from sqlalchemy import select as sa_select, desc
            from shared.db.models import Decision as DecisionModel, Indicators, Ohlcv

            ind_row = (await s.execute(
                sa_select(Indicators).order_by(desc(Indicators.time)).limit(1)
            )).scalar_one_or_none()
            atr = float((ind_row.data.get(atr_timeframe, {}) or {}).get("atr") or 300) if ind_row else 300.0

            if ob_snap is not None:
                current_price = ob_snap.top_ask
            else:
                last_ohlcv = (await s.execute(
                    sa_select(Ohlcv).where(Ohlcv.timeframe == "1m")
                    .order_by(desc(Ohlcv.time)).limit(1)
                )).scalar_one_or_none()
                current_price = float(last_ohlcv.close) if last_ohlcv else 80000.0
                logger.warning("orderbook.unavailable_using_last_close", price=current_price)

            gate = RiskGate(
                max_position_pct=max_pos, max_simultaneous_trades=max_sim,
                daily_stop_pct=daily_stop, max_drawdown_pct=max_drawdown_pct,
                max_slippage_pct=max_slippage_pct, taker_fee_pct=fees.taker,
                min_rr_ratio=min_rr_ratio, sl_atr_multiplier=sl_atr_multiplier,
                sl_atr_max_multiplier=calibration["sl_atr_max_multiplier"],
            )
            verdict = gate.validate(
                decision=decision, current_price=current_price, atr_ref=atr,
                open_positions_count=open_count, daily_pnl_pct=daily_pnl_frac,
                total_drawdown_pct=total_drawdown_frac, kill_switch=kill,
                usdt_balance=usdt, btc_held=btc,
                roundtrip_fee_pct=fees.taker * 2 * 100,
                min_fees_to_tp_ratio=float(calibration.get("min_fees_to_tp_ratio", 3.0)),
            )
            if not verdict.passed:
                logger.info("decision.rejected", reason=verdict.reason)
                latest_d = (await s.execute(
                    sa_select(DecisionModel).where(DecisionModel.agent == "decisor")
                    .order_by(desc(DecisionModel.ts)).limit(1)
                )).scalar_one_or_none()
                if latest_d is not None:
                    latest_d.rejected_reason = verdict.reason
                    await s.commit()
                return

            latest_d = (await s.execute(
                sa_select(DecisionModel).where(DecisionModel.agent == "decisor")
                .order_by(desc(DecisionModel.ts)).limit(1)
            )).scalar_one_or_none()
            if latest_d is None:
                return

            executor = Executor(exchange, s, symbol=settings.symbol)
            from shared.schemas import DecisorAction
            try:
                if decision.action == DecisorAction.BUY:
                    await executor.execute_buy(
                        decision=decision, decision_id=latest_d.id, usdt_balance=usdt,
                    )
                elif decision.action == DecisorAction.SELL:
                    open_positions = await pm.list_open()
                    if open_positions:
                        await executor.execute_sell(
                            trade_id=open_positions[0].trade_id,
                            decision_id=latest_d.id, close_reason="decisor_sell",
                        )
            except Exception as e:
                logger.error("execution.error", error=str(e))
                cb.record_exchange_failure()

    async def supervisor_tick() -> None:
        async with session_factory() as s:
            store = ConfigStore(s)
            sup_provider = LLMProvider(await store.get(ConfigKey.SUPERVISOR_PROVIDER))
            sup_fallbacks = _parse_providers(await store.get(ConfigKey.SUPERVISOR_FALLBACK_PROVIDERS))
            current_config = {
                "atr_timeframe": await store.get(ConfigKey.ATR_TIMEFRAME),
                "sl_atr_multiplier": await store.get_typed(ConfigKey.SL_ATR_MULTIPLIER),
                "min_rr_ratio": await store.get_typed(ConfigKey.MIN_RR_RATIO),
                "decisor_interval_min": await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN),
                "max_position_pct": await store.get_typed(ConfigKey.MAX_POSITION_PCT),
                "conf_threshold_trending_up": await store.get_typed(ConfigKey.CONF_THRESHOLD_TRENDING_UP),
                "conf_threshold_range": await store.get_typed(ConfigKey.CONF_THRESHOLD_RANGE),
                "conf_threshold_high_vol": await store.get_typed(ConfigKey.CONF_THRESHOLD_HIGH_VOL),
                "expected_holding_max_min": await store.get_typed(ConfigKey.EXPECTED_HOLDING_MAX_MIN),
            }
            sup = Supervisor(session=s, llm=llm, symbol=settings.symbol,
                             provider=sup_provider, fallbacks=sup_fallbacks)
            try:
                await sup.run(current_config=current_config)
                cb.record_llm_success()
            except Exception as e:
                logger.error("supervisor.error", error=str(e))
                cb.record_llm_failure()

    async def fees_tick() -> None:
        async with session_factory() as s:
            await FeeManager(exchange, s, symbol=settings.symbol).refresh()

    async def positions_tick() -> None:
        async with session_factory() as s:
            try:
                ticker = await exchange.fetch_ticker(settings.symbol)
                await PositionManager(s).refresh_unrealized(current_price=float(ticker["last"]))
            except Exception as e:
                logger.warning("positions.refresh_failed", error=str(e))

    async def order_tracker_tick() -> None:
        async with session_factory() as s:
            executor = Executor(exchange, s, symbol=settings.symbol)
            tracker = OrderTracker(exchange, s, executor, symbol=settings.symbol)
            try:
                await tracker.poll_once()
            except Exception as e:
                logger.error("order_tracker.error", error=str(e))

    async with session_factory() as s:
        store = ConfigStore(s)
        interval_min = int(await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN))
        cron = await store.get(ConfigKey.SUPERVISOR_CRON)

    sched.add_decisor(decisor_tick, interval_min=interval_min)
    sched.add_supervisor(supervisor_tick, cron=cron)
    sched.add_fee_refresh(fees_tick, hours=24)
    sched.add_position_refresh(positions_tick, seconds=30)
    sched.add_order_tracker(order_tracker_tick, seconds=30)
    sched.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(getattr(signal, sig_name), stop_event.set)
    try:
        await stop_event.wait()
    finally:
        sched.shutdown()
        await orderbook.stop()
        await exchange.close()
        await engine_db.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
