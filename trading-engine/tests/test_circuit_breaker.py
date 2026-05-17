from risk.circuit_breaker import CircuitBreaker


def test_no_breach_returns_ok():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    state = cb.evaluate(daily_pnl_pct=-0.01, total_drawdown_pct=-0.05)
    assert state.daily_stop_triggered is False
    assert state.kill_switch_triggered is False


def test_daily_stop_triggers_when_breached():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    state = cb.evaluate(daily_pnl_pct=-0.04, total_drawdown_pct=-0.05)
    assert state.daily_stop_triggered is True


def test_drawdown_state_reported_on_first_breach():
    # kill_switch_triggered refleja el estado aunque el engine aún no se pause
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, drawdown_consecutive_threshold=2)
    state = cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert state.kill_switch_triggered is True
    assert cb.engine_paused is False  # 1ra breach, umbral=2 → todavía no pausa


def test_drawdown_pauses_engine_after_consecutive_threshold():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, drawdown_consecutive_threshold=2)
    # GIVEN: primera breach — no pausa
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert cb.engine_paused is False
    # WHEN: segunda breach consecutiva — pausa
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert cb.engine_paused is True


def test_drawdown_counter_resets_when_drawdown_recovers():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, drawdown_consecutive_threshold=2)
    # GIVEN: una breach, luego recovery
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert cb._drawdown_consecutive_breaches == 1
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.05)  # sin breach
    assert cb._drawdown_consecutive_breaches == 0
    assert cb.engine_paused is False


def test_drawdown_threshold_1_pauses_immediately():
    # Threshold=1 reproduce el comportamiento original (pausa inmediata)
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, drawdown_consecutive_threshold=1)
    state = cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert state.kill_switch_triggered is True
    assert cb.engine_paused is True


def test_consecutive_llm_failures_pause():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, llm_failure_threshold=5)
    for _ in range(4):
        cb.record_llm_failure()
    assert cb.engine_paused is False
    cb.record_llm_failure()
    assert cb.engine_paused is True


def test_llm_success_resets_failure_count():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, llm_failure_threshold=5)
    cb.record_llm_failure()
    cb.record_llm_failure()
    cb.record_llm_success()
    assert cb._llm_consecutive_failures == 0


def test_drawdown_extreme_does_not_pause_on_first_tick_with_default_threshold():
    # Simula el escenario real: exchange caído → drawdown=-100% en 1 tick
    # Con threshold=2 (default), la 1ra ocurrencia NO pausa el engine
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    assert cb.drawdown_consecutive_threshold == 2
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-1.0)
    assert cb.engine_paused is False


def test_drawdown_pauses_on_second_consecutive_tick_extreme():
    # Si el exchange sigue caído 2 ticks → ahora sí pausa (drawdown real confirmado)
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-1.0)
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-1.0)
    assert cb.engine_paused is True
