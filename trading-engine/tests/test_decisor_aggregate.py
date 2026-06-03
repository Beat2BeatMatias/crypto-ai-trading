"""Tests for self-consistency aggregation."""
from shared.schemas import DecisorAction, DecisorOutput, MarketRegime
from agents.decisor_aggregate import aggregate_decisor_outputs


def _out(action: DecisorAction, conf: float = 0.6, sl: float | None = None) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["A"] if action == DecisorAction.BUY else [],
        action=action,
        confidence_base=conf,
        confidence_adjustment=0.0,
        confidence=conf,
        stop_loss=sl,
        take_profit=110_000.0 if sl else None,
        position_size_pct=0.05 if action == DecisorAction.BUY else 0.0,
        expected_holding_min=60,
        reasoning=f"sample {action.value}",
    )


def test_majority_buy():
    samples = [_out(DecisorAction.BUY, sl=95_000.0) for _ in range(3)]
    agg, meta = aggregate_decisor_outputs(samples)
    assert agg.action == DecisorAction.BUY
    assert meta["agreement"] == 1.0
    assert meta["n"] == 3


def test_tie_forces_hold():
    samples = [
        _out(DecisorAction.BUY, sl=95_000.0),
        _out(DecisorAction.HOLD),
        _out(DecisorAction.SELL),
    ]
    agg, meta = aggregate_decisor_outputs(samples)
    assert agg.action == DecisorAction.HOLD
    assert meta["consensus_uncertain"] is True
    assert "[CONSENSO_INCIERTO]" in agg.reasoning


def test_single_output_passthrough():
    o = _out(DecisorAction.HOLD)
    agg, meta = aggregate_decisor_outputs([o])
    assert agg is o
    assert meta["n"] == 1


def test_aggregate_short_majority_uses_median_sl_tp():
    samples = [
        DecisorOutput(
            regime=MarketRegime.TRENDING_DOWN,
            confluences=["H"],
            action=DecisorAction.SHORT,
            confidence_base=0.6,
            confidence_adjustment=0.0,
            confidence=0.6,
            stop_loss=101_000.0,
            take_profit=98_000.0,
            position_size_pct=0.10,
            expected_holding_min=60,
            reasoning="short 1",
        ),
        DecisorOutput(
            regime=MarketRegime.TRENDING_DOWN,
            confluences=["H"],
            action=DecisorAction.SHORT,
            confidence_base=0.6,
            confidence_adjustment=0.0,
            confidence=0.6,
            stop_loss=102_000.0,
            take_profit=97_000.0,
            position_size_pct=0.10,
            expected_holding_min=60,
            reasoning="short 2",
        ),
        DecisorOutput(
            regime=MarketRegime.TRENDING_DOWN,
            confluences=["H"],
            action=DecisorAction.SHORT,
            confidence_base=0.6,
            confidence_adjustment=0.0,
            confidence=0.6,
            stop_loss=101_500.0,
            take_profit=97_500.0,
            position_size_pct=0.10,
            expected_holding_min=60,
            reasoning="short 3",
        ),
    ]
    agg, meta = aggregate_decisor_outputs(samples)
    assert agg.action == DecisorAction.SHORT
    assert agg.stop_loss == 101_500.0
    assert agg.take_profit == 97_500.0
    assert meta["selected_action"] == "SHORT"
