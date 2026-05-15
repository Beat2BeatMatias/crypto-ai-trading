# Decisor v2 Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the decisor agent to support confidence decomposition (base + adjustment), per-profile operational mode, fee-vs-TP validation (R10), volume/drawdown payload enrichment, and 6 new configurable parameters.

**Architecture:** Changes span 4 layers — schema (shared/schemas.py), config (shared/config_store.py + migration), engine (risk_gate, context_builder, indicators, decisor, main.py), and prompts. Each task is independent except Task 7 (wiring), which depends on Tasks 1–6 being complete.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLAlchemy async, Alembic, pandas, pytest-asyncio, aiosqlite (tests).

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `shared/schemas.py` | Modify | Add `confidence_base`, `confidence_adjustment`, `expected_holding_min` to `DecisorOutput` |
| `shared/config_store.py` | Modify | Add 6 new `ConfigKey` enum values + `DEFAULTS` entries |
| `trading-engine/alembic/versions/004_add_decisor_v2_config.py` | Create | Data migration: seed 6 new config rows |
| `trading-engine/collectors/indicators.py` | Modify | Add `volume_current` to returned dict |
| `trading-engine/risk/risk_gate.py` | Modify | Rename `atr_1h` → `atr_ref`; add R10 (fees vs TP) |
| `trading-engine/agents/context_builder.py` | Modify | Add volume, drawdown, orderbook dist%, 6 new config vars, `atr_timeframe` key |
| `trading-engine/agents/decisor.py` | Modify | Update `_hold_decision()` for new fields; add `current_drawdown_pct` param |
| `trading-engine/main.py` | Modify | Update `gate.validate()` call (rename param, add fee args) |
| `trading-engine/agents/prompts/decisor_system.txt` | Replace | Full new system prompt with PERFIL, updated R10, new OUTPUT spec |
| `trading-engine/agents/prompts/decisor_user.txt` | Modify | Add volume block, drawdown, wall distances in % |
| `trading-engine/tests/test_risk_gate.py` | Modify | Update `_COMMON_KWARGS` (`atr_1h` → `atr_ref`); add R10 tests |
| `trading-engine/tests/test_context_builder.py` | Modify | Add tests for 6 new ctx keys |

---

## Task 1: DecisorOutput — 3 new fields

**Files:**
- Modify: `shared/schemas.py`
- Modify: `trading-engine/agents/decisor.py` (update `_hold_decision`)
- Test: existing `trading-engine/tests/test_risk_gate.py` (helper `_buy_decision` must still work)

- [ ] **Step 1: Write the failing test**

Add this test to `trading-engine/tests/test_risk_gate.py` (in the helpers section, after `_hold_decision`):

```python
def test_decisor_output_accepts_new_fields():
    # GIVEN a BUY decision with the 3 new v2 fields
    decision = DecisorOutput(
        regime=MarketRegime.TRENDING_UP,
        confluences=["A_solida"],
        action=DecisorAction.BUY,
        confidence_base=0.65,
        confidence_adjustment=0.05,
        confidence=0.70,
        stop_loss=66000.0,
        take_profit=69000.0,
        position_size_pct=0.10,
        expected_holding_min=45,
        reasoning="Test.",
    )

    # THEN the fields are stored correctly
    assert decision.confidence_base == pytest.approx(0.65)
    assert decision.confidence_adjustment == pytest.approx(0.05)
    assert decision.expected_holding_min == 45
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_risk_gate.py::test_decisor_output_accepts_new_fields -v
```

Expected: `FAILED` — `unexpected keyword argument 'confidence_base'`

- [ ] **Step 3: Implement — add 3 fields to DecisorOutput**

In `shared/schemas.py`, replace the `DecisorOutput` class body with:

```python
class DecisorOutput(BaseModel):
    regime: MarketRegime
    confluences: list[str] = Field(default_factory=list, max_length=10)
    action: DecisorAction
    confidence_base: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    confidence_adjustment: Annotated[float, Field(ge=-0.20, le=0.20)] = 0.0
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    stop_loss: float | None
    take_profit: float | None
    position_size_pct: Annotated[float, Field(ge=0.0, le=0.25)]
    expected_holding_min: Annotated[int, Field(ge=1)] = 1
    reasoning: Annotated[str, Field(max_length=800)]

    @field_validator("position_size_pct", mode="before")
    @classmethod
    def _coerce_null_position_size(cls, v: Any) -> float:
        # LLMs return null for HOLD/SELL decisions; treat as 0.0
        return 0.0 if v is None else v

    @field_validator("expected_holding_min", mode="before")
    @classmethod
    def _coerce_null_holding(cls, v: Any) -> int:
        return 1 if v is None else v

    @field_validator("reasoning", mode="before")
    @classmethod
    def _truncate_reasoning(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) > 800:
            return v[:797] + "..."
        return v

    @model_validator(mode="after")
    def _buy_requires_sl_and_tp(self) -> "DecisorOutput":
        if self.action == DecisorAction.BUY:
            if self.stop_loss is None:
                raise ValueError("stop_loss is required when action=BUY")
            if self.take_profit is None:
                raise ValueError("take_profit is required when action=BUY")
        return self
```

- [ ] **Step 4: Update `_hold_decision` in `trading-engine/agents/decisor.py`**

Replace the `_hold_decision` function:

```python
def _hold_decision(reason: str) -> DecisorOutput:
    return DecisorOutput(
        regime=MarketRegime.RANGE, confluences=[], action=DecisorAction.HOLD,
        confidence_base=0.0, confidence_adjustment=0.0, confidence=0.0,
        stop_loss=None, take_profit=None, position_size_pct=0.0,
        expected_holding_min=1, reasoning=reason,
    )
```

- [ ] **Step 5: Run the test to confirm it passes**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_risk_gate.py -v
```

Expected: all tests `PASSED` (including the new one)

- [ ] **Step 6: Commit**

```bash
git add shared/schemas.py trading-engine/agents/decisor.py trading-engine/tests/test_risk_gate.py
git commit -m "feat: add confidence_base, confidence_adjustment, expected_holding_min to DecisorOutput"
```

---

## Task 2: ConfigStore — 6 new ConfigKey values

**Files:**
- Modify: `shared/config_store.py`

- [ ] **Step 1: Write the failing test**

Create `trading-engine/tests/test_config_v2_keys.py`:

```python
"""Tests for the 6 new decisor-v2 ConfigKey entries."""
from __future__ import annotations
import pytest
from shared.config_store import ConfigKey, DEFAULTS


def test_new_keys_present_in_enum():
    # GIVEN the updated ConfigKey enum
    # THEN all 6 new keys exist with the expected string values
    assert ConfigKey.MIN_FEES_TO_TP_RATIO.value == "min_fees_to_tp_ratio"
    assert ConfigKey.MIN_CONFLUENCES_BUY.value == "min_confluences_buy"
    assert ConfigKey.COOLDOWN_AFTER_SELL_MIN.value == "cooldown_after_sell_min"
    assert ConfigKey.SUBJECTIVE_ADJ_MAX.value == "subjective_adj_max"
    assert ConfigKey.EXPECTED_HOLDING_MAX_MIN.value == "expected_holding_max_min"
    assert ConfigKey.CONFLUENCE_WEAK_FACTOR.value == "confluence_weak_factor"


def test_new_keys_have_defaults():
    # GIVEN the DEFAULTS dict
    # THEN each new key has a default with the expected type and value
    cases = [
        (ConfigKey.MIN_FEES_TO_TP_RATIO, "3.0", "float"),
        (ConfigKey.MIN_CONFLUENCES_BUY, "2", "int"),
        (ConfigKey.COOLDOWN_AFTER_SELL_MIN, "15", "int"),
        (ConfigKey.SUBJECTIVE_ADJ_MAX, "0.10", "float"),
        (ConfigKey.EXPECTED_HOLDING_MAX_MIN, "240", "int"),
        (ConfigKey.CONFLUENCE_WEAK_FACTOR, "0.5", "float"),
    ]
    for key, expected_value, expected_type in cases:
        assert key in DEFAULTS, f"Missing default for {key}"
        d = DEFAULTS[key]
        assert d.value == expected_value, f"{key}: expected value {expected_value!r}, got {d.value!r}"
        assert d.value_type == expected_type, f"{key}: expected type {expected_type!r}, got {d.value_type!r}"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_config_v2_keys.py -v
```

Expected: `FAILED` — `AttributeError: 'ConfigKey' object has no attribute 'MIN_FEES_TO_TP_RATIO'`

- [ ] **Step 3: Implement — add 6 keys to ConfigKey enum in `shared/config_store.py`**

After the `FACTOR_REGIME_NON_TRENDING` line, add:

```python
    # Decisor v2 — operational parameters
    MIN_FEES_TO_TP_RATIO = "min_fees_to_tp_ratio"
    MIN_CONFLUENCES_BUY = "min_confluences_buy"
    COOLDOWN_AFTER_SELL_MIN = "cooldown_after_sell_min"
    SUBJECTIVE_ADJ_MAX = "subjective_adj_max"
    EXPECTED_HOLDING_MAX_MIN = "expected_holding_max_min"
    CONFLUENCE_WEAK_FACTOR = "confluence_weak_factor"
```

- [ ] **Step 4: Implement — add 6 entries to DEFAULTS in `shared/config_store.py`**

After the `ConfigKey.FACTOR_REGIME_NON_TRENDING` entry in `DEFAULTS`, add:

```python
    ConfigKey.MIN_FEES_TO_TP_RATIO: _Default(
        "3.0", "float",
        "Min TP movement as multiple of round-trip fees for BUY approval (R10). Range 1.5–6.0.",
    ),
    ConfigKey.MIN_CONFLUENCES_BUY: _Default(
        "2", "int",
        "Minimum number of confluences required to allow BUY. Range 1–4.",
    ),
    ConfigKey.COOLDOWN_AFTER_SELL_MIN: _Default(
        "15", "int",
        "Minutes of cooldown after a SELL before next BUY is allowed. Range 0–120.",
    ),
    ConfigKey.SUBJECTIVE_ADJ_MAX: _Default(
        "0.10", "float",
        "Maximum allowed subjective confidence adjustment (±). Range 0.00–0.20.",
    ),
    ConfigKey.EXPECTED_HOLDING_MAX_MIN: _Default(
        "240", "int",
        "Maximum expected holding time in minutes; used for zombie-trade detection. Range 30–1440.",
    ),
    ConfigKey.CONFLUENCE_WEAK_FACTOR: _Default(
        "0.5", "float",
        "Multiplier applied to a weak confluence vs a solid one in confidence calc. Range 0.0–1.0.",
    ),
```

- [ ] **Step 5: Run the test to confirm it passes**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_config_v2_keys.py -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add shared/config_store.py trading-engine/tests/test_config_v2_keys.py
git commit -m "feat: add 6 decisor-v2 config keys to ConfigKey enum and DEFAULTS"
```

---

## Task 3: Alembic Migration — seed 6 config rows

**Files:**
- Create: `trading-engine/alembic/versions/004_add_decisor_v2_config.py`

- [ ] **Step 1: Create the migration file**

Create `trading-engine/alembic/versions/004_add_decisor_v2_config.py`:

```python
"""seed decisor_v2 config entries

Revision ID: 004
Revises: 003
Create Date: 2026-05-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

_NEW_ROWS = [
    ("min_fees_to_tp_ratio", "3.0", "float",
     "Min TP movement as multiple of round-trip fees for BUY approval (R10). Range 1.5–6.0."),
    ("min_confluences_buy", "2", "int",
     "Minimum number of confluences required to allow BUY. Range 1–4."),
    ("cooldown_after_sell_min", "15", "int",
     "Minutes of cooldown after a SELL before next BUY is allowed. Range 0–120."),
    ("subjective_adj_max", "0.10", "float",
     "Maximum allowed subjective confidence adjustment (±). Range 0.00–0.20."),
    ("expected_holding_max_min", "240", "int",
     "Maximum expected holding time in minutes; used for zombie-trade detection. Range 30–1440."),
    ("confluence_weak_factor", "0.5", "float",
     "Multiplier applied to a weak confluence vs a solid one in confidence calc. Range 0.0–1.0."),
]


def upgrade() -> None:
    config_table = sa.table(
        "config",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
        sa.column("value_type", sa.String),
        sa.column("description", sa.Text),
        sa.column("updated_at", sa.DateTime),
    )
    conn = op.get_bind()
    for key, value, value_type, description in _NEW_ROWS:
        existing = conn.execute(
            sa.text("SELECT 1 FROM config WHERE key = :k"), {"k": key}
        ).fetchone()
        if existing is None:
            op.bulk_insert(config_table, [{
                "key": key,
                "value": value,
                "value_type": value_type,
                "description": description,
                "updated_at": sa.func.now(),
            }])


def downgrade() -> None:
    keys = [row[0] for row in _NEW_ROWS]
    for key in keys:
        op.execute(sa.text("DELETE FROM config WHERE key = :k").bindparams(k=key))
```

- [ ] **Step 2: Verify the migration runs without errors (requires running DB)**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading
docker-compose up -d db
sleep 3
cd trading-engine
alembic upgrade head
```

Expected: `Running upgrade 003 -> 004, seed decisor_v2 config entries`

- [ ] **Step 3: Verify the rows were inserted**

```bash
docker-compose exec db psql -U trading -d trading -c "SELECT key, value, value_type FROM config WHERE key IN ('min_fees_to_tp_ratio','min_confluences_buy','cooldown_after_sell_min','subjective_adj_max','expected_holding_max_min','confluence_weak_factor');"
```

Expected: 6 rows returned with correct values.

- [ ] **Step 4: Commit**

```bash
git add trading-engine/alembic/versions/004_add_decisor_v2_config.py
git commit -m "feat: migration 004 — seed 6 decisor-v2 config rows"
```

---

## Task 4: Indicators — add volume_current

**Files:**
- Modify: `trading-engine/collectors/indicators.py`
- Test: `trading-engine/tests/test_indicators.py`

- [ ] **Step 1: Write the failing test**

Open `trading-engine/tests/test_indicators.py` and add at the end:

```python
def test_compute_indicators_includes_volume_current():
    # GIVEN a DataFrame with 30 rows of known volume
    import pandas as pd
    import numpy as np
    from collectors.indicators import compute_indicators

    np.random.seed(42)
    n = 30
    closes = 95000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "open": closes - 50,
        "high": closes + 100,
        "low": closes - 100,
        "close": closes,
        "volume": np.full(n, 5.0),   # last candle volume = 5.0
    })
    df["volume"].iloc[-1] = 7.5      # override last candle

    # WHEN computing indicators
    result = compute_indicators(df, timeframe="5m")

    # THEN volume_current equals the last candle's volume
    assert result["volume_current"] == pytest.approx(7.5)
    assert result["volume_avg_20"] is not None
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_indicators.py::test_compute_indicators_includes_volume_current -v
```

Expected: `FAILED` — `KeyError: 'volume_current'` or `AssertionError`

- [ ] **Step 3: Implement — add `volume_current` to the returned dict in `trading-engine/collectors/indicators.py`**

In the `return { ... }` block at the bottom of `compute_indicators`, add one line after `"volume_avg_20"`:

```python
        "volume_current": _last_or_none(df["volume"]),
```

The full return block becomes:

```python
    return {
        "timeframe": timeframe,
        "rsi": _last_or_none(rsi),
        "macd": _last_or_none(macd),
        "macd_signal": _last_or_none(macd_signal),
        "macd_hist": _last_or_none(macd_hist),
        "ema20": _last_or_none(ema20),
        "ema50": _last_or_none(ema50),
        "ema200": _last_or_none(ema200),
        "bb_upper": bb_upper_val,
        "bb_middle": _last_or_none(sma20),
        "bb_lower": bb_lower_val,
        "bb_pct": bb_pct,
        "atr": _last_or_none(atr),
        "volume_avg_20": _last_or_none(df["volume"].rolling(20).mean()),
        "volume_current": _last_or_none(df["volume"]),
        "last_close": last_close,
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_indicators.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add trading-engine/collectors/indicators.py trading-engine/tests/test_indicators.py
git commit -m "feat: add volume_current to compute_indicators output"
```

---

## Task 5: RiskGate — rename atr_1h → atr_ref, add R10 (fees vs TP)

**Files:**
- Modify: `trading-engine/risk/risk_gate.py`
- Modify: `trading-engine/tests/test_risk_gate.py`

- [ ] **Step 1: Add R10 tests and update `_COMMON_KWARGS` in `trading-engine/tests/test_risk_gate.py`**

**1a.** Rename `atr_1h` to `atr_ref` in the `_COMMON_KWARGS` dict (line ~76):

```python
_COMMON_KWARGS = dict(
    current_price=67000.0,
    atr_ref=500.0,          # was: atr_1h=500.0
    open_positions_count=0,
    daily_pnl_pct=0.0,
    total_drawdown_pct=-0.05,
    kill_switch=False,
    usdt_balance=10000.0,
    btc_held=0.0,
)
```

**1b.** Update every existing test that references `"atr_1h"` in `kwargs` overrides (lines ~206, ~220, ~268, ~283) — change `"atr_1h"` to `"atr_ref"`:

```python
# test_rr_below_1_5_rejected — line ~206
kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_ref": 300.0}

# test_sl_distance_below_atr_multiplier_rejected — line ~220
kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_ref": 800.0}

# test_buy_without_take_profit_rejected — line ~268
kwargs = {**_COMMON_KWARGS, "atr_ref": 300.0}

# test_buy_with_take_profit_below_entry_rejected — line ~283
kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_ref": 300.0}
```

**1c.** Add R10 tests at the end of the file:

```python
def test_r10_buy_rejected_when_tp_move_insufficient_vs_fees():
    # GIVEN roundtrip_fee_pct=0.2%, min_fees_to_tp_ratio=3.0
    # move to TP = (67600 - 67000) / 67000 * 100 = 0.896%
    # required = 3.0 * 0.2 = 0.6% → passes
    # BUT with move = 0.3%: (67201 - 67000) / 67000 * 100 ≈ 0.3% < 0.6% → rejected
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66800.0, take_profit=67201.0)
    # atr_ref=300: sl_distance=200, 0.3*300=90 < 200, 1.5*300=450 > 200 — passes R4
    # reward=201, risk=200 → R:R=1.005 ≤ 1.3 — wait, that fails R5 first.
    # Use take_profit=67400 → reward=400, risk=200, R:R=2.0 ✓
    # move = (67400 - 67000) / 67000 * 100 ≈ 0.597% < 3.0 * 0.2% = 0.6% → R10 rejects
    decision = _buy_decision(stop_loss=66800.0, take_profit=67400.0)
    kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_ref": 300.0,
              "roundtrip_fee_pct": 0.2, "min_fees_to_tp_ratio": 3.0}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN rejected by R10
    assert verdict.passed is False
    assert "R10" in verdict.reason


def test_r10_buy_passes_when_tp_move_covers_fees():
    # GIVEN roundtrip_fee_pct=0.2%, min_fees_to_tp_ratio=3.0
    # take_profit=67500 → move = (67500-67000)/67000*100 ≈ 0.746% > 0.6% → passes R10
    gate = _make_gate()
    decision = _buy_decision(stop_loss=66800.0, take_profit=67500.0)
    # sl_distance=200, atr_ref=300: 0.3*300=90 < 200 ✓, 1.5*300=450 > 200 ✓
    # reward=500, risk=200, R:R=2.5 > 1.3 ✓
    kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_ref": 300.0,
              "roundtrip_fee_pct": 0.2, "min_fees_to_tp_ratio": 3.0}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN passes all checks including R10
    assert verdict.passed is True


def test_r10_skipped_when_roundtrip_fee_zero():
    # GIVEN roundtrip_fee_pct=0 (testnet) — R10 must not apply
    gate = _make_gate()
    # Very small TP move: (67100-67000)/67000*100 ≈ 0.15% — would fail if R10 applied
    # sl_distance=200, atr_ref=300 ✓; reward=100, risk=200 → R:R=0.5 < 1.3 → fails R5
    # Use take_profit=67500 for valid R:R
    decision = _buy_decision(stop_loss=66800.0, take_profit=67500.0)
    kwargs = {**_COMMON_KWARGS, "current_price": 67000.0, "atr_ref": 300.0,
              "roundtrip_fee_pct": 0.0, "min_fees_to_tp_ratio": 3.0}

    # WHEN validated
    verdict = gate.validate(decision=decision, **kwargs)

    # THEN passes — R10 is skipped when fees are zero (testnet)
    assert verdict.passed is True
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_risk_gate.py -v
```

Expected: most tests `FAILED` with `TypeError: validate() got unexpected keyword argument 'atr_ref'`

- [ ] **Step 3: Implement — update `RiskGate.validate()` in `trading-engine/risk/risk_gate.py`**

Replace the entire `validate` method signature and body:

```python
    def validate(self, *, decision: DecisorOutput, current_price: float, atr_ref: float,
                 open_positions_count: int, daily_pnl_pct: float, total_drawdown_pct: float,
                 kill_switch: bool, usdt_balance: float, btc_held: float,
                 roundtrip_fee_pct: float = 0.0,
                 min_fees_to_tp_ratio: float = 3.0) -> RiskVerdict:
        # HOLD always passes
        if decision.action == DecisorAction.HOLD:
            return RiskVerdict(passed=True)

        # Total drawdown breach
        if total_drawdown_pct <= self.max_drawdown_pct:
            return RiskVerdict(False, f"max_drawdown breached: {total_drawdown_pct:.4f}")

        # Kill switch — only allow SELL to close
        if kill_switch:
            if decision.action == DecisorAction.SELL and btc_held > 0:
                return RiskVerdict(passed=True)
            return RiskVerdict(False, "kill_switch active — only SELL-to-close allowed")

        # SELL needs open position
        if decision.action == DecisorAction.SELL:
            if btc_held <= 0 or open_positions_count == 0:
                return RiskVerdict(False, "SELL requested but no open position to close")
            return RiskVerdict(passed=True)

        # BUY checks (R1–R10)
        if decision.stop_loss is None:
            return RiskVerdict(False, "BUY requires stop_loss")
        if decision.stop_loss >= current_price:
            return RiskVerdict(False, "stop_loss must be < current_price")
        if decision.position_size_pct > self.max_position_pct + 1e-9:
            return RiskVerdict(False, f"position_size_pct {decision.position_size_pct} > max {self.max_position_pct}")
        if open_positions_count >= self.max_simultaneous_trades:
            return RiskVerdict(False, f"max_simultaneous_trades reached: {open_positions_count}")
        if daily_pnl_pct <= self.daily_stop_pct:
            return RiskVerdict(False, f"daily P&L breach: {daily_pnl_pct:.4f}")
        sl_distance = current_price - decision.stop_loss
        sl_min = self.sl_atr_multiplier * atr_ref
        sl_max = self.sl_atr_max_multiplier * atr_ref
        if sl_distance < sl_min:
            return RiskVerdict(False, f"SL distance {sl_distance:.2f} < {self.sl_atr_multiplier}*ATR {sl_min:.2f}")
        if sl_distance > sl_max:
            return RiskVerdict(False, f"SL distance {sl_distance:.2f} > {self.sl_atr_max_multiplier}*ATR {sl_max:.2f}")
        if decision.take_profit is None:
            return RiskVerdict(False, "BUY requires take_profit")
        if decision.take_profit <= current_price:
            return RiskVerdict(False, "take_profit must be > current_price")
        reward = decision.take_profit - current_price
        if sl_distance > 0 and reward / sl_distance <= self.min_rr_ratio:
            return RiskVerdict(False, f"R:R ratio {reward/sl_distance:.2f} <= {self.min_rr_ratio}")

        # R10: TP move must cover round-trip fees (skipped in testnet where fees = 0)
        if roundtrip_fee_pct > 0:
            move_pct = (decision.take_profit - current_price) / current_price * 100
            min_move = min_fees_to_tp_ratio * roundtrip_fee_pct
            if move_pct < min_move:
                return RiskVerdict(
                    False,
                    f"R10: TP move ({move_pct:.3f}%) < {min_fees_to_tp_ratio}×fees ({min_move:.3f}%)",
                )

        return RiskVerdict(passed=True)
```

- [ ] **Step 4: Run all risk gate tests to confirm they pass**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_risk_gate.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/risk_gate.py trading-engine/tests/test_risk_gate.py
git commit -m "feat: rename atr_1h→atr_ref and add R10 fee-vs-TP validation to RiskGate"
```

---

## Task 6: ContextBuilder — new payload variables

**Files:**
- Modify: `trading-engine/agents/context_builder.py`
- Modify: `trading-engine/tests/test_context_builder.py`

New variables to add to the context dict:
- `atr_timeframe` (same as `atr_ref_tf`, explicit alias needed by new prompt)
- `sl_atr_max_multiplier` (currently only in calibration, needs explicit ctx entry)
- `volume_current` (from indicators JSONB, atr_timeframe candle)
- `volume_avg20` (from indicators JSONB, atr_timeframe candle)
- `volume_ratio` (volume_current / volume_avg20, 0 if avg20 is 0)
- `bid_wall_dist_pct` (from orderbook snapshot)
- `ask_wall_dist_pct` (from orderbook snapshot)
- `current_drawdown_pct` (new parameter)
- 6 new config vars: `min_fees_to_tp_ratio`, `min_confluences_buy`, `cooldown_after_sell_min`, `subjective_adj_max`, `expected_holding_max_min`, `confluence_weak_factor`

- [ ] **Step 1: Write the failing tests**

Add at the end of `trading-engine/tests/test_context_builder.py`:

```python
@pytest.mark.asyncio
async def test_volume_keys_are_present(session: AsyncSession):
    # GIVEN an Indicators row that includes volume data in the "1h" timeframe
    # (The fixture seeds "1h" data but not volume_current — need to add it)
    # First, update the seeded row to include volume_current in 1h
    from shared.db.models import Indicators
    from sqlalchemy import select, desc
    row = (await session.execute(select(Indicators).order_by(desc(Indicators.time)).limit(1))).scalar_one()
    row.data = {
        **row.data,
        "15m": {
            "last_close": 94950.0,
            "rsi": 52.0,
            "atr": 400.0,
            "volume_current": 3.5,
            "volume_avg_20": 2.0,
        },
    }
    await session.commit()

    # WHEN building the context with atr_timeframe="15m"
    ctx = await _build(session, atr_timeframe="15m")

    # THEN volume keys are present and correctly computed
    assert "volume_current" in ctx
    assert "volume_avg20" in ctx
    assert "volume_ratio" in ctx
    assert ctx["volume_current"] == pytest.approx(3.5)
    assert ctx["volume_avg20"] == pytest.approx(2.0)
    assert ctx["volume_ratio"] == pytest.approx(1.75)


@pytest.mark.asyncio
async def test_orderbook_wall_dist_pct_keys_present(session: AsyncSession):
    # GIVEN an orderbook snapshot where bid_wall_price=94000, ask_wall_price=96000, price=95000
    from collectors.orderbook_collector import OrderBookSnapshot
    ob = OrderBookSnapshot(
        spread=10.0, spread_pct=0.01,
        bid_total_btc=5.0, ask_total_btc=5.0,
        imbalance=1.0,
        bid_wall_price=94000.0, bid_wall_size=10.0, bid_wall_distance_pct=-1.053,
        ask_wall_price=96000.0, ask_wall_size=10.0, ask_wall_distance_pct=1.053,
        top_bid=94990.0, top_ask=95010.0,
    )

    # WHEN building the context
    ctx = await _build(session, orderbook=ob)

    # THEN bid/ask wall dist pct are present
    assert "bid_wall_dist_pct" in ctx
    assert "ask_wall_dist_pct" in ctx
    # bid_wall_dist_pct = (94000 - 95000) / 95000 * 100 ≈ -1.053
    assert ctx["bid_wall_dist_pct"] == pytest.approx(-1.053, abs=0.01)
    # ask_wall_dist_pct = (96000 - 95000) / 95000 * 100 ≈ 1.053
    assert ctx["ask_wall_dist_pct"] == pytest.approx(1.053, abs=0.01)


@pytest.mark.asyncio
async def test_current_drawdown_pct_passed_through(session: AsyncSession):
    # GIVEN current_drawdown_pct=-0.05 is passed in
    ctx = await _build(session, current_drawdown_pct=-0.05)

    # THEN it is present in the context
    assert ctx["current_drawdown_pct"] == pytest.approx(-0.05)


@pytest.mark.asyncio
async def test_atr_timeframe_key_in_context(session: AsyncSession):
    # GIVEN atr_timeframe="5m"
    ctx = await _build(session, atr_timeframe="5m")

    # THEN atr_timeframe key (not just atr_ref_tf) is present
    assert "atr_timeframe" in ctx
    assert ctx["atr_timeframe"] == "5m"
```

- [ ] **Step 2: Update `_build` helper to accept new params**

In the `_build` helper function in the test file, update the `defaults` dict to include the new parameters:

```python
async def _build(session: AsyncSession, **overrides) -> dict:
    builder = ContextBuilder(session, symbol="BTC/USDT")
    defaults = dict(
        orderbook=None,
        usdt_balance=1000.0,
        btc_held=0.0,
        playbook_content="# Playbook v0",
        max_simultaneous_trades=2,
        daily_stop_pct=0.02,
        decisor_interval_min=5,
        mode="PAPER_TRADING",
        taker_fee_pct=0.001,
        maker_fee_pct=0.001,
        current_drawdown_pct=0.0,
    )
    defaults.update(overrides)
    return await builder.build(**defaults)
```

- [ ] **Step 3: Run the tests to confirm they fail**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_context_builder.py -v -k "volume or wall_dist or drawdown or atr_timeframe"
```

Expected: `FAILED` — `TypeError: build() got an unexpected keyword argument 'current_drawdown_pct'`

- [ ] **Step 4: Implement — update `ContextBuilder.build()` in `trading-engine/agents/context_builder.py`**

**4a.** Add `current_drawdown_pct: float = 0.0` to the method signature (after `calibration`):

```python
    async def build(self, *, orderbook: OrderBookSnapshot | None, usdt_balance: float,
                    btc_held: float, playbook_content: str, max_position_pct: float = 0.10,
                    max_simultaneous_trades: int, daily_stop_pct: float,
                    decisor_interval_min: int, mode: str,
                    taker_fee_pct: float, maker_fee_pct: float,
                    atr_timeframe: str = "15m", min_rr_ratio: float = 1.3,
                    sl_atr_multiplier: float = 0.3,
                    calibration: dict | None = None,
                    current_drawdown_pct: float = 0.0) -> dict[str, Any]:
```

**4b.** After the `cal = calibration or {}` line, add:

```python
        sl_atr_max = cal.get("sl_atr_max_multiplier", 1.5)
        vol_tf = self._get(ind, atr_timeframe, "volume_current")
        vol_avg = self._get(ind, atr_timeframe, "volume_avg_20")
        volume_ratio = (vol_tf / vol_avg) if (vol_tf and vol_avg and vol_avg > 0) else 0.0
        bid_wall_dist_pct = (
            (orderbook.bid_wall_price - price) / price * 100
            if orderbook and price > 0 else 0.0
        )
        ask_wall_dist_pct = (
            (orderbook.ask_wall_price - price) / price * 100
            if orderbook and price > 0 else 0.0
        )
```

**4c.** In the `ctx = { ... }` dict, add the following entries (after the existing `"min_rr_ratio"` entry):

```python
            "atr_timeframe": atr_timeframe,
            "sl_atr_max_multiplier": sl_atr_max,
            "volume_current": vol_tf or 0.0,
            "volume_avg20": vol_avg or 0.0,
            "volume_ratio": volume_ratio,
            "bid_wall_dist_pct": bid_wall_dist_pct,
            "ask_wall_dist_pct": ask_wall_dist_pct,
            "current_drawdown_pct": current_drawdown_pct,
            # new decisor-v2 config vars (resolved from calibration if set, else defaults)
            "min_fees_to_tp_ratio": cal.get("min_fees_to_tp_ratio", 3.0),
            "min_confluences_buy": cal.get("min_confluences_buy", 2),
            "cooldown_after_sell_min": cal.get("cooldown_after_sell_min", 15),
            "subjective_adj_max": cal.get("subjective_adj_max", 0.10),
            "expected_holding_max_min": cal.get("expected_holding_max_min", 240),
            "confluence_weak_factor": cal.get("confluence_weak_factor", 0.5),
```

**Note:** The `sl_atr_max` variable was already computed earlier as `cal.get("sl_atr_max_multiplier", 1.5)` — confirm the assignment at the top of `build()` is `sl_atr_max = cal.get("sl_atr_max_multiplier", 1.5)` (it already exists; just ensure the `ctx` entry `"sl_atr_max_multiplier"` uses it).

- [ ] **Step 5: Run all context_builder tests**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/test_context_builder.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add trading-engine/agents/context_builder.py trading-engine/tests/test_context_builder.py
git commit -m "feat: add volume, drawdown, wall-dist-pct, and 6 v2 config vars to ContextBuilder"
```

---

## Task 7: Wire everything — Decisor + main.py

**Files:**
- Modify: `trading-engine/agents/decisor.py`
- Modify: `trading-engine/main.py`

- [ ] **Step 1: Update `Decisor.decide()` to accept and pass `current_drawdown_pct`**

In `trading-engine/agents/decisor.py`, add `current_drawdown_pct: float = 0.0` to the `decide()` signature:

```python
    async def decide(self, *, orderbook: OrderBookSnapshot | None, usdt_balance: float,
                     btc_held: float, max_position_pct: float, max_simultaneous_trades: int,
                     daily_stop_pct: float, decisor_interval_min: int, mode: str,
                     taker_fee: float, maker_fee: float,
                     atr_timeframe: str = "15m", min_rr_ratio: float = 1.3,
                     sl_atr_multiplier: float = 0.3,
                     calibration: dict | None = None,
                     current_drawdown_pct: float = 0.0) -> DecisorOutput:
```

Pass `current_drawdown_pct` to `context_builder.build()`:

```python
        ctx = await self.context_builder.build(
            orderbook=orderbook, usdt_balance=usdt_balance, btc_held=btc_held,
            playbook_content=playbook_content, max_position_pct=max_position_pct,
            max_simultaneous_trades=max_simultaneous_trades,
            daily_stop_pct=daily_stop_pct, decisor_interval_min=decisor_interval_min,
            mode=mode, taker_fee_pct=taker_fee, maker_fee_pct=maker_fee,
            atr_timeframe=atr_timeframe, min_rr_ratio=min_rr_ratio,
            sl_atr_multiplier=sl_atr_multiplier, calibration=calibration,
            current_drawdown_pct=current_drawdown_pct,
        )
```

- [ ] **Step 2: Update `gate.validate()` call in `trading-engine/main.py`**

Find the call at line ~213 and update it to use `atr_ref` and pass the fee params:

```python
            verdict = gate.validate(
                decision=decision, current_price=current_price, atr_ref=atr,
                open_positions_count=open_count, daily_pnl_pct=0.0,
                total_drawdown_pct=0.0, kill_switch=kill,
                usdt_balance=usdt, btc_held=btc,
                roundtrip_fee_pct=fees.taker * 2 * 100,
                min_fees_to_tp_ratio=float(calibration.get("min_fees_to_tp_ratio", 3.0)),
            )
```

- [ ] **Step 3: Run the full test suite**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 4: Commit**

```bash
git add trading-engine/agents/decisor.py trading-engine/main.py
git commit -m "feat: wire current_drawdown_pct, atr_ref, and fee params through decisor and main loop"
```

---

## Task 8: Replace prompts — system + user

**Files:**
- Replace: `trading-engine/agents/prompts/decisor_system.txt`
- Modify: `trading-engine/agents/prompts/decisor_user.txt`

- [ ] **Step 1: Replace `decisor_system.txt` with the new v2 prompt**

Write the following content to `trading-engine/agents/prompts/decisor_system.txt`:

```
Eres un agente cuantitativo de day trading especializado exclusivamente
en BTC/USDT en Binance Spot. Tu único objetivo es maximizar el P&L
ajustado por riesgo (Sharpe ratio) en horizontes de minutos a horas.

NO eres un asistente. NO das opiniones. NO operás otros activos.
Tu output es UNA decisión estructurada por ciclo, basada en evidencia.

═══════════════════════════════════════════════════════════════════
CONTEXTO OPERATIVO
═══════════════════════════════════════════════════════════════════
Modo actual: {mode}
Capital total: ${capital_total} USDT
Capital disponible: ${usdt_available} USDT
BTC en posición: {btc_held} BTC
Frecuencia de decisión: cada {decisor_interval_min} minutos

Fees actuales de tu cuenta Binance:
  Taker: {taker_fee_pct:.4f}%   Maker: {maker_fee_pct:.4f}%
  Round-trip (apertura + cierre como taker): {roundtrip_fee_pct:.4f}%
  [Si roundtrip_fee_pct == 0 estás en testnet — R10 no aplica]

ATR DE REFERENCIA (timeframe {atr_timeframe}): ${atr_ref:.0f} ({atr_ref_pct:.2f}% del precio)
  Rango SL permitido: ${atr_ref_min:.0f} – ${atr_ref_max:.0f}
R:R MINIMO CONFIGURADO: {min_rr_ratio}:1

═══════════════════════════════════════════════════════════════════
PERFIL DE OPERACION (auto-derivado de config)
═══════════════════════════════════════════════════════════════════
Frecuencia {decisor_interval_min}m + ATR {atr_timeframe} →
  • ≤ 10m / ATR 5m       → SCALPING
      Holding esperado: 10–60 min
      Priorizar confluencias: A (momentum), E (orderbook), F (volatilidad)
  • 15–30m / ATR 15m     → HIBRIDO
      Holding esperado: 30 min – 3 h
      Priorizar confluencias: A, C (estructura), G (soporte)
  • > 30m / ATR 1h       → DAY_TRADING
      Holding esperado: 1 – 8 h
      Priorizar confluencias: B (tendencia 1h), C, G

El campo "expected_holding_min" del output debe ser coherente con este perfil.
Holding máximo permitido: {expected_holding_max_min} minutos.

═══════════════════════════════════════════════════════════════════
JERARQUIA DE DECISION (en caso de conflicto, prevalece el orden)
═══════════════════════════════════════════════════════════════════
1. REGLAS ABSOLUTAS R1–R10 (verificadas por Risk Gate — no negociables)
2. Parámetros del sistema (umbrales, multiplicadores, factores)
3. Playbook activo (guía de comportamiento, NO reemplaza parámetros)
4. Confluencias técnicas del ciclo actual
Si el playbook contradice un parámetro del sistema, prevalece el sistema.

REGLA DE CONSISTENCIA (OBLIGATORIA):
Los parámetros del sistema son la única fuente de verdad para valores numéricos de riesgo.
Si el playbook especifica un umbral que difiere del sistema:
- IGNORÁ el valor del playbook. Usá siempre el valor inyectado en este prompt.
- Anotalo en el reasoning: "[DRIFT CONFIG] Playbook dice X, sistema usa Y — usando sistema."

═══════════════════════════════════════════════════════════════════
REGLAS ABSOLUTAS (el Risk Gate las verifica)
═══════════════════════════════════════════════════════════════════
R1.  position_size_pct máximo: {max_position_pct}
R2.  action=BUY requiere stop_loss OBLIGATORIO < precio_actual
R3.  action=BUY requiere take_profit OBLIGATORIO > precio_actual
     (null = rechazo automático)
R4.  action=BUY: distancia_SL entre {sl_atr_multiplier}x y {sl_atr_max_multiplier}x ATR({atr_timeframe})
     ATR({atr_timeframe}) este ciclo = ${atr_ref:.0f} ({atr_ref_pct:.2f}% del precio)
     Mínimo SL: ${atr_ref_min:.0f} ({sl_atr_multiplier}x ATR) | Máximo SL: ${atr_ref_max:.0f} ({sl_atr_max_multiplier}x ATR)
     Coloca el SL justo debajo del soporte técnico más cercano dentro del rango.
     SL fuera del rango = trade mal dimensionado, rechazarlo internamente y devolver HOLD.
R5.  action=BUY requiere R:R mínimo {min_rr_ratio}:1
     TP base = precio_actual + (distancia_SL × {min_rr_ratio})
     Ajustar TP al nivel de resistencia técnica más cercano dentro del rango.
R6.  action=SELL solo válido si hay posición LONG abierta
R7.  NUNCA shortear (mercado SPOT)
R8.  Si open_positions >= {max_simultaneous_trades}: solo HOLD o SELL
R9.  Si daily_pnl_pct <= {daily_stop_pct}: HOLD forzado
R10. action=BUY requiere que el movimiento esperado al TP cubra los fees:
     (take_profit - precio_actual) / precio_actual × 100 >= {min_fees_to_tp_ratio} × {roundtrip_fee_pct:.4f}%
     [Si roundtrip_fee_pct == 0 → R10 no aplica]

═══════════════════════════════════════════════════════════════════
CATALOGO DE CONFLUENCIAS (usar exactamente estos IDs)
═══════════════════════════════════════════════════════════════════
A_solida  | Momentum alcista 15m: MACD cruce + RSI < 60
A_debil   | Momentum alcista 15m: solo RSI
B_solida  | Tendencia alcista 1h: precio > EMA50_1h + MACD 1h positivo
B_debil   | Tendencia alcista 1h: precio > EMA50_1h solamente
C_solida  | Estructura: mínimos crecientes confirmados (2+ velas)
C_debil   | Estructura: un solo mínimo creciente
D_solida  | Volumen: volume_ratio > {adj_volume_ratio}x media reciente
D_debil   | Volumen: volume_ratio entre 1.2x y {adj_volume_ratio}x
E_solida  | Order book: imbalance > 1.3 (presión compradora fuerte)
E_debil   | Order book: imbalance 1.1–1.3
F_solida  | Volatilidad: ATR expanding (ATR actual > ATR avg 7d × 1.1)
F_debil   | Volatilidad: ATR moderado (0.9–1.1 × ATR avg 7d)
G_solida  | Soporte técnico: precio en EMA20/50 o BB_lower ± 0.3%
G_debil   | Soporte técnico: cerca del soporte pero sin tocar
H_solida  | Timeframes alineados: 1h, 15m y 5m todos alcistas
H_debil   | Timeframes alineados: solo 1h y 15m alcistas

Mínimo de confluencias para BUY: {min_confluences_buy}
Factor para confluencia débil en el cálculo: {confluence_weak_factor}x vs confluencia sólida

═══════════════════════════════════════════════════════════════════
REGIMEN DE MERCADO → ACCION ESPERADA
═══════════════════════════════════════════════════════════════════
TRENDING_UP     → BUY permitido con {min_confluences_buy}+ confluencias. Tamaño completo.
RANGE           → BUY solo cerca de soporte claro. Tamaño {factor_regime_non_trending:.0%} del máximo.
TRENDING_DOWN   → BUY bloqueado. Solo HOLD o SELL para cerrar posiciones.
HIGH_VOLATILITY → BUY con tamaño {factor_regime_non_trending:.0%}. SL amplio (cerca de {sl_atr_max_multiplier}x ATR).

JERARQUIA DE TIMEFRAMES (conflictos entre señales):
- Timeframe mayor tiene precedencia: 1h manda sobre 15m, 15m sobre 5m.
- MACD 1h negativo + MACD 15m positivo = rebote temporal en tendencia bajista → HOLD.
- Entrada solo válida si 1h y 15m coinciden en dirección.
- RSI 1h en sobrecompra (>{rsi_overbought_1h}) cancela señales alcistas de timeframes menores.

═══════════════════════════════════════════════════════════════════
PLAYBOOK ACTIVO
═══════════════════════════════════════════════════════════════════
{playbook}

═══════════════════════════════════════════════════════════════════
CRITERIOS DE SELL ANTICIPADO
═══════════════════════════════════════════════════════════════════
- Regime cambia a TRENDING_DOWN o HIGH_VOLATILITY adverso tras la entrada.
- Breakdown confirmado de soporte clave que invalida la tesis de la entrada.
- Divergencia bajista clara en RSI 1h.
Si ninguno aplica, NO hacer SELL — dejar que SL/TP hagan su trabajo.

ANTI-PATRONES:
- Overtrading: no forzar entrada cuando los últimos ciclos fueron HOLD válido
- FOMO en breakouts sin volumen
- Promediar a la baja
- Entrar en RANGE sin nivel de soporte claro
- Confiar en un solo timeframe o en señales de 5m contra tendencia de 1h

═══════════════════════════════════════════════════════════════════
CALIBRACION DE CONFIANZA (formula explícita)
═══════════════════════════════════════════════════════════════════
confidence_base = Σ(peso_confluencia_i) × peso_timeframe × peso_regime

peso de cada confluencia:
  sólida → 1.0 | débil → {confluence_weak_factor}
  normalizado sobre tabla de {min_confluences_buy}+ confluencias:
    0 → {conf_base_0} | 1 → {conf_base_1} | 2 → {conf_base_2} | 3 → {conf_base_3} | 4+ → {conf_base_4plus}

peso_timeframe (alineación temporal):
  1h y 15m alineados → 1.00
  solo 15m alineado → {peso_timeframe_partial}
  solo 5m alineado → {peso_timeframe_minimal}

peso_regime:
  TRENDING_UP → 1.00 | RANGE → {peso_regime_range} | HIGH_VOLATILITY → {peso_regime_high_vol}
  TRENDING_DOWN → BUY bloqueado (devolver HOLD directo)

ajustes adicionales:
  +{adj_volume_boost} si volume_ratio > {adj_volume_ratio}x
  {adj_antipattern_penalty} si hay anti-patrón presente
  {adj_spread_penalty} si spread > {adj_spread_threshold_pct}% del precio
  {adj_orderbook_penalty} si bid_wall > {adj_orderbook_ratio}x ask_wall en zona contraria

confidence_adjustment = ajuste subjetivo basado en contexto no modelado
  Rango permitido: [-{subjective_adj_max}, +{subjective_adj_max}]
  Justificarlo explícitamente en reasoning.

confidence = clamp(confidence_base + confidence_adjustment, 0.0, 1.0)

UMBRAL MINIMO PARA BUY (según regime):
  TRENDING_UP     → confidence >= {conf_threshold_trending_up}
  RANGE           → confidence >= {conf_threshold_range}
  HIGH_VOLATILITY → confidence >= {conf_threshold_high_vol}
Si confidence < umbral → action = HOLD obligatorio.
(El sistema re-verifica y fuerza HOLD si no se cumple.)

═══════════════════════════════════════════════════════════════════
OUTPUT — JSON EXACTO, sin texto extra
═══════════════════════════════════════════════════════════════════
{
  "regime": "TRENDING_UP" | "TRENDING_DOWN" | "RANGE" | "HIGH_VOLATILITY",
  "confluences": ["A_solida", "C_debil", ...],
  "action": "BUY" | "SELL" | "HOLD",
  "confidence_base": float 0.0–1.0  (resultado de la fórmula antes del ajuste),
  "confidence_adjustment": float dentro de [-{subjective_adj_max}, +{subjective_adj_max}],
  "confidence": float 0.0–1.0  (= clamp(confidence_base + confidence_adjustment, 0, 1)),
  "stop_loss": float (OBLIGATORIO si action=BUY, null si HOLD/SELL),
  "take_profit": float (OBLIGATORIO si action=BUY, null si HOLD/SELL),
  "position_size_pct": float calculado con fórmula determinista:
      position_size_pct = {max_position_pct} × factor_conf × factor_regime
      factor_conf:   conf 0.60–0.69 → {factor_conf_60} | 0.70–0.79 → {factor_conf_70} | 0.80–0.89 → {factor_conf_80} | 0.90+ → {factor_conf_90}
      factor_regime: TRENDING_UP → 1.00 | RANGE/HIGH_VOLATILITY → {factor_regime_non_trending}
      Para HOLD/SELL → 0.0
  "expected_holding_min": int >= 1  (minutos esperados hasta SL/TP o cierre activo),
  "reasoning": "Español. Max 800 chars. Formato:
      - Régimen: [regime y por qué]
      - Confluencias: [lista con IDs del catálogo y descripción breve]
      - Cálculo: [base=X×Y×Z=A | adj=±B | confidence=C | vs umbral=D → acción]
      - Conclusión: [1 frase clara]
      REGLA: Desglosar la fórmula paso a paso. No abreviar."
}
```

- [ ] **Step 2: Update `decisor_user.txt` with new variables**

Replace the entire content of `trading-engine/agents/prompts/decisor_user.txt`:

```
=== CICLO DE DECISION — {timestamp_utc} UTC ===

PRECIO BTC/USDT
  Actual: ${price:,.2f}
  Cambio: 1h {pct_1h:+.2f}% | 4h {pct_4h:+.2f}% | 24h {pct_24h:+.2f}%

INDICADORES TECNICOS
  5m:  RSI {rsi_5m:.0f} | BB% {bb_pct_5m:.0f}
  15m: RSI {rsi_15m:.0f} | MACD {macd_15m:+.1f}/{sig_15m:+.1f} hist {hist_15m:+.1f}
  1h:  RSI {rsi_1h:.0f} | MACD {macd_1h:+.1f}/{sig_1h:+.1f}
       EMA20 {ema20_1h:,.0f} EMA50 {ema50_1h:,.0f} EMA200 {ema200_1h:,.0f}
  4h:  RSI {rsi_4h:.0f} | EMA20 {ema20_4h:,.0f} EMA50 {ema50_4h:,.0f}
  ATR({atr_timeframe}) [referencia SL/TP]: ${atr_ref:.0f} ({atr_ref_pct:.2f}%)

VOLUMEN ({atr_timeframe})
  Vela actual: {volume_current:.3f} BTC | Promedio 20 velas: {volume_avg20:.3f} BTC | Ratio: {volume_ratio:.2f}x

ORDER BOOK
  Spread: ${spread:.2f} ({spread_pct:.4f}%)
  Imbalance: {imbalance:.2f} ({imbalance_label})
  Bid wall: ${bid_wall_price:,.0f} ({bid_wall_size:.1f} BTC) | Distancia: {bid_wall_dist_pct:+.2f}%
  Ask wall: ${ask_wall_price:,.0f} ({ask_wall_size:.1f} BTC) | Distancia: {ask_wall_dist_pct:+.2f}%

POSICIONES ABIERTAS: {open_positions_count} | LIMITE DEL SISTEMA: {max_simultaneous_trades}
{positions_block}

BALANCE
  USDT: ${usdt_available:,.2f} | BTC: {btc_held:.6f}

P&L HOY: ${pnl_today_usd:+,.2f} ({pnl_today_pct:+.2f}%) | Stop en: {daily_stop_pct}%
Drawdown actual: {current_drawdown_pct:+.2f}%

ULTIMAS DECISIONES
{last_decisions_block}

=== Decide ahora con el JSON exacto. ===
```

- [ ] **Step 3: Run the full test suite one more time**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -m pytest tests/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 4: Smoke-test prompt rendering (no DB needed)**

```bash
cd /Users/mfariasfalki/project/crypto-ai-trading/trading-engine
python -c "
from agents.prompt_manager import PromptManager
import asyncio

async def check():
    # Verify system prompt loads without import errors
    pm = PromptManager.__new__(PromptManager)
    sys = pm.load_system_prompt('decisor')
    usr = pm.load_user_template('decisor')
    print('system prompt lines:', len(sys.splitlines()))
    print('user template lines:', len(usr.splitlines()))
    print('atr_timeframe in system:', '{atr_timeframe}' in sys)
    print('confidence_base in system:', 'confidence_base' in sys)
    print('expected_holding_min in system:', 'expected_holding_min' in sys)
    print('volume_current in user:', 'volume_current' in usr)
    print('current_drawdown_pct in user:', 'current_drawdown_pct' in usr)
    print('bid_wall_dist_pct in user:', 'bid_wall_dist_pct' in usr)

asyncio.run(check())
"
```

Expected output:
```
system prompt lines: <number>
user template lines: <number>
atr_timeframe in system: True
confidence_base in system: True
expected_holding_min in system: True
volume_current in user: True
current_drawdown_pct in user: True
bid_wall_dist_pct in user: True
```

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/prompts/decisor_system.txt trading-engine/agents/prompts/decisor_user.txt
git commit -m "feat: replace decisor prompt with v2 — perfil operativo, catálogo confluencias, R10, campos confidence_base/adjustment/expected_holding_min"
```

---

## Self-Review

### Spec Coverage

| Spec requirement | Task that implements it |
|---|---|
| 6 nuevas configs en tabla `config` | Task 2 (enum) + Task 3 (migration) |
| Migración SQL para configs | Task 3 |
| 3 campos nuevos en DecisorOutput | Task 1 |
| Validación R10 fees vs TP en Risk Gate | Task 5 |
| ATR parametrizado por `atr_timeframe` en prompt | Task 8 (prompt uses `{atr_timeframe}` not hardcoded "1h") |
| `volume_current`, `volume_avg20`, `volume_ratio` en payload | Task 4 (indicators) + Task 6 (context) |
| `bid_wall_dist_pct`, `ask_wall_dist_pct` | Task 6 (context) + Task 8 (user prompt) |
| `current_drawdown_pct` | Task 6 (context) + Task 7 (decisor param) + Task 8 (user prompt) |
| Reemplazar prompt completo | Task 8 |
| `atr_ref_min`, `atr_ref_max` en sistema prompt | Task 8 (present in `{atr_ref_min:.0f}` / `{atr_ref_max:.0f}`) |
| `min_fees_to_tp_ratio` en gate.validate() call | Task 7 (main.py) |

### Placeholder Scan

No TBDs, TODOs, or "similar to Task N" in this plan. All code blocks are complete.

### Type Consistency

- `atr_ref` is used consistently in `risk_gate.py` validate() signature, `_COMMON_KWARGS` test dict, and `main.py` call.
- `current_drawdown_pct: float = 0.0` matches across `ContextBuilder.build()`, `Decisor.decide()`, and tests.
- `confidence_base`, `confidence_adjustment`, `expected_holding_min` field names match across `DecisorOutput`, `_hold_decision()`, and the test.
- `volume_current` key from `indicators.py` matches what `ContextBuilder` reads via `self._get(ind, atr_timeframe, "volume_current")`.
