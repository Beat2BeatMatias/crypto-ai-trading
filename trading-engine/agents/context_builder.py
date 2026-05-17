from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Indicators, Position, Decision, Trade
from collectors.orderbook_collector import OrderBookSnapshot
from agents.labelers import (
    get_operational_profile,
    get_tf_priority_order,
    get_profile_holding_range,
    get_profile_confluences,
    rsi_label,
    macd_label,
    trend_label,
    volatility_label,
    stoch_label,
    vwap_label,
    structure_label,
    imbalance_label,
)


_ALL_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

# Candles per day per timeframe (for ATR 7d history query)
_TF_ROWS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6}


class ContextBuilder:
    def __init__(self, session: AsyncSession, *, symbol: str):
        self.session = session
        self.symbol = symbol

    async def build(self, *, orderbook: OrderBookSnapshot | None,
                    usdt_balance: float, btc_held: float,
                    playbook_content: str,
                    max_position_pct: float = 0.10,
                    max_simultaneous_trades: int,
                    daily_stop_pct: float,
                    decisor_interval_min: int,
                    mode: str,
                    taker_fee_pct: float,
                    maker_fee_pct: float,
                    atr_timeframe: str = "15m",
                    min_rr_ratio: float = 1.3,
                    sl_atr_multiplier: float = 0.3,
                    calibration: dict | None = None,
                    current_drawdown_pct: float = 0.0) -> dict[str, Any]:

        cal = calibration or {}

        # ------------------------------------------------------------------ #
        # DB queries
        # ------------------------------------------------------------------ #
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

        today_start = datetime.combine(
            date.today(), datetime.min.time()
        ).replace(tzinfo=timezone.utc)
        trades_today = (await self.session.execute(
            select(Trade).where(
                Trade.ts_close >= today_start,
                Trade.status == "closed",
            )
        )).scalars().all()

        # ATR 7d history for expanding check
        hist_limit = _TF_ROWS_PER_DAY.get(atr_timeframe, 96) * 7
        hist_rows = (await self.session.execute(
            select(Indicators).order_by(desc(Indicators.time)).limit(hist_limit)
        )).scalars().all()

        # ------------------------------------------------------------------ #
        # Core scalars
        # ------------------------------------------------------------------ #
        price = (self._get(ind, "1h", "last_close")
                 or self._get(ind, "5m", "last_close") or 0.0)
        sl_atr_max = cal.get("sl_atr_max_multiplier", 1.5)
        roundtrip_fee_pct = taker_fee_pct * 2

        atr_ref_val = float(
            self._get(ind, atr_timeframe, "atr")
            or self._get(ind, "15m", "atr") or 0
        )
        atr_hist_values = [
            float((row.data.get(atr_timeframe, {}) or {}).get("atr") or 0)
            for row in hist_rows
            if (row.data.get(atr_timeframe, {}) or {}).get("atr")
        ]
        atr_avg_7d = (
            sum(atr_hist_values) / len(atr_hist_values)
            if atr_hist_values else atr_ref_val
        )
        atr_expanding = atr_ref_val > atr_avg_7d * 1.1 if atr_avg_7d > 0 else False

        # ------------------------------------------------------------------ #
        # Operational profile
        # ------------------------------------------------------------------ #
        profile = get_operational_profile(decisor_interval_min, atr_timeframe)
        tf_order = get_tf_priority_order(profile)
        holding_range = get_profile_holding_range(profile)
        profile_confluences = get_profile_confluences(profile)

        # ------------------------------------------------------------------ #
        # Per-TF indicator blocks with labels
        # ------------------------------------------------------------------ #
        tf_blocks: dict[str, dict[str, Any]] = {}
        for tf in _ALL_TIMEFRAMES:
            tf_data = ind.get(tf, {}) or {}
            if not tf_data:
                continue
            tf_price = tf_data.get("last_close") or price
            tf_blocks[tf] = self._build_tf_block(
                tf=tf,
                tf_data=tf_data,
                price=tf_price,
            )

        # ------------------------------------------------------------------ #
        # Cross-TF alignment summary
        # ------------------------------------------------------------------ #
        cross_tf = self._build_cross_tf_summary(tf_blocks, price)

        # ------------------------------------------------------------------ #
        # Key levels (EMAs, ATR bands, 24h high/low, VWAP, pivots placeholder)
        # ------------------------------------------------------------------ #
        ema50_1h = self._get(ind, "1h", "ema50") or 0
        ema200_1h = self._get(ind, "1h", "ema200") or 0
        ema50_4h = self._get(ind, "4h", "ema50") or 0
        ema200_4h = self._get(ind, "4h", "ema200") or 0

        vwap_1h = self._get(ind, "1h", "vwap") or 0
        vwap_15m = self._get(ind, "15m", "vwap") or 0

        high_24h = price * 1.02   # placeholder until we persist 24h stats
        low_24h = price * 0.98

        # ------------------------------------------------------------------ #
        # Portfolio state
        # ------------------------------------------------------------------ #
        total_capital = usdt_balance + btc_held * price
        pnl_today_usd = sum(float(t.pnl_usdt or 0) for t in trades_today)
        pnl_today_pct = (
            pnl_today_usd / max(total_capital, 1.0) * 100
            if total_capital > 0 else 0.0
        )
        unrealized_pnl = sum(float(p.unrealized_pnl or 0) for p in open_positions)
        wins_today = sum(1 for t in trades_today if (t.pnl_usdt or 0) > 0)
        losses_today = sum(1 for t in trades_today if (t.pnl_usdt or 0) < 0)

        # ------------------------------------------------------------------ #
        # Risk config block (compacto — solo lo que el LLM necesita razonar)
        # ------------------------------------------------------------------ #
        min_fees_to_tp = cal.get("min_fees_to_tp_ratio", 3.0)
        min_confluences = cal.get("min_confluences_buy", 2)
        cooldown_min = cal.get("cooldown_after_sell_min", 15)
        expected_holding_max = cal.get("expected_holding_max_min", 240)
        min_position_size = cal.get("min_position_size", 0.005)

        # ------------------------------------------------------------------ #
        # Order book derived
        # ------------------------------------------------------------------ #
        ob = orderbook
        spread_pct = ob.spread_pct if ob else 0.0
        imb = ob.imbalance if ob else 1.0
        adj_spread_threshold = cal.get("adj_spread_threshold_pct", 0.05)

        # ------------------------------------------------------------------ #
        # Flat ctx — backward-compatible keys (still used by prompt templates)
        # ------------------------------------------------------------------ #
        ctx: dict[str, Any] = {
            # Meta
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "mode": mode,
            "symbol": "BTC/USDT",

            # ---- Block A: Operational profile ----
            "block_a_profile": profile,
            "block_a_tf_order": tf_order,
            "block_a_holding_range_min": holding_range[0],
            "block_a_holding_range_max": holding_range[1],
            "block_a_priority_confluences": profile_confluences,
            "decisor_interval_min": decisor_interval_min,
            "atr_timeframe": atr_timeframe,
            "expected_holding_max_min": expected_holding_max,

            # ---- Block B: Market snapshot ----
            "price": price,
            "spread": ob.spread if ob else 0,
            "spread_pct": spread_pct,
            "pct_1h": 0.0,
            "pct_4h": 0.0,
            "pct_24h": 0.0,
            "pct_7d": 0.0,
            "atr_ref": atr_ref_val,
            "atr_ref_tf": atr_timeframe,
            "atr_ref_pct": (atr_ref_val / price * 100) if price else 0,
            "atr_avg_7d": atr_avg_7d,
            "atr_expanding": atr_expanding,
            "atr_ref_min": round(atr_ref_val * sl_atr_multiplier),
            "atr_ref_max": round(atr_ref_val * sl_atr_max),
            "volatility_label": volatility_label(
                self._get(ind, atr_timeframe, "atr_percentile")
            ),

            # ---- Block C: Technical timeframes (per-TF blocks) ----
            "block_c_tf_blocks": tf_blocks,
            "block_c_tf_order": tf_order,
            # Legacy flat keys still used by old prompt templates
            "rsi_1m": self._get(ind, "1m", "rsi") or 0,
            "rsi_5m": self._get(ind, "5m", "rsi") or 0,
            "rsi_15m": self._get(ind, "15m", "rsi") or 0,
            "rsi_1h": self._get(ind, "1h", "rsi") or 0,
            "rsi_4h": self._get(ind, "4h", "rsi") or 0,
            "bb_pct_1m": self._get(ind, "1m", "bb_pct") or 0,
            "bb_pct_5m": self._get(ind, "5m", "bb_pct") or 0,
            "macd_15m": self._get(ind, "15m", "macd") or 0,
            "sig_15m": self._get(ind, "15m", "macd_signal") or 0,
            "hist_15m": self._get(ind, "15m", "macd_hist") or 0,
            "macd_1h": self._get(ind, "1h", "macd") or 0,
            "sig_1h": self._get(ind, "1h", "macd_signal") or 0,
            "ema20_1h": self._get(ind, "1h", "ema20") or 0,
            "ema50_1h": ema50_1h,
            "ema200_1h": ema200_1h,
            "bb_up_1h": self._get(ind, "1h", "bb_upper") or 0,
            "bb_lo_1h": self._get(ind, "1h", "bb_lower") or 0,
            "ema20_4h": self._get(ind, "4h", "ema20") or 0,
            "ema50_4h": ema50_4h,
            "ema200_4h": ema200_4h,
            "atr_1h": self._get(ind, "1h", "atr") or 0,
            "atr_pct_1h": ((self._get(ind, "1h", "atr") or 0) / price * 100) if price else 0,

            # ---- Block D: Key levels ----
            "block_d_ema50_1h": ema50_1h,
            "block_d_ema200_1h": ema200_1h,
            "block_d_ema50_4h": ema50_4h,
            "block_d_ema200_4h": ema200_4h,
            "block_d_vwap_1h": vwap_1h,
            "block_d_vwap_15m": vwap_15m,
            "block_d_high_24h": high_24h,
            "block_d_low_24h": low_24h,
            "support_1h": ema50_1h * 0.99 if ema50_1h else 0,
            "resistance_1h": ema50_1h * 1.01 if ema50_1h else 0,
            "dist_support_pct": 0,
            "dist_resistance_pct": 0,
            "low_24h": low_24h,
            "high_24h": high_24h,
            "bid_wall_price": ob.bid_wall_price if ob else 0,
            "bid_wall_size": ob.bid_wall_size if ob else 0,
            "bid_wall_dist": ob.bid_wall_distance_pct if ob else 0,
            "bid_wall_dist_pct": ob.bid_wall_distance_pct if ob else 0,
            "ask_wall_price": ob.ask_wall_price if ob else 0,
            "ask_wall_size": ob.ask_wall_size if ob else 0,
            "ask_wall_dist": ob.ask_wall_distance_pct if ob else 0,
            "ask_wall_dist_pct": ob.ask_wall_distance_pct if ob else 0,

            # ---- Block E: Order book depth ----
            "imbalance": imb,
            "imbalance_label": imbalance_label(imb),
            "bid_btc": ob.bid_total_btc if ob else 0,
            "ask_btc": ob.ask_total_btc if ob else 0,
            "depth_01pct_bid_btc": ob.depth_01pct.bid_btc if (ob and ob.depth_01pct) else None,
            "depth_01pct_ask_btc": ob.depth_01pct.ask_btc if (ob and ob.depth_01pct) else None,
            "depth_025pct_bid_btc": ob.depth_025pct.bid_btc if (ob and ob.depth_025pct) else None,
            "depth_025pct_ask_btc": ob.depth_025pct.ask_btc if (ob and ob.depth_025pct) else None,
            "depth_05pct_bid_btc": ob.depth_05pct.bid_btc if (ob and ob.depth_05pct) else None,
            "depth_05pct_ask_btc": ob.depth_05pct.ask_btc if (ob and ob.depth_05pct) else None,
            "depth_1pct_bid_btc": ob.depth_1pct.bid_btc if (ob and ob.depth_1pct) else None,
            "depth_1pct_ask_btc": ob.depth_1pct.ask_btc if (ob and ob.depth_1pct) else None,
            "mid_impact_pct": ob.mid_impact_pct if ob else None,

            # ---- Block F: Cross-TF alignment ----
            "block_f_cross_tf": cross_tf,

            # ---- Block G: Recent decisions ----
            "last_decisions_block": self._format_last_decisions(last_decisions),
            "last_action": last_decisions[0].output.get("action") if last_decisions else "n/a",
            "last_confidence": last_decisions[0].output.get("confidence", 0) if last_decisions else 0,
            "last_reasoning": last_decisions[0].output.get("reasoning", "") if last_decisions else "",
            "last_decision_ago": "n/a",

            # ---- Block H: Portfolio state ----
            "capital_total": total_capital,
            "total_capital_usd": total_capital,
            "usdt_available": usdt_balance,
            "btc_held": btc_held,
            "btc_held_usd": btc_held * price,
            "pnl_today_usd": pnl_today_usd,
            "pnl_today_pct": pnl_today_pct,
            "unrealized_pnl_usd": unrealized_pnl,
            "trades_today_count": len(trades_today),
            "wins_today": wins_today,
            "losses_today": losses_today,
            "open_positions_count": len(open_positions),
            "positions_block": self._format_positions(open_positions),
            "current_drawdown_pct": current_drawdown_pct,
            "daily_margin_pct": daily_stop_pct * 100,

            # ---- Block I: Risk config ----
            "max_position_pct": max_position_pct,
            "min_position_size": min_position_size,
            "max_simultaneous_trades": max_simultaneous_trades,
            "daily_stop_pct": daily_stop_pct * 100,
            "taker_fee_pct": taker_fee_pct * 100,
            "maker_fee_pct": maker_fee_pct * 100,
            "roundtrip_fee_pct": roundtrip_fee_pct * 100,
            "min_rr_ratio": min_rr_ratio,
            "sl_atr_multiplier": sl_atr_multiplier,
            "sl_atr_max_multiplier": sl_atr_max,
            "min_fees_to_tp_ratio": min_fees_to_tp,
            "min_confluences_buy": min_confluences,
            "cooldown_after_sell_min": cooldown_min,
            "adj_spread_threshold_pct": adj_spread_threshold,
            "subjective_adj_max": cal.get("subjective_adj_max", 0.10),
            "confluence_weak_factor": cal.get("confluence_weak_factor", 0.5),

            # ---- Block J: Playbook ----
            "playbook": playbook_content,

            # ---- Calibration passthrough ----
            "volume_current": self._get(ind, atr_timeframe, "volume_current") or 0.0,
            "volume_avg20": self._get(ind, atr_timeframe, "volume_avg_20") or 0.0,
            "volume_ratio": self._safe_ratio(
                self._get(ind, atr_timeframe, "volume_current"),
                self._get(ind, atr_timeframe, "volume_avg_20"),
            ),
        }

        # Pass all calibration values so prompt {variable} references resolve
        if cal:
            ctx.update({k: v for k, v in cal.items() if k not in ctx})

        # Rendered text blocks for the prompt
        ctx["block_c_text"] = self._render_tf_blocks_text(
            tf_blocks, tf_order, atr_timeframe, atr_ref_val, price
        )
        ctx["block_d_text"] = self._render_key_levels_text(ctx, price)
        ctx["block_e_text"] = self._render_orderbook_text(ob, ctx)
        ctx["block_f_text"] = self._render_cross_tf_text(cross_tf)

        return ctx

    # ------------------------------------------------------------------ #
    # Per-TF indicator block builder
    # ------------------------------------------------------------------ #

    def _build_tf_block(self, *, tf: str, tf_data: dict,
                        price: float) -> dict[str, Any]:
        rsi = tf_data.get("rsi")
        macd = tf_data.get("macd")
        signal = tf_data.get("macd_signal")
        hist = tf_data.get("macd_hist")
        ema20 = tf_data.get("ema20")
        ema50 = tf_data.get("ema50")
        ema200 = tf_data.get("ema200")
        adx = tf_data.get("adx")
        stoch_k = tf_data.get("stoch_k")
        stoch_d = tf_data.get("stoch_d")
        vwap = tf_data.get("vwap")
        vwap_u1 = tf_data.get("vwap_upper_1")
        vwap_l1 = tf_data.get("vwap_lower_1")
        structure = tf_data.get("structure")
        atr_percentile = tf_data.get("atr_percentile")

        return {
            "tf": tf,
            "rsi": rsi,
            "rsi_label": rsi_label(rsi),
            "macd": macd,
            "macd_signal": signal,
            "macd_hist": hist,
            "macd_label": macd_label(macd, signal, hist),
            "ema9": tf_data.get("ema9"),
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "trend_label": trend_label(ema20, ema50, ema200, price, adx),
            "bb_upper": tf_data.get("bb_upper"),
            "bb_middle": tf_data.get("bb_middle"),
            "bb_lower": tf_data.get("bb_lower"),
            "bb_pct": tf_data.get("bb_pct"),
            "atr": tf_data.get("atr"),
            "atr_percentile": atr_percentile,
            "volatility_label": volatility_label(atr_percentile),
            "adx": adx,
            "plus_di": tf_data.get("plus_di"),
            "minus_di": tf_data.get("minus_di"),
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "stoch_label": stoch_label(stoch_k, stoch_d),
            "vwap": vwap,
            "vwap_upper_1": vwap_u1,
            "vwap_lower_1": vwap_l1,
            "vwap_upper_2": tf_data.get("vwap_upper_2"),
            "vwap_lower_2": tf_data.get("vwap_lower_2"),
            "vwap_dev_pct": tf_data.get("vwap_dev_pct"),
            "vwap_label": vwap_label(price, vwap, vwap_u1, vwap_l1),
            "volume_current": tf_data.get("volume_current"),
            "volume_avg_20": tf_data.get("volume_avg_20"),
            "volume_delta_20": tf_data.get("volume_delta_20"),
            "obv_slope": tf_data.get("obv_slope"),
            "structure": structure,
            "structure_label": structure_label(structure),
            "wick_upper_c2": tf_data.get("wick_upper_c2"),
            "wick_lower_c2": tf_data.get("wick_lower_c2"),
            "body_ratio_c2": tf_data.get("body_ratio_c2"),
        }

    # ------------------------------------------------------------------ #
    # Cross-TF alignment summary
    # ------------------------------------------------------------------ #

    def _build_cross_tf_summary(self, tf_blocks: dict,
                                price: float) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        bull_count = 0
        bear_count = 0
        tf_assessments: dict[str, str] = {}

        for tf in _ALL_TIMEFRAMES:
            blk = tf_blocks.get(tf)
            if not blk:
                continue
            tl = blk.get("trend_label", "n/d")
            tf_assessments[tf] = tl
            if "up" in tl:
                bull_count += 1
            elif "down" in tl:
                bear_count += 1

        total = bull_count + bear_count
        if total == 0:
            alignment = "neutral"
        elif bull_count >= bear_count * 2:
            alignment = "bullish_aligned"
        elif bear_count >= bull_count * 2:
            alignment = "bearish_aligned"
        else:
            alignment = "mixed"

        summary["tf_assessments"] = tf_assessments
        summary["alignment"] = alignment
        summary["bull_count"] = bull_count
        summary["bear_count"] = bear_count

        # MACD momentum alignment
        macd_bull = sum(
            1 for tf in ["15m", "1h", "4h"]
            if tf_blocks.get(tf, {}).get("macd_label", "").startswith("bullish")
        )
        macd_bear = sum(
            1 for tf in ["15m", "1h", "4h"]
            if tf_blocks.get(tf, {}).get("macd_label", "").startswith("bearish")
        )
        if macd_bull > macd_bear:
            summary["momentum"] = "bullish"
        elif macd_bear > macd_bull:
            summary["momentum"] = "bearish"
        else:
            summary["momentum"] = "mixed"

        return summary

    # ------------------------------------------------------------------ #
    # Text rendering for prompt injection
    # ------------------------------------------------------------------ #

    def _render_tf_blocks_text(self, tf_blocks: dict, tf_order: list[str],
                               atr_tf: str, atr_ref: float,
                               price: float) -> str:
        lines: list[str] = []
        priority_map = {tf: i for i, tf in enumerate(tf_order)}
        for tf in tf_order:
            blk = tf_blocks.get(tf)
            if not blk:
                continue
            idx = priority_map.get(tf, 99)
            prio = "ALTA" if idx < 2 else ("MEDIA" if idx < 3 else "BAJA")
            atr_val = blk.get("atr") or 0
            atr_pct = (atr_val / price * 100) if price else 0
            bb_pct = blk.get("bb_pct")
            bb_pct_str = f"{bb_pct:.0f}" if bb_pct is not None else "n/d"
            vol_c = blk.get("volume_current") or 0
            vol_a = blk.get("volume_avg_20") or 0
            vol_ratio = f"{vol_c/vol_a:.1f}x" if vol_a > 0 else "n/d"
            obv = blk.get("obv_slope")
            obv_str = f"{obv:+.3f}" if obv is not None else "n/d"
            vwap = blk.get("vwap")
            vwap_str = f"${vwap:,.0f} ({blk.get('vwap_label','n/d')})" if vwap else "n/d"
            adx = blk.get("adx")
            adx_str = f"{adx:.0f}" if adx is not None else "n/d"
            stoch_k = blk.get("stoch_k")
            stoch_d = blk.get("stoch_d")
            stoch_str = (f"%K={stoch_k:.0f} %D={stoch_d:.0f} ({blk.get('stoch_label','n/d')})"
                         if stoch_k is not None else "n/d")
            struct = blk.get("structure_label", "n/d")
            wick_u = blk.get("wick_upper_c2")
            wick_l = blk.get("wick_lower_c2")
            body_r = blk.get("body_ratio_c2")
            wick_str = (f"upper={wick_u:.2f} lower={wick_l:.2f} body={body_r:.2f}"
                        if wick_u is not None else "n/d")

            def _f(v: float | None, fmt: str = ".1f", fallback: str = "n/d") -> str:
                return format(v, fmt) if v is not None else fallback

            macd_v = blk.get("macd")
            macd_s = blk.get("macd_signal")
            macd_h = blk.get("macd_hist")
            macd_str = (
                f"{macd_v:+.1f}/{macd_s:+.1f} hist={macd_h:+.1f}"
                if macd_v is not None else "n/d"
            )

            section = [
                f"[{tf}] prioridad={prio} | trend={blk.get('trend_label','n/d')} | structure={struct}",
                f"  RSI={_f(blk.get('rsi'),',.1f')} ({blk.get('rsi_label','n/d')}) | "
                f"Stoch {stoch_str}",
                f"  MACD {macd_str} ({blk.get('macd_label','n/d')})",
                f"  EMA9={_f(blk.get('ema9'),',.0f')} EMA20={_f(blk.get('ema20'),',.0f')} "
                f"EMA50={_f(blk.get('ema50'),',.0f')} EMA200={_f(blk.get('ema200'),',.0f')}",
                f"  BB: upper={_f(blk.get('bb_upper'),',.0f')} mid={_f(blk.get('bb_middle'),',.0f')} "
                f"lower={_f(blk.get('bb_lower'),',.0f')} bb%={bb_pct_str}",
                f"  ATR={atr_val:.0f} ({atr_pct:.2f}%) | ADX={adx_str} | "
                f"volatility={blk.get('volatility_label','n/d')}",
                f"  VWAP={vwap_str}",
                f"  Volume: actual={vol_c:.3f}  avg20={vol_a:.3f}  ratio={vol_ratio} | "
                f"OBV_slope={obv_str}",
                f"  Vela actual — wick_ratio: {wick_str}",
            ]
            lines.extend(section)
            lines.append("")
        return "\n".join(lines).rstrip()

    def _render_key_levels_text(self, ctx: dict, price: float) -> str:
        def dist(level: float) -> str:
            if not level or not price:
                return "n/d"
            return f"{(level - price) / price * 100:+.2f}%"

        lines = [
            f"  Precio actual: ${price:,.2f}",
            f"  EMA50(1h):  ${ctx['block_d_ema50_1h']:,.0f}  {dist(ctx['block_d_ema50_1h'])}",
            f"  EMA200(1h): ${ctx['block_d_ema200_1h']:,.0f}  {dist(ctx['block_d_ema200_1h'])}",
            f"  EMA50(4h):  ${ctx['block_d_ema50_4h']:,.0f}  {dist(ctx['block_d_ema50_4h'])}",
            f"  EMA200(4h): ${ctx['block_d_ema200_4h']:,.0f}  {dist(ctx['block_d_ema200_4h'])}",
            f"  VWAP(1h):  ${ctx['block_d_vwap_1h']:,.0f}  {dist(ctx['block_d_vwap_1h'])}",
            f"  VWAP(15m): ${ctx['block_d_vwap_15m']:,.0f}  {dist(ctx['block_d_vwap_15m'])}",
            f"  High 24h: ${ctx['block_d_high_24h']:,.0f}  {dist(ctx['block_d_high_24h'])}",
            f"  Low  24h: ${ctx['block_d_low_24h']:,.0f}  {dist(ctx['block_d_low_24h'])}",
            f"  Bid wall: ${ctx['bid_wall_price']:,.0f} ({ctx['bid_wall_size']:.1f} BTC)  {dist(ctx['bid_wall_price'])}",
            f"  Ask wall: ${ctx['ask_wall_price']:,.0f} ({ctx['ask_wall_size']:.1f} BTC)  {dist(ctx['ask_wall_price'])}",
            f"  ATR SL válido: ${ctx['atr_ref_min']:.0f}–${ctx['atr_ref_max']:.0f} bajo el precio",
        ]
        return "\n".join(lines)

    def _render_orderbook_text(self, ob: OrderBookSnapshot | None,
                               ctx: dict) -> str:
        if not ob:
            return "  (sin snapshot de order book)"
        lines = [
            f"  Spread: ${ob.spread:.2f} ({ob.spread_pct:.4f}%)",
            f"  Imbalance: {ob.imbalance:.2f} ({ctx['imbalance_label']})",
            f"  Bid total: {ob.bid_total_btc:.3f} BTC | Ask total: {ob.ask_total_btc:.3f} BTC",
        ]
        for attr, label in [("depth_01pct", "±0.1%"), ("depth_025pct", "±0.25%"),
                             ("depth_05pct", "±0.5%"), ("depth_1pct", "±1.0%")]:
            depth = getattr(ob, attr, None)
            if depth:
                lines.append(
                    f"  Depth {label}: bid {depth.bid_btc:.2f} BTC / ask {depth.ask_btc:.2f} BTC"
                )
        if ob.mid_impact_pct is not None:
            lines.append(f"  Mid-price impact estimado: {ob.mid_impact_pct:+.3f}%")
        return "\n".join(lines)

    def _render_cross_tf_text(self, cross_tf: dict) -> str:
        assessments = cross_tf.get("tf_assessments", {})
        lines = ["  Tendencia por TF:"]
        for tf in _ALL_TIMEFRAMES:
            lbl = assessments.get(tf, "n/d")
            lines.append(f"    {tf}: {lbl}")
        lines.append(f"  Alineación global: {cross_tf.get('alignment','n/d')}")
        lines.append(f"  Momentum MACD: {cross_tf.get('momentum','n/d')}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Static helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get(ind: dict[str, Any], tf: str, key: str) -> Any:
        return (ind.get(tf, {}) or {}).get(key)

    @staticmethod
    def _safe_ratio(num: Any, den: Any) -> float:
        try:
            n, d = float(num), float(den)
            return n / d if d and d > 0 else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def _format_positions(positions: list) -> str:
        if not positions:
            return "  Ninguna"
        lines = []
        for i, p in enumerate(positions, 1):
            pnl = float(p.unrealized_pnl or 0)
            pnl_pct = float(p.unrealized_pct or 0)
            lines.append(
                f"  {i}. LONG {float(p.quantity_btc):.6f} BTC | "
                f"entry=${float(p.entry_price):,.2f} | "
                f"P&L no realizado: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_last_decisions(decisions: list) -> str:
        if not decisions:
            return "  Sin decisiones previas."
        lines = []
        for d in decisions:
            outcome = (d.outcome or {}).get("close_reason", "")
            outcome_str = f" [outcome={outcome}]" if outcome else ""
            two_pass = d.output.get("two_pass_triggered", False)
            tp_str = " [two-pass]" if two_pass else ""
            lines.append(
                f"  [{d.ts.strftime('%Y-%m-%dT%H:%M:%SZ')}] "
                f"action={d.output.get('action','?')} "
                f"confidence={float(d.output.get('confidence', 0)):.2f}"
                f"{outcome_str}{tp_str}"
            )
            # Inyectar detalle de coherence warnings para que el LLM aprenda
            # de sus inconsistencias pasadas en el ciclo siguiente
            warnings = d.output.get("coherence_warnings", [])
            if warnings:
                lines.append(
                    f"    ⚠ Inconsistencias detectadas en esa decisión "
                    f"({len(warnings)} regla{'s' if len(warnings) > 1 else ''}):"
                )
                for w in warnings[:4]:  # límite para no inflar tokens
                    rule = w.get("rule_id", "?")
                    msg = w.get("message", "")[:120]
                    lines.append(f"      [{rule}] {msg}")
                if len(warnings) > 4:
                    lines.append(f"      ... y {len(warnings) - 4} más.")
        return "\n".join(lines)
