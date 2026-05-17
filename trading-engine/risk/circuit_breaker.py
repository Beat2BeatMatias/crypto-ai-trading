from __future__ import annotations
from dataclasses import dataclass
import structlog

logger = structlog.get_logger()

@dataclass(frozen=True)
class CircuitState:
    daily_stop_triggered: bool
    kill_switch_triggered: bool

class CircuitBreaker:
    def __init__(self, *, daily_stop_pct: float, max_drawdown_pct: float,
                 llm_failure_threshold: int = 5, exchange_failure_threshold: int = 5,
                 drawdown_consecutive_threshold: int = 2):
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.llm_failure_threshold = llm_failure_threshold
        self.exchange_failure_threshold = exchange_failure_threshold
        self.drawdown_consecutive_threshold = drawdown_consecutive_threshold
        self._llm_consecutive_failures = 0
        self._exchange_consecutive_failures = 0
        self._drawdown_consecutive_breaches = 0
        self.engine_paused = False

    def evaluate(self, *, daily_pnl_pct: float, total_drawdown_pct: float) -> CircuitState:
        daily = daily_pnl_pct <= self.daily_stop_pct
        kill = total_drawdown_pct <= self.max_drawdown_pct
        if daily:
            logger.warning("circuit.daily_stop_triggered", pnl_pct=daily_pnl_pct)
        if kill:
            self._drawdown_consecutive_breaches += 1
            logger.warning(
                "circuit.drawdown_breach",
                drawdown_pct=total_drawdown_pct,
                consecutive=self._drawdown_consecutive_breaches,
                threshold=self.drawdown_consecutive_threshold,
            )
            if self._drawdown_consecutive_breaches >= self.drawdown_consecutive_threshold:
                logger.error(
                    "circuit.kill_switch_triggered",
                    drawdown_pct=total_drawdown_pct,
                    consecutive=self._drawdown_consecutive_breaches,
                )
                self.engine_paused = True
        else:
            self._drawdown_consecutive_breaches = 0
        return CircuitState(daily_stop_triggered=daily, kill_switch_triggered=kill)

    def record_llm_failure(self) -> None:
        self._llm_consecutive_failures += 1
        if self._llm_consecutive_failures >= self.llm_failure_threshold:
            self.engine_paused = True

    def record_llm_success(self) -> None:
        self._llm_consecutive_failures = 0

    def record_exchange_failure(self) -> None:
        self._exchange_consecutive_failures += 1
        if self._exchange_consecutive_failures >= self.exchange_failure_threshold:
            self.engine_paused = True

    def record_exchange_success(self) -> None:
        self._exchange_consecutive_failures = 0

    def update_thresholds(self, *, daily_stop_pct: float, max_drawdown_pct: float) -> None:
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
