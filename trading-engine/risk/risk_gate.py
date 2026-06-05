from __future__ import annotations
from dataclasses import dataclass
import structlog
from shared.notional_sizing import (
    entry_notional_usdt,
    estimate_qty_btc,
    r11_infeasible_reason,
    sl_exit_notional_usdt,
    tp_exit_notional_usdt,
)
from shared.schemas import DecisorOutput, DecisorAction, Direction, direction_for_action

logger = structlog.get_logger()


@dataclass(frozen=True)
class RiskVerdict:
    passed: bool
    rule_id: str | None = None
    reason: str | None = None


class RiskGate:
    def __init__(self, *, max_position_pct: float, max_simultaneous_trades: int,
                 daily_stop_pct: float, max_drawdown_pct: float,
                 max_slippage_pct: float, taker_fee_pct: float,
                 min_rr_ratio: float = 1.3, sl_atr_multiplier: float = 0.3,
                 sl_atr_max_multiplier: float = 1.5,
                 min_notional_usdt: float = 5.0, max_leverage: float = 1.0,
                 drawdown_protection_enabled: bool = True):
        self.max_position_pct = max_position_pct
        self.max_simultaneous_trades = max_simultaneous_trades
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.drawdown_protection_enabled = drawdown_protection_enabled
        self.max_slippage_pct = max_slippage_pct
        self.taker_fee_pct = taker_fee_pct
        self.min_rr_ratio = min_rr_ratio
        self.sl_atr_multiplier = sl_atr_multiplier
        self.sl_atr_max_multiplier = sl_atr_max_multiplier
        self.min_notional_usdt = min_notional_usdt
        self.max_leverage = max_leverage

    def validate(
        self,
        *,
        decision: DecisorOutput,
        current_price: float,
        atr_ref: float,
        open_positions_count: int,
        daily_pnl_pct: float,
        total_drawdown_pct: float,
        kill_switch: bool,
        usdt_balance: float = 0.0,
        btc_held: float = 0.0,
        available_margin: float | None = None,
        has_open_position: bool | None = None,
        open_position_side: str | None = None,
        leverage: float = 1.0,
        liquidation_price: float | None = None,
        funding_rate: float = 0.0,
        funding_rate_max_pct: float = 0.05,
        liquidation_buffer_atr: float = 2.0,
        roundtrip_fee_pct: float = 0.0,
        min_fees_to_tp_ratio: float = 3.0,
        trading_product: str = "spot",
    ) -> RiskVerdict:
        margin = available_margin if available_margin is not None else usdt_balance
        has_pos = (
            has_open_position if has_open_position is not None else (btc_held > 0 or open_positions_count > 0)
        )

        if decision.action == DecisorAction.HOLD:
            return RiskVerdict(passed=True)

        if (
            self.drawdown_protection_enabled
            and total_drawdown_pct <= self.max_drawdown_pct
        ):
            return self._reject(
                "R0_drawdown",
                f"max_drawdown breached: {total_drawdown_pct:.4f} <= {self.max_drawdown_pct}",
            )

        if kill_switch:
            if decision.action == DecisorAction.SELL and has_pos:
                return RiskVerdict(passed=True)
            return self._reject(
                "R0_kill_switch",
                "kill_switch active — only close (SELL) allowed",
            )

        if decision.action == DecisorAction.SELL:
            if not has_pos or open_positions_count == 0:
                return self._reject("R6", "SELL requested but no open position to close")
            return RiskVerdict(passed=True)

        direction = direction_for_action(decision.action)
        if direction is None:
            return RiskVerdict(passed=True)

        if decision.action == DecisorAction.SHORT and trading_product != "futures":
            return self._reject(
                "R7",
                f"SHORT not allowed when trading_product={trading_product} (spot only supports BUY)",
            )

        if decision.stop_loss is None:
            return self._reject("R2", f"{decision.action.value} requires stop_loss")

        if direction == Direction.LONG:
            if decision.stop_loss >= current_price:
                return self._reject(
                    "R2", f"LONG stop_loss {decision.stop_loss} >= current_price {current_price}",
                )
        else:
            if decision.stop_loss <= current_price:
                return self._reject(
                    "R2", f"SHORT stop_loss {decision.stop_loss} <= current_price {current_price}",
                )

        if decision.position_size_pct > self.max_position_pct + 1e-9:
            return self._reject(
                "R1",
                f"position_size_pct {decision.position_size_pct:.4f} > max {self.max_position_pct:.4f}",
            )

        if leverage > self.max_leverage + 1e-9:
            return self._reject("R12", f"leverage {leverage} > max {self.max_leverage}")

        if abs(funding_rate) > funding_rate_max_pct:
            return self._reject(
                "R15", f"funding rate {funding_rate} exceeds max {funding_rate_max_pct}",
            )

        infeasible = r11_infeasible_reason(
            margin=margin,
            max_position_pct=self.max_position_pct,
            min_notional_usdt=self.min_notional_usdt,
            leverage=leverage,
            trading_product=trading_product,
            price=current_price,
            direction=direction,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
        )
        if infeasible:
            return self._reject("R11", infeasible)

        notional_usdt = entry_notional_usdt(
            margin=margin,
            position_size_pct=decision.position_size_pct,
            leverage=leverage,
            trading_product=trading_product,
        )
        if notional_usdt < self.min_notional_usdt:
            return self._reject(
                "R11",
                f"notional {notional_usdt:.4f} USDT < min_notional {self.min_notional_usdt:.2f} USDT",
            )

        qty_btc_est = estimate_qty_btc(
            margin=margin,
            position_size_pct=decision.position_size_pct,
            price=current_price,
            leverage=leverage,
            trading_product=trading_product,
        )
        if qty_btc_est > 0 and decision.stop_loss is not None:
            sl_notional = sl_exit_notional_usdt(
                qty_btc=qty_btc_est,
                stop_loss=decision.stop_loss,
                direction=direction,
            )
            if sl_notional < self.min_notional_usdt:
                return self._reject(
                    "R11",
                    f"notional SL {sl_notional:.4f} USDT < min_notional {self.min_notional_usdt:.2f} USDT",
                )
        if qty_btc_est > 0 and decision.take_profit is not None:
            tp_notional = tp_exit_notional_usdt(
                qty_btc=qty_btc_est, take_profit=decision.take_profit,
            )
            if tp_notional < self.min_notional_usdt:
                return self._reject(
                    "R11",
                    f"notional TP {tp_notional:.4f} USDT < min_notional {self.min_notional_usdt:.2f} USDT",
                )

        if open_positions_count >= self.max_simultaneous_trades:
            return self._reject(
                "R8",
                f"max_simultaneous_trades reached: {open_positions_count}",
            )

        if daily_pnl_pct <= self.daily_stop_pct:
            return self._reject("R9", f"daily P&L breach: {daily_pnl_pct:.4f}")

        if direction == Direction.LONG:
            sl_distance = current_price - decision.stop_loss
            reward = decision.take_profit - current_price if decision.take_profit else 0.0
        else:
            sl_distance = decision.stop_loss - current_price
            reward = current_price - decision.take_profit if decision.take_profit else 0.0

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

        if decision.take_profit is None:
            return self._reject("R3", f"{decision.action.value} requires take_profit")

        if direction == Direction.LONG:
            if decision.take_profit <= current_price:
                return self._reject(
                    "R3",
                    f"LONG take_profit {decision.take_profit} <= current_price {current_price}",
                )
        else:
            if decision.take_profit >= current_price:
                return self._reject(
                    "R3",
                    f"SHORT take_profit {decision.take_profit} >= current_price {current_price}",
                )

        if sl_distance > 0 and reward / sl_distance < self.min_rr_ratio:
            return self._reject(
                "R5",
                f"R:R ratio {reward/sl_distance:.2f} < {self.min_rr_ratio}",
            )

        if liquidation_price is not None and atr_ref > 0 and decision.stop_loss is not None:
            buffer = liquidation_buffer_atr * atr_ref
            if direction == Direction.LONG and liquidation_price > decision.stop_loss - buffer:
                return self._reject("R13", "liquidation too close to SL (LONG)")
            if direction == Direction.SHORT and liquidation_price < decision.stop_loss + buffer:
                return self._reject("R13", "liquidation too close to SL (SHORT)")

        lev = max(leverage, 1.0)
        required_margin = notional_usdt / lev
        if required_margin > margin + 1e-9:
            return self._reject("R14", "insufficient available margin")

        if roundtrip_fee_pct > 0 and decision.take_profit is not None:
            move_pct = abs(reward) / current_price * 100
            slippage_cushion_pct = self.max_slippage_pct * 2 * 100
            min_move = min_fees_to_tp_ratio * roundtrip_fee_pct + slippage_cushion_pct
            if move_pct < min_move:
                return self._reject(
                    "R10",
                    f"TP move ({move_pct:.3f}%) < {min_fees_to_tp_ratio}×fees + slippage "
                    f"({min_move:.3f}%)",
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
