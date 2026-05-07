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
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
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
                    sl_atr_multiplier=sl_atr_multiplier,
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
                daily_stop_pct=daily_stop, max_drawdown_pct=-0.10,
                max_slippage_pct=0.003, taker_fee_pct=fees.taker,
                min_rr_ratio=min_rr_ratio, sl_atr_multiplier=sl_atr_multiplier,
            )
            verdict = gate.validate(
                decision=decision, current_price=current_price, atr_1h=atr,
                open_positions_count=open_count, daily_pnl_pct=0.0,
                total_drawdown_pct=0.0, kill_switch=kill,
                usdt_balance=usdt, btc_held=btc,
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
