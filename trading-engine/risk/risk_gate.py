from __future__ import annotations
from dataclasses import dataclass
from shared.schemas import DecisorOutput, DecisorAction

@dataclass(frozen=True)
class RiskVerdict:
    passed: bool
    reason: str | None = None

class RiskGate:
    def __init__(self, *, max_position_pct: float, max_simultaneous_trades: int,
                 daily_stop_pct: float, max_drawdown_pct: float,
                 max_slippage_pct: float, taker_fee_pct: float):
        self.max_position_pct = max_position_pct
        self.max_simultaneous_trades = max_simultaneous_trades
        self.daily_stop_pct = daily_stop_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_slippage_pct = max_slippage_pct
        self.taker_fee_pct = taker_fee_pct

    def validate(self, *, decision: DecisorOutput, current_price: float, atr_1h: float,
                 open_positions_count: int, daily_pnl_pct: float, total_drawdown_pct: float,
                 kill_switch: bool, usdt_balance: float, btc_held: float) -> RiskVerdict:
        # HOLD always passes
        if decision.action == DecisorAction.HOLD:
            return RiskVerdict(passed=True)

        # Total drawdown breach
        if total_drawdown_pct <= self.max_drawdown_pct:
            return RiskVerdict(False, f"max_drawdown breached: {total_drawdown_pct:.4f}")

        # Kill switch — only allow SELL to close
        if kill_switch:
            if decision.action == DecisorAction.SELL and btc_held > 0:
                return RiskVerdict(passed=True)
            return RiskVerdict(False, "kill_switch active — only SELL-to-close allowed")

        # SELL needs open position
        if decision.action == DecisorAction.SELL:
            if btc_held <= 0 or open_positions_count == 0:
                return RiskVerdict(False, "SELL requested but no open position to close")
            return RiskVerdict(passed=True)

        # BUY checks
        if decision.stop_loss is None:
            return RiskVerdict(False, "BUY requires stop_loss")
        if decision.stop_loss >= current_price:
            return RiskVerdict(False, "stop_loss must be < current_price")
        if decision.position_size_pct > self.max_position_pct + 1e-9:
            return RiskVerdict(False, f"position_size_pct {decision.position_size_pct} > max {self.max_position_pct}")
        if open_positions_count >= self.max_simultaneous_trades:
            return RiskVerdict(False, f"max_simultaneous_trades reached: {open_positions_count}")
        if daily_pnl_pct <= self.daily_stop_pct:
            return RiskVerdict(False, f"daily P&L breach: {daily_pnl_pct:.4f}")
        sl_distance = current_price - decision.stop_loss
        if sl_distance < 0.5 * atr_1h:
            return RiskVerdict(False, f"SL distance {sl_distance:.2f} < 0.5*ATR {0.5*atr_1h:.2f}")
        if decision.take_profit is not None:
            reward = decision.take_profit - current_price
            risk = sl_distance
            if risk > 0 and reward / risk < 1.5:
                return RiskVerdict(False, f"R:R ratio {reward/risk:.2f} < 1.5")
        return RiskVerdict(passed=True)
