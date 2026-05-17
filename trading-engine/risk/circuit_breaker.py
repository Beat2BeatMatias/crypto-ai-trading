from __future__ import annotations
import time
from dataclasses import dataclass
from enum import Enum
import structlog

logger = structlog.get_logger()

# Cooldown por defecto: 10 minutos sin nuevas fallas operativas → auto-reset.
_DEFAULT_OPERATIONAL_COOLDOWN_SEC = 600


class PauseReason(str, Enum):
    """Clasifica el motivo de pausa para decidir si aplica auto-reset."""
    LLM_FAILURES = "llm_failures"
    EXCHANGE_FAILURES = "exchange_failures"
    DRAWDOWN = "drawdown"
    DAILY_STOP = "daily_stop"
    MANUAL = "manual"


@dataclass(frozen=True)
class CircuitState:
    daily_stop_triggered: bool
    kill_switch_triggered: bool


class CircuitBreaker:
    """Gestiona la pausa del engine ante fallas operativas o breaches financieros.

    Pausa operativa (LLM / exchange):
        Se activa tras N fallas consecutivas. Se auto-resetea cuando el
        cooldown expira SIN nuevas fallas — el engine puede retomar
        sin intervención humana.

    Pausa financiera (daily_stop / max_drawdown):
        Requiere reset explícito del operador. No se auto-resetea.
    """

    def __init__(
        self,
        *,
        daily_stop_pct: float,
        max_drawdown_pct: float,
        llm_failure_threshold: int = 5,
        exchange_failure_threshold: int = 5,
        drawdown_consecutive_threshold: int = 2,
        operational_cooldown_sec: int = _DEFAULT_OPERATIONAL_COOLDOWN_SEC,
    ) -> None:
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.llm_failure_threshold = llm_failure_threshold
        self.exchange_failure_threshold = exchange_failure_threshold
        self.drawdown_consecutive_threshold = drawdown_consecutive_threshold
        self.operational_cooldown_sec = operational_cooldown_sec

        self._llm_consecutive_failures = 0
        self._exchange_consecutive_failures = 0
        self._drawdown_consecutive_breaches = 0

        self.engine_paused = False
        self._pause_reason: PauseReason | None = None
        self._pause_ts: float | None = None

    # ── Public interface ─────────────────────────────────────────────────────

    def evaluate(self, *, daily_pnl_pct: float, total_drawdown_pct: float) -> CircuitState:
        """Evalúa métricas financieras y pausa el engine si se superan los límites."""
        daily = daily_pnl_pct <= self.daily_stop_pct
        kill = total_drawdown_pct <= self.max_drawdown_pct

        if daily and not self.engine_paused:
            logger.warning("circuit.daily_stop_triggered", pnl_pct=daily_pnl_pct)
            self._pause(PauseReason.DAILY_STOP)

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
                self._pause(PauseReason.DRAWDOWN)
        else:
            self._drawdown_consecutive_breaches = 0

        return CircuitState(daily_stop_triggered=daily, kill_switch_triggered=kill)

    def maybe_auto_reset(self) -> bool:
        """Intenta auto-resetear si la pausa es operativa y el cooldown expiró.

        Returns True si se realizó un reset automático.
        Solo aplica a PauseReason.LLM_FAILURES y EXCHANGE_FAILURES.
        Pausas financieras (DRAWDOWN, DAILY_STOP) requieren reset manual.
        """
        if not self.engine_paused:
            return False
        if self._pause_reason not in (PauseReason.LLM_FAILURES, PauseReason.EXCHANGE_FAILURES):
            return False
        if self._pause_ts is None:
            return False
        elapsed = time.monotonic() - self._pause_ts
        if elapsed < self.operational_cooldown_sec:
            return False

        logger.warning(
            "circuit.auto_reset",
            reason=self._pause_reason,
            elapsed_sec=int(elapsed),
            cooldown_sec=self.operational_cooldown_sec,
        )
        self.engine_paused = False
        self._pause_reason = None
        self._pause_ts = None
        self._llm_consecutive_failures = 0
        self._exchange_consecutive_failures = 0
        return True

    def record_llm_failure(self) -> None:
        self._llm_consecutive_failures += 1
        if self._llm_consecutive_failures >= self.llm_failure_threshold:
            self._pause(PauseReason.LLM_FAILURES)

    def record_llm_success(self) -> None:
        self._llm_consecutive_failures = 0

    def record_exchange_failure(self) -> None:
        self._exchange_consecutive_failures += 1
        if self._exchange_consecutive_failures >= self.exchange_failure_threshold:
            self._pause(PauseReason.EXCHANGE_FAILURES)

    def record_exchange_success(self) -> None:
        self._exchange_consecutive_failures = 0

    def update_thresholds(self, *, daily_stop_pct: float, max_drawdown_pct: float) -> None:
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct

    # ── Internal ─────────────────────────────────────────────────────────────

    def _pause(self, reason: PauseReason) -> None:
        if self.engine_paused and self._pause_reason in (
            PauseReason.DRAWDOWN, PauseReason.DAILY_STOP
        ):
            return
        self.engine_paused = True
        self._pause_reason = reason
        self._pause_ts = time.monotonic()
        logger.error("circuit.engine_paused", reason=reason)
