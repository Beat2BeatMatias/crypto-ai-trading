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
from risk.fees import effective_roundtrip_fee_pct
from risk.circuit_breaker import CircuitBreaker
from scheduler import EngineScheduler
from notifications import notify, TelegramEvent

logger = structlog.get_logger()

FUTURES_SYMBOL = "BTC/USDT:USDT"


def resolve_engine_symbol(product: str, spot_symbol: str) -> str:
    return FUTURES_SYMBOL if product == "futures" else spot_symbol


def validate_futures_sizing(
    *,
    available_margin: float,
    max_position_pct: float,
    leverage: int,
    min_notional: float,
) -> tuple[bool, str]:
    max_trade_notional = available_margin * max_position_pct * leverage
    if max_trade_notional < min_notional:
        return False, (
            f"futures.sizing_unfeasible: max trade notional {max_trade_notional:.2f} "
            f"< min_notional {min_notional:.2f}"
        )
    return True, ""


async def _compute_risk_metrics(
    session, usdt_balance: float, btc_balance: float = 0.0
) -> tuple[float, float]:
    """Calcula daily_pnl y total_drawdown reales para las reglas R9 y drawdown del Risk Gate.

    El drawdown se calcula sobre el valor total del portafolio (USDT + BTC × precio actual)
    para evitar falsos positivos cuando capital está desplegado en posiciones BTC abiertas.

    Returns:
        daily_pnl_frac: P&L de trades cerrados hoy como fracción (ej. -0.03 = -3%).
        total_drawdown_frac: drawdown desde el pico de portfolio (negativo o cero).
                             Si drawdown_reset_ts está configurado, el pico se calcula
                             solo desde esa fecha en adelante.
    """
    from datetime import date, datetime, timezone
    from sqlalchemy import select
    from shared.db.models import Trade as _Trade, BalanceSnapshot as _BalSnap, Ohlcv as _Ohlcv
    from shared.config_store import ConfigStore, ConfigKey

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

    # Precio actual BTC para valorar el portafolio total.
    price_row = (await session.execute(
        select(_Ohlcv).where(_Ohlcv.timeframe == "1m")
        .order_by(_Ohlcv.time.desc()).limit(1)
    )).scalar_one_or_none()
    current_btc_price = float(price_row.close) if price_row else 0.0

    # Valor total del portafolio actual: USDT libre + BTC libre × precio.
    current_portfolio = usdt_balance + btc_balance * current_btc_price

    # Drawdown total: portfolio actual vs pico histórico de portfolio.
    # Se aproxima el valor histórico de cada snapshot como usdt + btc × precio_actual.
    # Si drawdown_reset_ts está configurado, solo se considera historia posterior a esa fecha.
    peak_query = select(_BalSnap.usdt, _BalSnap.btc)
    try:
        store = ConfigStore(session)
        reset_ts_str = await store.get(ConfigKey.DRAWDOWN_RESET_TS)
        if reset_ts_str:
            reset_ts = datetime.fromisoformat(reset_ts_str)
            peak_query = peak_query.where(_BalSnap.ts >= reset_ts)
    except (KeyError, ValueError):
        pass

    peak_row = (await session.execute(
        peak_query.order_by(
            (_BalSnap.usdt + _BalSnap.btc * current_btc_price).desc()
        ).limit(1)
    )).first()

    if peak_row is not None:
        peak_portfolio = float(peak_row.usdt) + float(peak_row.btc) * current_btc_price
    else:
        peak_portfolio = max(current_portfolio, 1.0)

    total_drawdown_frac = (
        (current_portfolio - peak_portfolio) / peak_portfolio
        if peak_portfolio > 0 else 0.0
    )

    return daily_pnl_frac, total_drawdown_frac


async def _persist_circuit_breaker_pause(session_factory, reason: str) -> None:
    """Escribe el estado de pausa del circuit breaker en la BD para que el web lo lea."""
    try:
        async with session_factory() as s:
            store = ConfigStore(s)
            await store.set(ConfigKey.ENGINE_PAUSED, "true", changed_by="circuit_breaker")
            await store.set(ConfigKey.ENGINE_PAUSE_REASON, reason, changed_by="circuit_breaker")
    except Exception as e:
        logger.error("circuit_breaker.persist_pause_failed", error=str(e))


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

    try:
        import ollama as ollama_lib
        ollama_client = (
            ollama_lib.AsyncClient(
                host=settings.ollama_base_url,
                headers={"Authorization": f"Bearer {settings.ollama_api_key}"},
            )
            if settings.ollama_api_key
            else None
        )
        if ollama_client is None:
            logger.info("ollama.client_not_configured")
    except Exception as e:
        ollama_client = None
        logger.warning("ollama.client_init_failed", error=str(e))

    llm = LLMClient(gemini_client=gemini_client, groq_client=groq_client,
                    ollama_client=ollama_client)

    from execution.exchange_adapter import build_adapter

    trading_product = settings.trading_product
    engine_adapter = build_adapter(trading_product)
    engine_symbol = resolve_engine_symbol(trading_product, settings.symbol)
    exchange = engine_adapter.build_client()

    # Bootstrap
    async with session_factory() as s:
        store = ConfigStore(s)
        await store.seed_defaults()
        db_product = await store.get(ConfigKey.TRADING_PRODUCT)
        if db_product in ("spot", "futures"):
            trading_product = db_product
        engine_adapter = build_adapter(trading_product)
        engine_symbol = resolve_engine_symbol(trading_product, settings.symbol)
        exchange = engine_adapter.build_client()
        if trading_product == "futures":
            try:
                max_lev = int(await store.get_typed(ConfigKey.MAX_LEVERAGE))
                margin_mode = await store.get(ConfigKey.MARGIN_MODE)
                await engine_adapter.setup_symbol(
                    engine_symbol, leverage=max_lev, margin_mode=margin_mode,
                )
                bal = await engine_adapter.fetch_balance()
                min_notional = engine_adapter.min_notional(engine_symbol)
                max_pos = await store.get_typed(ConfigKey.MAX_POSITION_PCT)
                ok, reason = validate_futures_sizing(
                    available_margin=bal.available,
                    max_position_pct=max_pos,
                    leverage=max_lev,
                    min_notional=min_notional,
                )
                if not ok:
                    logger.error("engine.futures_sizing_unfeasible", reason=reason)
                    await notify(TelegramEvent.ENGINE_PAUSED, {"motivo": reason})
                    trading_product = "spot"
                    engine_adapter = build_adapter("spot")
                    engine_symbol = settings.symbol
                    exchange = engine_adapter.build_client()
            except Exception as e:
                logger.error("engine.futures_setup_failed", error=str(e))
                trading_product = "spot"
                engine_adapter = build_adapter("spot")
                engine_symbol = settings.symbol
                exchange = engine_adapter.build_client()
        await PromptManager(s).seed_playbook_v0()
        fee_mgr = FeeManager(exchange, s, symbol=engine_symbol)
        await fee_mgr.refresh()
        await store.set(ConfigKey.ENGINE_PAUSED, "false", changed_by="system")
        await store.set(ConfigKey.ENGINE_PAUSE_REASON, "", changed_by="system")

    orderbook = OrderBookCollector(symbol=engine_symbol, exchange=exchange)
    try:
        await orderbook.start()
        logger.info("orderbook.ws_started", symbol=settings.symbol)
    except Exception as e:
        logger.warning("orderbook.ws_start_failed_continuing_without_live_book", error=str(e))
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)  # defaults; updated each tick from config
    sched = EngineScheduler()

    async def decisor_tick() -> None:
        # Sincronizar estado de pausa desde DB: permite que un reset manual desde la UI
        # (que escribe ENGINE_PAUSED=false en la DB) sea efectivo sin reiniciar el proceso.
        # Sin esta sincronización, el objeto cb queda pausado en memoria indefinidamente
        # incluso después de un reset explícito del operador para pausas financieras.
        if cb.engine_paused:
            async with session_factory() as s:
                store = ConfigStore(s)
                db_paused = await store.get_typed(ConfigKey.ENGINE_PAUSED)
            if not db_paused:
                logger.warning("engine.manual_reset_detected", reason=str(cb._pause_reason))
                cb.manual_reset()
                await notify(TelegramEvent.ENGINE_RESUMED, {"motivo": "manual_reset"})
            elif cb.maybe_auto_reset():
                logger.warning("engine.auto_reset_after_cooldown")
                async with session_factory() as s:
                    store = ConfigStore(s)
                    await store.set(ConfigKey.ENGINE_PAUSED, "false", changed_by="circuit_breaker")
                    await store.set(ConfigKey.ENGINE_PAUSE_REASON, "", changed_by="circuit_breaker")
                await notify(TelegramEvent.ENGINE_RESUMED, {
                    "cooldown_sec": cb.operational_cooldown_sec,
                })
            else:
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
                # Risk Gate / SL bounds
                "sl_atr_max_multiplier": await store.get_typed(ConfigKey.SL_ATR_MAX_MULTIPLIER),
                "min_fees_to_tp_ratio": await store.get_typed(ConfigKey.MIN_FEES_TO_TP_RATIO),
                # Guías para el LLM — se inyectan en el system prompt (no enforcement)
                "conf_threshold_trending_up": await store.get_typed(ConfigKey.CONF_THRESHOLD_TRENDING_UP),
                "conf_threshold_range": await store.get_typed(ConfigKey.CONF_THRESHOLD_RANGE),
                "conf_threshold_high_vol": await store.get_typed(ConfigKey.CONF_THRESHOLD_HIGH_VOL),
                "rsi_overbought_1h": await store.get_typed(ConfigKey.RSI_OVERBOUGHT_1H),
                "conf_base_0": await store.get_typed(ConfigKey.CONF_BASE_0),
                "conf_base_1": await store.get_typed(ConfigKey.CONF_BASE_1),
                "conf_base_2": await store.get_typed(ConfigKey.CONF_BASE_2),
                "conf_base_3": await store.get_typed(ConfigKey.CONF_BASE_3),
                "conf_base_4plus": await store.get_typed(ConfigKey.CONF_BASE_4PLUS),
                "peso_regime_range": await store.get_typed(ConfigKey.PESO_REGIME_RANGE),
                "peso_regime_high_vol": await store.get_typed(ConfigKey.PESO_REGIME_HIGH_VOL),
                "adj_volume_boost": await store.get_typed(ConfigKey.ADJ_VOLUME_BOOST),
                "adj_volume_ratio": await store.get_typed(ConfigKey.ADJ_VOLUME_RATIO),
                "adj_spread_penalty": await store.get_typed(ConfigKey.ADJ_SPREAD_PENALTY),
                "adj_spread_threshold_pct": await store.get_typed(ConfigKey.ADJ_SPREAD_THRESHOLD_PCT),
                "confluence_weak_factor": await store.get_typed(ConfigKey.CONFLUENCE_WEAK_FACTOR),
                "block_k_max_lines": await store.get_typed(ConfigKey.BLOCK_K_MAX_LINES),
                "block_k_window_hours": await store.get_typed(ConfigKey.BLOCK_K_WINDOW_HOURS),
                "min_roundtrip_fee_pct": await store.get_typed(ConfigKey.MIN_ROUNDTRIP_FEE_PCT),
                "min_position_size": await store.get_typed(ConfigKey.MIN_POSITION_SIZE),
                "risk_per_trade_pct": await store.get_typed(ConfigKey.RISK_PER_TRADE_PCT),
            }
            coherence_strict = await store.get_typed(ConfigKey.COHERENCE_STRICT_MODE)
            two_pass = await store.get_typed(ConfigKey.TWO_PASS_ENABLED)
            decisor_temperature = await store.get_typed(ConfigKey.DECISOR_LLM_TEMPERATURE)
            decisor_self_consistency_n = await store.get_typed(ConfigKey.DECISOR_SELF_CONSISTENCY_N)

            collector = PriceCollector(exchange, s, symbol=engine_symbol)
            try:
                for tf in ("1m", "5m", "15m", "1h", "4h"):
                    await collector.fetch_and_persist(timeframe=tf)
            except Exception as e:
                logger.warning("engine.ohlcv_fetch_failed_using_cached_data", error=str(e))
            await collector.compute_and_persist_indicators()

            fees = FeeManager(exchange, s, symbol=engine_symbol)
            await fees.get_or_refresh()

            balance_fetch_ok = True
            try:
                if trading_product == "futures":
                    bv = await engine_adapter.fetch_balance()
                    usdt = bv.available
                    btc = 0.0
                    usdt_locked = max(0.0, bv.total - bv.available)
                    btc_locked = 0.0
                    margin_balance = bv.total
                    available_margin = bv.available
                else:
                    balance = await exchange.fetch_balance()
                    usdt = float(balance.get("free", {}).get("USDT", 0.0))
                    btc = float(balance.get("free", {}).get("BTC", 0.0))
                    usdt_locked = float(balance.get("used", {}).get("USDT", 0.0))
                    btc_locked = float(balance.get("used", {}).get("BTC", 0.0))
                    margin_balance = None
                    available_margin = usdt
                cb.record_exchange_success()
                from shared.db.models import BalanceSnapshot
                snap = BalanceSnapshot(
                    usdt=usdt, btc=btc,
                    usdt_locked=usdt_locked, btc_locked=btc_locked,
                    source="binance",
                )
                if trading_product == "futures":
                    snap.margin_balance = margin_balance
                    snap.available_margin = available_margin
                s.add(snap)
                await s.commit()
            except Exception as e:
                logger.warning("engine.balance_unavailable_using_db_fallback", error=str(e))
                balance_fetch_ok = False
                # Exchange down: no USDT (prevents new BUYs), BTC from open positions in DB
                usdt = 0.0
                usdt_locked = 0.0
                btc_locked  = 0.0
                from sqlalchemy import select as _sel
                from shared.db.models import Position as _Pos
                _open = (await s.execute(
                    _sel(_Pos).where(_Pos.status == "open")
                )).scalars().all()
                btc = sum(float(p.quantity_btc) for p in _open)

            # Solo evaluar drawdown cuando el balance fue obtenido correctamente.
            # Si el exchange está caído y usdt=0, compararlo contra el pico histórico
            # generaría un drawdown artificial del 100% que pausaría el engine por error.
            if balance_fetch_ok:
                daily_pnl_frac, total_drawdown_frac = await _compute_risk_metrics(
                    s, usdt, btc_balance=btc
                )
                prev_paused = cb.engine_paused
                state = cb.evaluate(daily_pnl_pct=daily_pnl_frac, total_drawdown_pct=total_drawdown_frac)
                if state.daily_stop_triggered and not prev_paused:
                    await notify(TelegramEvent.DAILY_STOP, {
                        "daily_pnl": f"{daily_pnl_frac:.2%}",
                        "límite": f"{cb.daily_stop_pct:.2%}",
                    })
                if state.kill_switch_triggered and not prev_paused:
                    await notify(TelegramEvent.DRAWDOWN_HIGH, {
                        "drawdown": f"{total_drawdown_frac:.2%}",
                        "límite": f"{cb.max_drawdown_pct:.2%}",
                    })
                if cb.engine_paused and not prev_paused:
                    reason = (
                        f"max_drawdown breached: {total_drawdown_frac:.2%}"
                        if total_drawdown_frac <= cb.max_drawdown_pct
                        else f"daily_stop breached: {daily_pnl_frac:.2%}"
                    )
                    logger.error("engine.paused_by_circuit_breaker",
                                 daily_pnl=daily_pnl_frac, drawdown=total_drawdown_frac)
                    await notify(TelegramEvent.KILL_SWITCH, {"motivo": reason})
                    await _persist_circuit_breaker_pause(session_factory, reason)
                    return
                elif cb.engine_paused:
                    return
            else:
                daily_pnl_frac, total_drawdown_frac = 0.0, 0.0

            pm = PositionManager(s)
            open_count = await pm.count_open()
            open_positions = await pm.list_open()
            has_open_position = len(open_positions) > 0
            open_position_side = (
                getattr(open_positions[0], "position_side", None) if open_positions else None
            )
            leverage = float(await store.get_typed(ConfigKey.MAX_LEVERAGE))
            liquidation_price = None
            funding_rate = 0.0
            funding_rate_max = float(await store.get_typed(ConfigKey.FUNDING_RATE_MAX_PCT))
            liq_buffer_atr = float(await store.get_typed(ConfigKey.LIQUIDATION_BUFFER_ATR))
            min_notional = float(calibration.get("min_notional_usdt", 5.0))
            if trading_product == "futures":
                try:
                    min_notional = engine_adapter.min_notional(engine_symbol)
                    funding_rate = await engine_adapter.fetch_funding_rate(engine_symbol)
                    if open_positions:
                        for pv in await engine_adapter.fetch_positions():
                            if pv.direction and pv.liquidation_price:
                                liquidation_price = pv.liquidation_price
                                break
                except Exception as e:
                    logger.warning("engine.futures_risk_context_failed", error=str(e))

            decisor = Decisor(session=s, llm=llm, symbol=engine_symbol,
                              provider=decisor_provider, fallbacks=fallbacks,
                              coherence_strict_mode=coherence_strict,
                              two_pass_enabled=two_pass,
                              llm_temperature=decisor_temperature,
                              self_consistency_n=decisor_self_consistency_n)
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
                    trading_product=trading_product,
                    funding_rate=funding_rate if trading_product == "futures" else 0.0,
                    available_margin=usdt,
                    open_position_side=open_position_side,
                    liquidation_price=liquidation_price,
                )
                cb.record_llm_success()
            except Exception as e:
                logger.error("engine.decisor_error", error=str(e))
                cb.record_llm_failure()
                if cb.engine_paused:
                    await notify(TelegramEvent.LLM_FAILURE_STREAK, {
                        "fallas_consecutivas": cb._llm_consecutive_failures,
                        "error": str(e)[:200],
                    })
                    await notify(TelegramEvent.ENGINE_PAUSED, {"motivo": "llm_failures"})
                    await _persist_circuit_breaker_pause(
                        session_factory, f"llm_failures: {cb._llm_consecutive_failures} consecutivas"
                    )
                return

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
                min_notional_usdt=min_notional,
                max_leverage=leverage,
            )
            verdict = gate.validate(
                decision=decision, current_price=current_price, atr_ref=atr,
                open_positions_count=open_count, daily_pnl_pct=daily_pnl_frac,
                total_drawdown_pct=total_drawdown_frac, kill_switch=kill,
                usdt_balance=usdt, btc_held=btc,
                available_margin=usdt,
                has_open_position=has_open_position,
                open_position_side=open_position_side,
                leverage=leverage,
                liquidation_price=liquidation_price,
                funding_rate=funding_rate,
                funding_rate_max_pct=funding_rate_max,
                liquidation_buffer_atr=liq_buffer_atr,
                roundtrip_fee_pct=effective_roundtrip_fee_pct(
                    taker_fee=fees.taker,
                    floor_pct=float(calibration.get("min_roundtrip_fee_pct", 0.20)),
                ),
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

            use_adapter = trading_product == "futures"
            executor = Executor(
                exchange, s, symbol=engine_symbol,
                adapter=engine_adapter if use_adapter else None,
            )
            from shared.schemas import DecisorAction, Direction
            try:
                if decision.action == DecisorAction.BUY:
                    if use_adapter:
                        await executor.execute_open(
                            direction=Direction.LONG,
                            decision=decision,
                            decision_id=latest_d.id,
                            available_margin=usdt,
                            price=current_price,
                        )
                    else:
                        await executor.execute_buy(
                            decision=decision, decision_id=latest_d.id, usdt_balance=usdt,
                        )
                elif decision.action == DecisorAction.SHORT:
                    await executor.execute_open(
                        direction=Direction.SHORT,
                        decision=decision,
                        decision_id=latest_d.id,
                        available_margin=usdt,
                        price=current_price,
                    )
                elif decision.action == DecisorAction.SELL:
                    if open_positions:
                        tid = open_positions[0].trade_id
                        if use_adapter or (
                            getattr(open_positions[0], "position_side", "LONG") == "SHORT"
                        ):
                            await executor.execute_close(
                                trade_id=tid,
                                decision_id=latest_d.id,
                                close_reason="decisor_sell",
                            )
                        else:
                            await executor.execute_sell(
                                trade_id=tid,
                                decision_id=latest_d.id,
                                close_reason="decisor_sell",
                            )
            except Exception as e:
                logger.error("execution.error", error=str(e))
                # Persistir el error de ejecución en la decisión para que sea visible en el dashboard.
                try:
                    error_msg = str(e)
                    rejected_label = f"execution_error: {error_msg[:160]}"
                    latest_d.rejected_reason = rejected_label
                    await s.commit()
                except Exception as persist_err:
                    logger.warning("execution.persist_error_failed", error=str(persist_err))
                cb.record_exchange_failure()
                if cb.engine_paused:
                    await notify(TelegramEvent.EXCHANGE_FAILURE_STREAK, {
                        "fallas_consecutivas": cb._exchange_consecutive_failures,
                        "error": str(e)[:200],
                    })
                    await notify(TelegramEvent.ENGINE_PAUSED, {"motivo": "exchange_failures"})
                    await _persist_circuit_breaker_pause(
                        session_factory, f"exchange_failures: {cb._exchange_consecutive_failures} consecutivas"
                    )

    async def supervisor_tick() -> None:
        async with session_factory() as s:
            store = ConfigStore(s)
            sup_provider = LLMProvider(await store.get(ConfigKey.SUPERVISOR_PROVIDER))
            sup_fallbacks = _parse_providers(await store.get(ConfigKey.SUPERVISOR_FALLBACK_PROVIDERS))
            current_config = {
                # ENFORCEMENT (Risk Gate los aplica)
                "atr_timeframe": await store.get(ConfigKey.ATR_TIMEFRAME),
                "sl_atr_multiplier": await store.get_typed(ConfigKey.SL_ATR_MULTIPLIER),
                "sl_atr_max_multiplier": await store.get_typed(ConfigKey.SL_ATR_MAX_MULTIPLIER),
                "min_rr_ratio": await store.get_typed(ConfigKey.MIN_RR_RATIO),
                "default_rr_ratio": await store.get_typed(ConfigKey.DEFAULT_RR_RATIO),
                "max_position_pct": await store.get_typed(ConfigKey.MAX_POSITION_PCT),
                "min_fees_to_tp_ratio": await store.get_typed(ConfigKey.MIN_FEES_TO_TP_RATIO),
                # OPERACIONAL
                "decisor_interval_min": await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN),
                # GUÍAS LLM auto-ajustables
                "expected_holding_max_min": await store.get_typed(ConfigKey.EXPECTED_HOLDING_MAX_MIN),
                "cooldown_after_sell_min": await store.get_typed(ConfigKey.COOLDOWN_AFTER_SELL_MIN),
                "conf_threshold_trending_up": await store.get_typed(ConfigKey.CONF_THRESHOLD_TRENDING_UP),
                "conf_threshold_range": await store.get_typed(ConfigKey.CONF_THRESHOLD_RANGE),
                "conf_threshold_high_vol": await store.get_typed(ConfigKey.CONF_THRESHOLD_HIGH_VOL),
                # TOGGLES LLM-centric v1.3
                "coherence_strict_mode": await store.get_typed(ConfigKey.COHERENCE_STRICT_MODE),
                "two_pass_enabled": await store.get_typed(ConfigKey.TWO_PASS_ENABLED),
            }
            sup = Supervisor(session=s, llm=llm, symbol=engine_symbol,
                             provider=sup_provider, fallbacks=sup_fallbacks)
            try:
                await sup.run(current_config=current_config)
                cb.record_llm_success()
            except Exception as e:
                logger.error("supervisor.error", error=str(e))
                cb.record_llm_failure()

    async def balance_tick() -> None:
        """Persiste el snapshot de balance (free + locked) cada 60 s independientemente
        del ciclo del decisor, para mantener la UI actualizada en todo momento."""
        try:
            bal = await exchange.fetch_balance()
            usdt_free   = float(bal.get("free", {}).get("USDT", 0.0))
            btc_free    = float(bal.get("free", {}).get("BTC",  0.0))
            usdt_locked = float(bal.get("used", {}).get("USDT", 0.0))
            btc_locked  = float(bal.get("used", {}).get("BTC",  0.0))
            from shared.db.models import BalanceSnapshot
            async with session_factory() as s:
                s.add(BalanceSnapshot(
                    usdt=usdt_free, btc=btc_free,
                    usdt_locked=usdt_locked, btc_locked=btc_locked,
                    source="binance",
                ))
                await s.commit()
        except Exception as e:
            logger.warning("balance_tick.failed", error=str(e))

    async def fees_tick() -> None:
        async with session_factory() as s:
            await FeeManager(exchange, s, symbol=engine_symbol).refresh()

    async def positions_tick() -> None:
        async with session_factory() as s:
            try:
                ticker = await exchange.fetch_ticker(engine_symbol)
                await PositionManager(s).refresh_unrealized(current_price=float(ticker["last"]))
            except Exception as e:
                logger.warning("positions.refresh_failed", error=str(e))

    async def order_tracker_tick() -> None:
        async with session_factory() as s:
            executor = Executor(
                exchange, s, symbol=engine_symbol,
                adapter=engine_adapter if trading_product == "futures" else None,
            )
            tracker = OrderTracker(exchange, s, executor, symbol=engine_symbol)
            try:
                await tracker.poll_once()
            except Exception as e:
                logger.error("order_tracker.error", error=str(e))

    async def outcome_attribution_tick_wrapper() -> None:
        from agents.outcome_attribution_job import outcome_attribution_tick
        from agents.postmortem_job import outcome_postmortem_tick

        horizon_min = 240
        coverage_threshold_pct = 30.0
        window_hours = 25
        postmortem_enabled = True
        max_per_tick = 5
        provider_name = "gemini-2.5-flash"
        postmortem_fallbacks: list[LLMProvider] = []
        try:
            async with session_factory() as s:
                store = ConfigStore(s)
                horizon_min = int(await store.get_typed(ConfigKey.OUTCOME_ATTRIBUTION_HORIZON_MIN))
                coverage_threshold_pct = float(await store.get_typed(ConfigKey.OUTCOME_COVERAGE_THRESHOLD_PCT))
                window_hours = int(await store.get_typed(ConfigKey.OUTCOME_ATTRIBUTION_WINDOW_HOURS))
                postmortem_enabled = bool(await store.get_typed(ConfigKey.POSTMORTEM_ENABLED))
                max_per_tick = int(await store.get_typed(ConfigKey.POSTMORTEM_MAX_PER_TICK))
                provider_name = str(await store.get(ConfigKey.POSTMORTEM_PROVIDER))
                postmortem_fallbacks = _parse_providers(
                    await store.get(ConfigKey.POSTMORTEM_FALLBACK_PROVIDERS)
                )
        except Exception as e:
            logger.warning("outcome_attribution.config_read_failed", error=str(e))

        await outcome_attribution_tick(
            session_factory=session_factory,
            horizon_min=horizon_min,
            coverage_threshold_pct=coverage_threshold_pct,
            window_hours=window_hours,
        )

        if not postmortem_enabled:
            return
        try:
            await outcome_postmortem_tick(
                session_factory=session_factory,
                llm=llm,
                max_per_tick=max_per_tick,
                provider_name=provider_name,
                fallback_providers=postmortem_fallbacks,
                window_hours=window_hours,
            )
        except Exception as e:
            logger.error("postmortem.job.error", error=str(e))

    async with session_factory() as s:
        store = ConfigStore(s)
        interval_min = int(await store.get_typed(ConfigKey.DECISOR_INTERVAL_MIN))
        cron = await store.get(ConfigKey.SUPERVISOR_CRON)
        outcome_interval_min = int(await store.get_typed(ConfigKey.OUTCOME_ATTRIBUTION_INTERVAL_MIN))

    sched.add_decisor(decisor_tick, interval_min=interval_min)
    sched.add_supervisor(supervisor_tick, cron=cron)
    sched.add_fee_refresh(fees_tick, hours=24)
    sched.add_balance_refresh(balance_tick, seconds=60)
    sched.add_position_refresh(positions_tick, seconds=30)
    sched.add_order_tracker(order_tracker_tick, seconds=10)
    sched.add_outcome_attribution(outcome_attribution_tick_wrapper, interval_min=outcome_interval_min)
    logger.info(
        "scheduler.outcome_attribution_registered",
        interval_min=outcome_interval_min,
        postmortem_chained=True,
    )
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
