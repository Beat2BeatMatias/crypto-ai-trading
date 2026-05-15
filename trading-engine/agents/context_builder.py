from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Indicators, Position, Decision, Trade
from collectors.orderbook_collector import OrderBookSnapshot


class ContextBuilder:
    def __init__(self, session: AsyncSession, *, symbol: str):
        self.session = session
        self.symbol = symbol

    async def build(self, *, orderbook: OrderBookSnapshot | None, usdt_balance: float,
                    btc_held: float, playbook_content: str, max_position_pct: float = 0.10,
                    max_simultaneous_trades: int, daily_stop_pct: float,
                    decisor_interval_min: int, mode: str,
                    taker_fee_pct: float, maker_fee_pct: float,
                    atr_timeframe: str = "15m", min_rr_ratio: float = 1.3,
                    sl_atr_multiplier: float = 0.3,
                    calibration: dict | None = None,
                    current_drawdown_pct: float = 0.0) -> dict[str, Any]:
        ind_row = (await self.session.execute(
            select(Indicators).order_by(desc(Indicators.time)).limit(1)
        )).scalar_one_or_none()
        ind = ind_row.data if ind_row else {}

        open_positions = (await self.session.execute(
            select(Position).where(Position.status == "open")
        )).scalars().all()

        last_decisions = (await self.session.execute(
            select(Decision).where(Decision.agent == "decisor")
            .order_by(desc(Decision.ts)).limit(3)
        )).scalars().all()

        today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
        trades_today = (await self.session.execute(
            select(Trade).where(
                Trade.ts_close >= today_start,
                Trade.status == "closed",
            )
        )).scalars().all()

        price = self._get(ind, "1h", "last_close") or self._get(ind, "5m", "last_close") or 0.0
        roundtrip_fee_pct = taker_fee_pct * 2
        cal = calibration or {}
        sl_atr_max = cal.get("sl_atr_max_multiplier", 1.5)

        # ATR 7-day average: query historical rows for the configured timeframe.
        _tf_rows_per_day = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}
        hist_limit = _tf_rows_per_day.get(atr_timeframe, 96) * 7
        hist_rows = (await self.session.execute(
            select(Indicators).order_by(desc(Indicators.time)).limit(hist_limit)
        )).scalars().all()
        atr_hist_values = [
            float((row.data.get(atr_timeframe, {}) or {}).get("atr") or 0)
            for row in hist_rows
            if (row.data.get(atr_timeframe, {}) or {}).get("atr")
        ]
        atr_ref_val = float(self._get(ind, atr_timeframe, "atr") or self._get(ind, "15m", "atr") or 0)
        atr_avg_7d_val = sum(atr_hist_values) / len(atr_hist_values) if atr_hist_values else atr_ref_val
        atr_expanding = atr_ref_val > atr_avg_7d_val * 1.1 if atr_avg_7d_val > 0 else False

        vol_tf = self._get(ind, atr_timeframe, "volume_current")
        vol_avg = self._get(ind, atr_timeframe, "volume_avg_20")
        volume_ratio = (vol_tf / vol_avg) if (vol_tf and vol_avg and vol_avg > 0) else 0.0
        bid_wall_dist_pct = (
            (orderbook.bid_wall_price - price) / price * 100
            if orderbook and price > 0 else 0.0
        )
        ask_wall_dist_pct = (
            (orderbook.ask_wall_price - price) / price * 100
            if orderbook and price > 0 else 0.0
        )

        ctx = {
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "mode": mode,
            "decisor_interval_min": decisor_interval_min,
            "max_simultaneous_trades": max_simultaneous_trades,
            "daily_stop_pct": daily_stop_pct * 100,
            "max_position_pct": max_position_pct,
            "playbook": playbook_content,
            "taker_fee_pct": taker_fee_pct * 100,
            "maker_fee_pct": maker_fee_pct * 100,
            "roundtrip_fee_pct": roundtrip_fee_pct * 100,
            "capital_total": usdt_balance + btc_held * price,
            "usdt_available": usdt_balance,
            "btc_held": btc_held,
            "btc_held_usd": btc_held * price,
            "total_capital_usd": usdt_balance + btc_held * price,
            "price": price,
            "rsi_1m": self._get(ind, "1m", "rsi") or 0,
            "rsi_5m": self._get(ind, "5m", "rsi") or 0,
            "rsi_15m": self._get(ind, "15m", "rsi") or 0,
            "rsi_1h": self._get(ind, "1h", "rsi") or 0,
            "rsi_4h": self._get(ind, "4h", "rsi") or 0,
            "bb_pct_1m": self._get(ind, "1m", "bb_pct") or 0,
            "bb_pct_5m": self._get(ind, "5m", "bb_pct") or 0,
            "vol_5m": 1.0,
            "macd_15m": self._get(ind, "15m", "macd") or 0,
            "sig_15m": self._get(ind, "15m", "macd_signal") or 0,
            "hist_15m": self._get(ind, "15m", "macd_hist") or 0,
            "macd_1h": self._get(ind, "1h", "macd") or 0,
            "sig_1h": self._get(ind, "1h", "macd_signal") or 0,
            "ema20_1h": self._get(ind, "1h", "ema20") or 0,
            "ema50_1h": self._get(ind, "1h", "ema50") or 0,
            "ema200_1h": self._get(ind, "1h", "ema200") or 0,
            "bb_up_1h": self._get(ind, "1h", "bb_upper") or 0,
            "bb_lo_1h": self._get(ind, "1h", "bb_lower") or 0,
            "ema20_4h": self._get(ind, "4h", "ema20") or 0,
            "ema50_4h": self._get(ind, "4h", "ema50") or 0,
            "ema200_4h": self._get(ind, "4h", "ema200") or 0,
            "atr_1h": self._get(ind, "1h", "atr") or 0,
            "atr_pct_1h": ((self._get(ind, "1h", "atr") or 0) / price * 100) if price else 0,
            "atr_avg_7d": atr_avg_7d_val,
            "atr_expanding": atr_expanding,
            "atr_ref": self._get(ind, atr_timeframe, "atr") or self._get(ind, "15m", "atr") or 0,
            "atr_ref_tf": atr_timeframe,
            "atr_ref_pct": ((self._get(ind, atr_timeframe, "atr") or 0) / price * 100) if price else 0,
            "atr_ref_min": round((self._get(ind, atr_timeframe, "atr") or self._get(ind, "15m", "atr") or 0) * sl_atr_multiplier),
            "atr_ref_max": round((self._get(ind, atr_timeframe, "atr") or self._get(ind, "15m", "atr") or 0) * sl_atr_max),
            "sl_atr_multiplier": sl_atr_multiplier,
            "min_rr_ratio": min_rr_ratio,
            "volatility_label": "normal",
            "support_1h": (self._get(ind, "1h", "ema50") or 0) * 0.99,
            "resistance_1h": (self._get(ind, "1h", "ema50") or 0) * 1.01,
            "dist_support_pct": 0, "dist_resistance_pct": 0,
            "low_24h": price * 0.98, "high_24h": price * 1.02,
            "pct_1h": 0, "pct_4h": 0, "pct_24h": 0, "pct_7d": 0,
            "spread": orderbook.spread if orderbook else 0,
            "spread_pct": orderbook.spread_pct if orderbook else 0,
            "bid_btc": orderbook.bid_total_btc if orderbook else 0,
            "ask_btc": orderbook.ask_total_btc if orderbook else 0,
            "imbalance": orderbook.imbalance if orderbook else 1.0,
            "imbalance_label": "balanced" if orderbook is None else (
                "buy_pressure" if orderbook.imbalance > 1.2
                else "sell_pressure" if orderbook.imbalance < 0.8 else "balanced"),
            "bid_wall_price": orderbook.bid_wall_price if orderbook else 0,
            "bid_wall_size": orderbook.bid_wall_size if orderbook else 0,
            "bid_wall_dist": orderbook.bid_wall_distance_pct if orderbook else 0,
            "ask_wall_price": orderbook.ask_wall_price if orderbook else 0,
            "ask_wall_size": orderbook.ask_wall_size if orderbook else 0,
            "ask_wall_dist": orderbook.ask_wall_distance_pct if orderbook else 0,
            "open_positions_count": len(open_positions),
            "positions_block": self._format_positions(open_positions),
            "pnl_today_usd": sum(float(t.pnl_usdt or 0) for t in trades_today),
            "pnl_today_pct": (
                sum(float(t.pnl_usdt or 0) for t in trades_today)
                / max(usdt_balance + btc_held * price, 1.0) * 100
            ),
            "unrealized_pnl_usd": sum(float(p.unrealized_pnl or 0) for p in open_positions),
            "trades_today_count": len(trades_today),
            "wins_today": sum(1 for t in trades_today if (t.pnl_usdt or 0) > 0),
            "losses_today": sum(1 for t in trades_today if (t.pnl_usdt or 0) < 0),
            "daily_margin_pct": daily_stop_pct * 100,
            "last_decisions_block": self._format_last_decisions(last_decisions),
            "last_action": last_decisions[0].output.get("action") if last_decisions else "n/a",
            "last_confidence": last_decisions[0].output.get("confidence", 0) if last_decisions else 0,
            "last_reasoning": last_decisions[0].output.get("reasoning", "") if last_decisions else "",
            "last_decision_ago": "n/a",
            "atr_timeframe": atr_timeframe,
            "sl_atr_max_multiplier": sl_atr_max,
            "volume_current": vol_tf or 0.0,
            "volume_avg20": vol_avg or 0.0,
            "volume_ratio": volume_ratio,
            "bid_wall_dist_pct": bid_wall_dist_pct,
            "ask_wall_dist_pct": ask_wall_dist_pct,
            "current_drawdown_pct": current_drawdown_pct,
            "min_fees_to_tp_ratio": cal.get("min_fees_to_tp_ratio", 3.0),
            "min_confluences_buy": cal.get("min_confluences_buy", 2),
            "cooldown_after_sell_min": cal.get("cooldown_after_sell_min", 15),
            "subjective_adj_max": cal.get("subjective_adj_max", 0.10),
            "expected_holding_max_min": cal.get("expected_holding_max_min", 240),
            "confluence_weak_factor": cal.get("confluence_weak_factor", 0.5),
            "adj_spread_threshold_pct": cal.get("adj_spread_threshold_pct", 0.05),
        }
        # Merge calibration values so all {variable} references in the prompt resolve correctly.
        if cal:
            ctx.update(cal)
        return ctx

    @staticmethod
    def _get(ind: dict[str, Any], tf: str, key: str) -> Any:
        return (ind.get(tf, {}) or {}).get(key)

    @staticmethod
    def _format_positions(positions: list) -> str:
        if not positions:
            return "  Ninguna"
        return "\n".join(
            f"  {i}. LONG {float(p.quantity_btc):.6f} BTC | entry ${float(p.entry_price):,.2f}"
            for i, p in enumerate(positions, 1)
        )

    @staticmethod
    def _format_last_decisions(decisions: list) -> str:
        if not decisions:
            return "  Sin decisiones previas."
        lines = []
        for d in decisions:
            outcome = (d.outcome or {}).get("close_reason", "")
            outcome_str = f" [outcome={outcome}]" if outcome else ""
            lines.append(
                f"  [{d.ts.strftime('%Y-%m-%dT%H:%M:%SZ')}] "
                f"action={d.output.get('action','?')} "
                f"confidence={float(d.output.get('confidence', 0)):.2f}"
                f"{outcome_str}"
            )
        return "\n".join(lines)
