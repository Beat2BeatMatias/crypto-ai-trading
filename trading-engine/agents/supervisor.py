from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Decision, Trade, PlaybookVersion
from agents.llm_client import LLMClient, LLMProvider
from agents.prompt_manager import PromptManager

logger = structlog.get_logger()

_CONFIG_SUGGESTION_PROMPT = """Eres un analista cuantitativo de risk management. Basándote en las métricas
de trading del período, sugiere los valores óptimos para los parámetros de configuración del sistema.

MÉTRICAS DEL PERÍODO:
- Trades cerrados: {closed_trades}
- Win rate: {win_rate}%
- Profit factor: {profit_factor}
- P&L total: ${total_pnl}
- Avg ganancia: ${avg_win}
- Avg pérdida: ${avg_loss}
- Decisiones BUY: {buy_count} ({buy_pct:.1f}%)
- Decisiones HOLD: {hold_count} ({hold_pct:.1f}%)
- BUYs bloqueados por Risk Gate: {rejected_count}

CONFIGURACIÓN ACTUAL:
- atr_timeframe: {atr_timeframe}
- sl_atr_multiplier: {sl_atr_multiplier}
- min_rr_ratio: {min_rr_ratio}
- decisor_interval_min: {decisor_interval_min}
- max_position_pct: {max_position_pct}

OPCIONES VÁLIDAS:
- atr_timeframe: "5m" | "15m" | "1h"
- sl_atr_multiplier: 0.1 a 0.8 (cuanto menor, más trades pero más riesgo)
- min_rr_ratio: 1.0 a 3.0
- decisor_interval_min: 5 a 60
- max_position_pct: 0.01 a 0.20

Responde ÚNICAMENTE con JSON válido, sin texto extra:
{{
  "suggestions": [
    {{
      "key": "atr_timeframe",
      "current": "{atr_timeframe}",
      "suggested": "valor",
      "reason": "explicación breve en español"
    }},
    {{
      "key": "sl_atr_multiplier",
      "current": "{sl_atr_multiplier}",
      "suggested": valor_numerico,
      "reason": "explicación breve en español"
    }},
    {{
      "key": "min_rr_ratio",
      "current": "{min_rr_ratio}",
      "suggested": valor_numerico,
      "reason": "explicación breve en español"
    }},
    {{
      "key": "decisor_interval_min",
      "current": "{decisor_interval_min}",
      "suggested": valor_entero,
      "reason": "explicación breve en español"
    }},
    {{
      "key": "max_position_pct",
      "current": "{max_position_pct}",
      "suggested": valor_numerico,
      "reason": "explicación breve en español"
    }}
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

        if metrics["closed_trades"] < self.min_trades:
            logger.info("supervisor.insufficient_data", closed=metrics["closed_trades"])
            rejected_reason = f"insufficient_data: {metrics['closed_trades']} < {self.min_trades}"
        else:
            try:
                previous = await self.prompt_manager.get_active_playbook()
                previous_content = previous.content if previous else "# (vacio)"
                decisions_since = (await self.session.execute(
                    select(Decision).where(Decision.ts >= since, Decision.agent == "decisor")
                    .order_by(Decision.ts.asc())
                )).scalars().all()
                decisions_dump = "\n".join([
                    json.dumps({"ts": d.ts.isoformat(), "action": d.output.get("action"),
                                "confidence": d.output.get("confidence"),
                                "reasoning": (d.output.get("reasoning") or "")[:120],
                                "executed": d.executed})
                    for d in decisions_since
                ])
                ctx = {
                    **metrics,
                    "previous_version": previous.version if previous else 0,
                    "new_version": (previous.version + 1) if previous else 0,
                    "previous_playbook": previous_content,
                    "decisions_dump": decisions_dump,
                    "date": datetime.now(tz=timezone.utc).date().isoformat(),
                    "min_trades": self.min_trades,
                }
                system_prompt = self.prompt_manager.load_system_prompt("supervisor")
                user_prompt = self.prompt_manager.render_user_prompt("supervisor", ctx, strict=False)
                resp = await self.llm.call(provider=self.provider, system_prompt=system_prompt,
                                            user_prompt=user_prompt, fallbacks=self.fallbacks)
                output["playbook"] = resp.text
                await self.prompt_manager.save_playbook(
                    content=resp.text, model=self.provider.value,
                    trades_analyzed=metrics["closed_trades"],
                    win_rate=metrics["win_rate"],
                    pnl_summary={"pnl_usdt": metrics["total_pnl"], "avg_win": metrics["avg_win"]},
                )
                logger.info("supervisor.playbook_saved", version=ctx["new_version"])

                # Segunda llamada LLM: sugerencias de configuración
                if current_config:
                    try:
                        suggestions = await self._generate_config_suggestions(metrics, current_config)
                        output["config_suggestions"] = suggestions
                        logger.info("supervisor.suggestions_generated",
                                    count=len(suggestions.get("suggestions", [])))
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

    async def _generate_config_suggestions(self, metrics: dict, current_config: dict) -> dict:
        prompt = _CONFIG_SUGGESTION_PROMPT.format(
            closed_trades=metrics["closed_trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            total_pnl=metrics["total_pnl"],
            avg_win=metrics["avg_win"],
            avg_loss=metrics["avg_loss"],
            buy_count=metrics["buy_count"],
            buy_pct=metrics["buy_pct"],
            hold_count=metrics["hold_count"],
            hold_pct=metrics["hold_pct"],
            rejected_count=metrics["rejected_count"],
            atr_timeframe=current_config.get("atr_timeframe", "15m"),
            sl_atr_multiplier=current_config.get("sl_atr_multiplier", 0.3),
            min_rr_ratio=current_config.get("min_rr_ratio", 1.3),
            decisor_interval_min=current_config.get("decisor_interval_min", 10),
            max_position_pct=current_config.get("max_position_pct", 0.05),
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
        action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for d in decisions:
            a = d.output.get("action", "HOLD")
            action_counts[a] = action_counts.get(a, 0) + 1
        n = max(len(decisions), 1)
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
            "profit_factor": (sum(float(t.pnl_usdt) for t in wins) /
                              abs(sum(float(t.pnl_usdt) for t in losses))) if losses else 0,
            "avg_win": round(avg_win, 2),
            "avg_win_pct": 0.0, "avg_loss": round(avg_loss, 2), "avg_loss_pct": 0.0,
            "total_pnl": round(total_pnl, 2), "total_pnl_pct": 0.0,
            "max_dd_pct": 0.0, "avg_holding_min": 0,
            "open_btc": 0, "close_btc": 0, "low_24h": 0, "high_24h": 0,
            "pct_24h": 0, "atr_avg": 0, "atr_pct": 0, "vol_label": "normal",
        }
