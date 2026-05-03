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

def test_total_drawdown_triggers_kill_switch():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    state = cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert state.kill_switch_triggered is True

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
