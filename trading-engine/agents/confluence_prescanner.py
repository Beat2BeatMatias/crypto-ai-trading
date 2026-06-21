"""
ConfluencePrescanner — evaluacion deterministica pre-LLM de confluencias A-P.

Corre antes de la llamada al LLM para inyectar en el prompt un bloque
[DETECCION AUTOMATICA DE CONFLUENCIAS] con las confluencias que los
indicadores numericos realmente muestran.

Objetivo: eliminar el error de interpretacion del LLM sobre cuando una
confluencia esta presente. El LLM luego decide CUALES usar y si su
analisis cualitativo discrepa, debe explicarlo en [SENALES].
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def _tbf(ctx: dict[str, Any], tf: str, key: str) -> float:
    """Lee un valor float de block_c_tf_blocks."""
    blocks = ctx.get("block_c_tf_blocks", {})
    blk = blocks.get(tf, {})
    if isinstance(blk, dict):
        val = blk.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0


def _tbf_opt(ctx: dict[str, Any], tf: str, key: str) -> float | None:
    """Como _tbf pero retorna None si no hay dato."""
    blocks = ctx.get("block_c_tf_blocks", {})
    blk = blocks.get(tf, {})
    if isinstance(blk, dict):
        val = blk.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _ctx_f(ctx: dict[str, Any], key: str) -> float:
    """Lee un valor float directo del contexto."""
    val = ctx.get(key)
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            pass
    return 0.0


def _pct_dist(price: float, level: float) -> float | None:
    if price and level:
        return (level - price) / price * 100
    return None


_DETECTED = "DETECTADO"
_MARGINAL = "MARGINAL"
_AUSENTE = "AUSENTE"

_EMOJI = {_DETECTED: "[OK]", _MARGINAL: "[??]", _AUSENTE: "[--]"}


_WICK_MIN_TOTAL = 0.25  # la suma de ambas mechas debe superar 25% del candle


def _wick_bearish(ctx: dict[str, Any], tf: str) -> bool:
    """True si la mecha superior domina (vela bajista)."""
    up = _tbf_opt(ctx, tf, "wick_upper_c2")
    low = _tbf_opt(ctx, tf, "wick_lower_c2")
    if up is not None and low is not None and (up + low) >= _WICK_MIN_TOTAL:
        return up > low * 1.5
    return False


def _wick_bullish(ctx: dict[str, Any], tf: str) -> bool:
    """True si la mecha inferior domina (vela alcista / rechazo abajo)."""
    up = _tbf_opt(ctx, tf, "wick_upper_c2")
    low = _tbf_opt(ctx, tf, "wick_lower_c2")
    if low is not None and up is not None and (up + low) >= _WICK_MIN_TOTAL:
        return low > up * 1.5
    return False


def _obv_positive(ctx: dict[str, Any], tf: str, atol: float = 0.0) -> bool:
    obv = _tbf_opt(ctx, tf, "obv_slope")
    if obv is not None:
        return obv > atol
    return False


def _obv_negative(ctx: dict[str, Any], tf: str, atol: float = 0.0) -> bool:
    obv = _tbf_opt(ctx, tf, "obv_slope")
    if obv is not None:
        return obv < -atol
    return False


class ConfluencePrescanner:
    """
    Evalua las 16 confluencias del catalogo A-P usando datos numericos del contexto.

    Uso:
        prescanner = ConfluencePrescanner()
        resultados = prescanner.scan(ctx)
        texto = prescanner.render(resultados)
    """

    def scan(self, ctx: dict[str, Any]) -> dict[str, dict]:
        primary = str(ctx.get("confluence_primary_tf", "15m"))
        secondary = str(ctx.get("confluence_secondary_tf", "1h"))
        price = _ctx_f(ctx, "price")

        return {
            "A": self._a(ctx, primary, secondary),
            "B": self._b(ctx, primary, secondary),
            "C": self._c(ctx, primary, secondary, price),
            "D": self._d(ctx, primary, secondary),
            "E": self._e(ctx),
            "F": self._f(ctx, primary, price),
            "G": self._g(ctx),
            "H": self._h(ctx, primary, secondary),
            "I": self._i(ctx, primary, secondary),
            "J": self._j(ctx, primary, secondary),
            "K": self._k(ctx, primary, secondary, price),
            "L": self._l(ctx),
            "M": self._m(ctx),
            "N": self._n(ctx, primary, price),
            "O": self._o(ctx),
            "P": self._p(ctx, primary, secondary),
        }

    # ── ALISTAS (BUY / LONG) ───────────────────────────────────────── #

    def _a(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """RSI_OVERSOLD_BOUNCE — RSI <35 en TF primario/secundario + mecha baja."""
        rsi_p = _tbf(ctx, primary, "rsi")
        rsi_s = _tbf(ctx, secondary, "rsi")
        rsi_below = rsi_p < 35 or rsi_s < 35
        bullish = _wick_bullish(ctx, primary) or _wick_bullish(ctx, secondary)
        rsi_detail = f"RSI({primary})={rsi_p:.1f}, RSI({secondary})={rsi_s:.1f}"

        if rsi_below and bullish:
            return {
                "status": _DETECTED,
                "detail": f"{rsi_detail} — RSI <35 + mecha baja dominante",
            }
        if rsi_below:
            return {
                "status": _MARGINAL,
                "detail": f"{rsi_detail} — RSI <35 pero sin mecha baja confirmada",
            }
        return {
            "status": _AUSENTE,
            "detail": f"{rsi_detail} — ambos >=35",
        }

    def _b(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """MACD_BULLISH_CROSS — MACD > Signal en TF p/s + hist positivo."""
        macd_p = _tbf(ctx, primary, "macd")
        sig_p = _tbf(ctx, primary, "macd_signal")
        macd_s = _tbf(ctx, secondary, "macd")
        sig_s = _tbf(ctx, secondary, "macd_signal")
        hist_p = _tbf(ctx, primary, "macd_hist")

        crossed_p = macd_p > sig_p
        crossed_s = macd_s > sig_s
        hist_pos = hist_p > 0

        if (crossed_p or crossed_s) and hist_pos:
            return {
                "status": _DETECTED,
                "detail": (
                    f"MACD({primary})={macd_p:+.1f} Signal={sig_p:+.1f} "
                    f"(cruzado={crossed_p}) hist={hist_p:+.1f}>0"
                ),
            }
        if crossed_p or crossed_s:
            return {
                "status": _MARGINAL,
                "detail": (
                    f"MACD({primary})={macd_p:+.1f} Signal={sig_p:+.1f} "
                    f"— cruzado pero hist={hist_p:+.1f} <=0"
                ),
            }
        return {
            "status": _AUSENTE,
            "detail": (
                f"MACD({primary})={macd_p:+.1f} <= Signal={sig_p:+.1f}, "
                f"MACD({secondary})={macd_s:+.1f} <= Signal={sig_s:+.1f}"
                if not crossed_p and not crossed_s
                else f"MACD {primary} no cruzado al alza"
            ),
        }

    def _c(self, ctx: dict[str, Any], primary: str, secondary: str, price: float) -> dict:
        """EMA_SUPPORT_HOLD — precio cerca de EMA50 1h con mecha baja."""
        ema50_1h = _tbf(ctx, "1h", "ema50")
        if not price or not ema50_1h:
            return {"status": _AUSENTE, "detail": "EMA50 1h o precio no disponibles"}

        dist = _pct_dist(price, ema50_1h)
        cerca = dist is not None and abs(dist) < 1.0
        # mecha baja en TFs de maxima prioridad (primario/secundario + 5m/1m)
        mecha = (
            _wick_bullish(ctx, primary)
            or _wick_bullish(ctx, secondary)
            or _wick_bullish(ctx, "5m")
            or _wick_bullish(ctx, "1m")
        )

        detail = f"dist_soporte={dist:+.2f}% | precio sobre EMA50 1h" if dist and dist < 0 else (
            f"dist_soporte={dist:+.2f}% | precio bajo EMA50 1h" if dist else ""
        )

        if cerca and mecha:
            return {"status": _DETECTED, "detail": f"{detail} + mecha baja presente"}
        if cerca:
            return {"status": _MARGINAL, "detail": f"{detail} pero sin mecha baja"}
        return {
            "status": _AUSENTE,
            "detail": f"dist_soporte={dist:+.2f}% (fuera de ±1.0%)" if dist else detail,
        }

    def _d(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """BB_LOWER_REVERSAL — BB%% 5m <5 + vela de rechazo."""
        bb5 = _tbf_opt(ctx, "5m", "bb_pct")
        if bb5 is None:
            return {"status": _AUSENTE, "detail": "BB%% 5m no disponible"}
        if bb5 < 5:
            mecha = _wick_bullish(ctx, "5m")
            detail = f"BB%% 5m={bb5:.0f}% <5%"
            if mecha:
                return {"status": _DETECTED, "detail": f"{detail} + mecha baja"}
            return {"status": _MARGINAL, "detail": f"{detail} pero sin mecha baja confirmada"}
        return {"status": _AUSENTE, "detail": f"BB%% 5m={bb5:.0f}% >=5%"}

    def _e(self, ctx: dict[str, Any]) -> dict:
        """ORDERBOOK_BID_PRESSURE — imbalance >1.2 + bid_wall cerca."""
        imb = _ctx_f(ctx, "imbalance")
        bid_dist = _ctx_f(ctx, "bid_wall_dist_pct")
        if not imb:
            return {"status": _AUSENTE, "detail": "sin datos de order book"}
        if imb > 1.2 and (bid_dist == 0 or bid_dist < 0.3):
            return {
                "status": _DETECTED,
                "detail": f"imbalance={imb:.2f} >1.2, bid_wall_dist={bid_dist:.2f}%",
            }
        return {
            "status": _AUSENTE,
            "detail": f"imbalance={imb:.2f} (<=1.2)" if imb <= 1.2 else
            f"imbalance={imb:.2f} >1.2 pero bid_wall_dist={bid_dist:.2f}% >=0.3%",
        }

    def _f(self, ctx: dict[str, Any], primary: str, price: float) -> dict:
        """BREAKOUT_VOL_CONFIRMED — ruptura resistencia + vol >1.5x + OBV+."""
        high_24h = _ctx_f(ctx, "high_24h")
        res_1h = _ctx_f(ctx, "resistance_1h")
        vol_ratio = _ctx_f(ctx, "volume_ratio")
        obv_pos = _obv_positive(ctx, primary) or _obv_positive(ctx, "1h")

        above_high = price > high_24h > 0
        above_res = price > res_1h > 0
        vol_alto = vol_ratio > 1.5

        if (above_high or above_res) and vol_alto and obv_pos:
            return {
                "status": _DETECTED,
                "detail": f"precio ${price:,.0f} > {'high_24h' if above_high else 'resistencia 1h'}, vol_ratio={vol_ratio:.1f}x, OBV+",
            }
        if (above_high or above_res) and vol_alto:
            return {
                "status": _MARGINAL,
                "detail": f"precio sobre resistencia pero OBV no positivo",
            }
        return {
            "status": _AUSENTE,
            "detail": (
                f"precio ${price:,.0f} en rango (high_24h=${high_24h:,.0f}), vol_ratio={vol_ratio:.1f}x"
            ),
        }

    def _g(self, ctx: dict[str, Any]) -> dict:
        """HIGHER_TF_ALIGNMENT — 1h=uptrend + RSI 4h>50 + EMA20 4h > EMA50 4h."""
        cross = ctx.get("block_f_cross_tf", {})
        tf_assess = cross.get("tf_assessments", {}) if isinstance(cross, dict) else {}
        trend_1h = str(tf_assess.get("1h", ""))

        rsi_4h = _tbf(ctx, "4h", "rsi")
        e20_4h = _tbf(ctx, "4h", "ema20")
        e50_4h = _tbf(ctx, "4h", "ema50")

        uptrend = "up" in trend_1h.lower() if trend_1h else False
        rsi_ok = rsi_4h > 50
        emas_ok = e20_4h > e50_4h > 0

        if uptrend and rsi_ok and emas_ok:
            return {
                "status": _DETECTED,
                "detail": f"1h={trend_1h}, RSI 4h={rsi_4h:.1f}>50, EMA20 4h={e20_4h:,.0f} > EMA50 4h={e50_4h:,.0f}",
            }

        parts = []
        if not uptrend:
            parts.append(f"1h={trend_1h or 'n/d'}")
        if not rsi_ok:
            parts.append(f"RSI 4h={rsi_4h:.1f}<=50")
        if not emas_ok and e20_4h and e50_4h:
            parts.append(f"EMA20 4h={e20_4h:,.0f} <= EMA50 4h={e50_4h:,.0f}")
        return {"status": _AUSENTE, "detail": ", ".join(parts) if parts else "datos insuficientes"}

    def _h(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """RANGE_SUPPORT_TOUCH — at_range_low o Stoch <20 o BB%% <10 en TF p/s."""
        at_low = _ctx_f(ctx, "at_range_low")
        stoch_p = _tbf_opt(ctx, primary, "stoch_k")
        stoch_s = _tbf_opt(ctx, secondary, "stoch_k")
        bb_p = _tbf_opt(ctx, primary, "bb_pct")
        bb_s = _tbf_opt(ctx, secondary, "bb_pct")

        stoch_low = (stoch_p is not None and stoch_p < 20) or (stoch_s is not None and stoch_s < 20)
        bb_low = (bb_p is not None and bb_p < 10) or (bb_s is not None and bb_s < 10)

        detail = (
            f"at_range_low={bool(at_low)} | "
            f"Stoch({primary})={stoch_p or 'n/d'} {'<20' if stoch_p is not None and stoch_p < 20 else '>=20'} | "
            f"BB%({primary})={bb_p or 'n/d'} {'<10' if bb_p is not None and bb_p < 10 else '>=10'}"
        )

        if bool(at_low) or (stoch_low and bb_low):
            return {"status": _DETECTED, "detail": detail}
        if stoch_low or bb_low:
            return {"status": _MARGINAL, "detail": f"{detail} — una senial pero no ambas"}
        return {"status": _AUSENTE, "detail": detail}

    # ── BAJISTAS (SHORT, futures) ──────────────────────────────────── #

    def _i(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """RSI_OVERBOUGHT_REJECTION — RSI >65 en TF p/s + mecha alta."""
        rsi_p = _tbf(ctx, primary, "rsi")
        rsi_s = _tbf(ctx, secondary, "rsi")
        rsi_above = rsi_p > 65 or rsi_s > 65
        bearish = _wick_bearish(ctx, primary) or _wick_bearish(ctx, secondary)
        rsi_detail = f"RSI({primary})={rsi_p:.1f}, RSI({secondary})={rsi_s:.1f}"

        if rsi_above and bearish:
            return {
                "status": _DETECTED,
                "detail": f"{rsi_detail} — RSI >65 + mecha alta dominante",
            }
        if rsi_above:
            return {
                "status": _MARGINAL,
                "detail": f"{rsi_detail} — RSI >65 pero sin mecha alta confirmada",
            }
        return {
            "status": _AUSENTE,
            "detail": f"{rsi_detail} — ambos <=65",
        }

    def _j(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """MACD_BEARISH_CROSS — MACD < Signal en TF p/s + hist negativo."""
        macd_p = _tbf(ctx, primary, "macd")
        sig_p = _tbf(ctx, primary, "macd_signal")
        macd_s = _tbf(ctx, secondary, "macd")
        sig_s = _tbf(ctx, secondary, "macd_signal")
        hist_p = _tbf(ctx, primary, "macd_hist")

        crossed_p = macd_p < sig_p
        crossed_s = macd_s < sig_s
        hist_neg = hist_p < 0

        if (crossed_p or crossed_s) and hist_neg:
            return {
                "status": _DETECTED,
                "detail": (
                    f"MACD({primary})={macd_p:+.1f} Signal={sig_p:+.1f} "
                    f"(cruzado={crossed_p}) hist={hist_p:+.1f}<0"
                ),
            }
        if crossed_p or crossed_s:
            return {
                "status": _MARGINAL,
                "detail": (
                    f"MACD({primary})={macd_p:+.1f} Signal={sig_p:+.1f} "
                    f"— cruzado pero hist={hist_p:+.1f} >=0"
                ),
            }
        return {
            "status": _AUSENTE,
            "detail": (
                f"MACD({primary})={macd_p:+.1f} >= Signal={sig_p:+.1f}, "
                f"MACD({secondary})={macd_s:+.1f} >= Signal={sig_s:+.1f}"
            ),
        }

    def _k(self, ctx: dict[str, Any], primary: str, secondary: str, price: float) -> dict:
        """BEARISH_EMA_REJECTION — precio cerca de EMA200 1h con mecha alta."""
        ema200_1h = _tbf(ctx, "1h", "ema200")
        if not price or not ema200_1h:
            return {"status": _AUSENTE, "detail": "EMA200 1h o precio no disponibles"}

        dist = _pct_dist(price, ema200_1h)
        cerca = dist is not None and abs(dist) < 1.0 and dist > 0  # precio bajo EMA200
        mecha = (
            _wick_bearish(ctx, primary)
            or _wick_bearish(ctx, secondary)
            or _wick_bearish(ctx, "5m")
            or _wick_bearish(ctx, "1m")
        )

        detail = f"dist_resistencia={dist:+.2f}%"

        if cerca and mecha:
            return {"status": _DETECTED, "detail": f"{detail} + mecha alta presente"}
        if cerca:
            return {"status": _MARGINAL, "detail": f"{detail} pero sin mecha alta"}
        return {"status": _AUSENTE, "detail": f"{detail} — precio no cerca de EMA200"}

    def _l(self, ctx: dict[str, Any]) -> dict:
        """BB_UPPER_REJECTION — BB%% 5m >95 + vela de rechazo."""
        bb5 = _tbf_opt(ctx, "5m", "bb_pct")
        if bb5 is None:
            return {"status": _AUSENTE, "detail": "BB%% 5m no disponible"}
        if bb5 > 95:
            mecha = _wick_bearish(ctx, "5m")
            detail = f"BB%% 5m={bb5:.0f}% >95%"
            if mecha:
                return {"status": _DETECTED, "detail": f"{detail} + mecha alta"}
            return {"status": _MARGINAL, "detail": f"{detail} pero sin mecha alta"}
        return {"status": _AUSENTE, "detail": f"BB%% 5m={bb5:.0f}% <=95%"}

    def _m(self, ctx: dict[str, Any]) -> dict:
        """ORDERBOOK_ASK_PRESSURE — imbalance <0.8 + ask_wall cerca."""
        imb = _ctx_f(ctx, "imbalance")
        ask_dist = _ctx_f(ctx, "ask_wall_dist_pct")
        if not imb:
            return {"status": _AUSENTE, "detail": "sin datos de order book"}
        if imb < 0.8 and (ask_dist == 0 or ask_dist < 0.3):
            return {
                "status": _DETECTED,
                "detail": f"imbalance={imb:.2f} <0.8, ask_wall_dist={ask_dist:.2f}%",
            }
        return {
            "status": _AUSENTE,
            "detail": f"imbalance={imb:.2f} (>=0.8)" if imb >= 0.8 else
            f"imbalance={imb:.2f} <0.8 pero ask_wall_dist={ask_dist:.2f}% >=0.3%",
        }

    def _n(self, ctx: dict[str, Any], primary: str, price: float) -> dict:
        """BREAKDOWN_VOL_CONFIRMED — ruptura soporte + vol >1.5x + OBV-."""
        low_24h = _ctx_f(ctx, "low_24h")
        ema50_1h = _tbf(ctx, "1h", "ema50")
        vol_ratio = _ctx_f(ctx, "volume_ratio")
        obv_neg = _obv_negative(ctx, primary) or _obv_negative(ctx, "1h")

        below_low = price < low_24h if low_24h > 0 else False
        below_ema50 = price < ema50_1h * 0.99 if ema50_1h > 0 else False  # 1% bajo EMA50
        vol_alto = vol_ratio > 1.5

        if (below_low or below_ema50) and vol_alto and obv_neg:
            return {
                "status": _DETECTED,
                "detail": f"precio ${price:,.0f} bajo soporte, vol_ratio={vol_ratio:.1f}x, OBV-",
            }
        if (below_low or below_ema50) and vol_alto:
            return {
                "status": _MARGINAL,
                "detail": f"precio bajo soporte pero OBV no negativo",
            }
        return {
            "status": _AUSENTE,
            "detail": f"precio ${price:,.0f} sobre soportes (low_24h=${low_24h:,.0f}), vol_ratio={vol_ratio:.1f}x",
        }

    def _o(self, ctx: dict[str, Any]) -> dict:
        """LOWER_TF_BEARISH_ALIGNMENT — 1h=downtrend + RSI 4h<50 + EMA20 4h < EMA50 4h."""
        cross = ctx.get("block_f_cross_tf", {})
        tf_assess = cross.get("tf_assessments", {}) if isinstance(cross, dict) else {}
        trend_1h = str(tf_assess.get("1h", ""))

        rsi_4h = _tbf(ctx, "4h", "rsi")
        e20_4h = _tbf(ctx, "4h", "ema20")
        e50_4h = _tbf(ctx, "4h", "ema50")

        downtrend = "down" in trend_1h.lower() if trend_1h else False
        rsi_bajo = rsi_4h < 50
        emas_ok = e20_4h < e50_4h > 0

        if downtrend and rsi_bajo and emas_ok:
            return {
                "status": _DETECTED,
                "detail": f"1h={trend_1h}, RSI 4h={rsi_4h:.1f}<50, EMA20 4h={e20_4h:,.0f} < EMA50 4h={e50_4h:,.0f}",
            }

        parts = []
        if not downtrend:
            parts.append(f"1h={trend_1h or 'n/d'} (no downtrend)")
        if not rsi_bajo:
            parts.append(f"RSI 4h={rsi_4h:.1f}>=50")
        if not emas_ok and e20_4h and e50_4h:
            parts.append(f"EMA20 4h={e20_4h:,.0f} >= EMA50 4h={e50_4h:,.0f}")
        return {"status": _AUSENTE, "detail": ", ".join(parts) if parts else "datos insuficientes"}

    def _p(self, ctx: dict[str, Any], primary: str, secondary: str) -> dict:
        """RANGE_RESISTANCE_TOUCH — at_range_high o Stoch >80 o BB%% >90 en TF p/s."""
        at_high = _ctx_f(ctx, "at_range_high")
        stoch_p = _tbf_opt(ctx, primary, "stoch_k")
        stoch_s = _tbf_opt(ctx, secondary, "stoch_k")
        bb_p = _tbf_opt(ctx, primary, "bb_pct")
        bb_s = _tbf_opt(ctx, secondary, "bb_pct")

        stoch_high = (stoch_p is not None and stoch_p > 80) or (stoch_s is not None and stoch_s > 80)
        bb_high = (bb_p is not None and bb_p > 90) or (bb_s is not None and bb_s > 90)

        detail = (
            f"at_range_high={bool(at_high)} | "
            f"Stoch({primary})={stoch_p or 'n/d'} {'>80' if stoch_p is not None and stoch_p > 80 else '<=80'} | "
            f"BB%({primary})={bb_p or 'n/d'} {'>90' if bb_p is not None and bb_p > 90 else '<=90'} | "
            f"Stoch({secondary})={stoch_s or 'n/d'} {'>80' if stoch_s is not None and stoch_s > 80 else '<=80'}"
        )

        if bool(at_high) or (stoch_high and bb_high):
            return {"status": _DETECTED, "detail": detail}
        if stoch_high or bb_high:
            return {"status": _MARGINAL, "detail": f"{detail} — una senial pero no ambas"}
        return {"status": _AUSENTE, "detail": detail}

    # ── RENDER ─────────────────────────────────────────────────────── #

    def render(self, results: dict[str, dict]) -> str:
        """Renderiza resultados como bloque de texto para el prompt del LLM."""
        bullish_codes = "ABCDEFGH"
        bearish_codes = "IJKLMNOP"
        names = {
            "A": "RSI_OVERSOLD_BOUNCE",
            "B": "MACD_BULLISH_CROSS",
            "C": "EMA_SUPPORT_HOLD",
            "D": "BB_LOWER_REVERSAL",
            "E": "ORDERBOOK_BID_PRESSURE",
            "F": "BREAKOUT_VOL_CONFIRMED",
            "G": "HIGHER_TF_ALIGNMENT",
            "H": "RANGE_SUPPORT_TOUCH",
            "I": "RSI_OVERBOUGHT_REJECTION",
            "J": "MACD_BEARISH_CROSS",
            "K": "BEARISH_EMA_REJECTION",
            "L": "BB_UPPER_REJECTION",
            "M": "ORDERBOOK_ASK_PRESSURE",
            "N": "BREAKDOWN_VOL_CONFIRMED",
            "O": "LOWER_TF_BEARISH_ALIGNMENT",
            "P": "RANGE_RESISTANCE_TOUCH",
        }

        lines = [
            "═══════════════════════════════════════════════════════════════",
            "BLOQUE CP — CONFLUENCIAS VERIFICADAS (deteccion automatica)",
            "[OK] cumple criterios  [??] marginal  [--] ausente",
            "═══════════════════════════════════════════════════════════════",
            "",
            "ALCISTAS (BUY/LONG):",
        ]

        for code in bullish_codes:
            r = results.get(code, {})
            status = r.get("status", _AUSENTE)
            detail = r.get("detail", "")
            label = _EMOJI.get(status, "[--]")
            lines.append(f"  {code} {label} {names[code]:28s} {detail}")

        lines += ["", "BAJISTAS (SHORT, futuros):"]

        for code in bearish_codes:
            r = results.get(code, {})
            status = r.get("status", _AUSENTE)
            detail = r.get("detail", "")
            label = _EMOJI.get(status, "[--]")
            lines.append(f"  {code} {label} {names[code]:28s} {detail}")

        lines += [
            "",
            "INSTRUCCION: Si una confluencia aparece como [OK] y tu analisis coincide,",
            "declarala en tu output. Si aparece como [--] pero consideras que hay evidencia",
            "tecnica valida, explicalo en [SENALES] y declarala igualmente.",
            "═══════════════════════════════════════════════════════════════",
        ]

        return "\n".join(lines)
