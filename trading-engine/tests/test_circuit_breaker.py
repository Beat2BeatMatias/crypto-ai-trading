import time
from unittest.mock import patch
from risk.circuit_breaker import CircuitBreaker, PauseReason


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


def test_pause_reason_llm_failures():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, llm_failure_threshold=3)
    for _ in range(3):
        cb.record_llm_failure()
    assert cb.engine_paused is True
    assert cb._pause_reason == PauseReason.LLM_FAILURES


def test_pause_reason_exchange_failures():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, exchange_failure_threshold=3)
    for _ in range(3):
        cb.record_exchange_failure()
    assert cb.engine_paused is True
    assert cb._pause_reason == PauseReason.EXCHANGE_FAILURES


def test_auto_reset_after_cooldown_for_llm_failures():
    # GIVEN: engine pausado por fallas LLM
    cb = CircuitBreaker(
        daily_stop_pct=-0.03, max_drawdown_pct=-0.10,
        llm_failure_threshold=3, operational_cooldown_sec=600,
    )
    for _ in range(3):
        cb.record_llm_failure()
    assert cb.engine_paused is True

    # WHEN: cooldown no expiró → maybe_auto_reset devuelve False
    assert cb.maybe_auto_reset() is False
    assert cb.engine_paused is True

    # WHEN: simulamos que el cooldown expiró
    cb._pause_ts = time.monotonic() - 601
    assert cb.maybe_auto_reset() is True
    assert cb.engine_paused is False
    assert cb._pause_reason is None


def test_auto_reset_not_allowed_for_financial_breach():
    # GIVEN: engine pausado por daily_stop (breach financiero)
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10, operational_cooldown_sec=1)
    cb.evaluate(daily_pnl_pct=-0.05, total_drawdown_pct=0.0)
    assert cb.engine_paused is True
    assert cb._pause_reason == PauseReason.DAILY_STOP

    # WHEN: cooldown "expirado" → aún así NO se auto-resetea (requiere intervención humana)
    cb._pause_ts = time.monotonic() - 10
    assert cb.maybe_auto_reset() is False
    assert cb.engine_paused is True


def test_auto_reset_not_triggered_when_not_paused():
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    assert cb.engine_paused is False
    assert cb.maybe_auto_reset() is False


def test_financial_pause_not_overridden_by_operational():
    # Si ya está pausado por drawdown (financiero), una racha LLM no cambia el motivo
    cb = CircuitBreaker(
        daily_stop_pct=-0.03, max_drawdown_pct=-0.10,
        drawdown_consecutive_threshold=1, llm_failure_threshold=3,
    )
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert cb._pause_reason == PauseReason.DRAWDOWN

    for _ in range(3):
        cb.record_llm_failure()
    # El motivo de pausa no se reemplaza por LLM_FAILURES
    assert cb._pause_reason == PauseReason.DRAWDOWN


def test_manual_reset_clears_drawdown_pause():
    # GIVEN: engine pausado por drawdown (pausa financiera, requiere intervención humana)
    cb = CircuitBreaker(
        daily_stop_pct=-0.03, max_drawdown_pct=-0.10,
        drawdown_consecutive_threshold=1,
    )
    cb.evaluate(daily_pnl_pct=0.0, total_drawdown_pct=-0.11)
    assert cb.engine_paused is True
    assert cb._pause_reason == PauseReason.DRAWDOWN

    # WHEN: operador hace reset manual (DB escribe ENGINE_PAUSED=false → main.py llama manual_reset)
    cb.manual_reset()

    # THEN: engine vuelve a estar activo con todos los contadores limpios
    assert cb.engine_paused is False
    assert cb._pause_reason is None
    assert cb._pause_ts is None
    assert cb._drawdown_consecutive_breaches == 0
    assert cb._llm_consecutive_failures == 0
    assert cb._exchange_consecutive_failures == 0


def test_manual_reset_clears_daily_stop_pause():
    # GIVEN: engine pausado por daily_stop
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    cb.evaluate(daily_pnl_pct=-0.05, total_drawdown_pct=0.0)
    assert cb.engine_paused is True
    assert cb._pause_reason == PauseReason.DAILY_STOP

    # WHEN
    cb.manual_reset()

    # THEN
    assert cb.engine_paused is False
    assert cb._pause_reason is None


def test_manual_reset_on_active_engine_is_noop():
    # GIVEN: engine no pausado
    cb = CircuitBreaker(daily_stop_pct=-0.03, max_drawdown_pct=-0.10)
    assert cb.engine_paused is False

    # WHEN: llamar manual_reset no cambia nada
    cb.manual_reset()

    # THEN
    assert cb.engine_paused is False
    assert cb._pause_reason is None
