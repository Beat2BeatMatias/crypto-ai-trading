from __future__ import annotations
from dataclasses import dataclass
import structlog
from shared.schemas import DecisorOutput, DecisorAction

logger = structlog.get_logger()


@dataclass(frozen=True)
class RiskVerdict:
    passed: bool
    rule_id: str | None = None    # "R1" … "R10" | "R0_drawdown" | "R0_kill_switch"
    reason: str | None = None


class RiskGate:
    def __init__(self, *, max_position_pct: float, max_simultaneous_trades: int,
                 daily_stop_pct: float, max_drawdown_pct: float,
                 max_slippage_pct: float, taker_fee_pct: float,
                 min_rr_ratio: float = 1.3, sl_atr_multiplier: float = 0.3,
                 sl_atr_max_multiplier: float = 1.5):
        self.max_position_pct = max_position_pct
        self.max_simultaneous_trades = max_simultaneous_trades
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_slippage_pct = max_slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.min_rr_ratio = min_rr_ratio
        self.sl_atr_multiplier = sl_atr_multiplier
        self.sl_atr_max_multiplier = sl_atr_max_multiplier

    def validate(self, *, decision: DecisorOutput, current_price: float, atr_ref: float,
                 open_positions_count: int, daily_pnl_pct: float, total_drawdown_pct: float,
                 kill_switch: bool, usdt_balance: float, btc_held: float,
                 roundtrip_fee_pct: float = 0.0,
                 min_fees_to_tp_ratio: float = 3.0) -> RiskVerdict:

        # HOLD siempre pasa
        if decision.action == DecisorAction.HOLD:
            return RiskVerdict(passed=True)

        # R0_drawdown — drawdown total superado
        if total_drawdown_pct <= self.max_drawdown_pct:
            return self._reject(
                "R0_drawdown",
                f"max_drawdown breached: {total_drawdown_pct:.4f} <= {self.max_drawdown_pct}",
            )

        # R0_kill_switch
        if kill_switch:
            if decision.action == DecisorAction.SELL and btc_held > 0:
                return RiskVerdict(passed=True)
            return self._reject(
                "R0_kill_switch",
                "kill_switch active — only SELL-to-close allowed",
            )

        # R6 — SELL sin posición
        if decision.action == DecisorAction.SELL:
            if btc_held <= 0 or open_positions_count == 0:
                return self._reject("R6", "SELL requested but no open position to close")
            return RiskVerdict(passed=True)

        # --- BUY checks ---

        # R2 — stop_loss presente y < precio
        if decision.stop_loss is None:
            return self._reject("R2", "BUY requires stop_loss")
        if decision.stop_loss >= current_price:
            return self._reject("R2", f"stop_loss {decision.stop_loss} >= current_price {current_price}")

        # R1 — tamaño de posición
        if decision.position_size_pct > self.max_position_pct + 1e-9:
            return self._reject(
                "R1",
                f"position_size_pct {decision.position_size_pct:.4f} > max {self.max_position_pct:.4f}",
            )

        # R8 — posiciones simultáneas
        if open_positions_count >= self.max_simultaneous_trades:
            return self._reject(
                "R8",
                f"max_simultaneous_trades reached: {open_positions_count}",
            )

        # R9 — daily stop
        if daily_pnl_pct <= self.daily_stop_pct:
            return self._reject("R9", f"daily P&L breach: {daily_pnl_pct:.4f}")

        # R4 — distancia SL en banda ATR
        sl_distance = current_price - decision.stop_loss
        sl_min = self.sl_atr_multiplier * atr_ref
        sl_max = self.sl_atr_max_multiplier * atr_ref
        if sl_distance < sl_min:
            return self._reject(
                "R4",
                f"SL distance {sl_distance:.2f} < {self.sl_atr_multiplier}×ATR {sl_min:.2f}",
            )
        if sl_distance > sl_max:
            return self._reject(
                "R4",
                f"SL distance {sl_distance:.2f} > {self.sl_atr_max_multiplier}×ATR {sl_max:.2f}",
            )

        # R3 — take_profit presente y > precio
        if decision.take_profit is None:
            return self._reject("R3", "BUY requires take_profit")
        if decision.take_profit <= current_price:
            return self._reject(
                "R3",
                f"take_profit {decision.take_profit} <= current_price {current_price}",
            )

        # R5 — R:R mínimo
        reward = decision.take_profit - current_price
        if sl_distance > 0 and reward / sl_distance <= self.min_rr_ratio:
            return self._reject(
                "R5",
                f"R:R ratio {reward/sl_distance:.2f} <= {self.min_rr_ratio}",
            )

        # R10 — movimiento al TP cubre fees (no aplica en testnet con fees=0)
        if roundtrip_fee_pct > 0:
            move_pct = (decision.take_profit - current_price) / current_price * 100
            min_move = min_fees_to_tp_ratio * roundtrip_fee_pct
            if move_pct < min_move:
                return self._reject(
                    "R10",
                    f"TP move ({move_pct:.3f}%) < {min_fees_to_tp_ratio}×fees ({min_move:.3f}%)",
                )

        return RiskVerdict(passed=True)

    @staticmethod
    def _reject(rule_id: str, reason: str) -> RiskVerdict:
        logger.warning(
            "risk_gate.rejected",
            rule_id=rule_id,
            category="hard_constraint",
            reason=reason,
        )
        return RiskVerdict(passed=False, rule_id=rule_id, reason=reason)
