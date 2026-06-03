"""Aggregate multiple DecisorOutput samples (self-consistency voting)."""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from shared.schemas import DecisorAction, DecisorOutput, MarketRegime


def aggregate_decisor_outputs(outputs: list[DecisorOutput]) -> tuple[DecisorOutput, dict[str, Any]]:
    if not outputs:
        raise ValueError("aggregate_decisor_outputs requires at least one output")
    if len(outputs) == 1:
        o = outputs[0]
        return o, {
            "n": 1,
            "votes": {o.action.value: 1},
            "agreement": 1.0,
            "selected_action": o.action.value,
        }

    n = len(outputs)
    action_counts = Counter(o.action.value for o in outputs)
    winner_action_str, winner_count = action_counts.most_common(1)[0]
    agreement = winner_count / n

    if winner_count * 2 <= n:
        hold = _consensus_hold(action_counts)
        meta = {
            "n": n,
            "votes": dict(action_counts),
            "agreement": agreement,
            "selected_action": "HOLD",
            "consensus_uncertain": True,
        }
        return hold, meta

    winner_action = DecisorAction(winner_action_str)
    winners = [o for o in outputs if o.action == winner_action]

    regime_counts = Counter(o.regime.value for o in winners)
    regime = MarketRegime(regime_counts.most_common(1)[0][0])

    conf_adj = statistics.median([o.confidence_adjustment for o in winners])
    base = statistics.median([o.confidence_base for o in winners])

    if winner_action in (DecisorAction.BUY, DecisorAction.SHORT):
        sl_vals = [o.stop_loss for o in winners if o.stop_loss is not None]
        tp_vals = [o.take_profit for o in winners if o.take_profit is not None]
        stop_loss = statistics.median(sl_vals) if sl_vals else winners[0].stop_loss
        take_profit = statistics.median(tp_vals) if tp_vals else winners[0].take_profit
        holding = int(round(statistics.median([o.expected_holding_min for o in winners])))
        confluences = _union_confluences(winners)
        reasoning = _pick_reasoning(winners, winner_action, agreement, n)
        out = DecisorOutput(
            regime=regime,
            confluences=confluences,
            action=winner_action,
            confidence_base=base,
            confidence_adjustment=conf_adj,
            confidence=0.0,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size_pct=statistics.median([o.position_size_pct for o in winners]),
            expected_holding_min=max(1, holding),
            reasoning=reasoning,
        )
    elif winner_action == DecisorAction.SELL:
        reasoning = _pick_reasoning(winners, winner_action, agreement, n)
        out = DecisorOutput(
            regime=regime,
            confluences=_union_confluences(winners),
            action=DecisorAction.SELL,
            confidence_base=base,
            confidence_adjustment=conf_adj,
            confidence=0.0,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.0,
            expected_holding_min=1,
            reasoning=reasoning,
        )
    else:
        reasoning = _pick_reasoning(winners, winner_action, agreement, n)
        out = DecisorOutput(
            regime=regime,
            confluences=[],
            action=DecisorAction.HOLD,
            confidence_base=base,
            confidence_adjustment=conf_adj,
            confidence=0.0,
            stop_loss=None,
            take_profit=None,
            position_size_pct=0.0,
            expected_holding_min=1,
            reasoning=reasoning,
        )

    meta = {
        "n": n,
        "votes": dict(action_counts),
        "agreement": round(agreement, 3),
        "selected_action": winner_action.value,
        "consensus_uncertain": False,
    }
    return out, meta


def _consensus_hold(action_counts: Counter) -> DecisorOutput:
    votes = ", ".join(f"{k}={v}" for k, v in action_counts.most_common())
    return DecisorOutput(
        regime=MarketRegime.RANGE,
        confluences=[],
        action=DecisorAction.HOLD,
        confidence_base=0.0,
        confidence_adjustment=0.0,
        confidence=0.0,
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        expected_holding_min=1,
        reasoning=f"[CONSENSO_INCIERTO] Sin mayoría clara ({votes}) → HOLD.",
    )


def _union_confluences(outputs: list[DecisorOutput]) -> list[str]:
    seen: list[str] = []
    for o in outputs:
        for c in o.confluences:
            if c not in seen:
                seen.append(c)
    return seen[:10]


def _pick_reasoning(
    winners: list[DecisorOutput],
    action: DecisorAction,
    agreement: float,
    n: int,
) -> str:
    best = max(winners, key=lambda o: o.confidence)
    prefix = f"[CONSENSO {int(round(agreement * n))}/{n} {action.value}] "
    body = (best.reasoning or "").strip()
    combined = prefix + body
    if len(combined) > 1000:
        return combined[:997] + "..."
    return combined
