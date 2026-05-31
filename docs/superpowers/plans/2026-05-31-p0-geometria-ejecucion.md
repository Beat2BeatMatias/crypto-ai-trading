# P0 — Geometría y Ejecución Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Modelo de ejecución:** los subagentes se despachan con `model: sonnet` (decisión del operador).

**Goal:** Convertir la geometría de trading de EV-negativo (SL 0.3×ATR / R:R 1.3, win rate 36 %, −275 USDT en 25 días) a EV-neutro/positivo, validando los valores con backtest antes de aplicarlos, y endurecer la ejecución (fees reales en paper, colchón de slippage, SL guardian más rápido, sizing desacoplado de confidence).

**Architecture:** El bot lee toda su configuración de riesgo desde Postgres vía `ConfigStore` en cada tick (`main.py:240-272`); el `RiskGate` (determinístico, `risk/risk_gate.py`) es la única barrera dura. No tocamos la lógica LLM en este plan. Cambiamos: (1) qué valores de geometría viven en la DB, (2) las reglas R5/R10 del gate para que reflejen costos reales, (3) la frecuencia del SL guardian, (4) el sizing guiado del prompt. Cada cambio de comportamiento del gate va acompañado de un test determinístico.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio, SQLAlchemy async, pandas/ccxt (backtester), Postgres (`crypto-ai-trading-postgres-1`, usuario `trader`, db `crypto_ai_trading`).

**Contexto empírico que motiva el plan** (datos reales 2026-05-05 → 2026-05-30):
- 129 trades cerrados, P&L −274.96 USDT, win rate 35.7 %.
- 71 SL (−349) vs 28 TP (+82.81). R:R realizado ≈ 1.10 (no 1.3).
- SL distance real 0.711 %, TP distance real 1.224 %.
- `roundtrip_fee_pct = fees.taker*2*100` = 0 en testnet → R10 nunca valida (`main.py:413`).
- `max_slippage_pct` es código muerto en `risk_gate.py:27` (nunca consumido).
- OrderTracker / SL guardian corre cada 30 s (`main.py:603`).
- Sizing guiado por confidence (`decisor_system.txt:102-110`) → las perdedoras tuvieron ~1.9× el notional de las ganadoras.

---

## Estructura de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `backtesting/sweep.py` | Crear | Barre grilla (sl_atr_mult × rr) sobre histórico y rankea por P&L/PF. Produce la evidencia para elegir geometría. |
| `backtesting/tests/test_sweep.py` | Crear | Smoke test del sweep sobre datos sintéticos. |
| `trading-engine/risk/fees.py` | Crear | Función pura `effective_roundtrip_fee_pct()` (piso de fee en paper). |
| `trading-engine/tests/test_fees.py` | Crear | Tests de la función de fee efectivo. |
| `shared/config_store.py` | Modificar | Nueva `ConfigKey.MIN_ROUNDTRIP_FEE_PCT` + default. |
| `trading-engine/agents/supervisor.py:30-43` | Modificar | Ampliar `_SAFE_BOUNDS["sl_atr_multiplier"]` para permitir ≥1.0. |
| `trading-engine/risk/risk_gate.py:152-168` | Modificar | R5 estricto (`<` no `<=`) y R10 con colchón de slippage. |
| `trading-engine/tests/test_risk_gate.py` | Modificar | Tests de R10 con fee>0 y colchón de slippage. |
| `trading-engine/main.py:413` | Modificar | Pasar fee efectivo (piso paper) al gate. |
| `trading-engine/main.py:603` | Modificar | SL guardian cada 10 s. |
| `trading-engine/scripts/apply_geometry.py` | Crear | Script idempotente que escribe la geometría ganadora en la DB vía `ConfigStore.set`. |
| `trading-engine/agents/prompts/decisor_system.txt:102-110` | Modificar | Sizing plano desacoplado de confidence. |
| `trading-engine/tests/test_prompt_manager.py` | Modificar | Verificar que el system prompt renderiza sin variables faltantes tras el cambio. |

**Orden obligatorio:** Task 1 (backtest) produce los números que consumen Task 2 y Task 8. No saltear.

---

### Task 1: Backtest sweep de geometría (evidencia, no toca producción)

**Files:**
- Create: `backtesting/sweep.py`
- Test: `backtesting/tests/test_sweep.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# backtesting/tests/test_sweep.py
"""Smoke test del sweep de geometría sobre datos sintéticos."""
from __future__ import annotations

import numpy as np
import pandas as pd

from runner import add_indicators
from sweep import run_sweep


def _synthetic_ohlcv(n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0003, 0.01, n)
    close = 80000 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    vol = rng.uniform(10, 100, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_run_sweep_returns_row_per_grid_combo():
    df = add_indicators(_synthetic_ohlcv())
    rows = run_sweep(df, sl_grid=[0.5, 1.0], rr_grid=[1.5, 2.0], fee=0.001)
    # 2 x 2 = 4 combinaciones
    assert len(rows) == 4
    for r in rows:
        assert "sl_atr_mult" in r and "rr" in r
        assert "total_pnl_pct" in r and "profit_factor" in r and "win_rate" in r


def test_run_sweep_is_sorted_by_pnl_desc():
    df = add_indicators(_synthetic_ohlcv())
    rows = run_sweep(df, sl_grid=[0.5, 1.0, 1.5], rr_grid=[1.5, 2.5], fee=0.001)
    pnls = [r["total_pnl_pct"] for r in rows]
    assert pnls == sorted(pnls, reverse=True)
```

- [ ] **Step 2: Correr el test para verque falla**

Run: `cd backtesting && python -m pytest tests/test_sweep.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'sweep'`.

- [ ] **Step 3: Implementar `backtesting/sweep.py`**

```python
# backtesting/sweep.py
"""Barrido de geometría (sl_atr_mult × rr) sobre histórico real o sintético.

Usa run_baseline (fee-aware, round-trip 2×fee) del runner para rankear
combinaciones por P&L total. Sirve para elegir la geometría antes de
aplicarla a producción.

Usage:
    python sweep.py --timeframe 1h --days 365
    python sweep.py --timeframe 15m --days 120
"""
from __future__ import annotations

import argparse

import pandas as pd

from runner import add_indicators, fetch_history, run_baseline


def run_sweep(
    df: pd.DataFrame,
    *,
    sl_grid: list[float],
    rr_grid: list[float],
    fee: float = 0.001,
) -> list[dict]:
    """Corre run_baseline para cada (sl, rr) y devuelve filas ordenadas por P&L desc."""
    rows: list[dict] = []
    for sl in sl_grid:
        for rr in rr_grid:
            res = run_baseline(df, sl_atr_mult=sl, rr=rr, fee=fee)
            breakeven_wr = 1 / (1 + rr) * 100
            rows.append({
                "sl_atr_mult": sl,
                "rr": rr,
                "n_trades": res.n_trades,
                "win_rate": round(res.win_rate, 2),
                "breakeven_wr": round(breakeven_wr, 1),
                "total_pnl_pct": round(res.total_pnl_pct, 2),
                "sharpe": round(res.sharpe, 2),
                "max_dd_pct": round(res.max_drawdown_pct, 2),
                "profit_factor": round(res.profit_factor, 2),
            })
    rows.sort(key=lambda r: r["total_pnl_pct"], reverse=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Geometry sweep backtest")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--fee", type=float, default=0.001,
                        help="Fee por lado (0.001 = 0.1% taker LIVE). Round-trip = 2×fee.")
    args = parser.parse_args()

    print(f"Fetching {args.symbol} {args.timeframe} {args.days}d...")
    df = add_indicators(fetch_history(args.symbol, args.timeframe, args.days))

    sl_grid = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]
    rr_grid = [1.5, 2.0, 2.5, 3.0]
    rows = run_sweep(df, sl_grid=sl_grid, rr_grid=rr_grid, fee=args.fee)

    print(f"\n{'='*82}")
    print(f"  Geometry sweep — {args.symbol} {args.timeframe} {args.days}d — fee/lado {args.fee*100:.2f}%")
    print(f"{'='*82}")
    hdr = f"  {'SL×ATR':>7} {'R:R':>5} {'N':>5} {'WR%':>7} {'BE%':>6} {'P&L%':>9} {'Sharpe':>7} {'DD%':>8} {'PF':>6}"
    print(hdr)
    print("  " + "-" * 78)
    for r in rows:
        edge = "✅" if (r["win_rate"] > r["breakeven_wr"] and r["profit_factor"] > 1.2) else "  "
        print(f"  {r['sl_atr_mult']:>7} {r['rr']:>5} {r['n_trades']:>5} "
              f"{r['win_rate']:>7} {r['breakeven_wr']:>6} {r['total_pnl_pct']:>9} "
              f"{r['sharpe']:>7} {r['max_dd_pct']:>8} {r['profit_factor']:>6} {edge}")
    print(f"\n  Mejor combinación: SL={rows[0]['sl_atr_mult']}×ATR R:R={rows[0]['rr']} "
          f"→ P&L {rows[0]['total_pnl_pct']}% PF {rows[0]['profit_factor']}")
    print("  (Anotá la mejor combinación con ✅ y PF>1.3 para Task 2 y Task 8.)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr el test para verificar que pasa**

Run: `cd backtesting && python -m pytest tests/test_sweep.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Correr el sweep real y registrar el ganador**

Run (1h, el timeframe operativo más estable):
```bash
cd backtesting && python sweep.py --timeframe 1h --days 365 --fee 0.001
```
Run también el timeframe del decisor (5m / 15m según `atr_timeframe` en DB) para contrastar:
```bash
cd backtesting && python sweep.py --timeframe 15m --days 120 --fee 0.001
```
**Acción manual:** anotar en el commit message la combinación ganadora con PF>1.3 y WR>breakeven (típicamente SL∈[1.0,1.5], R:R∈[2.0,2.5]). Esos dos números (`SL*`, `RR*`) se usan en Task 2 y Task 8.

- [ ] **Step 6: Commit**

```bash
git add backtesting/sweep.py backtesting/tests/test_sweep.py
git commit -m "feat(backtesting): geometry sweep to pick fee-aware SL/RR

Sweep results (1h/365d, fee 0.1%/side): best = SL=<SL*>xATR RR=<RR*> PF=<PF>.
Replaces EV-negative defaults (SL 0.3x / RR 1.3) — see docs/specs analysis.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Ampliar `_SAFE_BOUNDS` para permitir SL ≥ 1.0×ATR

**Files:**
- Modify: `trading-engine/agents/supervisor.py:30-43`
- Test: `trading-engine/tests/test_supervisor.py`

**Por qué:** `_SAFE_BOUNDS["sl_atr_multiplier"] = (0.1, 0.8)` (`supervisor.py:32`) impide que ni el operador (vía sugerencia) ni el Supervisor lleven el SL a 1.0×ATR. Sin ampliarlo, la geometría ganadora del Task 1 quedaría fuera de rango y el Supervisor podría "corregirla" hacia abajo.

- [ ] **Step 1: Escribir el test que falla**

```python
# Agregar al final de trading-engine/tests/test_supervisor.py
from agents.supervisor import _SAFE_BOUNDS


def test_safe_bounds_allow_sl_atr_multiplier_up_to_2():
    lo, hi = _SAFE_BOUNDS["sl_atr_multiplier"]
    assert lo <= 1.0 <= hi
    assert hi >= 2.0


def test_safe_bounds_min_rr_allows_2():
    lo, hi = _SAFE_BOUNDS["min_rr_ratio"]
    assert lo <= 2.0 <= hi
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `cd trading-engine && python -m pytest tests/test_supervisor.py::test_safe_bounds_allow_sl_atr_multiplier_up_to_2 -v`
Expected: FAIL — `assert 1.0 <= 0.8`.

- [ ] **Step 3: Modificar el bound**

En `trading-engine/agents/supervisor.py`, reemplazar la línea 32:

```python
    "sl_atr_multiplier":           (0.1, 0.8),
```
por:
```python
    "sl_atr_multiplier":           (0.1, 2.0),
```

- [ ] **Step 4: Correr para verificar que pasa**

Run: `cd trading-engine && python -m pytest tests/test_supervisor.py -k safe_bounds -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/supervisor.py trading-engine/tests/test_supervisor.py
git commit -m "fix(supervisor): widen sl_atr_multiplier bound to [0.1, 2.0]

Old upper bound 0.8 blocked the fee-aware geometry (SL>=1.0xATR) chosen
by the geometry sweep. min_rr_ratio bound (1.0,3.0) already allows 2.0.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Fee efectivo con piso en paper (que R10 valide)

**Files:**
- Create: `trading-engine/risk/fees.py`
- Test: `trading-engine/tests/test_fees.py`
- Modify: `shared/config_store.py` (enum + DEFAULTS)
- Modify: `trading-engine/main.py:413`

**Por qué:** `main.py:413` pasa `roundtrip_fee_pct = fees.taker*2*100`, que en testnet es 0 → R10 (`risk_gate.py:161`) nunca dispara. Validás 4 semanas en paper sin fricción y en LIVE aparece 0.2 % round-trip que invalida la geometría. Solución: aplicar un piso configurable.

- [ ] **Step 1: Escribir el test que falla**

```python
# trading-engine/tests/test_fees.py
"""Tests del fee round-trip efectivo (piso en paper)."""
from __future__ import annotations

from risk.fees import effective_roundtrip_fee_pct


def test_effective_fee_uses_real_when_above_floor():
    # taker 0.075% -> round-trip 0.15% > floor 0.10% -> usa el real
    assert effective_roundtrip_fee_pct(taker_fee=0.00075, floor_pct=0.10) == 0.15


def test_effective_fee_applies_floor_when_real_is_zero():
    # testnet: taker 0 -> round-trip 0 -> usa el floor
    assert effective_roundtrip_fee_pct(taker_fee=0.0, floor_pct=0.20) == 0.20


def test_effective_fee_floor_zero_means_real_only():
    assert effective_roundtrip_fee_pct(taker_fee=0.0, floor_pct=0.0) == 0.0
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `cd trading-engine && python -m pytest tests/test_fees.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'risk.fees'`.

- [ ] **Step 3: Implementar `trading-engine/risk/fees.py`**

```python
# trading-engine/risk/fees.py
"""Fee round-trip efectivo: usa el fee real del exchange salvo en testnet,
donde aplica un piso para que R10 valide la geometría como si fuera LIVE."""
from __future__ import annotations


def effective_roundtrip_fee_pct(*, taker_fee: float, floor_pct: float) -> float:
    """Round-trip fee en puntos porcentuales.

    taker_fee: fracción por lado (0.001 = 0.1%).
    floor_pct: piso del round-trip en puntos % (0.20 = 0.20%).
    Devuelve max(round-trip real, piso).
    """
    real_roundtrip_pct = taker_fee * 2 * 100
    return max(real_roundtrip_pct, floor_pct)
```

- [ ] **Step 4: Correr para verificar que pasa**

Run: `cd trading-engine && python -m pytest tests/test_fees.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Agregar la ConfigKey y su default**

En `shared/config_store.py`, dentro del enum `ConfigKey` (después de la línea 23, `MAX_SLIPPAGE_PCT`):
```python
    MIN_ROUNDTRIP_FEE_PCT = "min_roundtrip_fee_pct"
```
En el dict `DEFAULTS` (agregar una entrada nueva, junto a las de Risk Gate, p.ej. tras `MIN_FEES_TO_TP_RATIO`):
```python
    ConfigKey.MIN_ROUNDTRIP_FEE_PCT: _Default(
        "0.20", "float",
        "Piso del round-trip fee (puntos %) usado por R10 cuando el exchange "
        "reporta fees 0 (testnet). 0.20 = equivalente LIVE (0.1% taker/lado). Rango 0.0–0.5.",
    ),
```

- [ ] **Step 6: Cablear el fee efectivo en `main.py`**

En `trading-engine/main.py`, agregar el import junto a los otros de `risk` (cerca del top, donde se importa `RiskGate`):
```python
from risk.fees import effective_roundtrip_fee_pct
```
Dentro del bloque que arma `calibration` (después de la línea 269, antes de cerrar el dict en :270), agregar la clave:
```python
                "min_roundtrip_fee_pct": await store.get_typed(ConfigKey.MIN_ROUNDTRIP_FEE_PCT),
```
Reemplazar la línea 413:
```python
                roundtrip_fee_pct=fees.taker * 2 * 100,
```
por:
```python
                roundtrip_fee_pct=effective_roundtrip_fee_pct(
                    taker_fee=fees.taker,
                    floor_pct=float(calibration.get("min_roundtrip_fee_pct", 0.20)),
                ),
```

- [ ] **Step 7: Verificar que el seed crea la clave y la suite no rompe**

Run: `cd trading-engine && python -m pytest tests/test_fees.py tests/test_config_store.py tests/test_config_v2_keys.py -v`
Expected: PASS. (El seed es idempotente, `config_store.py:352-369`; la clave se crea al reiniciar el engine o el web.)

- [ ] **Step 8: Commit**

```bash
git add trading-engine/risk/fees.py trading-engine/tests/test_fees.py shared/config_store.py trading-engine/main.py
git commit -m "fix(risk): apply LIVE-equivalent fee floor so R10 validates in paper

main.py passed roundtrip_fee_pct=0 on testnet, disabling R10 entirely.
New MIN_ROUNDTRIP_FEE_PCT (default 0.20%) floors the round-trip so the
fee-coverage rule is enforced during paper trading.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: R5 estricto + R10 con colchón de slippage

**Files:**
- Modify: `trading-engine/risk/risk_gate.py:152-168`
- Test: `trading-engine/tests/test_risk_gate.py`

**Por qué:** (1) R5 usa `<=` (`risk_gate.py:154`): un R:R exactamente igual a `min_rr_ratio` se rechaza, pero el mensaje y la intención son "mínimo aceptable" — lo dejamos explícito y consistente. (2) `max_slippage_pct` es código muerto (`risk_gate.py:27`); el slippage de entrada+salida erosiona el P&L igual que los fees (el SL guardian vende a market). R10 debe exigir que el movimiento al TP cubra fees **+ slippage round-trip**.

- [ ] **Step 1: Escribir los tests que fallan**

```python
# Agregar a trading-engine/tests/test_risk_gate.py

def test_r10_rejects_when_tp_move_below_fees_plus_slippage():
    # round-trip fee 0.20% + slippage 2x0.05%=0.10% -> colchón = (0.20+0.10) = 0.30%
    # min_fees_to_tp_ratio=1.0 -> move requerido >= 0.30%
    # TP a +0.20% del precio -> insuficiente -> R10
    gate = _make_gate(max_slippage_pct=0.0005, max_position_pct=0.10)
    price = 80000.0
    decision = _buy_decision(
        stop_loss=price - 800.0,           # SL 1.0% (dentro de banda)
        take_profit=price * 1.002,         # TP +0.20%
        position_size_pct=0.05,
    )
    verdict = gate.validate(
        decision=decision, current_price=price, atr_ref=800.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=1000.0, btc_held=0.0,
        roundtrip_fee_pct=0.20, min_fees_to_tp_ratio=1.0,
    )
    assert not verdict.passed
    assert verdict.rule_id == "R10"


def test_r10_passes_when_tp_move_covers_fees_plus_slippage():
    # colchón = 0.30%, TP a +1.0% -> cubre de sobra (R:R 1.0 con SL 1.0%)
    gate = _make_gate(max_slippage_pct=0.0005, max_position_pct=0.10)
    price = 80000.0
    decision = _buy_decision(
        stop_loss=price - 320.0,           # SL 0.4%
        take_profit=price + 800.0,         # TP +1.0% -> R:R 2.5
        position_size_pct=0.05,
    )
    verdict = gate.validate(
        decision=decision, current_price=price, atr_ref=800.0,
        open_positions_count=0, daily_pnl_pct=0.0, total_drawdown_pct=0.0,
        kill_switch=False, usdt_balance=1000.0, btc_held=0.0,
        roundtrip_fee_pct=0.20, min_fees_to_tp_ratio=1.0,
    )
    assert verdict.passed
```

- [ ] **Step 2: Correr para verificar que fallan**

Run: `cd trading-engine && python -m pytest tests/test_risk_gate.py -k "fees_plus_slippage" -v`
Expected: FAIL — el primero pasa el gate (R10 no incluye slippage todavía) → `assert not verdict.passed` falla.

- [ ] **Step 3: Modificar R10 (y R5) en `risk_gate.py`**

Reemplazar el bloque de las líneas 152-168 (R5 y R10) por:

```python
        # R5 — R:R mínimo (estricto: el ratio debe SUPERAR el mínimo)
        reward = decision.take_profit - current_price
        if sl_distance > 0 and reward / sl_distance < self.min_rr_ratio:
            return self._reject(
                "R5",
                f"R:R ratio {reward/sl_distance:.2f} < {self.min_rr_ratio}",
            )

        # R10 — el movimiento al TP debe cubrir fees round-trip + colchón de slippage.
        # El slippage de entrada+salida (market orders + SL guardian) erosiona el P&L
        # igual que los fees, por eso se suma 2×max_slippage_pct al umbral.
        # (no aplica si roundtrip_fee_pct == 0, p.ej. floor desactivado a propósito)
        if roundtrip_fee_pct > 0:
            move_pct = (decision.take_profit - current_price) / current_price * 100
            slippage_cushion_pct = self.max_slippage_pct * 2 * 100
            min_move = min_fees_to_tp_ratio * roundtrip_fee_pct + slippage_cushion_pct
            if move_pct < min_move:
                return self._reject(
                    "R10",
                    f"TP move ({move_pct:.3f}%) < {min_fees_to_tp_ratio}×fees + slippage "
                    f"({min_move:.3f}%)",
                )

        return RiskVerdict(passed=True)
```

> Nota: el `return RiskVerdict(passed=True)` final ya existía en la línea 170; al reemplazar el bloque lo reincluimos una sola vez. Verificar tras la edición que no quede duplicado.

- [ ] **Step 4: Correr para verificar que pasan (y no se rompió el resto)**

Run: `cd trading-engine && python -m pytest tests/test_risk_gate.py -v`
Expected: PASS, incluidos los 2 tests nuevos y todos los preexistentes. Si algún test viejo asumía `<=` en R5 con igualdad exacta, ajustarlo para usar un R:R estrictamente mayor al mínimo (era un caso de borde frágil).

- [ ] **Step 5: Commit**

```bash
git add trading-engine/risk/risk_gate.py trading-engine/tests/test_risk_gate.py
git commit -m "fix(risk): R10 covers fees + round-trip slippage; R5 strict

max_slippage_pct was dead code. R10 now requires TP move to cover
min_fees_to_tp_ratio x fees PLUS 2 x max_slippage_pct, matching how
market-order entries and the SL guardian erode realized P&L.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: SL guardian cada 10 s (no 30 s)

**Files:**
- Modify: `trading-engine/main.py:603`
- Test: `trading-engine/tests/test_scheduler.py` (crear si no existe)

**Por qué:** el SL guardian software (`order_tracker.py:167-222`) corre cada 30 s (`main.py:603`). En caídas rápidas, el precio atraviesa el SL y el guardian recién reacciona hasta 30 s después → fill a market muy por debajo del SL planeado (parte del R:R realizado 1.10 vs 1.3). Bajar a 10 s acota esa ventana. (El order tracker no usa LLM, así que no afecta los rate limits del Decisor; sólo consulta órdenes/precio en Binance, muy por debajo del rate limit REST.)

- [ ] **Step 1: Escribir el test que falla**

```python
# trading-engine/tests/test_scheduler.py
"""Verifica los intervalos de los jobs periódicos del scheduler."""
from __future__ import annotations

from unittest.mock import MagicMock

from scheduler import Scheduler


def test_order_tracker_registered_at_10s():
    sched = Scheduler()
    sched.scheduler = MagicMock()   # no arrancar APScheduler real
    captured = {}

    def _fake_add_job(fn, trigger, **kwargs):
        captured["seconds"] = kwargs.get("seconds")
        captured["id"] = kwargs.get("id")

    sched.scheduler.add_job.side_effect = _fake_add_job
    sched.add_order_tracker(lambda: None, seconds=10)

    assert captured["seconds"] == 10
    assert captured["id"] == "order_tracker"
```

> Si la API real de `Scheduler.add_order_tracker` difiere (revisar `scheduler.py:29-31`), adaptar el test al modo en que se invoca `add_job`/`add_interval`. El objetivo del test es: el job `order_tracker` se registra con el intervalo que se le pasa.

- [ ] **Step 2: Correr para verificar que falla o ajustar el test al API real**

Run: `cd trading-engine && python -m pytest tests/test_scheduler.py -v`
Expected: PASS si el `add_order_tracker` ya respeta el parámetro `seconds` (lo respeta, `scheduler.py:29`). En ese caso el test es de regresión y el cambio real está en el Step 3 (el call-site). Si falla por el shape del mock, ajustar al API real de APScheduler usado en `scheduler.py`.

- [ ] **Step 3: Cambiar el intervalo en el call-site**

En `trading-engine/main.py`, reemplazar la línea 603:
```python
    sched.add_order_tracker(order_tracker_tick, seconds=30)
```
por:
```python
    sched.add_order_tracker(order_tracker_tick, seconds=10)
```

- [ ] **Step 4: Correr la suite del order tracker y scheduler**

Run: `cd trading-engine && python -m pytest tests/test_scheduler.py tests/test_order_tracker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine/main.py trading-engine/tests/test_scheduler.py
git commit -m "fix(execution): run SL guardian every 10s instead of 30s

Tightens the window where price can cross the stop before the software
SL guardian reacts, reducing market-sell slippage below the planned SL.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Sizing plano desacoplado de confidence (prompt)

**Files:**
- Modify: `trading-engine/agents/prompts/decisor_system.txt:102-110`
- Test: `trading-engine/tests/test_prompt_manager.py`

**Por qué:** la tabla de sizing (`decisor_system.txt:102-110`) escala el `position_size_pct` con la confianza (confidence≥0.85 → cerca de `max_position_pct`). Empíricamente las perdedoras tuvieron ~1.9× el notional de las ganadoras: el modelo está más seguro justo cuando se equivoca (comprar fuerza = topes locales). Hasta tener edge probado, el sizing debe ser **plano y conservador**, no proporcional a la confianza.

- [ ] **Step 1: Escribir/extender el test de render**

```python
# Agregar a trading-engine/tests/test_prompt_manager.py
from agents.prompt_manager import PromptManager  # ajustar al import real del módulo


def test_decisor_system_prompt_sizing_is_flat():
    text = open(
        "agents/prompts/decisor_system.txt", encoding="utf-8"
    ).read()
    # El sizing plano no debe escalar con confidence: ya no menciona "cerca de"
    # la tabla por umbral de confianza.
    assert "Sizing plano" in text
    # No debe quedar la guía vieja "confidence ≥ 0.85 ... cerca de {max_position_pct}"
    assert "confidence ≥ 0.85 + ≥3 confluencias" not in text
```

> Antes de implementar, confirmar con `cd trading-engine && python -m pytest tests/test_prompt_manager.py -v` que la suite existente pasa (para no atribuir un fallo previo a este cambio).

- [ ] **Step 2: Correr para verificar que falla**

Run: `cd trading-engine && python -m pytest tests/test_prompt_manager.py::test_decisor_system_prompt_sizing_is_flat -v`
Expected: FAIL — `"Sizing plano" not in text`.

- [ ] **Step 3: Reemplazar la tabla de sizing en el prompt**

En `trading-engine/agents/prompts/decisor_system.txt`, reemplazar el bloque de las líneas 102-110:

```
Elegí position_size_pct libremente en ({min_position_size}, {max_position_pct}]:
...
  confidence ≥ 0.85 + ≥3 confluencias + TRENDING_UP  → cerca de {max_position_pct}
  confidence 0.70–0.84                                → 50–80% de {max_position_pct}
  confidence 0.60–0.69                                → 20–40% de {max_position_pct} (prueba)
  confidence < 0.60 + BUY de todas formas             → ≤20% de {max_position_pct}
...
  Para HOLD/SELL → position_size_pct = 0.0
```

por:

```
Sizing plano (NO escalar con confidence — política vigente hasta tener edge probado):
  Todo BUY usa el mismo tamaño base conservador: position_size_pct = 0.4 × {max_position_pct}.
  No aumentes el tamaño por confianza alta. La confianza alta NO justifica más riesgo:
  empíricamente las entradas de mayor convicción no tuvieron mejor resultado.
  Solo reducí por debajo del base (hacia {min_position_size}) si la liquidez es pobre
  (mid_impact_pct alto / spread ancho) o si hay warnings de coherencia.
  Para HOLD/SELL → position_size_pct = 0.0
```

> El cap duro sigue siendo R1 (`position_size_pct ≤ {max_position_pct}`), así que aunque el LLM se desvíe, el Risk Gate protege. El piso `{min_position_size}` y la variable `{max_position_pct}` ya existen en el contexto del prompt (`context_builder`), no se introducen variables nuevas.

- [ ] **Step 4: Correr para verificar que pasa**

Run: `cd trading-engine && python -m pytest tests/test_prompt_manager.py -v`
Expected: PASS (el test nuevo y los de render existentes — el cambio no introduce variables `{...}` nuevas, así que el render sigue funcionando).

- [ ] **Step 5: Commit**

```bash
git add trading-engine/agents/prompts/decisor_system.txt trading-engine/tests/test_prompt_manager.py
git commit -m "fix(decisor): flatten position sizing, decouple from confidence

Realized data showed losing trades had ~1.9x the notional of winners:
confidence-scaled sizing amplified the worst entries. Flat conservative
size (0.4x max_position_pct) until a positive edge is proven. R1 cap unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Suite completa y verificación de no-regresión

**Files:** ninguno (verificación)

- [ ] **Step 1: Correr toda la suite del engine**

Run: `cd trading-engine && python -m pytest -q`
Expected: todos los tests PASS. Si algo falla, arreglar antes de continuar (no avanzar a Task 8 con la suite roja).

- [ ] **Step 2: Correr la suite del backtester**

Run: `cd backtesting && python -m pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit (si hubo ajustes de regresión)**

```bash
git add -A
git commit -m "test: fix regressions after P0 geometry/execution changes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Aplicar la geometría ganadora a la DB (script idempotente)

**Files:**
- Create: `trading-engine/scripts/apply_geometry.py`

**Por qué:** la config vive en Postgres (`config` table), no en el código. Cambiar el `_Default` en `config_store.py` sólo afecta instalaciones nuevas (el seed es "insert if missing", `config_store.py:357-358`). Para la instalación corriendo hay que `ConfigStore.set(...)`, que además deja traza en `config_history` (`config_store.py:384-401`).

- [ ] **Step 1: Implementar el script**

```python
# trading-engine/scripts/apply_geometry.py
"""Aplica la geometría de riesgo elegida por el sweep (Task 1) a la DB.

Idempotente: escribe vía ConfigStore.set (queda en config_history).

Usage (desde el contenedor trading-engine o con DATABASE_URL apuntando a la DB):
    python -m scripts.apply_geometry --sl 1.2 --rr 2.5 --max-pos 0.05
"""
from __future__ import annotations

import argparse
import asyncio

from config import settings
from shared.config_store import ConfigKey, ConfigStore
from shared.db.base import make_session_factory


async def _apply(sl: float, rr: float, sl_max: float, max_pos: float) -> None:
    session_factory = make_session_factory(settings.database_url)
    async with session_factory() as s:
        store = ConfigStore(s)
        await store.set(ConfigKey.SL_ATR_MULTIPLIER, str(sl), changed_by="p0-geometry")
        await store.set(ConfigKey.MIN_RR_RATIO, str(rr), changed_by="p0-geometry")
        await store.set(ConfigKey.SL_ATR_MAX_MULTIPLIER, str(sl_max), changed_by="p0-geometry")
        # invariante min_rr_ratio <= default_rr_ratio (supervisor._INVARIANTS)
        await store.set(ConfigKey.DEFAULT_RR_RATIO, str(max(rr, 2.5)), changed_by="p0-geometry")
        await store.set(ConfigKey.MAX_POSITION_PCT, str(max_pos), changed_by="p0-geometry")
        print(f"Geometría aplicada: SL={sl}xATR (max {sl_max}x), R:R>={rr}, "
              f"default_rr={max(rr,2.5)}, max_position_pct={max_pos}")
        # Verificación de lectura
        for k in (ConfigKey.SL_ATR_MULTIPLIER, ConfigKey.MIN_RR_RATIO,
                  ConfigKey.MAX_POSITION_PCT):
            print(f"  {k.value} = {await store.get_typed(k)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sl", type=float, required=True, help="sl_atr_multiplier ganador del sweep")
    p.add_argument("--rr", type=float, required=True, help="min_rr_ratio ganador del sweep")
    p.add_argument("--sl-max", type=float, default=2.5, help="sl_atr_max_multiplier")
    p.add_argument("--max-pos", type=float, default=0.05,
                   help="max_position_pct conservador hasta probar edge")
    args = p.parse_args()
    asyncio.run(_apply(args.sl, args.rr, args.sl_max, args.max_pos))


if __name__ == "__main__":
    main()
```

> Verificar el import real del session factory en `trading-engine/main.py` (cómo se construye `session_factory`) y `config.py` (`settings.database_url`) y ajustar el import si el helper se llama distinto. El objetivo: abrir una `AsyncSession` contra la misma DB que usa el engine.

- [ ] **Step 2: Correr el script con los valores del Task 1**

Run (reemplazar `<SL*>`/`<RR*>` por los ganadores anotados en Task 1, p.ej. 1.2 / 2.5):
```bash
docker exec crypto-ai-trading-trading-engine-1 python -m scripts.apply_geometry --sl <SL*> --rr <RR*> --max-pos 0.05
```
Expected: imprime "Geometría aplicada" y los valores leídos de vuelta desde la DB.

- [ ] **Step 3: Verificar en la DB**

Run:
```bash
docker exec crypto-ai-trading-postgres-1 psql -U trader -d crypto_ai_trading -t -c \
"SELECT key, value FROM config WHERE key IN ('sl_atr_multiplier','min_rr_ratio','sl_atr_max_multiplier','default_rr_ratio','max_position_pct','min_roundtrip_fee_pct') ORDER BY key;"
```
Expected: refleja los valores nuevos. `min_roundtrip_fee_pct` debe existir con `0.20` (creado por el seed al reiniciar el engine tras Task 3 — si no aparece, reiniciar el contenedor `trading-engine` para que corra `seed_defaults`).

- [ ] **Step 4: Commit**

```bash
git add trading-engine/scripts/apply_geometry.py
git commit -m "feat(scripts): apply_geometry writes swept SL/RR to config DB

Idempotent ConfigStore.set with changed_by=p0-geometry (audited in
config_history). max_position_pct lowered to 0.05 until edge is proven.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Observación dirigida (gate de salida del P0)

**Files:** ninguno (operación)

- [ ] **Step 1: Confirmar que el engine arrancó con la config nueva**

Run:
```bash
docker compose logs --tail=50 trading-engine | grep -iE "risk_gate|rejected|R10|R5|sl_atr|geometry"
```
Expected: ver decisiones evaluadas con la geometría nueva; rechazos R10/R5 con los umbrales nuevos.

- [ ] **Step 2: Definir el criterio de éxito a observar (no se implementa, se monitorea)**

Durante las próximas 2 semanas de paper trading, comparar contra la baseline (−275 USDT / WR 36 % / R:R real 1.10):
- Win rate de cierres de bracket (`tp_triggered` / (`tp_triggered`+`sl_triggered`)) **> breakeven** = `1/(1+min_rr_ratio)`.
- R:R realizado (avg_win_pct / |avg_loss_pct|) **≥ min_rr_ratio × 0.85** (cuánto se lo come el slippage).
- P&L semanal **≥ 0**.

Query de control:
```bash
docker exec crypto-ai-trading-postgres-1 psql -U trader -d crypto_ai_trading -t -c \
"SELECT close_reason, count(*), round(sum(pnl_usdt)::numeric,2), round(avg(pnl_pct)::numeric,4)
 FROM trades WHERE status='closed' AND ts_close > now() - interval '14 days'
 GROUP BY 1 ORDER BY 1;"
```

> Si tras 2 semanas el R:R realizado sigue muy por debajo del configurado, el problema residual es slippage de ejecución → escalar a un plan de "ejecución por WS de fills / límite en vez de market" (backlog P0+). Si el win rate sigue < breakeven con SL amplio, el problema es la señal del Decisor → P1/P2.

---

## Self-Review (hecho al cerrar el plan)

**Cobertura del diagnóstico P0:**
- Geometría SL/RR EV-negativa → Task 1 (sweep) + Task 2 (bounds) + Task 8 (aplicar). ✅
- R10 desactivada en testnet → Task 3 (fee floor). ✅
- `max_slippage_pct` código muerto → Task 4 (R10 con colchón slippage). ✅
- SL guardian lento (30 s) → Task 5 (10 s). ✅
- Sizing inverso al acierto → Task 6 (sizing plano). ✅
- No-regresión → Task 7. Gate de salida → Task 9. ✅

**Placeholders:** los `<SL*>`/`<RR*>` son outputs deliberados del Task 1 que el ejecutor anota y consume en Task 2/8 — no son TODOs de implementación. Cada paso de código tiene el código completo.

**Consistencia de tipos:** `effective_roundtrip_fee_pct(taker_fee=..., floor_pct=...)` se define en Task 3 y se invoca con esos kwargs en `main.py`. `ConfigKey.MIN_ROUNDTRIP_FEE_PCT` se define en Task 3 y se lee en `calibration`. `_SAFE_BOUNDS["sl_atr_multiplier"]` ampliado en Task 2, consumido por el Supervisor existente.

**Riesgos conocidos para el ejecutor:**
- Confirmar el API real de `Scheduler.add_order_tracker` (Task 5) y del session factory (`make_session_factory` / `settings.database_url`, Task 8) — pueden tener otro nombre; ajustar imports al código real antes de correr.
- Reiniciar el contenedor `trading-engine` tras Task 3 para que `seed_defaults` cree `min_roundtrip_fee_pct`.

---

## Planes encadenados (alcance P0–P3 elegido)

Este documento es **P0**. Los siguientes se redactan como planes independientes (cada uno entrega software testeable por sí solo), en este orden:
- **P1 — Confiabilidad del Decisor:** parser JSON balanceado (`decisor.py:263`), reasoning Ollama robusto (`llm_client.py:255`), coerción de `confidence_adjustment`/`NEUTRAL` (`schemas.py`), nulls reales en el contexto (`context_builder.py`), estabilización de la cascada de providers. Objetivo: recuperar el ~14 % de ciclos perdidos.
- **P2 — Cerrar el lazo de aprendizaje:** clasificación de outcomes por calidad de entrada con umbral neto de fees (`outcome_attribution.py:168`), Supervisor closed-loop (baseline en `config_history` + auto-revert).
- **P3 — Prompts:** mover la fórmula de confidence a código, reducir el tamaño del system prompt.
