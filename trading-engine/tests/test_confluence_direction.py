from types import SimpleNamespace

from agents.confluence_registry import render_registry_block
from agents.labelers import format_profile_confluences_prompt, get_profile_confluences
from risk.coherence_checker import CoherenceChecker
from shared.confluence_direction import (
    parse_registry_direction,
    registry_direction_allows_action,
)
from shared.schemas import DecisorAction, DecisorOutput, MarketRegime


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
        code="K",
        title="Fake breakdown",
        definition_md="[SHORT] Precio pierde soporte con volumen.",
        active=True,
    )
    block = render_registry_block([entry])
    assert "solo SHORT" in block
    assert "K." in block


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
        confluences=["K"],
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
        "registry_direction_by_code": {"K": "SHORT"},
    }
    warnings = checker._c9_promoted_direction_vs_action(decision, ctx)
    assert len(warnings) == 1
    assert warnings[0].rule_id == "C9"
