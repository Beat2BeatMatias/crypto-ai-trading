from types import SimpleNamespace

from agents.confluence_registry import render_registry_block
from agents.labelers import format_profile_confluences_prompt, get_profile_confluences
from risk.coherence_checker import CoherenceChecker
from shared.confluence_direction import (
    classify_confluences_by_direction,
    detect_confidence_jump_opposing_mix,
    filter_confluences_for_direction,
    has_opposing_confluence_mix,
    parse_registry_direction,
    registry_direction_allows_action,
    resolve_opposing_confluences,
)
from shared.schemas import DecisorAction, DecisorOutput, MarketRegime, Direction


def test_parse_registry_direction_tags():
    assert parse_registry_direction("[LONG] rebote") == "LONG"
    assert parse_registry_direction("[SHORT] breakdown") == "SHORT"
    assert parse_registry_direction("[AMBOS] rango") == "BOTH"
    assert parse_registry_direction("[LONG] x [SHORT] y") == "BOTH"
    assert parse_registry_direction("sin tag") is None


def test_registry_direction_allows_action():
    assert registry_direction_allows_action("LONG", "BUY", trading_product="futures")
    assert not registry_direction_allows_action("LONG", "SHORT", trading_product="futures")
    assert registry_direction_allows_action("SHORT", "SHORT", trading_product="futures")
    assert registry_direction_allows_action("BOTH", "BUY", trading_product="spot")
    assert registry_direction_allows_action(None, "SHORT", trading_product="futures")


def test_render_registry_block_shows_direction_hint():
    entry = SimpleNamespace(
        code="Q",
        title="Fake breakdown",
        definition_md="[SHORT] Precio pierde soporte con volumen.",
        active=True,
    )
    block = render_registry_block([entry])
    assert "solo SHORT" in block
    assert "Q." in block


def test_profile_confluences_futures_includes_short_codes():
    spot = get_profile_confluences("HIBRIDO", trading_product="spot")
    fut = get_profile_confluences("HIBRIDO", trading_product="futures")
    assert "I" not in spot
    assert "I" in fut and "J" in fut
    prompt = format_profile_confluences_prompt("HIBRIDO", trading_product="futures")
    assert "SHORT (futuros)" in prompt


def test_c9_promoted_direction_mismatch():
    checker = CoherenceChecker()
    decision = DecisorOutput(
        action=DecisorAction.BUY,
        regime=MarketRegime.RANGE,
        confluences=["Q"],
        confidence=0.7,
        confidence_base=0.7,
        confidence_adjustment=0.0,
        reasoning="[DECISION] t [MERCADO] t [SENALES] t [CONFIANZA] t [NIVELES] t",
        stop_loss=90_000.0,
        take_profit=95_000.0,
        position_size_pct=0.1,
        expected_holding_min=60,
    )
    ctx = {
        "price": 92_000.0,
        "trading_product": "futures",
        "registry_direction_by_code": {"Q": "SHORT"},
    }
    warnings = checker._c9_promoted_direction_vs_action(decision, ctx)
    assert len(warnings) == 1
    assert warnings[0].rule_id == "C9"
    assert warnings[0].evidence["kind"] == "registry_tag"


def test_classify_confluences_by_direction_static():
    longs, shorts, neutral = classify_confluences_by_direction(["B", "J", "K"])
    assert longs == ["B"]
    assert shorts == ["J", "K"]
    assert neutral == []


def test_has_opposing_confluence_mix():
    assert has_opposing_confluence_mix(["B", "J"])
    assert not has_opposing_confluence_mix(["B", "C"])
    assert not has_opposing_confluence_mix(["J"])


def test_filter_confluences_for_short_direction():
    kept, dropped = filter_confluences_for_direction(["B", "J"], Direction.SHORT)
    assert kept == ["J"]
    assert dropped == ["B"]


def test_resolve_opposing_confluences_trending_down_hold():
    decision = DecisorOutput(
        action=DecisorAction.HOLD,
        regime=MarketRegime.TRENDING_DOWN,
        confluences=["B", "J"],
        confidence=0.5,
        confidence_base=0.5,
        confidence_adjustment=0.0,
        reasoning="test",
        stop_loss=None,
        take_profit=None,
        position_size_pct=0.0,
        expected_holding_min=1,
    )
    kept, dropped = resolve_opposing_confluences(
        decision.confluences,
        direction=Direction.SHORT,
    )
    assert kept == ["J"]
    assert dropped == ["B"]


def test_detect_confidence_jump_opposing_mix_inflated():
    alert = detect_confidence_jump_opposing_mix(
        current_confidence=0.4675,
        previous_confidence=0.34,
        confluences=["B", "J"],
        inflated_confidence=0.595,
    )
    assert alert is not None
    assert alert["alert"] == "confidence_jump_opposing_mix"
    assert alert["inflated_delta"] > 0.15
