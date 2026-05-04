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

class Supervisor:
    def __init__(self, *, session: AsyncSession, llm: LLMClient, symbol: str,
                 provider: LLMProvider = LLMProvider.GEMINI_PRO,
                 fallback: LLMProvider | None = LLMProvider.GROQ_LLAMA,
                 min_trades: int = 5,
                 prompt_manager: PromptManager | None = None):
        self.session = session
        self.llm = llm
        self.symbol = symbol
        self.provider = provider
        self.fallback = fallback
        self.min_trades = min_trades
        self.prompt_manager = prompt_manager or PromptManager(session)

    async def run(self) -> None:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        metrics = await self._compute_metrics(since)
        if metrics["closed_trades"] < self.min_trades:
            logger.info("supervisor.insufficient_data", closed=metrics["closed_trades"])
            return
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
                                    user_prompt=user_prompt, fallback=self.fallback)
        self.session.add(Decision(
            ts=datetime.now(tz=timezone.utc),
            agent="supervisor", model=self.provider.value,
            tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
            latency_ms=resp.latency_ms,
            input={k: str(v)[:500] for k, v in ctx.items()},
            output={"playbook": resp.text}, executed=True,
        ))
        await self.session.commit()
        await self.prompt_manager.save_playbook(
            content=resp.text, model=self.provider.value,
            trades_analyzed=metrics["closed_trades"],
            win_rate=metrics["win_rate"],
            pnl_summary={"pnl_usdt": metrics["total_pnl"], "avg_win": metrics["avg_win"]},
        )
        logger.info("supervisor.playbook_saved", version=ctx["new_version"])

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
