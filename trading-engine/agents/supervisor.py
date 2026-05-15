from __future__ import annotations
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import (
    Decision, Trade, PlaybookVersion, Ohlcv, Indicators,
    ConfigEntry, ConfigHistory, FeeSnapshot, BalanceSnapshot,
)
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager
from shared.config_store import ConfigKey, ConfigStore

logger = structlog.get_logger()

# Safe ranges for auto-applying supervisor suggestions.
# Values outside these bounds are rejected to guard against LLM hallucinations.
# daily_stop_pct and max_drawdown_pct are intentionally excluded — too critical for auto-apply.
_SAFE_BOUNDS: dict[str, tuple] = {
    # Ola 1 original
    "sl_atr_multiplier":           (0.1, 0.8),
    "min_rr_ratio":                (1.0, 3.0),
    "decisor_interval_min":        (5, 60),
    "max_position_pct":            (0.01, 0.20),
    "conf_threshold_trending_up":  (0.40, 0.85),
    "conf_threshold_range":        (0.50, 0.90),
    "conf_threshold_high_vol":     (0.60, 0.95),
    # Ola 2 additions
    "sl_atr_max_multiplier":       (0.5, 3.0),
    "min_confluences_buy":         (1, 4),
    "min_fees_to_tp_ratio":        (1.5, 6.0),
    "cooldown_after_sell_min":     (0, 120),
    "rsi_overbought_1h":           (60, 85),
    "expected_holding_max_min":    (30, 1440),
}
_VALID_ATR_TIMEFRAMES = {"5m", "15m", "1h"}

# Cross-parameter invariants: value_of[a] must be <= value_of[b] after applying suggestions.
# If a suggestion would violate an invariant, it is rejected with an explicit reason.
_INVARIANTS: list[tuple[str, str, str]] = [
    ("sl_atr_multiplier",          "sl_atr_max_multiplier",   "sl_atr_multiplier <= sl_atr_max_multiplier"),
    ("min_rr_ratio",               "default_rr_ratio",        "min_rr_ratio <= default_rr_ratio"),
    ("conf_threshold_trending_up", "conf_threshold_range",    "conf_threshold_trending_up <= conf_threshold_range"),
    ("conf_threshold_range",       "conf_threshold_high_vol", "conf_threshold_range <= conf_threshold_high_vol"),
]

_CONFIG_SUGGESTION_PROMPT = """Eres un analista cuantitativo de risk management. Basándote en las métricas
de trading del período, sugiere los valores óptimos para los parámetros de configuración del sistema.

MÉTRICAS DEL PERÍODO:
- Trades cerrados: {closed_trades}
- Win rate: {win_rate}%
- Profit factor: {profit_factor}
- P&L total: ${total_pnl}
- Avg ganancia: ${avg_win} | Avg pérdida: ${avg_loss}
- Avg holding: {avg_holding_min} min
- Sharpe período: {sharpe_period}
- Decisiones BUY: {buy_count} ({buy_pct:.1f}%)
- Decisiones HOLD: {hold_count} ({hold_pct:.1f}%)
- BUYs bloqueados por Risk Gate: {rejected_count}
- SL tocados: {sl_hits} | TP alcanzados: {tp_hits}

CONFIGURACIÓN ACTUAL:
- atr_timeframe: {atr_timeframe}
- sl_atr_multiplier: {sl_atr_multiplier}
- sl_atr_max_multiplier: {sl_atr_max_multiplier}
- min_rr_ratio: {min_rr_ratio}
- decisor_interval_min: {decisor_interval_min}
- max_position_pct: {max_position_pct}
- conf_threshold_trending_up: {conf_threshold_trending_up}
- conf_threshold_range: {conf_threshold_range}
- conf_threshold_high_vol: {conf_threshold_high_vol}
- min_confluences_buy: {min_confluences_buy}
- min_fees_to_tp_ratio: {min_fees_to_tp_ratio}
- cooldown_after_sell_min: {cooldown_after_sell_min}
- rsi_overbought_1h: {rsi_overbought_1h}
- expected_holding_max_min: {expected_holding_max_min}

OPCIONES VÁLIDAS:
- atr_timeframe: "5m" | "15m" | "1h"
- sl_atr_multiplier: 0.1 a 0.8 (cuanto menor, más trades pero más riesgo; debe ser < sl_atr_max_multiplier)
- sl_atr_max_multiplier: 0.5 a 3.0 (siempre mayor que sl_atr_multiplier)
- min_rr_ratio: 1.0 a 3.0
- decisor_interval_min: 5 a 60
- max_position_pct: 0.01 a 0.20
- conf_threshold_trending_up: 0.40 a 0.85 (umbral de confianza para BUY en tendencia alcista; debe ser <= conf_threshold_range)
- conf_threshold_range: 0.50 a 0.90 (umbral para BUY en rango; debe ser <= conf_threshold_high_vol)
- conf_threshold_high_vol: 0.60 a 0.95 (umbral para BUY en alta volatilidad; el más exigente)
- min_confluences_buy: 1 a 4 (confluencias mínimas A-H para autorizar BUY; subir si demasiados trades perdedores)
- min_fees_to_tp_ratio: 1.5 a 6.0 (el TP debe ser este múltiplo del costo de fees; subir si SL/TP desbalanceado)
- cooldown_after_sell_min: 0 a 120 (minutos de espera tras SELL antes de nuevo BUY; subir si hay overtrading)
- rsi_overbought_1h: 60 a 85 (RSI 1h máximo para señales alcistas; bajar en mercado sobrecomprado)
- expected_holding_max_min: 30 a 1440 (tiempo máximo esperado en posición en minutos; ajustar al perfil operativo)

INVARIANTES OBLIGATORIAS (el sistema rechazará sugerencias que las violen):
- sl_atr_multiplier DEBE ser <= sl_atr_max_multiplier
- min_rr_ratio DEBE ser <= default_rr_ratio (actualmente {default_rr_ratio})
- conf_threshold_trending_up <= conf_threshold_range <= conf_threshold_high_vol

Responde ÚNICAMENTE con JSON válido, sin texto extra:
{{
  "suggestions": [
    {{"key": "atr_timeframe", "current": "{atr_timeframe}", "suggested": "valor", "reason": "explicación breve en español"}},
    {{"key": "sl_atr_multiplier", "current": "{sl_atr_multiplier}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "sl_atr_max_multiplier", "current": "{sl_atr_max_multiplier}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "min_rr_ratio", "current": "{min_rr_ratio}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "decisor_interval_min", "current": "{decisor_interval_min}", "suggested": 0, "reason": "explicación breve en español"}},
    {{"key": "max_position_pct", "current": "{max_position_pct}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "conf_threshold_trending_up", "current": "{conf_threshold_trending_up}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "conf_threshold_range", "current": "{conf_threshold_range}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "conf_threshold_high_vol", "current": "{conf_threshold_high_vol}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "min_confluences_buy", "current": "{min_confluences_buy}", "suggested": 0, "reason": "explicación breve en español"}},
    {{"key": "min_fees_to_tp_ratio", "current": "{min_fees_to_tp_ratio}", "suggested": 0.0, "reason": "explicación breve en español"}},
    {{"key": "cooldown_after_sell_min", "current": "{cooldown_after_sell_min}", "suggested": 0, "reason": "explicación breve en español"}},
    {{"key": "rsi_overbought_1h", "current": "{rsi_overbought_1h}", "suggested": 0, "reason": "explicación breve en español"}},
    {{"key": "expected_holding_max_min", "current": "{expected_holding_max_min}", "suggested": 0, "reason": "explicación breve en español"}}
  ],
  "summary": "resumen en 1-2 oraciones del estado del sistema y el principal ajuste recomendado"
}}"""


class Supervisor:
    def __init__(self, *, session: AsyncSession, llm: LLMClient, symbol: str,
                 provider: LLMProvider = LLMProvider.GEMINI_PRO,
                 fallbacks: list[LLMProvider] | None = None,
                 min_trades: int = 5,
                 prompt_manager: PromptManager | None = None):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.provider = provider
        self.fallbacks = fallbacks or [LLMProvider.GROQ_LLAMA]
        self.min_trades = min_trades
        self.prompt_manager = prompt_manager or PromptManager(session)

    async def run(self, *, current_config: dict | None = None) -> None:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        metrics = await self._compute_metrics(since)
        rejected_reason = None
        resp = None
        ctx = None
        output: dict = {}

        mode = "diagnostic" if metrics["closed_trades"] < self.min_trades else "normal"
        if mode == "diagnostic":
            logger.info("supervisor.diagnostic_mode", closed=metrics["closed_trades"],
                        min_trades=self.min_trades)

        try:
            op_ctx = await self._compute_operational_context(since)

            cfg = current_config or {}
            decisor_interval_min = int(cfg.get("decisor_interval_min", 10))
            atr_timeframe_cfg = str(cfg.get("atr_timeframe", "15m"))
            expected_holding_max = int(cfg.get("expected_holding_max_min", 240))
            operative_profile = self._derive_operative_profile(decisor_interval_min, atr_timeframe_cfg)

            since_7d = datetime.now(tz=timezone.utc) - timedelta(days=7)
            weekly_gate = await self._compute_weekly_gate(since_7d)

            previous = await self.prompt_manager.get_active_playbook()
            raw_previous = previous.content if previous else "# (vacío)"
            previous_version_num = previous.version if previous else 0
            previous_content = self._trim_previous_playbook(raw_previous, version=previous_version_num)
            decisions_since = (await self.session.execute(
                select(Decision).where(Decision.ts >= since, Decision.agent == "decisor")
                .order_by(Decision.ts.asc())
            )).scalars().all()
            # Limit to last 40 decisions to avoid exceeding provider token limits.
            # Earlier decisions are already captured in the aggregate metrics above.
            _MAX_DECISIONS_DUMP = 40
            decisions_sample = decisions_since[-_MAX_DECISIONS_DUMP:]
            decisions_dump = "\n".join([
                json.dumps({
                    "ts": d.ts.isoformat(),
                    "action": d.output.get("action"),
                    "confidence": d.output.get("confidence"),
                    "regime": d.output.get("regime"),
                    "confluences": d.output.get("confluences", []),
                    "executed": d.executed,
                    "rejected_reason": d.rejected_reason or None,
                    "reasoning": (d.output.get("reasoning") or "")[:60],
                })
                for d in decisions_sample
            ])
            if len(decisions_since) > _MAX_DECISIONS_DUMP:
                decisions_dump = (
                    f"[{len(decisions_since) - _MAX_DECISIONS_DUMP} decisiones anteriores omitidas — "
                    f"resumen en metricas]\n" + decisions_dump
                )

            mode_header = (
                "\n[MODO DIAGNÓSTICO] Sin trades cerrados en 24h. "
                "Diagnosticá por qué no se ejecutaron trades: puede ser mercado lateral/bajista (HOLD correcto), "
                "playbook demasiado restrictivo, o entradas bloqueadas por el Risk Gate. "
                "Analizá las métricas y el contexto de mercado antes de decidir si ajustar el playbook.\n"
                if mode == "diagnostic" else ""
            )

            ctx = {
                **metrics,
                "mode": mode,
                "mode_header": mode_header,
                "previous_version": previous.version if previous else 0,
                "new_version": (previous.version + 1) if previous else 0,
                "previous_playbook": previous_content,
                "decisions_dump": decisions_dump,
                "decisions_sample_count": len(decisions_sample),
                "date": datetime.now(tz=timezone.utc).date().isoformat(),
                "min_trades": self.min_trades,
                # A2: bloques de análisis enriquecido
                "regime_distribution_block": self._format_regime_distribution(
                    metrics["regime_distribution"]
                ),
                "rejection_breakdown_block": self._format_rejection_breakdown(
                    metrics["rejection_breakdown"]
                ),
                # A3: contexto operacional
                "kill_switch_status": "ACTIVO ⚠️" if op_ctx["kill_switch"] else "inactivo",
                "config_changes_block": self._format_config_changes(op_ctx["config_changes"]),
                "roundtrip_fee_pct": op_ctx["roundtrip_fee_pct"],
                # A4: perfil operativo
                "operative_profile": operative_profile,
                "decisor_interval_min_cfg": decisor_interval_min,
                "atr_timeframe_cfg": atr_timeframe_cfg,
                "expected_holding_max_min": expected_holding_max,
                # A1: gate LIVE semanal
                "weekly_gate_block": self._format_weekly_gate(weekly_gate),
            }
            system_prompt = self.prompt_manager.load_system_prompt("supervisor")
            user_prompt = self.prompt_manager.render_user_prompt("supervisor", ctx, strict=False)
            resp = await self.llm.call(provider=self.provider, system_prompt=system_prompt,
                                        user_prompt=user_prompt, fallbacks=self.fallbacks,
                                        json_mode=False)
            output["playbook"] = resp.text
            output["mode"] = mode
            await self.prompt_manager.save_playbook(
                content=resp.text, model=self.provider.value,
                trades_analyzed=metrics["closed_trades"],
                win_rate=metrics["win_rate"],
                pnl_summary={"pnl_usdt": metrics["total_pnl"], "avg_win": metrics["avg_win"]},
            )
            logger.info("supervisor.playbook_saved", version=ctx["new_version"], mode=mode)

            # Segunda llamada LLM: sugerencias de configuración y auto-apply dentro de guardrails
            if current_config:
                try:
                    suggestions = await self._generate_config_suggestions(metrics, current_config)
                    applied, rejected = await self._apply_config_suggestions(suggestions, current_config)
                    output["config_suggestions"] = suggestions
                    output["config_applied"] = applied
                    output["config_rejected"] = rejected
                    logger.info("supervisor.suggestions_processed",
                                total=len(suggestions.get("suggestions", [])),
                                applied=len(applied), rejected=len(rejected))
                except Exception as e:
                    logger.warning("supervisor.suggestions_failed", error=str(e))

        except Exception as e:
            logger.error("supervisor.error", error=str(e))
            rejected_reason = f"llm_error: {type(e).__name__}"

        self.session.add(Decision(
            ts=datetime.now(tz=timezone.utc),
            agent="supervisor", model=self.provider.value,
            tokens_in=resp.tokens_in if resp else 0,
            tokens_out=resp.tokens_out if resp else 0,
            latency_ms=resp.latency_ms if resp else 0,
            input={k: str(v)[:500] for k, v in ctx.items()} if ctx else {},
            output=output,
            executed=rejected_reason is None,
            rejected_reason=rejected_reason,
        ))
        await self.session.commit()

    async def _apply_config_suggestions(
        self, suggestions: dict, current_config: dict
    ) -> tuple[list[dict], list[dict]]:
        """Apply suggestions that are within safe bounds and don't violate invariants.

        Invariants are checked incrementally: each accepted suggestion is tentatively
        applied to a working copy of the config before checking cross-parameter rules.
        This prevents the supervisor from leaving the config in an inconsistent state
        (e.g. sl_atr_multiplier > sl_atr_max_multiplier).
        """
        applied: list[dict] = []
        rejected: list[dict] = []
        store = ConfigStore(self.session)
        working = dict(current_config)

        for s in suggestions.get("suggestions", []):
            key = s.get("key", "")
            suggested = s.get("suggested")
            current = s.get("current")
            reason = s.get("reason", "")

            if suggested is None or suggested == current:
                continue

            if key == "atr_timeframe":
                if str(suggested) not in _VALID_ATR_TIMEFRAMES:
                    rejected.append({**s, "reject_reason": f"valor '{suggested}' no es un timeframe válido"})
                    continue
                working[key] = str(suggested)
                await store.set(ConfigKey(key), str(suggested), changed_by="supervisor")
                applied.append(s)
                logger.info("supervisor.config_applied", key=key, old=current, new=suggested, reason=reason)

            elif key in _SAFE_BOUNDS:
                lo, hi = _SAFE_BOUNDS[key]
                try:
                    val = float(suggested)
                except (TypeError, ValueError):
                    rejected.append({**s, "reject_reason": "valor no numérico"})
                    continue
                if not (lo <= val <= hi):
                    rejected.append({**s, "reject_reason": f"{val} fuera del rango permitido [{lo}, {hi}]"})
                    logger.warning("supervisor.config_rejected_out_of_bounds",
                                   key=key, suggested=val, lo=lo, hi=hi)
                    continue

                # Tentatively apply and validate cross-parameter invariants.
                tentative = {**working, key: val}
                invariant_violation: str | None = None
                for (key_a, key_b, description) in _INVARIANTS:
                    try:
                        va = float(tentative.get(key_a, 0))
                        vb = float(tentative.get(key_b, 999))
                    except (TypeError, ValueError):
                        continue
                    if va > vb:
                        invariant_violation = f"viola invariante: {description} ({va} > {vb})"
                        break
                if invariant_violation:
                    rejected.append({**s, "reject_reason": invariant_violation})
                    logger.warning("supervisor.config_rejected_invariant",
                                   key=key, suggested=val, reason=invariant_violation)
                    continue

                working[key] = val
                await store.set(ConfigKey(key), str(val), changed_by="supervisor")
                applied.append(s)
                logger.info("supervisor.config_applied", key=key, old=current, new=val, reason=reason)

            else:
                rejected.append({**s, "reject_reason": "parámetro no elegible para auto-apply"})

        return applied, rejected

    async def _compute_operational_context(self, since: datetime) -> dict:
        """Fetch kill_switch status, config changes, and latest fee for operational context (A3)."""
        _INTERNAL_KEYS = {"supervisor_run_now"}

        kill_switch_entry = (await self.session.execute(
            select(ConfigEntry).where(ConfigEntry.key == "kill_switch")
        )).scalar_one_or_none()
        kill_switch = (kill_switch_entry.value.lower() == "true") if kill_switch_entry else False

        config_changes = (await self.session.execute(
            select(ConfigHistory).where(ConfigHistory.ts >= since)
            .order_by(ConfigHistory.ts.asc())
        )).scalars().all()
        config_changes = [c for c in config_changes if c.key not in _INTERNAL_KEYS]

        fee_snap = (await self.session.execute(
            select(FeeSnapshot).order_by(FeeSnapshot.ts.desc()).limit(1)
        )).scalar_one_or_none()
        roundtrip_fee_pct = float(fee_snap.taker_fee * 2) * 100 if fee_snap else 0.0

        return {
            "kill_switch": kill_switch,
            "config_changes": config_changes,
            "roundtrip_fee_pct": round(roundtrip_fee_pct, 4),
        }

    @staticmethod
    def _format_regime_distribution(regime_counts: dict) -> str:
        lines = []
        for regime, counts in regime_counts.items():
            total = sum(counts.values())
            if total > 0:
                lines.append(
                    f"  {regime}: {total} decisiones → "
                    f"BUY {counts['BUY']} | SELL {counts['SELL']} | HOLD {counts['HOLD']}"
                )
        return "\n".join(lines) if lines else "  Sin decisiones en el período"

    @staticmethod
    def _format_rejection_breakdown(breakdown: dict) -> str:
        if not breakdown:
            return "  Sin rechazos en el período"
        return "\n".join(f"  {k}: {v}" for k, v in sorted(breakdown.items()))

    @staticmethod
    def _format_config_changes(changes: list) -> str:
        if not changes:
            return "  Sin cambios de configuración"
        return "\n".join(
            f"  [{c.ts.strftime('%H:%M UTC')}] {c.key}: {c.old_value!r} → {c.new_value!r} (por: {c.changed_by})"
            for c in changes
        )

    @staticmethod
    def _derive_operative_profile(decisor_interval_min: int, atr_timeframe: str) -> str:
        if decisor_interval_min <= 10 and atr_timeframe == "5m":
            return "SCALPING"
        if 15 <= decisor_interval_min <= 30 and atr_timeframe in ("15m", "5m"):
            return "HÍBRIDO"
        return "DAY_TRADING"

    @staticmethod
    def _compute_profit_factor(wins: list, losses: list) -> float:
        """Profit factor robusto: 0.0 si no hay wins, 999.0 si hay wins sin losses (racha perfecta)."""
        total_wins = sum(float(t.pnl_usdt) for t in wins) if wins else 0.0
        total_losses = abs(sum(float(t.pnl_usdt) for t in losses)) if losses else 0.0
        if total_losses == 0.0:
            return 999.0 if total_wins > 0 else 0.0
        return round(total_wins / total_losses, 2)

    async def _compute_weekly_gate(self, since_7d: datetime) -> dict:
        """Compute 7-day cumulative metrics and evaluate LIVE gate criteria (A1)."""
        trades_7d = (await self.session.execute(
            select(Trade).where(Trade.ts_open >= since_7d, Trade.status == "closed")
            .order_by(Trade.ts_close.asc().nullsfirst())
        )).scalars().all()

        if not trades_7d:
            return {"has_data": False}

        wins = [t for t in trades_7d if float(t.pnl_usdt or 0) > 0]
        losses = [t for t in trades_7d if float(t.pnl_usdt or 0) < 0]
        wr = len(wins) / len(trades_7d) * 100
        pf = self._compute_profit_factor(wins, losses)

        # Max DD en USDT y % (relativo al capital inicial de la semana)
        start_snap = (await self.session.execute(
            select(BalanceSnapshot).where(BalanceSnapshot.ts >= since_7d)
            .order_by(BalanceSnapshot.ts.asc()).limit(1)
        )).scalar_one_or_none()
        initial_usdt = float(start_snap.usdt) if start_snap else None

        running = 0.0
        equity_peak = 0.0
        max_dd_usdt = 0.0
        for t in trades_7d:
            running += float(t.pnl_usdt or 0)
            if running > equity_peak:
                equity_peak = running
            dd = running - equity_peak
            if dd < max_dd_usdt:
                max_dd_usdt = dd
        max_dd_pct = (abs(max_dd_usdt) / initial_usdt * 100) if initial_usdt and initial_usdt > 0 else 0.0

        # Sharpe anualizado a partir de retornos diarios
        daily_pnl: dict = defaultdict(float)
        for t in trades_7d:
            day = (t.ts_close or t.ts_open).date()
            daily_pnl[day] += float(t.pnl_pct or 0)
        daily_returns = list(daily_pnl.values())
        sharpe_7d = 0.0
        if len(daily_returns) >= 2:
            mean_r = sum(daily_returns) / len(daily_returns)
            var = sum((x - mean_r) ** 2 for x in daily_returns) / (len(daily_returns) - 1)
            std_r = math.sqrt(var) if var > 0 else 0.0
            sharpe_7d = round(mean_r / std_r * math.sqrt(365), 2) if std_r > 0 else 0.0

        # Thresholds del spec §5.4 (paper → LIVE gate)
        gate_wr = wr > 52.0
        gate_pf = pf > 1.5
        gate_dd = max_dd_pct < 5.0
        gate_sharpe = sharpe_7d > 1.0

        return {
            "has_data": True,
            "trades_7d": len(trades_7d),
            "wr_7d": round(wr, 1),
            "pf_7d": round(pf, 2),
            "max_dd_pct_7d": round(max_dd_pct, 2),
            "max_dd_usdt_7d": round(abs(max_dd_usdt), 2),
            "sharpe_7d": sharpe_7d,
            "gate_wr": gate_wr,
            "gate_pf": gate_pf,
            "gate_dd": gate_dd,
            "gate_sharpe": gate_sharpe,
            "gate_overall": gate_wr and gate_pf and gate_dd and gate_sharpe,
        }

    @staticmethod
    def _format_weekly_gate(gate: dict) -> str:
        if not gate.get("has_data"):
            return "  Sin datos suficientes (menos de 1 semana de operación)"

        def chk(ok: bool) -> str:
            return "✓" if ok else "✗"

        overall = (
            "✅ PASA — apto para avanzar a LIVE"
            if gate["gate_overall"]
            else "❌ NO PASA — no cumple todos los gates"
        )
        return (
            f"  Trades: {gate['trades_7d']} | "
            f"WR: {gate['wr_7d']:.1f}% [{chk(gate['gate_wr'])} >52%] | "
            f"PF: {gate['pf_7d']:.2f} [{chk(gate['gate_pf'])} >1.5]\n"
            f"  Max DD: {gate['max_dd_pct_7d']:.2f}% [{chk(gate['gate_dd'])} <5%] | "
            f"Sharpe 7d: {gate['sharpe_7d']:.2f} [{chk(gate['gate_sharpe'])} >1.0]\n"
            f"  → {overall}"
        )

    @staticmethod
    def _trim_previous_playbook(content: str, *, version: int = 0, max_chars: int = 1000) -> str:
        """Keep only key sections to limit token usage for large playbooks (M3)."""
        if len(content) <= max_chars:
            return content
        priority_prefixes = (
            "## Régimen esperado",
            "## Reglas específicas",
            "## Cambios vs",
        )
        lines = content.split("\n")
        kept: list[str] = [lines[0]] if lines else []
        section_buf: list[str] = []
        in_priority = False
        for line in lines[1:]:
            if line.startswith("## "):
                if in_priority and section_buf:
                    kept.extend(section_buf)
                in_priority = any(line.startswith(p) for p in priority_prefixes)
                section_buf = [line]
            else:
                section_buf.append(line)
        if in_priority and section_buf:
            kept.extend(section_buf)
        trimmed = "\n".join(kept)
        note = f"\n[Secciones abreviadas — playbook completo ({len(content)} chars) en BD v{version}]"
        if len(trimmed) > max_chars:
            trimmed = trimmed[:max_chars]
        return trimmed + note

    async def _generate_config_suggestions(self, metrics: dict, current_config: dict) -> dict:
        prompt = _CONFIG_SUGGESTION_PROMPT.format(
            closed_trades=metrics["closed_trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            total_pnl=metrics["total_pnl"],
            avg_win=metrics["avg_win"],
            avg_loss=metrics["avg_loss"],
            avg_holding_min=metrics.get("avg_holding_min", 0),
            sharpe_period=metrics.get("sharpe_period", 0.0),
            buy_count=metrics["buy_count"],
            buy_pct=metrics["buy_pct"],
            hold_count=metrics["hold_count"],
            hold_pct=metrics["hold_pct"],
            rejected_count=metrics["rejected_count"],
            sl_hits=metrics.get("sl_hits", 0),
            tp_hits=metrics.get("tp_hits", 0),
            atr_timeframe=current_config.get("atr_timeframe", "15m"),
            sl_atr_multiplier=current_config.get("sl_atr_multiplier", 0.3),
            sl_atr_max_multiplier=current_config.get("sl_atr_max_multiplier", 1.5),
            min_rr_ratio=current_config.get("min_rr_ratio", 1.3),
            default_rr_ratio=current_config.get("default_rr_ratio", 2.0),
            decisor_interval_min=current_config.get("decisor_interval_min", 10),
            max_position_pct=current_config.get("max_position_pct", 0.05),
            conf_threshold_trending_up=current_config.get("conf_threshold_trending_up", 0.60),
            conf_threshold_range=current_config.get("conf_threshold_range", 0.70),
            conf_threshold_high_vol=current_config.get("conf_threshold_high_vol", 0.80),
            min_confluences_buy=current_config.get("min_confluences_buy", 2),
            min_fees_to_tp_ratio=current_config.get("min_fees_to_tp_ratio", 3.0),
            cooldown_after_sell_min=current_config.get("cooldown_after_sell_min", 15),
            rsi_overbought_1h=current_config.get("rsi_overbought_1h", 70),
            expected_holding_max_min=current_config.get("expected_holding_max_min", 240),
        )
        resp = await self.llm.call(
            provider=self.provider, system_prompt="", user_prompt=prompt,
            fallbacks=self.fallbacks,
        )
        raw = resp.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    async def _compute_metrics(self, since: datetime) -> dict:
        decisions = (await self.session.execute(
            select(Decision).where(Decision.ts >= since, Decision.agent == "decisor")
        )).scalars().all()
        trades = (await self.session.execute(
            select(Trade).where(Trade.ts_open >= since, Trade.status == "closed")
        )).scalars().all()

        wins = [t for t in trades if t.pnl_usdt and float(t.pnl_usdt) > 0]
        losses = [t for t in trades if t.pnl_usdt and float(t.pnl_usdt) < 0]
        total_pnl = sum(float(t.pnl_usdt or 0) for t in trades)
        win_rate = len(wins) / len(trades) * 100 if trades else 0.0
        avg_win = sum(float(t.pnl_usdt) for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(float(t.pnl_usdt) for t in losses) / len(losses) if losses else 0.0
        avg_win_pct = sum(float(t.pnl_pct or 0) for t in wins) / len(wins) if wins else 0.0
        avg_loss_pct = sum(float(t.pnl_pct or 0) for t in losses) / len(losses) if losses else 0.0
        avg_holding = (sum(
            (t.ts_close - t.ts_open).total_seconds() / 60
            for t in trades if t.ts_close and t.ts_open
        ) / len(trades)) if trades else 0

        # Close reason breakdown
        close_reasons = {"sl_triggered": 0, "tp_triggered": 0, "bracket_fill": 0, "manual_close": 0}
        for t in trades:
            key = t.close_reason or "manual_close"
            close_reasons[key] = close_reasons.get(key, 0) + 1

        action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for d in decisions:
            a = d.output.get("action", "HOLD")
            action_counts[a] = action_counts.get(a, 0) + 1
        n = max(len(decisions), 1)

        # Price data from OHLCV 1h
        ohlcv_rows = (await self.session.execute(
            select(Ohlcv).where(Ohlcv.timeframe == "1h", Ohlcv.time >= since)
            .order_by(Ohlcv.time.asc())
        )).scalars().all()
        open_btc = float(ohlcv_rows[0].open) if ohlcv_rows else 0
        close_btc = float(ohlcv_rows[-1].close) if ohlcv_rows else 0
        low_24h = min(float(r.low) for r in ohlcv_rows) if ohlcv_rows else 0
        high_24h = max(float(r.high) for r in ohlcv_rows) if ohlcv_rows else 0
        pct_24h = ((close_btc - open_btc) / open_btc * 100) if open_btc else 0

        # ATR average from indicators
        ind_row = (await self.session.execute(
            select(Indicators).order_by(Indicators.time.desc()).limit(1)
        )).scalar_one_or_none()
        atr_1h = float((ind_row.data.get("1h", {}) or {}).get("atr") or 0) if ind_row else 0
        atr_pct = (atr_1h / close_btc * 100) if close_btc else 0
        vol_label = "alta" if atr_pct > 2.0 else "baja" if atr_pct < 0.5 else "normal"

        # -- A2: Distribución de decisiones por régimen --
        regime_counts: dict[str, dict[str, int]] = {
            r: {"BUY": 0, "HOLD": 0, "SELL": 0}
            for r in ("TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOLATILITY", "UNKNOWN")
        }
        for d in decisions:
            r = d.output.get("regime") or "UNKNOWN"
            if r not in regime_counts:
                r = "UNKNOWN"
            a = d.output.get("action", "HOLD")
            regime_counts[r][a] = regime_counts[r].get(a, 0) + 1

        # -- A2: Histograma de confidence --
        conf_high = sum(1 for d in decisions if float(d.output.get("confidence") or 0) >= 0.70)
        conf_mid  = sum(1 for d in decisions if 0.60 <= float(d.output.get("confidence") or 0) < 0.70)
        conf_low  = sum(1 for d in decisions if float(d.output.get("confidence") or 0) < 0.60)

        # -- A2: Breakdown de rechazos por regla --
        rejection_breakdown: dict[str, int] = {}
        for d in decisions:
            if d.rejected_reason:
                # Extract rule prefix: "R1: ...", "parse_error", "llm_error", etc.
                prefix = d.rejected_reason.split(":")[0].strip().split(" ")[0]
                rejection_breakdown[prefix] = rejection_breakdown.get(prefix, 0) + 1

        # -- A2: Max drawdown real y Sharpe del período --
        sorted_closed = sorted(trades, key=lambda t: t.ts_close or t.ts_open)
        running_pnl = 0.0
        equity_peak = 0.0
        max_dd_usdt = 0.0
        for t in sorted_closed:
            running_pnl += float(t.pnl_usdt or 0)
            if running_pnl > equity_peak:
                equity_peak = running_pnl
            dd = running_pnl - equity_peak
            if dd < max_dd_usdt:
                max_dd_usdt = dd

        pnl_series = [float(t.pnl_pct or 0) for t in sorted_closed if t.pnl_pct is not None]
        sharpe_period = 0.0
        if len(pnl_series) >= 2:
            mean_r = sum(pnl_series) / len(pnl_series)
            variance = sum((x - mean_r) ** 2 for x in pnl_series) / (len(pnl_series) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0.0
            sharpe_period = round(mean_r / std_r, 2) if std_r > 0 else 0.0

        return {
            "total_decisions": len(decisions),
            "buy_count": action_counts["BUY"],
            "sell_count": action_counts["SELL"],
            "hold_count": action_counts["HOLD"],
            "buy_pct": action_counts["BUY"] / n * 100,
            "sell_pct": action_counts["SELL"] / n * 100,
            "hold_pct": action_counts["HOLD"] / n * 100,
            "rejected_count": sum(1 for d in decisions if d.rejected_reason),
            "closed_trades": len(trades),
            "win_rate": round(win_rate, 2),
            "profit_factor": self._compute_profit_factor(wins, losses),
            "avg_win": round(avg_win, 2),
            "avg_win_pct": round(avg_win_pct, 3),
            "avg_loss": round(avg_loss, 2),
            "avg_loss_pct": round(avg_loss_pct, 3),
            "avg_holding_min": round(avg_holding),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": 0.0,
            "sl_hits": close_reasons.get("sl_triggered", 0),
            "tp_hits": close_reasons.get("tp_triggered", 0) + close_reasons.get("bracket_fill", 0),
            "manual_closes": close_reasons.get("manual_close", 0),
            "open_btc": round(open_btc),
            "close_btc": round(close_btc),
            "low_24h": round(low_24h),
            "high_24h": round(high_24h),
            "pct_24h": round(pct_24h, 2),
            "atr_avg": round(atr_1h),
            "atr_pct": round(atr_pct, 2),
            "vol_label": vol_label,
            # A2 additions
            "regime_distribution": regime_counts,
            "conf_high": conf_high,
            "conf_mid": conf_mid,
            "conf_low": conf_low,
            "rejection_breakdown": rejection_breakdown,
            "max_dd_usdt": round(max_dd_usdt, 2),
            "sharpe_period": sharpe_period,
        }
