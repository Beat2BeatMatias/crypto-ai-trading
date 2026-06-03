from __future__ import annotations
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import (
    Decision, Trade, PlaybookVersion, Ohlcv, Indicators,
    ConfigEntry, ConfigHistory, FeeSnapshot, BalanceSnapshot,
    DecisionOutcome, ConfluenceCandidate, ConfluenceRegistry,
)
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager
from shared.config_store import ConfigKey, ConfigStore

logger = structlog.get_logger()

# Safe ranges for auto-applying supervisor suggestions (numeric keys).
# Values outside these bounds are rejected to guard against LLM hallucinations.
# daily_stop_pct and max_drawdown_pct are intentionally excluded — too critical for auto-apply.
#
# v1.3 LLM-Centric review:
#  - `min_confluences_buy` removed: ahora es solo una guía en el prompt del Decisor que el LLM
#    puede ignorar; auto-ajustarlo es ruido.
#  - `rsi_overbought_1h` removed: el LLM ve el RSI completo en contexto y razona sobre él;
#    cambiar el threshold es cosmético, no modifica comportamiento real.
_SAFE_BOUNDS: dict[str, tuple] = {
    # ENFORCEMENT (Risk Gate los aplica)
    "sl_atr_multiplier":           (0.1, 2.0),
    "sl_atr_max_multiplier":       (0.5, 20.0),
    "min_rr_ratio":                (1.0, 3.0),
    "max_position_pct":            (0.01, 0.20),
    "min_fees_to_tp_ratio":        (1.5, 6.0),
    # GUÍAS LLM con impacto medible (CoherenceChecker o anclaje fuerte del prompt)
    "expected_holding_max_min":    (30, 1440),  # auditado por C6
    "cooldown_after_sell_min":     (0, 120),    # norma dura en el system prompt
    "conf_threshold_trending_up":  (0.40, 0.85),
    "conf_threshold_range":        (0.50, 0.90),
    "conf_threshold_high_vol":     (0.60, 0.95),
}

# Boolean toggles que el Supervisor puede activar/desactivar.
# Aplicados sólo cuando la sugerencia es claramente "true" o "false".
# Razón de incorporación (v1.3 LLM-Centric):
#  - coherence_strict_mode: si C1/C2/C3 son persistentes, conviene activar para bloquear hallucinations.
#  - two_pass_enabled: si gatilla mucho sin mejorar outcomes, conviene desactivar para ahorrar tokens.
_SAFE_TOGGLES: set[str] = {
    "coherence_strict_mode",
    "two_pass_enabled",
    "supervisor_config_auto_apply",
}

# Cross-parameter invariants: value_of[a] must be <= value_of[b] after applying suggestions.
# If a suggestion would violate an invariant, it is rejected with an explicit reason.
_INVARIANTS: list[tuple[str, str, str]] = [
    ("sl_atr_multiplier",          "sl_atr_max_multiplier",   "sl_atr_multiplier <= sl_atr_max_multiplier"),
    ("min_rr_ratio",               "default_rr_ratio",        "min_rr_ratio <= default_rr_ratio"),
    ("conf_threshold_trending_up", "conf_threshold_range",    "conf_threshold_trending_up <= conf_threshold_range"),
    ("conf_threshold_range",       "conf_threshold_high_vol", "conf_threshold_range <= conf_threshold_high_vol"),
]

_REVERT_WR_DELTA     = 10.0   # pp: si WR bajó > 10 puntos → revertir
_REVERT_PF_DELTA     = 0.30   # absoluto: si PF bajó > 0.30 → revertir
_REVERT_WINDOW_HOURS = 48     # horas para buscar baseline previo

# Defaults used when current_config does not provide the ratification keys.
# Mirror shared/config_store.py DEFAULTS for MAX_PLAYBOOK_AGE_DAYS / PLAYBOOK_FORCE_REGEN_WR_DELTA_PCT.
_RATIFY_DEFAULTS: dict[str, float] = {
    "max_playbook_age_days": 7,
    "playbook_force_regen_wr_delta_pct": 15.0,
}


def _safe_float(value) -> float | None:
    """Convert a value to float, returning None if conversion fails."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_json_strict(text: str) -> dict:
    """Extrae el primer objeto JSON balanceado tolerando prosa, fences y tags <think>."""
    import re as _re
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    stripped = text.strip()
    if stripped.startswith("```"):
        parts = stripped.split("```")
        if len(parts) >= 3:
            block = parts[1]
            block = _re.sub(r"^[a-zA-Z]+\s*\n?", "", block)
            stripped = block.strip()
    start = stripped.find("{")
    if start == -1:
        return json.loads(stripped)
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(stripped[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start : i + 1])
    return json.loads(stripped)

_CONFIG_SUGGESTION_PROMPT = """Eres un analista cuantitativo de risk management. Basándote en las métricas
de trading del período, sugiere los valores óptimos para los parámetros de configuración del sistema.

CONTEXTO ARQUITECTÓNICO (v1.3 LLM-Centric):
El Decisor es un LLM autónomo. Los parámetros sugeridos abajo tienen DOS roles distintos:
  • ENFORCEMENT (Risk Gate los aplica): sl_atr_multiplier, sl_atr_max_multiplier, min_rr_ratio,
    max_position_pct, min_fees_to_tp_ratio. Cambiarlos restringe o relaja qué trades pueden ejecutarse.
  • GUÍAS AL LLM (referencias inyectadas en el prompt): conf_threshold_*, rsi_overbought_1h,
    min_confluences_buy, cooldown_after_sell_min. Cambiarlos modifica cómo el LLM razona, pero
    el LLM tiene autonomía para desviarse con justificación.
Ajustá los ENFORCEMENT cuando haya pérdidas concretas (SL/TP desbalanceados, trades zombie).
Ajustá las GUÍAS cuando el LLM esté tomando decisiones sistemáticamente sub-óptimas.

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

AUDITORÍA LLM-CENTRIC:
- Sizing promedio BUYs: {avg_position_size_pct:.4f} (vs max_position_pct={max_position_pct})
- Confidence promedio BUYs: {avg_buy_confidence:.3f}
- Coherence warnings totales: {coherence_warnings_total} en {decisions_with_warnings}/{total_decisions} decisiones
- Two-pass auto-correcciones: {two_pass_triggered_count}

ANÁLISIS CONTRAFÁCTICO (últimas 24h):
  Decisiones evaluadas: {evaluated_decisions}
  HOLDs missed: {missed_count} ({missed_rate:.1f}%) | BUYs malos: {bad_buy_count} ({bad_buy_rate:.1f}%)
  SHORTs malos: {bad_short_count} ({bad_short_rate:.1f}%) | Bloqueados buenos: {blocked_good_count}

INTERPRETACIÓN DE LA AUDITORÍA:
- Si avg_buy_confidence está SIEMPRE por encima de conf_threshold_range pero el WR es bajo →
  los thresholds están desalineados con la realidad; subirlos sin riesgo de cortar trades válidos.
- Si avg_position_size_pct es muy bajo respecto a max_position_pct y el WR es alto →
  el LLM es conservador con sizing; revisar conf_base_* para que escale más en alta confianza.
- Si coherence_warnings >15% de las decisiones → revisar conf_threshold_* y min_confluences_buy;
  el LLM está chocando con guías demasiado restrictivas o contradictorias con el playbook.

CONFIGURACIÓN ACTUAL:
- atr_timeframe: {atr_timeframe}
- sl_atr_multiplier: {sl_atr_multiplier}
- sl_atr_max_multiplier: {sl_atr_max_multiplier}
- min_rr_ratio: {min_rr_ratio}
- decisor_interval_min: {decisor_interval_min}
- max_position_pct: {max_position_pct}
- min_fees_to_tp_ratio: {min_fees_to_tp_ratio}
- expected_holding_max_min: {expected_holding_max_min}
- cooldown_after_sell_min: {cooldown_after_sell_min}
- conf_threshold_trending_up: {conf_threshold_trending_up}
- conf_threshold_range: {conf_threshold_range}
- conf_threshold_high_vol: {conf_threshold_high_vol}
- coherence_strict_mode: {coherence_strict_mode}
- two_pass_enabled: {two_pass_enabled}

OPCIONES VÁLIDAS (con criterio LLM-Centric):

ENFORCEMENT — Risk Gate los aplica con dureza:
- sl_atr_multiplier: 0.1 a 2.0 (cuanto menor, SL más cerca; debe ser < sl_atr_max_multiplier).
  Criterio: bajar si los SL llegan tarde y dejan grandes pérdidas; subir si te sacan con ruido.
- sl_atr_max_multiplier: 0.5 a 20.0 — techo del SL. Si rechaza muchos BUYs por R4, subir levemente.
- min_rr_ratio: 1.0 a 3.0 — subir si avg_loss > avg_win con persistencia.
- max_position_pct: 0.01 a 0.20 — subir SOLO si WR>60% y PF>1.5 sostenidos. Bajar ante drawdown.
- min_fees_to_tp_ratio: 1.5 a 6.0 — subir si los TPs apenas pasan fees (rentabilidad marginal).

PARÁMETROS DE SOLO LECTURA (informativo, NO sugerir cambios):
- decisor_interval_min: intervalo entre ciclos del Decisor. Solo el operador puede modificarlo.
  NO incluir este parámetro en el array `suggestions`.
- atr_timeframe: timeframe del ATR de referencia (5m / 15m / 1h). Solo el operador puede modificarlo.
  NO incluir este parámetro en el array `suggestions`.

GUÍAS LLM (sólo recalibrar si hay desalineación medible):
- expected_holding_max_min: 30 a 1440 — auditado por CoherenceChecker C6.
  Criterio: ajustar al avg_holding_min observado +50% si el LLM está siendo coherente.
- cooldown_after_sell_min: 0 a 120 — norma dura del system prompt.
  Criterio: subir si hay overtrading evidente (BUYs inmediatos post-SELL con pérdidas).
- conf_threshold_trending_up: 0.40 a 0.85 — debe ser <= conf_threshold_range.
- conf_threshold_range: 0.50 a 0.90 — debe ser <= conf_threshold_high_vol.
- conf_threshold_high_vol: 0.60 a 0.95.
  Criterio para los conf_threshold_*: ajustar SÓLO si avg_buy_confidence está sistemáticamente
  desalineado con el outcome (ej: avg_buy_confidence=0.75 pero WR=35% → subir thresholds).
  Si avg_buy_confidence es razonablemente predictivo de WR, no tocar.

TOGGLES BOOLEANOS (decisión binaria con criterio claro):
- coherence_strict_mode: true | false.
  Activar (true) si coherence_warnings_total / total_decisions > 0.25 sostenido
    Y al menos la mitad de warnings son C1/C2/C3 (inconsistencias factuales).
  Desactivar (false) si la tasa es < 0.02 por dos ciclos consecutivos (overhead innecesario).
  No cambiar si no hay señal clara en ninguna dirección.
- two_pass_enabled: true | false.
  Desactivar (false) si two_pass_triggered_count es alto (>30% de decisiones) pero el outcome
    promedio post-correction no mejora (waste de tokens).
  Activar (true) si está apagado y hay warnings C1/C2/C3 frecuentes (recuperás auto-corrección).
  En la duda: dejar activado (default true) — es barato comparado con un trade malo.

INVARIANTES OBLIGATORIAS (el sistema rechazará sugerencias que las violen):
- sl_atr_multiplier DEBE ser <= sl_atr_max_multiplier
- min_rr_ratio DEBE ser <= default_rr_ratio (actualmente {default_rr_ratio})
- conf_threshold_trending_up <= conf_threshold_range <= conf_threshold_high_vol

REGLA GENERAL: si una key no requiere ajuste, omitila del array `suggestions`. NO incluyas
sugerencias que repiten el valor actual ni "ajustes" sin razón concreta basada en métricas.

Responde ÚNICAMENTE con JSON válido, sin texto extra:
{{
  "suggestions": [
    {{"key": "nombre_key", "current": "valor_actual", "suggested": "valor_propuesto", "reason": "explicación breve en español basada en métricas concretas"}}
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
        """Run the daily supervisor cycle.

        Two-phase flow (see 01-functional-spec.md §F5.bis.5):
          1. Ratification — deterministic guardrails + optional LLM eval call.
          2. Regeneration — full LLM playbook call ONLY when phase 1 says regenerate.
        Plus a third LLM call for config suggestions (independent of phase 1/2).

        Persistence guarantees (AC-14):
          - Exactly one Decision row per run (`agent="supervisor"`), regardless of veredict.
          - A new PlaybookVersion is inserted ONLY when phase 1 resolves "regenerate".
        """
        since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        metrics = await self._compute_metrics(since)
        if current_config:
            metrics["trading_product"] = str(
                current_config.get("trading_product", "spot"),
            ).lower()
        rejected_reason = None
        gen_resp = None
        eval_tokens_in = 0
        eval_tokens_out = 0
        eval_latency_ms = 0
        ctx: dict | None = None
        output: dict = {}

        mode = "diagnostic" if metrics["closed_trades"] < self.min_trades else "normal"
        if mode == "diagnostic":
            logger.info("supervisor.diagnostic_mode", closed=metrics["closed_trades"],
                        min_trades=self.min_trades)

        try:
            op_ctx = await self._compute_operational_context(since)
            metrics["kill_switch_in_period"] = op_ctx.get("kill_switch_in_period", False)

            # ----- Closed-loop revert: check if prior config changes degraded metrics -----
            reverted = await self._maybe_revert_degraded_config(metrics)
            if reverted:
                logger.info("supervisor.reverted_degraded_config_keys", keys=reverted)

            active_playbook = await self.prompt_manager.get_active_playbook()

            # ----- Phase 1: ratification verdict -----
            verdict = await self._evaluate_ratification(
                metrics=metrics,
                active_playbook=active_playbook,
                current_config=current_config,
                mode=mode,
            )
            eval_tokens_in = int(verdict.get("eval_tokens_in", 0) or 0)
            eval_tokens_out = int(verdict.get("eval_tokens_out", 0) or 0)
            eval_latency_ms = int(verdict.get("eval_latency_ms", 0) or 0)

            output["mode"] = mode
            output["ratified"] = bool(verdict["ratify"])
            output["ratify_reason"] = verdict.get("ratify_reason")
            output["force_regen_reason"] = verdict.get("force_regen_reason")
            output["playbook_age_days"] = verdict.get("playbook_age_days")
            output["playbook_win_rate_baseline"] = verdict.get("playbook_win_rate_baseline")

            if verdict["ratify"]:
                logger.info(
                    "supervisor.playbook_ratified",
                    version=active_playbook.version if active_playbook else None,
                    reason=verdict.get("ratify_reason"),
                    mode=mode,
                )
                output["active_version"] = active_playbook.version if active_playbook else None
            else:
                # ----- Phase 2: regeneration -----
                ctx, gen_resp = await self._regenerate_playbook(
                    since=since,
                    metrics=metrics,
                    mode=mode,
                    op_ctx=op_ctx,
                    active_playbook=active_playbook,
                    current_config=current_config,
                )
                output["playbook"] = gen_resp.text
                new_version = (active_playbook.version + 1) if active_playbook else 0
                output["new_version"] = new_version
                logger.info(
                    "supervisor.playbook_saved",
                    version=new_version,
                    mode=mode,
                    regen_reason=verdict.get("force_regen_reason") or "llm_decision",
                )

            # ----- Phase 3: config suggestions (ventana larga; auto-apply opcional) -----
            if current_config:
                try:
                    store = ConfigStore(self.session)
                    auto_apply = await store.get_typed(ConfigKey.SUPERVISOR_CONFIG_AUTO_APPLY)
                    config_window_h = int(
                        await store.get_typed(ConfigKey.SUPERVISOR_CONFIG_WINDOW_HOURS)
                    )
                    min_evaluated = int(
                        await store.get_typed(ConfigKey.SUPERVISOR_CONFIG_MIN_EVALUATED)
                    )
                    config_since = datetime.now(tz=timezone.utc) - timedelta(hours=config_window_h)
                    config_metrics = await self._compute_metrics(config_since)
                    suggestions = await self._generate_config_suggestions(
                        config_metrics, current_config,
                    )
                    applied, rejected = await self._apply_config_suggestions(
                        suggestions,
                        current_config,
                        auto_apply_enabled=auto_apply,
                        config_metrics=config_metrics,
                        min_evaluated_decisions=min_evaluated,
                    )
                    output["config_suggestions"] = suggestions
                    output["config_applied"] = applied
                    output["config_rejected"] = rejected
                    output["config_metrics_window_hours"] = config_window_h
                    output["config_auto_apply_enabled"] = auto_apply
                    if applied:
                        output["config_applied_baseline"] = {
                            "win_rate": float(metrics.get("win_rate", 0.0)),
                            "profit_factor": float(metrics.get("profit_factor", 0.0)),
                            "applied_keys": [s["key"] for s in applied],
                            "ts": datetime.now(tz=timezone.utc).isoformat(),
                        }
                    logger.info("supervisor.suggestions_processed",
                                total=len(suggestions.get("suggestions", [])),
                                applied=len(applied), rejected=len(rejected))
                except Exception as e:
                    logger.warning("supervisor.suggestions_failed", error=str(e))

            try:
                promotions = await self._promote_confluence_candidates(
                    active_playbook=active_playbook,
                    current_config=current_config or {},
                )
                output["confluence_promotions"] = promotions
            except Exception as e:
                logger.warning("supervisor.confluence_promotion_failed", error=str(e))

        except Exception as e:
            logger.error("supervisor.error", error=str(e))
            rejected_reason = f"llm_error: {type(e).__name__}"

        self.session.add(Decision(
            ts=datetime.now(tz=timezone.utc),
            agent="supervisor", model=self.provider.value,
            tokens_in=(gen_resp.tokens_in if gen_resp else 0) + eval_tokens_in,
            tokens_out=(gen_resp.tokens_out if gen_resp else 0) + eval_tokens_out,
            latency_ms=(gen_resp.latency_ms if gen_resp else 0) + eval_latency_ms,
            input={k: str(v)[:500] for k, v in ctx.items()} if ctx else {},
            output=output,
            executed=rejected_reason is None,
            rejected_reason=rejected_reason,
        ))
        await self.session.commit()

    async def _evaluate_ratification(
        self,
        *,
        metrics: dict,
        active_playbook,
        current_config: dict | None,
        mode: str,
    ) -> dict:
        """Phase 1: decide `ratify | regenerate`.

        Returns a dict with keys:
          - ratify (bool)
          - ratify_reason (str | None) — LLM reason when ratify=True
          - force_regen_reason (str | None) — deterministic guardrail reason when ratify=False
          - playbook_age_days (int | None)
          - playbook_win_rate_baseline (float | None)
          - eval_tokens_in / eval_tokens_out / eval_latency_ms (int) — only if LLM was called
        """
        if active_playbook is None:
            return self._regen_verdict("no_active_playbook", age_days=None, baseline_wr=None)

        cfg = current_config or {}
        try:
            max_age = int(cfg.get("max_playbook_age_days", _RATIFY_DEFAULTS["max_playbook_age_days"]))
        except (TypeError, ValueError):
            max_age = int(_RATIFY_DEFAULTS["max_playbook_age_days"])
        try:
            wr_delta_pct = float(cfg.get(
                "playbook_force_regen_wr_delta_pct",
                _RATIFY_DEFAULTS["playbook_force_regen_wr_delta_pct"],
            ))
        except (TypeError, ValueError):
            wr_delta_pct = float(_RATIFY_DEFAULTS["playbook_force_regen_wr_delta_pct"])

        now = datetime.now(tz=timezone.utc)
        ts_gen = active_playbook.ts_generated
        if ts_gen is not None and ts_gen.tzinfo is None:
            ts_gen = ts_gen.replace(tzinfo=timezone.utc)
        age_days = (now - ts_gen).days if ts_gen else 0

        baseline_wr = float(active_playbook.win_rate or 0.0)
        wr_24h = float(metrics.get("win_rate", 0.0))

        if age_days >= max_age:
            return self._regen_verdict(
                f"playbook_age_days={age_days} >= max_playbook_age_days={max_age}",
                age_days=age_days, baseline_wr=baseline_wr,
            )

        if baseline_wr > 0 and abs(wr_24h - baseline_wr) > wr_delta_pct:
            return self._regen_verdict(
                f"abs(wr_24h={wr_24h:.1f} - baseline_wr={baseline_wr:.1f}) > "
                f"playbook_force_regen_wr_delta_pct={wr_delta_pct}",
                age_days=age_days, baseline_wr=baseline_wr,
            )

        active_regime = PromptManager.parse_regime_from_playbook(active_playbook.content)
        dominant_regime = self._dominant_regime(metrics.get("regime_distribution") or {})
        if (
            active_regime not in (None, "NEUTRAL", "UNKNOWN")
            and dominant_regime not in (None, "NEUTRAL", "UNKNOWN")
            and active_regime != dominant_regime
        ):
            return self._regen_verdict(
                f"regime_changed: playbook_regime={active_regime} vs market_dominant={dominant_regime}",
                age_days=age_days, baseline_wr=baseline_wr,
            )

        if metrics.get("kill_switch_in_period"):
            return self._regen_verdict(
                "kill_switch_was_triggered_in_period",
                age_days=age_days, baseline_wr=baseline_wr,
            )

        # No deterministic guardrail fired — consult the LLM.
        try:
            trimmed_pb = self._trim_previous_playbook(
                active_playbook.content,
                version=active_playbook.version,
                max_chars=600,
            )
            eval_ctx = {
                **metrics,
                "date": now.date().isoformat(),
                "previous_version": active_playbook.version,
                "previous_playbook": trimmed_pb,
                "playbook_age_days": age_days,
                "playbook_win_rate_baseline": round(baseline_wr, 1),
                "dominant_regime": dominant_regime or "UNKNOWN",
                "regime_distribution_block": self._format_regime_distribution(
                    metrics.get("regime_distribution") or {}
                ),
                "rejection_breakdown_block": self._format_rejection_breakdown(
                    metrics.get("rejection_breakdown") or {}
                ),
                "coherence_breakdown_block": self._format_coherence_breakdown(
                    metrics.get("coherence_by_rule") or {},
                    int(metrics.get("total_decisions") or 0),
                ),
                "expected_holding_max_min": (current_config or {}).get("expected_holding_max_min", 240),
                "winners_confluences": self._format_confluence_histogram(
                    metrics.get("winners_confluences") or {}
                ),
                "losers_confluences": self._format_confluence_histogram(
                    metrics.get("losers_confluences") or {}
                ),
            }
            system_prompt = self.prompt_manager.load_system_prompt("supervisor_eval")
            user_prompt = self.prompt_manager.render_user_prompt(
                "supervisor_eval", eval_ctx, strict=False,
            )
            resp = await self.llm.call(
                provider=self.provider, system_prompt=system_prompt,
                user_prompt=user_prompt, fallbacks=self.fallbacks, json_mode=True,
            )
            parsed = _parse_json_strict(resp.text)
            ratify = bool(parsed.get("ratify", False))
            if ratify:
                return {
                    "ratify": True,
                    "ratify_reason": str(parsed.get("reason") or "").strip()[:240],
                    "force_regen_reason": None,
                    "playbook_age_days": age_days,
                    "playbook_win_rate_baseline": round(baseline_wr, 1),
                    "eval_tokens_in": resp.tokens_in,
                    "eval_tokens_out": resp.tokens_out,
                    "eval_latency_ms": resp.latency_ms,
                }
            verdict = self._regen_verdict(
                None, age_days=age_days, baseline_wr=baseline_wr,
            )
            verdict["eval_tokens_in"] = resp.tokens_in
            verdict["eval_tokens_out"] = resp.tokens_out
            verdict["eval_latency_ms"] = resp.latency_ms
            return verdict
        except Exception as e:
            logger.warning(
                "supervisor.eval_failed_defaulting_to_regenerate",
                error=str(e), error_type=type(e).__name__,
            )
            return self._regen_verdict(
                f"eval_llm_error: {type(e).__name__}",
                age_days=age_days, baseline_wr=baseline_wr,
            )

    @staticmethod
    def _regen_verdict(reason: str | None, *, age_days: int | None, baseline_wr: float | None) -> dict:
        return {
            "ratify": False,
            "ratify_reason": None,
            "force_regen_reason": reason,
            "playbook_age_days": age_days,
            "playbook_win_rate_baseline": round(baseline_wr, 1) if baseline_wr is not None else None,
        }

    @staticmethod
    def _normalize_bool(value) -> bool | None:
        """Convert LLM-suggested boolean to canonical bool, or None if not recognizable.

        Accepts: True/False, "true"/"false" (case insensitive), 1/0, "1"/"0".
        Rejects everything else to guard against ambiguous LLM outputs.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1"):
                return True
            if v in ("false", "0"):
                return False
        return None

    @staticmethod
    def _dominant_regime(regime_distribution: dict) -> str | None:
        """Return the regime with the most decisions; None if no signal."""
        totals = {
            r: sum(counts.values())
            for r, counts in regime_distribution.items()
            if r != "UNKNOWN" and sum(counts.values()) > 0
        }
        if not totals:
            return None
        return max(totals, key=totals.get)

    async def _regenerate_playbook(
        self,
        *,
        since: datetime,
        metrics: dict,
        mode: str,
        op_ctx: dict,
        active_playbook,
        current_config: dict | None,
    ):
        """Phase 2: full LLM call to produce a new playbook + persist a new PlaybookVersion.

        Returns (ctx_for_audit, llm_response).
        """
        cfg = current_config or {}
        decisor_interval_min = int(cfg.get("decisor_interval_min", 10))
        atr_timeframe_cfg = str(cfg.get("atr_timeframe", "15m"))
        expected_holding_max = int(cfg.get("expected_holding_max_min", 240))
        operative_profile = self._derive_operative_profile(decisor_interval_min, atr_timeframe_cfg)

        since_7d = datetime.now(tz=timezone.utc) - timedelta(days=7)
        weekly_gate = await self._compute_weekly_gate(since_7d)

        raw_previous = active_playbook.content if active_playbook else "# (vacío)"
        previous_version_num = active_playbook.version if active_playbook else 0
        previous_content = self._trim_previous_playbook(raw_previous, version=previous_version_num)

        decisions_since = (await self.session.execute(
            select(Decision).where(Decision.ts >= since, Decision.agent == "decisor")
            .order_by(Decision.ts.asc())
        )).scalars().all()
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

        postmortem_lessons_block = await self._fetch_postmortem_lessons(window_days=7)

        ctx = {
            **metrics,
            "mode": mode,
            "mode_header": mode_header,
            "postmortem_lessons_block": postmortem_lessons_block,
            "previous_version": previous_version_num,
            "new_version": (previous_version_num + 1) if active_playbook else 0,
            "previous_playbook": previous_content,
            "decisions_dump": decisions_dump,
            "decisions_sample_count": len(decisions_sample),
            "date": datetime.now(tz=timezone.utc).date().isoformat(),
            "min_trades": self.min_trades,
            "regime_distribution_block": self._format_regime_distribution(
                metrics.get("regime_distribution") or {}
            ),
            "rejection_breakdown_block": self._format_rejection_breakdown(
                metrics.get("rejection_breakdown") or {}
            ),
            "coherence_breakdown_block": self._format_coherence_breakdown(
                metrics.get("coherence_by_rule") or {},
                int(metrics.get("total_decisions") or 0),
            ),
            "kill_switch_status": "ACTIVO ⚠️" if op_ctx["kill_switch"] else "inactivo",
            "config_changes_block": self._format_config_changes(op_ctx["config_changes"]),
            "roundtrip_fee_pct": op_ctx["roundtrip_fee_pct"],
            "operative_profile": operative_profile,
            "decisor_interval_min_cfg": decisor_interval_min,
            "atr_timeframe_cfg": atr_timeframe_cfg,
            "expected_holding_max_min": expected_holding_max,
            "weekly_gate_block": self._format_weekly_gate(weekly_gate),
            "winners_confluences": self._format_confluence_histogram(
                metrics.get("winners_confluences") or {}
            ),
            "losers_confluences": self._format_confluence_histogram(
                metrics.get("losers_confluences") or {}
            ),
        }
        system_prompt = self.prompt_manager.load_system_prompt("supervisor")
        user_prompt = self.prompt_manager.render_user_prompt("supervisor", ctx, strict=False)
        resp = await self.llm.call(
            provider=self.provider, system_prompt=system_prompt,
            user_prompt=user_prompt, fallbacks=self.fallbacks, json_mode=False,
        )
        await self.prompt_manager.save_playbook(
            content=resp.text, model=self.provider.value,
            trades_analyzed=metrics["closed_trades"],
            win_rate=metrics["win_rate"],
            pnl_summary={"pnl_usdt": metrics["total_pnl"], "avg_win": metrics["avg_win"]},
        )
        return ctx, resp

    async def _maybe_revert_degraded_config(self, current_metrics: dict) -> list[str]:
        """Busca el último baseline del Supervisor y revierte las claves si las
        métricas actuales degradaron significativamente.
        Retorna lista de claves revertidas (vacía si no hay degradación).
        """
        from sqlalchemy import desc
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=_REVERT_WINDOW_HOURS)

        rows = (await self.session.execute(
            select(Decision)
            .where(Decision.agent == "supervisor", Decision.ts >= cutoff)
            .order_by(desc(Decision.ts))
            .limit(10)
        )).scalars().all()

        prev = next(
            (r for r in rows if r.output and "config_applied_baseline" in r.output),
            None,
        )
        if prev is None:
            return []

        baseline     = prev.output["config_applied_baseline"]
        baseline_wr  = baseline.get("win_rate")
        baseline_pf  = baseline.get("profit_factor")
        applied_keys = baseline.get("applied_keys", [])

        if baseline_wr is None or baseline_pf is None or not applied_keys:
            return []

        current_wr = float(current_metrics.get("win_rate", 0.0))
        current_pf = float(current_metrics.get("profit_factor", 0.0))
        wr_degraded = current_wr < float(baseline_wr) - _REVERT_WR_DELTA
        pf_degraded = current_pf < float(baseline_pf) - _REVERT_PF_DELTA

        if not (wr_degraded or pf_degraded):
            return []

        logger.warning(
            "supervisor.config_degradation_detected",
            baseline_wr=baseline_wr, current_wr=current_wr,
            baseline_pf=baseline_pf, current_pf=current_pf,
            applied_keys=applied_keys,
        )

        store = ConfigStore(self.session)
        reverted: list[str] = []
        for key in applied_keys:
            try:
                history_entry = (await self.session.execute(
                    select(ConfigHistory)
                    .where(
                        ConfigHistory.key == key,
                        ConfigHistory.changed_by == "supervisor",
                        ConfigHistory.ts >= cutoff,
                    )
                    .order_by(ConfigHistory.ts)
                    .limit(1)
                )).scalar_one_or_none()
                if history_entry and history_entry.old_value:
                    await store.set(ConfigKey(key), history_entry.old_value,
                                    changed_by="supervisor:revert")
                    reverted.append(key)
                    logger.warning("supervisor.config_reverted", key=key,
                                   reverted_to=history_entry.old_value)
            except Exception as e:
                logger.error("supervisor.config_revert_failed", key=key, error=str(e))

        return reverted

    async def _apply_config_suggestions(
        self,
        suggestions: dict,
        current_config: dict,
        *,
        auto_apply_enabled: bool = True,
        config_metrics: dict | None = None,
        min_evaluated_decisions: int = 30,
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

        if not auto_apply_enabled:
            for s in suggestions.get("suggestions", []):
                if s.get("suggested") is None or s.get("suggested") == s.get("current"):
                    continue
                rejected.append({
                    **s,
                    "reject_reason": "auto-apply desactivado (supervisor_config_auto_apply=false)",
                })
            return applied, rejected

        if config_metrics is not None:
            evaluated = int(config_metrics.get("evaluated_decisions", 0))
        else:
            evaluated = min_evaluated_decisions
        if evaluated < min_evaluated_decisions:
            for s in suggestions.get("suggestions", []):
                if s.get("suggested") is None or s.get("suggested") == s.get("current"):
                    continue
                rejected.append({
                    **s,
                    "reject_reason": (
                        f"muestra insuficiente: {evaluated} decisiones evaluadas "
                        f"< {min_evaluated_decisions} requeridas"
                    ),
                })
            return applied, rejected

        for s in suggestions.get("suggestions", []):
            key = s.get("key", "")
            suggested = s.get("suggested")
            current = s.get("current")
            reason = s.get("reason", "")

            if suggested is None or suggested == current:
                continue

            if key in _SAFE_TOGGLES:
                normalized = self._normalize_bool(suggested)
                if normalized is None:
                    rejected.append({**s, "reject_reason": f"valor '{suggested}' no es booleano válido (true/false)"})
                    continue
                working[key] = normalized
                await store.set(ConfigKey(key), "true" if normalized else "false", changed_by="supervisor")
                applied.append(s)
                logger.info("supervisor.config_toggle_applied", key=key, old=current, new=normalized, reason=reason)

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
        """Fetch kill_switch status, config changes, and latest fee for operational context (A3).

        `kill_switch_in_period` is True when the kill switch is currently active OR
        when it was activated at any point inside the period. Used by the ratification
        guardrails (§F5.bis.5) to force regeneration when the operator intervened.
        """
        _INTERNAL_KEYS = {"supervisor_run_now"}

        kill_switch_entry = (await self.session.execute(
            select(ConfigEntry).where(ConfigEntry.key == "kill_switch")
        )).scalar_one_or_none()
        kill_switch = (kill_switch_entry.value.lower() == "true") if kill_switch_entry else False

        config_changes_all = (await self.session.execute(
            select(ConfigHistory).where(ConfigHistory.ts >= since)
            .order_by(ConfigHistory.ts.asc())
        )).scalars().all()
        config_changes = [c for c in config_changes_all if c.key not in _INTERNAL_KEYS]

        kill_switch_in_period = kill_switch or any(
            c.key == "kill_switch" and (c.new_value or "").lower() == "true"
            for c in config_changes_all
        )

        fee_snap = (await self.session.execute(
            select(FeeSnapshot).order_by(FeeSnapshot.ts.desc()).limit(1)
        )).scalar_one_or_none()
        roundtrip_fee_pct = float(fee_snap.taker_fee * 2) * 100 if fee_snap else 0.0

        return {
            "kill_switch": kill_switch,
            "kill_switch_in_period": kill_switch_in_period,
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
                    f"BUY {counts['BUY']} | SHORT {counts.get('SHORT', 0)} | "
                    f"SELL {counts['SELL']} | HOLD {counts['HOLD']}"
                )
        return "\n".join(lines) if lines else "  Sin decisiones en el período"

    @staticmethod
    def _format_rejection_breakdown(breakdown: dict) -> str:
        if not breakdown:
            return "  Sin rechazos en el período"
        return "\n".join(f"  {k}: {v}" for k, v in sorted(breakdown.items()))

    @staticmethod
    def _format_coherence_breakdown(breakdown: dict, total_decisions: int) -> str:
        """Render coherence warnings (C1-C8) for the audit block in the supervisor prompt.

        C1-C3: factual inconsistencies — high counts signal the playbook is leading the LLM
               into hallucinations. In strict_mode these become rejections.
        C4-C6: operational drifts (low-confluence confidence, low-confidence BUY, holding range).
        C7: R:R real below min_rr_ratio — always critical, LLM cannot self-correct in two-pass.
        C8: extended confluence from registry declared without verify_spec evidence.
        """
        if not breakdown:
            return "  Sin warnings en el período (LLM coherente)"
        lines = []
        rate_total = (sum(breakdown.values()) / total_decisions * 100) if total_decisions else 0.0
        for rid, cnt in sorted(breakdown.items()):
            lines.append(f"  {rid}: {cnt} ({cnt / total_decisions * 100:.1f}% de decisiones)" if total_decisions else f"  {rid}: {cnt}")
        lines.append(f"  TOTAL: {sum(breakdown.values())} warnings ({rate_total:.1f}% rate)")
        return "\n".join(lines)

    @staticmethod
    def _format_confluence_histogram(histogram: dict) -> str:
        if not histogram:
            return "(sin datos)"
        sorted_items = sorted(histogram.items(), key=lambda x: x[1], reverse=True)
        return " | ".join(f"{code}:{count}" for code, count in sorted_items)

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
            cut = trimmed.rfind('\n', 0, max_chars)
            trimmed = trimmed[:cut if cut > 0 else max_chars]
            trimmed += "\n[... truncado ...]"
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
            avg_position_size_pct=metrics.get("avg_position_size_pct", 0.0),
            avg_buy_confidence=metrics.get("avg_buy_confidence", 0.0),
            coherence_warnings_total=metrics.get("coherence_warnings_total", 0),
            decisions_with_warnings=metrics.get("decisions_with_warnings", 0),
            total_decisions=metrics.get("total_decisions", 0),
            two_pass_triggered_count=metrics.get("two_pass_triggered_count", 0),
            atr_timeframe=current_config.get("atr_timeframe", "15m"),
            sl_atr_multiplier=current_config.get("sl_atr_multiplier", 0.3),
            sl_atr_max_multiplier=current_config.get("sl_atr_max_multiplier", 1.5),
            min_rr_ratio=current_config.get("min_rr_ratio", 1.3),
            default_rr_ratio=current_config.get("default_rr_ratio", 2.0),
            decisor_interval_min=current_config.get("decisor_interval_min", 10),
            max_position_pct=current_config.get("max_position_pct", 0.05),
            min_fees_to_tp_ratio=current_config.get("min_fees_to_tp_ratio", 3.0),
            expected_holding_max_min=current_config.get("expected_holding_max_min", 240),
            cooldown_after_sell_min=current_config.get("cooldown_after_sell_min", 15),
            conf_threshold_trending_up=current_config.get("conf_threshold_trending_up", 0.60),
            conf_threshold_range=current_config.get("conf_threshold_range", 0.70),
            conf_threshold_high_vol=current_config.get("conf_threshold_high_vol", 0.80),
            coherence_strict_mode=current_config.get("coherence_strict_mode", False),
            two_pass_enabled=current_config.get("two_pass_enabled", True),
            missed_rate=metrics.get("missed_rate", 0.0),
            bad_buy_rate=metrics.get("bad_buy_rate", 0.0),
            missed_count=metrics.get("missed_count", 0),
            bad_buy_count=metrics.get("bad_buy_count", 0),
            bad_short_count=metrics.get("bad_short_count", 0),
            bad_short_rate=metrics.get("bad_short_rate", 0.0),
            blocked_good_count=metrics.get("blocked_good_count", 0),
            evaluated_decisions=metrics.get("evaluated_decisions", 0),
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

        action_counts = {"BUY": 0, "SHORT": 0, "SELL": 0, "HOLD": 0}
        for d in decisions:
            a = d.output.get("action", "HOLD")
            action_counts[a] = action_counts.get(a, 0) + 1
        n = max(len(decisions), 1)
        short_count = action_counts.get("SHORT", 0)
        short_pct = short_count / n * 100

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
            r: {"BUY": 0, "SHORT": 0, "HOLD": 0, "SELL": 0}
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

        # -- LLM-centric (v1.3): coherence warnings, two-pass, sizing --
        coherence_by_rule: dict[str, int] = {}
        decisions_with_warnings = 0
        two_pass_count = 0
        position_sizes: list[float] = []
        short_position_sizes: list[float] = []
        buy_confidences: list[float] = []
        short_confidences: list[float] = []
        for d in decisions:
            out = d.output or {}
            warnings = out.get("coherence_warnings") or []
            if warnings:
                decisions_with_warnings += 1
                for w in warnings:
                    rid = (w.get("rule_id") if isinstance(w, dict) else None) or "?"
                    coherence_by_rule[rid] = coherence_by_rule.get(rid, 0) + 1
            if out.get("two_pass_triggered"):
                two_pass_count += 1
            action = out.get("action")
            if action in ("BUY", "SHORT"):
                try:
                    pz = float(out.get("position_size_pct") or 0.0)
                    if pz > 0:
                        if action == "SHORT":
                            short_position_sizes.append(pz)
                        else:
                            position_sizes.append(pz)
                except (TypeError, ValueError):
                    pass
                try:
                    cf = float(out.get("confidence") or 0.0)
                    if cf > 0:
                        if action == "SHORT":
                            short_confidences.append(cf)
                        else:
                            buy_confidences.append(cf)
                except (TypeError, ValueError):
                    pass
        coherence_warnings_total = sum(coherence_by_rule.values())
        avg_position_size_pct = (
            round(sum(position_sizes) / len(position_sizes), 4)
            if position_sizes else 0.0
        )
        avg_buy_confidence = (
            round(sum(buy_confidences) / len(buy_confidences), 3)
            if buy_confidences else 0.0
        )
        avg_short_position_size_pct = (
            round(sum(short_position_sizes) / len(short_position_sizes), 4)
            if short_position_sizes else 0.0
        )
        avg_short_confidence = (
            round(sum(short_confidences) / len(short_confidences), 3)
            if short_confidences else 0.0
        )

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

        # -- Contrafactual outcomes (PR2) --
        outcome_rows = (await self.session.execute(
            select(DecisionOutcome)
            .join(Decision, Decision.id == DecisionOutcome.decision_id)
            .where(Decision.ts >= since, Decision.agent == "decisor")
        )).scalars().all()

        evaluated_decisions = len([o for o in outcome_rows if o.classification not in ("PENDING", "UNKNOWN")])
        missed_count = len([o for o in outcome_rows if o.classification == "MISSED_OPPORTUNITY"])
        bad_buy_count = len([o for o in outcome_rows if o.classification == "BAD_BUY"])
        good_short_count = len([o for o in outcome_rows if o.classification == "GOOD_SHORT"])
        bad_short_count = len([o for o in outcome_rows if o.classification == "BAD_SHORT"])
        blocked_good_count = len([o for o in outcome_rows if o.classification == "BLOCKED_GOOD_TRADE"])
        blocked_good_short_count = 0
        correctly_blocked_count = len([o for o in outcome_rows if o.classification == "CORRECTLY_BLOCKED"])
        good_hold_count = len([o for o in outcome_rows if o.classification == "GOOD_HOLD"])
        good_buy_count = len([o for o in outcome_rows if o.classification == "GOOD_BUY"])

        missed_rate = (missed_count / evaluated_decisions * 100) if evaluated_decisions > 0 else 0.0
        bad_buy_rate = (bad_buy_count / max(good_buy_count + bad_buy_count, 1) * 100)
        bad_short_rate = (bad_short_count / max(good_short_count + bad_short_count, 1) * 100)

        decisions_by_id = {d.id: d for d in decisions}

        winners_confluences: dict[str, int] = {}
        losers_confluences: dict[str, int] = {}
        winners_short_confluences: dict[str, int] = {}
        losers_short_confluences: dict[str, int] = {}
        for o in outcome_rows:
            d_out = decisions_by_id.get(o.decision_id)
            if not d_out:
                continue
            confs = (d_out.output or {}).get("confluences") or []
            if o.classification == "GOOD_BUY":
                for c in confs:
                    winners_confluences[c] = winners_confluences.get(c, 0) + 1
            elif o.classification == "BAD_BUY":
                for c in confs:
                    losers_confluences[c] = losers_confluences.get(c, 0) + 1
            elif o.classification == "GOOD_SHORT":
                for c in confs:
                    winners_short_confluences[c] = winners_short_confluences.get(c, 0) + 1
            elif o.classification == "BAD_SHORT":
                for c in confs:
                    losers_short_confluences[c] = losers_short_confluences.get(c, 0) + 1
            elif o.classification == "BLOCKED_GOOD_TRADE":
                if (d_out.output or {}).get("action") == "SHORT":
                    blocked_good_short_count += 1

        top_misses = sorted(
            [o for o in outcome_rows if o.classification == "MISSED_OPPORTUNITY"],
            key=lambda o: float(o.mfe_pct or 0),
            reverse=True,
        )[:5]
        top_misses_lines = []
        for o in top_misses:
            d = decisions_by_id.get(o.decision_id)
            inp = (d.input or {}) if d else {}
            out = (d.output or {}) if d else {}
            regime = out.get("regime") or inp.get("regime") or "?"
            trading_product = str(inp.get("trading_product") or "spot").lower()
            if trading_product == "futures" and regime == "TRENDING_DOWN":
                counterfactual = "SHORT"
            elif regime == "TRENDING_UP":
                counterfactual = "LONG"
            else:
                counterfactual = "LONG"
            confidence = out.get("confidence") or "?"
            confluences = out.get("confluences") or []
            reasoning = (out.get("reasoning") or "")[:120]
            rsi_15m = _safe_float(inp.get("rsi_15m") or inp.get("rsi"))
            macd_hist = _safe_float(inp.get("hist_15m") or inp.get("hist"))
            adx = _safe_float(inp.get("adx_15m") or inp.get("adx"))
            vol_ratio = _safe_float(inp.get("volume_ratio"))
            bb_pct = _safe_float(inp.get("bb_pct_15m") or inp.get("bb_pct_5m"))
            cross_tf = (inp.get("block_f_cross_tf") or {}).get("alignment") or "?"
            conf_str = f"{confidence:.2f}" if isinstance(confidence, float) else str(confidence)
            confluences_str = "+".join(confluences) if confluences else "—"
            indicators = (
                f"RSI15m={rsi_15m:.0f}" if rsi_15m is not None else ""
            ) + (
                f" MACD_hist={macd_hist:+.1f}" if macd_hist is not None else ""
            ) + (
                f" ADX={adx:.0f}" if adx is not None else ""
            ) + (
                f" vol_ratio={vol_ratio:.1f}x" if vol_ratio is not None else ""
            ) + (
                f" BB%={bb_pct:.0f}" if bb_pct is not None else ""
            ) + (
                f" TF_align={cross_tf}" if cross_tf != "?" else ""
            )
            top_misses_lines.append(
                f"  [{(d.ts.strftime('%H:%M') if d else '?')} UTC] "
                f"miss ({counterfactual}) +{float(o.mfe_pct or 0):.2f}% en {o.time_to_mfe_min}min "
                f"(mae {float(o.mae_pct or 0):+.2f}%) | "
                f"producto={trading_product} régimen={regime} conf={conf_str} "
                f"confluencias={confluences_str}\n"
                f"    indicadores: {indicators.strip()}\n"
                f"    reasoning: {reasoning}"
            )
        top_misses_block = "\n".join(top_misses_lines) or "  (sin misses en el período)"

        open_confluence_candidates = (await self.session.execute(
            select(func.count()).select_from(ConfluenceCandidate).where(
                ConfluenceCandidate.status == "open",
            )
        )).scalar_one()
        active_confluence_registry = (await self.session.execute(
            select(func.count()).select_from(ConfluenceRegistry).where(
                ConfluenceRegistry.active.is_(True),
            )
        )).scalar_one()

        return {
            "total_decisions": len(decisions),
            "buy_count": action_counts["BUY"],
            "short_count": short_count,
            "sell_count": action_counts["SELL"],
            "hold_count": action_counts["HOLD"],
            "buy_pct": action_counts["BUY"] / n * 100,
            "short_pct": short_pct,
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
            # LLM-centric (v1.3) — auditoría del Decisor autónomo
            "coherence_warnings_total": coherence_warnings_total,
            "coherence_by_rule": coherence_by_rule,
            "decisions_with_warnings": decisions_with_warnings,
            "two_pass_triggered_count": two_pass_count,
            "avg_position_size_pct": avg_position_size_pct,
            "avg_buy_confidence": avg_buy_confidence,
            # Contrafactual (PR2)
            "evaluated_decisions": evaluated_decisions,
            "total_outcome_rows": len(outcome_rows),
            "missed_count": missed_count,
            "bad_buy_count": bad_buy_count,
            "blocked_good_count": blocked_good_count,
            "correctly_blocked_count": correctly_blocked_count,
            "good_hold_count": good_hold_count,
            "good_buy_count": good_buy_count,
            "good_short_count": good_short_count,
            "bad_short_count": bad_short_count,
            "bad_short_rate": round(bad_short_rate, 1),
            "blocked_good_short_count": blocked_good_short_count,
            "missed_rate": round(missed_rate, 1),
            "bad_buy_rate": round(bad_buy_rate, 1),
            "top_misses_block": top_misses_block,
            "winners_confluences": winners_confluences,
            "losers_confluences": losers_confluences,
            "winners_short_confluences": winners_short_confluences,
            "losers_short_confluences": losers_short_confluences,
            "avg_short_position_size_pct": avg_short_position_size_pct,
            "avg_short_confidence": avg_short_confidence,
            "open_confluence_candidates": open_confluence_candidates,
            "active_confluence_registry": active_confluence_registry,
            "trading_product": "spot",
        }

    async def _fetch_postmortem_lessons(self, *, window_days: int = 7, max_lessons: int = 10) -> str:
        """Fetch completed remap/guidance lessons from the last `window_days` days.

        Deduplicates by pattern_tag keeping the highest-confidence entry.
        Returns a formatted block ready for the supervisor prompt.
        """
        since = datetime.now(tz=timezone.utc) - timedelta(days=window_days)
        rows = (await self.session.execute(
            select(DecisionOutcome)
            .where(
                DecisionOutcome.postmortem_status == "completed",
                DecisionOutcome.computed_at >= since,
                DecisionOutcome.lesson_normalized.isnot(None),
            )
            .order_by(DecisionOutcome.computed_at.desc())
            .limit(50)
        )).scalars().all()

        _ROUTES = frozenset({"remap", "guidance"})
        by_tag: dict[str, dict] = {}
        for row in rows:
            norm = row.lesson_normalized or {}
            if norm.get("route") not in _ROUTES:
                continue
            tag = norm.get("pattern_tag") or norm.get("dedupe_key") or "unknown"
            conf = float(norm.get("confidence") or 0)
            prev = by_tag.get(tag)
            if prev is None or conf > float(prev.get("confidence") or 0):
                by_tag[tag] = norm

        if not by_tag:
            return "  (sin lecciones remap/guidance en los últimos 7 días)"

        ordered = sorted(by_tag.values(), key=lambda x: float(x.get("confidence") or 0), reverse=True)
        lines: list[str] = []
        for norm in ordered[:max_lessons]:
            route = norm.get("route", "?")
            tag = norm.get("pattern_tag", "?")
            conf = float(norm.get("confidence") or 0)
            if route == "remap":
                payload = norm.get("remap") or {}
                codes = ", ".join(payload.get("misapplied_confluences") or []) or "n/d"
                correction = (payload.get("correction") or norm.get("block_k_line") or "")[:200]
                lines.append(f"  [{route}] pattern={tag} conf={conf:.2f} | confluencias_mal_usadas={codes}")
                lines.append(f"    → {correction}")
            else:
                payload = norm.get("guidance") or {}
                message = (payload.get("message") or norm.get("block_k_line") or "")[:200]
                applies = payload.get("applies_when") or {}
                ctx_str = f" (aplica cuando: {applies})" if applies else ""
                lines.append(f"  [{route}] pattern={tag} conf={conf:.2f}{ctx_str}")
                lines.append(f"    → {message}")

        return "\n".join(lines)

    async def _promote_confluence_candidates(
        self,
        *,
        active_playbook,
        current_config: dict,
    ) -> list[dict]:
        from agents.confluence_registry import promote_eligible_candidates

        min_occ = int(current_config.get("confluence_promotion_min_occurrences", 3))
        window = int(current_config.get("confluence_promotion_window_days", 7))
        max_active = int(current_config.get("confluence_registry_max_active", 5))
        playbook_content = active_playbook.content if active_playbook else ""
        return await promote_eligible_candidates(
            self.session,
            min_occurrences=min_occ,
            window_days=window,
            max_active=max_active,
            playbook_content=playbook_content,
        )
