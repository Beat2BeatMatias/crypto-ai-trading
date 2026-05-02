# Crypto AI Trading — Design Document

**Date:** 2026-05-02
**Status:** Draft (pending user approval)
**Authors:** Matías + Claude (brainstorm session)
**Audience:** Implementation team / `writing-plans` skill

---

## 1. Executive summary

Build a fully-autonomous AI-driven crypto day trading bot for **BTC/USDT spot only on Binance**, powered by two coordinated LLM agents:

- **Decisor** (Gemini 2.5 Flash, every 5 minutes): observes market state, decides BUY / SELL / HOLD, outputs structured JSON with reasoning.
- **Supervisor** (Gemini 2.5 Pro, daily at 00:00 UTC): analyzes the decisor's recent performance, generates an updated "playbook" (lessons learned) that the decisor reads on every cycle.

A deterministic **Risk Gate** (pure Python, no LLM) sits between the decisor and the order executor as the last line of defense.

The system runs in three Docker containers (`trading-engine`, `web`, `postgres`) plus a React dashboard. All configuration is editable from the frontend and persisted in PostgreSQL.

---

## 2. Goals & non-goals

### Goals
- **G1.** Single-pair (BTC/USDT), single-exchange (Binance Spot) day trading bot with full automation in LIVE mode.
- **G2.** Two-agent architecture (Decisor + Supervisor) using only **free-tier LLM providers**.
- **G3.** Deterministic risk safeguards independent from LLM judgment.
- **G4.** Complete audit trail of every LLM decision (input, output, outcome) for debugging and supervisor learning.
- **G5.** Frontend dashboard for monitoring + configuration, written in Spanish.
- **G6.** Paper trading on Binance Testnet before any real money.
- **G7.** Backtesting capability before paper trading.

### Non-goals (explicit out of scope for v1)
- ❌ Margin or futures trading (spot only, ever).
- ❌ Multi-pair / multi-exchange arbitrage (already covered by `crypto-arbitrage`).
- ❌ Shorting (impossible in spot).
- ❌ Multi-user / SaaS productization.
- ❌ Mobile app.
- ❌ On-chain analytics (deferred to v2).
- ❌ Sentiment / news analysis (deferred to v2).
- ❌ Reinforcement learning end-to-end.
- ❌ HFT / sub-second scalping.

---

## 3. Constraints & assumptions

### Constraints
- **C1.** Use only LLM providers with genuine free tiers (no trial expirations).
- **C2.** Single user, self-hosted; no compliance/KYC obligations.
- **C3.** Spot only. SPOT means BUY = open long with USDT, SELL = close long to USDT.
- **C4.** Decisor frequency must fit within Gemini 2.5 Flash free tier (1500 req/day → 5-min ticks ≈ 288/day, comfortable margin).
- **C5.** Risk gate must be deterministic (Python only). No LLM calls in the critical execution path.

### Assumptions
- **A1.** Binance Spot Testnet remains free and API-compatible with mainnet.
- **A2.** Gemini 2.5 Flash and Pro free tiers remain available.
- **A3.** User can run Docker locally with adequate resources (~2 GB RAM minimum).
- **A4.** Initial paper trading capital (testnet) is $10,000 USDT virtual.
- **A5.** Initial real capital (Phase 7) will be small ($200–500 USD).

---

## 4. Architecture

### 4.1 Process topology

Three runtime containers + database:

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  trading-engine  │  │       web        │  │     frontend     │
│  (Python proc)   │  │ FastAPI + asyncio│  │  React 19 + nginx│
│  no exposed port │  │  exposes :8000   │  │  exposes :3000   │
└──────────────────┘  └──────────────────┘  └──────────────────┘
         │                     │                      │
         │                     │                      │ (HTTP/WS proxy)
         │   writes/reads      │   reads/writes       │
         └─────────────────────┴──────────────────────┘
                               │
                               ▼
                   ┌──────────────────────┐
                   │  PostgreSQL 17       │
                   │  (single source of   │
                   │   truth)             │
                   └──────────────────────┘
```

**Communication:** Engine and web communicate **only via PostgreSQL**. No message queue, no shared memory, no IPC. Config changes from the UI are written to the `config` table; engine polls and applies on next cycle.

**Rationale:** Selected over a monolithic single-process approach (Approach A) for fault isolation — if the web crashes, trading continues; if trading crashes, the dashboard still functions for diagnostics. Selected over microservices (Approach C) because adding Redis Streams + 3 extra containers is unjustified complexity for single-pair single-user trading.

### 4.2 Component map

```
┌─────────────────────────────────────────────────────────────────┐
│  trading-engine                                                 │
│                                                                 │
│  ┌─────────────────┐   ┌────────────────────────────────────┐   │
│  │  COLLECTORS     │   │  AGENT LOOP (every 5 min)          │   │
│  │                 │   │                                    │   │
│  │ PriceCollector  │──▶│  ContextBuilder                   │   │
│  │ • OHLCV via     │   │   gathers indicators, OB snapshot,│   │
│  │   CCXT REST     │   │   positions, balance, playbook    │   │
│  │ • pandas-ta     │   │         │                          │   │
│  │ • multi-TF      │   │         ▼                          │   │
│  │                 │   │  Decisor (Gemini 2.5 Flash)       │   │
│  │ OrderBookColl.  │──▶│   parses JSON output              │   │
│  │ • Binance WS    │   │         │                          │   │
│  │ • in-memory     │   │         ▼                          │   │
│  │   top 10 levels │   │  RiskGate (pure Python)           │   │
│  │                 │   │   reject or pass                  │   │
│  └─────────────────┘   │         │                          │   │
│                        │         ▼ (if pass)                │   │
│                        │  Executor (CCXT)                   │   │
│                        │   places orders, sets SL/TP        │   │
│                        └────────────────────────────────────┘   │
│                                                                 │
│  PositionManager (poll every 30s)                              │
│   • detects SL/TP fills, updates outcome on decisions table    │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Supervisor (00:00 UTC daily, Gemini 2.5 Pro)           │  │
│  │   1. Read 24h of decisions + trades + outcomes           │  │
│  │   2. Compute aggregate metrics                           │  │
│  │   3. Call Gemini Pro with template                       │  │
│  │   4. Persist new playbook version, mark active           │  │
│  │   5. Trigger automatic rollback if performance degrades  │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │ all writes/reads
         ▼
   PostgreSQL 17
         │
         ▼
   web (FastAPI) ──── REST + WebSocket ───▶ frontend (React)
```

---

## 5. Tech stack

### Backend (both `trading-engine` and `web` containers)

| Layer | Choice |
|-------|--------|
| Language | Python 3.12 |
| Web framework | FastAPI 0.115+ |
| Async runtime | asyncio + uvloop |
| ORM | SQLAlchemy 2.0 (async) |
| DB driver | asyncpg |
| Migrations | Alembic |
| Database | PostgreSQL 17 |
| Scheduler | APScheduler 3.x |
| Exchange client | CCXT (REST) + ccxt.pro (WebSocket) |
| Technical indicators | pandas-ta |
| Data manipulation | pandas + numpy |
| LLM client (primary) | google-genai (Gemini SDK) |
| LLM client (fallback) | groq SDK |
| HTTP async | httpx |
| Validation | Pydantic 2 |
| Logging | structlog (JSON structured) |
| Testing | pytest + pytest-asyncio + freezegun |
| Backtesting (Phase 6) | vectorbt |

### Frontend (`frontend` container)

| Layer | Choice |
|-------|--------|
| Framework | React 19 |
| Build | Vite |
| Styling | TailwindCSS v4 |
| Charts | Recharts or lightweight-charts |
| WebSocket | native browser WS API |
| Locale | es-AR (UI in Spanish) |
| Server | nginx (production) / vite dev (development) |

---

## 6. Data model

### 6.1 PostgreSQL schema

```sql
-- Time-series of OHLCV per timeframe
CREATE TABLE ohlcv (
    time        TIMESTAMPTZ NOT NULL,
    timeframe   VARCHAR(4)  NOT NULL,
    open        NUMERIC(18,8),
    high        NUMERIC(18,8),
    low         NUMERIC(18,8),
    close       NUMERIC(18,8),
    volume      NUMERIC(24,8),
    PRIMARY KEY (time, timeframe)
);
CREATE INDEX idx_ohlcv_tf ON ohlcv (timeframe, time DESC);

-- Computed indicators snapshot (one row per decisor tick)
CREATE TABLE indicators (
    time        TIMESTAMPTZ PRIMARY KEY,
    data        JSONB NOT NULL
);
CREATE INDEX idx_indicators_data ON indicators USING GIN (data);

-- Every LLM decision (full audit trail)
CREATE TABLE decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent           VARCHAR(20) NOT NULL,    -- 'decisor' | 'supervisor'
    model           VARCHAR(50) NOT NULL,
    tokens_in       INT,
    tokens_out      INT,
    latency_ms      INT,
    input           JSONB NOT NULL,
    output          JSONB NOT NULL,
    outcome         JSONB,
    trade_id        UUID,                     -- FK added after trades created
    executed        BOOLEAN DEFAULT false,
    rejected_reason VARCHAR(200)
);
CREATE INDEX idx_decisions_ts     ON decisions (ts DESC);
CREATE INDEX idx_decisions_output ON decisions USING GIN (output);
CREATE INDEX idx_decisions_input  ON decisions USING GIN (input);

-- Trades (orders that actually executed)
CREATE TABLE trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id     UUID REFERENCES decisions(id),
    ts_open         TIMESTAMPTZ NOT NULL,
    ts_close        TIMESTAMPTZ,
    side            VARCHAR(4) NOT NULL,
    quantity_btc    NUMERIC(18,8) NOT NULL,
    entry_price     NUMERIC(18,8) NOT NULL,
    exit_price      NUMERIC(18,8),
    pnl_usdt        NUMERIC(18,4),
    pnl_pct         NUMERIC(8,4),
    status          VARCHAR(12) NOT NULL,    -- 'open','closed','cancelled'
    stop_loss       NUMERIC(18,8),
    take_profit     NUMERIC(18,8),
    close_reason    VARCHAR(20),
    order_id_open   VARCHAR(50),
    order_id_close  VARCHAR(50),
    fees_usdt       NUMERIC(18,4)
);
CREATE INDEX idx_trades_status ON trades (status);
CREATE INDEX idx_trades_ts     ON trades (ts_open DESC);

ALTER TABLE decisions ADD CONSTRAINT fk_decisions_trade
    FOREIGN KEY (trade_id) REFERENCES trades(id);

-- Open positions (real-time view)
CREATE TABLE positions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_id        UUID REFERENCES trades(id),
    symbol          VARCHAR(20) NOT NULL DEFAULT 'BTC/USDT',
    quantity_btc    NUMERIC(18,8) NOT NULL,
    entry_price     NUMERIC(18,8) NOT NULL,
    current_price   NUMERIC(18,8),
    unrealized_pnl  NUMERIC(18,4),
    unrealized_pct  NUMERIC(8,4),
    status          VARCHAR(10) DEFAULT 'open',
    opened_at       TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ
);

-- Playbook versions generated by Supervisor
CREATE TABLE playbook_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version         INT NOT NULL UNIQUE,
    ts_generated    TIMESTAMPTZ NOT NULL DEFAULT now(),
    content         TEXT NOT NULL,
    model           VARCHAR(50),
    trades_analyzed INT,
    win_rate        NUMERIC(5,2),
    pnl_summary     JSONB,
    active          BOOLEAN DEFAULT false
);
CREATE UNIQUE INDEX idx_playbook_active ON playbook_versions (active) WHERE active = true;

-- Runtime config (key-value, editable from UI)
CREATE TABLE config (
    key             VARCHAR(60) PRIMARY KEY,
    value           TEXT NOT NULL,
    value_type      VARCHAR(20) NOT NULL,    -- 'int','float','string','bool','json'
    description     TEXT,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Audit log of config changes
CREATE TABLE config_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    key         VARCHAR(60) NOT NULL,
    old_value   TEXT,
    new_value   TEXT NOT NULL,
    changed_by  VARCHAR(60) DEFAULT 'system'
);

-- Pre-computed daily stats
CREATE TABLE daily_stats (
    date            DATE PRIMARY KEY,
    decisions_total INT DEFAULT 0,
    trades_executed INT DEFAULT 0,
    wins            INT DEFAULT 0,
    losses          INT DEFAULT 0,
    pnl_usdt        NUMERIC(18,4),
    pnl_pct         NUMERIC(8,4),
    max_drawdown    NUMERIC(8,4),
    breakdown       JSONB
);
```

### 6.2 Default config keys (seeded on first run)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `PAPER_TRADING` | `PAPER_TRADING` or `LIVE` |
| `max_position_pct` | float | `0.10` | Max % capital per trade |
| `max_simultaneous_trades` | int | `2` | Max concurrent open positions |
| `daily_stop_pct` | float | `-0.03` | Daily P&L stop (-3%) |
| `max_drawdown_pct` | float | `-0.10` | Total drawdown limit (-10%) |
| `max_slippage_pct` | float | `0.003` | Max acceptable slippage (0.3%) |
| `default_rr_ratio` | float | `2.0` | Default take-profit ratio |
| `decisor_interval_min` | int | `5` | Decisor frequency in minutes |
| `supervisor_cron` | string | `0 0 * * *` | Supervisor schedule (UTC) |
| `decisor_provider` | string | `gemini-2.5-flash` | Primary LLM for decisor |
| `supervisor_provider` | string | `gemini-2.5-pro` | LLM for supervisor |
| `fallback_provider` | string | `groq-llama-3.3-70b` | Fallback LLM |
| `llm_max_retries` | int | `3` | Retries on LLM failure |
| `llm_timeout_sec` | int | `30` | LLM call timeout |
| `orderbook_levels` | int | `10` | Order book depth in context |
| `kill_switch` | bool | `false` | Emergency stop |

---

## 7. Prompts (the core of the system)

The prompts are the differentiator between a generic LLM trading toy and a useful agent. They live in code as templates loaded at runtime, with the active playbook injected dynamically.

### 7.1 Decisor — System prompt

```text
═══════════════════════════════════════════════════════════════════════
  ROLE
═══════════════════════════════════════════════════════════════════════
Eres un agente cuantitativo de day trading especializado exclusivamente
en BTC/USDT en Binance Spot. Tu único objetivo es maximizar el P&L
ajustado por riesgo (Sharpe ratio) en horizontes de minutos a horas.

NO eres un asistente. NO das opiniones. NO operás otros activos.
Tu output es UNA decisión estructurada por ciclo, basada en evidencia.

═══════════════════════════════════════════════════════════════════════
  CONTEXTO OPERATIVO
═══════════════════════════════════════════════════════════════════════
Modo actual: {mode}                  ← PAPER_TRADING | LIVE
Capital total: ${capital_total} USDT
Capital disponible: ${usdt_available} USDT
BTC en posición: {btc_held} BTC
Frecuencia de decisión: cada {decisor_interval_min} minutos
Fee por orden: 0.1% (taker), considerar 0.2% round-trip

═══════════════════════════════════════════════════════════════════════
  REGLAS ABSOLUTAS (el Risk Gate las verifica — violarlas → orden rechazada)
═══════════════════════════════════════════════════════════════════════
R1. position_size_pct ∈ [0.01, {max_position_pct}]
R2. action = "BUY"  → stop_loss OBLIGATORIO, < precio_actual
R3. action = "BUY"  → take_profit recomendado, > precio_actual
R4. action = "BUY"  → distancia_SL ≥ 0.5 × ATR(1h)
R5. action = "BUY"  → R:R mínimo 1.5:1
R6. action = "SELL" → solo válido si hay posición LONG abierta
R7. NUNCA shortear (estás en SPOT)
R8. Si {open_positions_count} ≥ {max_simultaneous_trades} → solo "HOLD" o "SELL"
R9. Si daily_pnl_pct ≤ {daily_stop_pct} → "HOLD" forzado

═══════════════════════════════════════════════════════════════════════
  FRAMEWORK DE DECISIÓN — sigue ESTOS PASOS antes de responder
═══════════════════════════════════════════════════════════════════════
PASO 1 — RÉGIMEN DE MERCADO
  Identificar: TENDENCIA / RANGO / VOLATILIDAD ALTA
  • Tendencia alcista: EMA20 > EMA50 > EMA200 en 1h y 4h
  • Tendencia bajista: EMA20 < EMA50 < EMA200 en 1h y 4h
  • Rango: EMAs entrelazadas
  • Volatilidad alta: ATR(1h) > 1.5× ATR(1h) promedio 7d

PASO 2 — CONFLUENCIA DE SEÑALES
  Setup de calidad requiere ≥ 3 confirmaciones independientes:
  • RSI rebotando desde sobreventa/sobrecompra
  • MACD cruzando línea de señal
  • Precio respetando EMA20 o EMA50 como S/R
  • Bollinger band squeeze + breakout
  • Volumen confirmando movimiento (>1.3x promedio)
  • Order book imbalance favorable (>1.2 a favor)

PASO 3 — MICROESTRUCTURA
  • BUY: imbalance > 1.0 y bid wall cerca como soporte
  • SELL: imbalance < 1.0 o ask wall actuando como resistencia
  • Spread > 0.05% → desconfiar (baja liquidez)

PASO 4 — RIESGO Y SIZING
  • Calcular distancia al SL en %
  • Fórmula: position_size_pct = min(0.10, 0.005 / (distancia_SL_pct))
    → riesgo por trade ≈ 0.5% del capital

PASO 5 — DECISIÓN FINAL
  HOLD si:
  • <3 confirmaciones de confluencia
  • Régimen ambiguo
  • Cerca de S/R sin confirmación
  • Spread > 0.05% o orderbook ralo

═══════════════════════════════════════════════════════════════════════
  PLAYBOOK ACTIVO (lecciones del Supervisor — ALTA PRIORIDAD)
═══════════════════════════════════════════════════════════════════════
{playbook}
═══════════════════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════════════════
  ANTI-PATRONES
═══════════════════════════════════════════════════════════════════════
✗ Overtrading: si los últimos N ciclos fueron HOLD por buena razón, NO
  forzar entrada solo porque "ya hace rato no operás".
✗ FOMO en breakouts sin volumen.
✗ Promediar a la baja.
✗ SL ajustado en alta volatilidad.
✗ Confiar en un solo timeframe.
✗ Ignorar contexto macro del playbook.

═══════════════════════════════════════════════════════════════════════
  EJEMPLOS DE DECISIONES BIEN ARGUMENTADAS
═══════════════════════════════════════════════════════════════════════

EJEMPLO 1 — BUY con alta confianza
{
  "regime": "TRENDING_UP",
  "confluences": ["ema_alignment_1h_4h","rsi_oversold_bounce_5m",
    "macd_bullish_cross_15m","volume_confirmation_1.8x","orderbook_imbalance_1.4"],
  "action": "BUY",
  "confidence": 0.78,
  "stop_loss": 66400.0,
  "take_profit": 67800.0,
  "position_size_pct": 0.06,
  "reasoning": "EMAs alineadas alcista 1h/4h, RSI 5m rebotó de 28, MACD 15m cruzó alcista con vol 1.8x, OB imbalance 1.4 a compra. SL bajo soporte EMA50 1h, R:R 2.3:1."
}

EJEMPLO 2 — HOLD por overtrading risk
{
  "regime": "RANGE",
  "confluences": ["rsi_neutral_5m","macd_flat_15m"],
  "action": "HOLD",
  "confidence": 0.65,
  "stop_loss": null,
  "take_profit": null,
  "position_size_pct": 0.0,
  "reasoning": "Mercado en rango, solo 2 confluencias débiles. Últimas 8 decisiones fueron HOLD acertadamente. No forzar entrada — esperar breakout con volumen."
}

EJEMPLO 3 — SELL para cerrar posición ganadora
{
  "regime": "TRENDING_UP",
  "confluences": ["take_profit_proximity","rsi_overbought_5m","ask_wall_resistance"],
  "action": "SELL",
  "confidence": 0.82,
  "stop_loss": null,
  "take_profit": null,
  "position_size_pct": 0.0,
  "reasoning": "Posición en +1.8%, RSI 5m a 78 (sobrecompra), ask wall 30 BTC a $67,750. Cerrar antes que TP automático."
}

EJEMPLO 4 — HOLD por circuit breaker proximity
{
  "regime": "HIGH_VOLATILITY",
  "confluences": ["high_atr","wide_spread"],
  "action": "HOLD",
  "confidence": 0.9,
  "stop_loss": null,
  "take_profit": null,
  "position_size_pct": 0.0,
  "reasoning": "ATR 2.3x promedio, spread 0.08%, daily P&L -2.4% (margen 0.6% al stop diario). No arriesgar."
}

═══════════════════════════════════════════════════════════════════════
  OUTPUT — JSON EXACTO, sin texto extra, sin markdown
═══════════════════════════════════════════════════════════════════════
{
  "regime": "TRENDING_UP" | "TRENDING_DOWN" | "RANGE" | "HIGH_VOLATILITY",
  "confluences": [<lista de strings>],
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": <float 0.0–1.0>,
  "stop_loss": <float | null>,
  "take_profit": <float | null>,
  "position_size_pct": <float 0.01–0.10>,
  "reasoning": "<español, máximo 240 chars, los 3 factores clave>"
}

VALIDACIÓN ANTES DE RESPONDER:
- ¿action=BUY pero stop_loss=null? → corregir a HOLD
- ¿confluences tiene <3 items y action=BUY? → bajar confidence (<0.5) o HOLD
- ¿confidence > 0.9 sin justificación fuerte? → bajar a 0.7
```

### 7.2 Decisor — User prompt template

```text
═══════════════════════════════════════════════════════════════════════
  CICLO DE DECISIÓN — {timestamp_utc} UTC
═══════════════════════════════════════════════════════════════════════

PRECIO BTC/USDT
  Actual:    ${price:,.2f}
  Cambio:    1h {pct_1h:+.2f}% │ 4h {pct_4h:+.2f}% │ 24h {pct_24h:+.2f}% │ 7d {pct_7d:+.2f}%
  Rango 24h: ${low_24h:,.0f} – ${high_24h:,.0f}

INDICADORES TÉCNICOS
  Timeframe 1m   RSI={rsi_1m:.0f}  BB%={bb_pct_1m:.0f}
  Timeframe 5m   RSI={rsi_5m:.0f}  BB%={bb_pct_5m:.0f}  vol_ratio={vol_5m:.2f}x
  Timeframe 15m  RSI={rsi_15m:.0f}  MACD={macd_15m:+.1f}/{sig_15m:+.1f} hist={hist_15m:+.1f}
  Timeframe 1h   RSI={rsi_1h:.0f}  MACD={macd_1h:+.1f}/{sig_1h:+.1f}
                 EMA20={ema20_1h:,.0f}  EMA50={ema50_1h:,.0f}  EMA200={ema200_1h:,.0f}
                 BB upper={bb_up_1h:,.0f}  BB lower={bb_lo_1h:,.0f}
  Timeframe 4h   RSI={rsi_4h:.0f}  EMA20={ema20_4h:,.0f}  EMA50={ema50_4h:,.0f}  EMA200={ema200_4h:,.0f}
  ATR(1h)        ${atr_1h:.0f}  ({atr_pct_1h:.2f}% del precio)
  ATR 7d avg     ${atr_avg_7d:.0f}  → volatilidad: {volatility_label}

NIVELES CLAVE
  Soporte 1h:    ${support_1h:,.0f}  (distancia: {dist_support_pct:+.2f}%)
  Resistencia 1h:${resistance_1h:,.0f}  (distancia: {dist_resistance_pct:+.2f}%)

ORDER BOOK (top 10 niveles)
  Spread:       ${spread:.2f} ({spread_pct:.4f}%)
  Bid total:    {bid_btc:.2f} BTC en 10 niveles
  Ask total:    {ask_btc:.2f} BTC en 10 niveles
  Imbalance:    {imbalance:.2f}  ({imbalance_label})
  Bid wall:     ${bid_wall_price:,.0f}  ({bid_wall_size:.1f} BTC) — distancia {bid_wall_dist:.2f}%
  Ask wall:     ${ask_wall_price:,.0f}  ({ask_wall_size:.1f} BTC) — distancia {ask_wall_dist:.2f}%

POSICIONES ABIERTAS ({open_positions_count}/{max_simultaneous_trades})
{positions_block}

BALANCE
  USDT disponible: ${usdt_available:,.2f}
  BTC en posición: {btc_held:.6f} BTC (≈ ${btc_held_usd:,.2f})
  Capital total:   ${total_capital_usd:,.2f}

P&L DEL DÍA (UTC)
  Realizado:   ${pnl_today_usd:+,.2f} ({pnl_today_pct:+.2f}%)
  No realizado:${unrealized_pnl_usd:+,.2f}
  Trades hoy:  {trades_today_count}  (W: {wins_today}, L: {losses_today})
  Daily stop:  {daily_stop_pct}%  → margen: {daily_margin_pct:+.2f}%

ÚLTIMAS 3 DECISIONES
{last_decisions_block}

═══════════════════════════════════════════════════════════════════════
  AHORA — sigue el framework y responde con el JSON.
═══════════════════════════════════════════════════════════════════════
```

### 7.3 Supervisor — System prompt

```text
═══════════════════════════════════════════════════════════════════════
  ROLE
═══════════════════════════════════════════════════════════════════════
Eres el Supervisor de un agente de day trading BTC/USDT en Binance Spot.
Tu trabajo: analizar el rendimiento de las últimas 24h y producir un
playbook actualizado que el Decisor leerá en cada decisión.

Eres un meta-agente. NO operás. NO das opiniones de mercado en general.
Tu output es un documento estructurado con lecciones extraídas del data.

═══════════════════════════════════════════════════════════════════════
  METODOLOGÍA DE ANÁLISIS
═══════════════════════════════════════════════════════════════════════
PASO 1 — Métricas globales
  Win rate, avg win, avg loss, profit factor, drawdown intra-day

PASO 2 — Análisis por categoría
  • Trades ganadores: ¿qué confluencias?
  • Trades perdedores: ¿SL ajustado? ¿FOMO? ¿qué fallé en detectar?
  • Decisiones HOLD: ¿acertadas o oportunidad perdida?

PASO 3 — Patrones recurrentes
  • Cluster de pérdidas: ¿qué tenían en común?
  • Cluster de ganancias: ¿setup específico?
  • Sesgos del Decisor: ¿overtrading? ¿overconfidence? ¿reluctance?

PASO 4 — Régimen del mercado
  Identificar régimen dominante 24-72h y proyectar bias para próximas 24h.

PASO 5 — Generación del nuevo playbook
  Reglas accionables y específicas (no genéricas).
  ✓ "Evitar BUY cuando MACD 15m está plano y RSI 5m > 60"
  ✗ "Tener cuidado con la sobrecompra"
  Marcar diferencias: [NUEVO], [REFORZADO], [REVISADO].

═══════════════════════════════════════════════════════════════════════
  ESTRUCTURA OBLIGATORIA DEL PLAYBOOK
═══════════════════════════════════════════════════════════════════════
# Playbook v{N} — {fecha} UTC

## 📊 Métricas del período
[bullets con números]

## 🟢 Setups que funcionaron
[2-4 setups específicos con confluencias exactas]

## 🔴 Patrones a evitar
[2-4 patrones específicos]

## 📈 Contexto de mercado actual
[1-2 párrafos: régimen, S/R, niveles macro]

## 🎯 Bias para próximas 24h
[BULLISH | BEARISH | NEUTRAL — con justificación]

## 📋 Reglas específicas (máximo 6)
[Reglas concretas con valores numéricos]

## 🔄 Cambios vs playbook anterior
[Qué cambió, qué se mantuvo, qué se eliminó]

═══════════════════════════════════════════════════════════════════════
  REGLAS DE CALIDAD
═══════════════════════════════════════════════════════════════════════
• Máximo 600 palabras totales.
• En español.
• Si datos insuficientes (<5 trades), playbook anterior se mantiene + nota.
• Cambios drásticos solo con evidencia fuerte (≥10 trades de soporte).
• NO copiar el playbook anterior tal cual — siempre destilar nuevas observaciones.
```

### 7.4 Supervisor — User prompt template

```text
═══════════════════════════════════════════════════════════════════════
  REVISIÓN DIARIA — {date} UTC
═══════════════════════════════════════════════════════════════════════

MÉTRICAS DEL PERÍODO (últimas 24h)
  Decisiones totales:    {total_decisions}
  └─ BUY:                {buy_count}  ({buy_pct:.0f}%)
  └─ SELL:               {sell_count} ({sell_pct:.0f}%)
  └─ HOLD:               {hold_count} ({hold_pct:.0f}%)
  └─ Rechazadas R.Gate:  {rejected_count}

  Trades cerrados:       {closed_trades}
  Win rate:              {win_rate:.1f}%
  Profit factor:         {profit_factor:.2f}
  Avg win:               ${avg_win:+,.2f} ({avg_win_pct:+.2f}%)
  Avg loss:              ${avg_loss:+,.2f} ({avg_loss_pct:+.2f}%)
  P&L total:             ${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)
  Max drawdown intraday: {max_dd_pct:.2f}%
  Tiempo medio en pos:   {avg_holding_min:.0f} min

CONTEXTO DEL MERCADO (últimas 24h)
  Precio BTC: apertura ${open_btc:,.0f} → cierre ${close_btc:,.0f} ({pct_24h:+.2f}%)
  Rango: ${low_24h:,.0f} – ${high_24h:,.0f}
  Volatilidad: ATR(1h) avg ${atr_avg:.0f} ({atr_pct:.2f}%)
  Volumen: {vol_label} respecto a 7d

DECISIONES Y OUTCOMES (cronológico abreviado)
{decisions_dump}

PLAYBOOK ANTERIOR (v{previous_version})
{previous_playbook}

═══════════════════════════════════════════════════════════════════════
  AHORA — analiza y genera el nuevo playbook v{new_version}.
═══════════════════════════════════════════════════════════════════════
```

### 7.5 Bootstrap playbook (v0, committed to repo)

This is the playbook used during the first 24h before the Supervisor has run for the first time.

```markdown
# Playbook v0 — Bootstrap (sin datos históricos)

## 📊 Métricas del período
N/A — versión inicial sin trades históricos.

## 🟢 Setups que funcionaron
*(A confirmarse con data real. Heurísticas de literatura técnica:)*
- Pullback a EMA50 1h en tendencia alcista confirmada con RSI 5m saliendo de sobreventa.
- Breakout de Bollinger superior 15m con volumen >1.5x promedio y MACD 1h alcista.

## 🔴 Patrones a evitar
- Compras contra-tendencia macro (EMA200 4h descendente y precio bajo EMA50 4h).
- Entradas en spreads anómalos (>0.05%).
- Trades durante eventos macro programados sin awareness.

## 📈 Contexto de mercado actual
Sin contexto histórico. El primer ciclo del Supervisor establecerá el baseline.

## 🎯 Bias para próximas 24h
NEUTRAL — sin evidencia para bias direccional.

## 📋 Reglas específicas
1. Siempre exigir ≥3 confluencias antes de un BUY.
2. R:R mínimo 1.5:1; preferir 2:1.
3. position_size_pct conservador en bootstrap: máx 0.05 (5%) primera semana.
4. Si daily P&L < -1.5% → solo HOLD el resto del día.
5. Si volatilidad ATR(1h) > 1.5x promedio 7d → reducir size 50%.
6. Si últimas 5 decisiones fueron HOLD por buena razón → seguir HOLD.

## 🔄 Cambios vs playbook anterior
N/A — primera versión.
```

### 7.6 Token efficiency strategy

To stay comfortably within Gemini 2.5 Flash free tier:

| Technique | Savings |
|-----------|---------|
| Gemini prompt caching for system prompt + playbook | -50–75% billed tokens |
| Pre-computed indicators (no raw OHLCV in prompt) | -90% vs raw |
| Truncated decimals (`$67,234.50` not `$67,234.4827392`) | -10% |
| Aggregated order book (top 3 + total + walls, not 10 raw) | -70% |
| Only last 3 decisions, not last 10 | -50% |
| Skip redundant timeframes when consistent | -20% |

Estimated tokens per call (with cache hit): **~750 effective tokens**.
Daily volume: 288 calls × 750 = **216k tokens/day** → well within 1M tokens/min Flash limit.

---

## 8. Risk management

### 8.1 Risk Gate (deterministic, pure Python)

The Risk Gate sits between the Decisor's output and the Executor. It rejects any order that violates configured constraints. **No LLM is involved.**

Checks performed in order:

1. **Schema validation** (Pydantic): output must match expected JSON shape.
2. **Action validity**: BUY requires `stop_loss != null`; SELL requires open position.
3. **Position size**: `position_size_pct ≤ max_position_pct`.
4. **Concurrency**: open positions count `≤ max_simultaneous_trades`.
5. **Slippage estimate**: estimated execution price within `max_slippage_pct` of mid.
6. **R:R validation**: BUY must satisfy `(TP - entry) ≥ 1.5 × (entry - SL)`.
7. **SL distance**: BUY must satisfy `(entry - SL) ≥ 0.5 × ATR(1h)`.
8. **Daily stop**: if `daily_pnl_pct ≤ daily_stop_pct`, force HOLD.
9. **Total drawdown**: if `total_drawdown ≤ max_drawdown_pct`, force kill switch.
10. **Kill switch**: if `kill_switch == true`, reject all BUYs, allow SELL to close.

Rejected orders are logged in `decisions.rejected_reason` and never reach the Executor.

### 8.2 Circuit breakers

- **Daily stop**: when triggered, set `kill_switch_daily = true` until next 00:00 UTC.
- **Total drawdown**: when triggered, set `kill_switch = true` permanently. Requires manual user reset from UI with double confirmation.
- **LLM failure**: 5 consecutive LLM call failures (after retries) → engine pauses + alert.
- **Exchange failure**: 5 consecutive Binance API errors → engine pauses + alert.

### 8.3 Playbook safety

- All playbook versions retained (immutable history).
- Automatic rollback: if 7 days post-update show >2× drawdown vs prior 7 days, revert to previous version + alert.
- Manual rollback button in UI.
- New playbook is validated for structural integrity (sections present, word count) before being marked active.

---

## 9. Frontend

### 9.1 Pages / views

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | Dashboard | Live price, current position, P&L gauge, last decision card |
| `/trades` | TradeHistory | Closed trades table with filters |
| `/decisions` | DecisionLog | Audit trail of every LLM call |
| `/playbook` | PlaybookViewer | Active playbook + version history |
| `/config` | ConfigPanel | All editable runtime config (see §6.2) |
| `/health` | SystemHealth | Engine status, LLM latency, exchange connectivity |

### 9.2 Real-time updates

WebSocket connection from frontend to `web` container. Server-side, the WS feed polls Postgres every 1s for new events and pushes:

- `price_update` — every 5s
- `position_update` — when P&L changes by ≥0.1% or every 30s
- `decision_emitted` — when new row in `decisions`
- `trade_opened` / `trade_closed` — when state changes
- `playbook_updated` — when supervisor finishes a run
- `kill_switch_triggered` — immediate

### 9.3 Config panel (editable from UI)

See §6.2 for full default keys. The panel groups them in sections:
- **Riesgo** (max_position_pct, max_simultaneous_trades, daily_stop_pct, max_drawdown_pct, max_slippage_pct, default_rr_ratio)
- **Timing** (decisor_interval_min, supervisor_cron, mode toggle PAPER/LIVE)
- **LLM** (decisor_provider, supervisor_provider, fallback_provider, max_retries, timeout)
- **Data** (timeframes included, orderbook_levels, lookback_candles)
- **Prompts (advanced)** (edit system prompts, view/edit playbook manually, reset to default)
- **Notifications** (channel, alert triggers)
- **Kill Switch** (big red button)

Every change is written to `config` and audit-logged in `config_history`.

LIVE mode toggle requires double confirmation modal with explicit text input: `"CONFIRMO TRADING REAL"`.

---

## 10. Phase plan

| Phase | Duration | Deliverable | Success criterion |
|-------|----------|-------------|-------------------|
| **1. Infrastructure** | 2 weeks | Docker Compose, schema, Alembic migrations, CCXT testnet connection, PriceCollector + OrderBookCollector, basic health endpoints | Indicators computed and persisted; testnet auth works |
| **2. Decisor agent** | 1 week | LLMClient (Gemini + Groq fallback), ContextBuilder, PromptManager, Decisor loop in PAPER mode (no execution) | 48h of valid JSON decisions logged in DB, >99% parse rate |
| **3. Risk Gate + Executor** | 1 week | RiskGate, CircuitBreaker, Executor with CCXT, PositionManager, OrderTracker | Complete trades on testnet with SL/TP working |
| **4. Supervisor** | 1 week | APScheduler daily job, metrics computation, Gemini Pro call, playbook versioning, automatic rollback | Playbook v2 generated coherently after a paper trading day |
| **5. Frontend** | 1 week | React dashboard, WebSocket feed, all pages, config panel, kill switch | UI functional, kill switch tested end-to-end |
| **6. Backtesting + Paper trading** | 2 weeks | Backtesting module (vectorbt), 4 weeks minimum paper trading on testnet | Sharpe >1.0, max drawdown <5%, win rate >52% sustained 4 consecutive weeks |
| **7. Live trading** | Ongoing | Switch to LIVE with $200–500 USD real capital | Metrics maintained for 1 week before scaling |

---

## 11. Success criteria for v1

### Technical
- ✅ End-to-end flow: market data → decisor → risk gate → executor → outcome → supervisor → updated playbook
- ✅ JSON parse rate from LLM > 99%
- ✅ Kill switch closes all positions in < 10s
- ✅ Engine survives Binance WS disconnections (auto-reconnect)
- ✅ Engine survives LLM provider outages (fallback)
- ✅ All decisions audit-logged with prompt/response

### Trading (paper, 4-week window)
- ✅ Sharpe ratio > 1.0
- ✅ Max drawdown < 5%
- ✅ Win rate > 52%
- ✅ Profit factor > 1.5
- ✅ No HOLD-only stretches > 24 consecutive hours unjustified

### Operational
- ✅ Daily LLM cost = $0 (within free tiers)
- ✅ Total infra cost in dev = $0 (local Docker)
- ✅ Total infra cost in production v1 = $0–5/month (Oracle Free Tier or similar)

---

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM hallucinations producing bad orders | High | Risk Gate (deterministic) is the last line of defense |
| Free tier limits change / disappear | Medium | Multi-provider abstraction (Gemini + Groq); easy to swap |
| Binance API changes | Medium | CCXT abstracts most; pin version, monitor changelog |
| Supervisor generates toxic playbook | Medium | Automatic rollback on performance degradation; manual override |
| Race conditions on position close | Medium | Postgres advisory locks during executor critical section |
| Backtest results don't match live | High | Mandatory paper trading minimum 4 weeks; walk-forward validation |
| User leaves LIVE mode on after testing | High | LIVE toggle requires double confirmation; daily reminder if active >7d |
| Cold start without playbook | Low | Bootstrap `playbook_v0.md` committed to repo |
| All positions stuck open during LLM outage | Medium | Stop-loss orders are placed at exchange (broker-side), not bot-side |
| Database corruption / data loss | Medium | Daily `pg_dump` backup; Postgres data volume on persistent storage |

---

## 13. Future work (out of scope for v1)

- **v2.1** — Add sentiment analysis (CryptoPanic API + LLM summary).
- **v2.2** — Add basic on-chain signals (exchange net flows, stablecoin supply changes).
- **v2.3** — Multi-pair support (top 5 caps) with separate playbooks per pair.
- **v3.0** — Add a third "Risk Critic" agent for adversarial pre-trade review.
- **v3.1** — Telegram / Discord notification integrations.
- **v3.2** — Strategy A/B testing harness (run two playbooks in parallel with split capital).
- **v4.0** — Migrate executor to Go for sub-100ms latency (only if scalping mode added).

---

## 14. Open questions / things to validate during implementation

1. Exact Gemini 2.5 Flash free tier rate limits as of implementation date — confirm 1500 req/day still holds.
2. Binance Spot Testnet rate limits and parity with mainnet — confirm endpoints used in v1 are mirrored.
3. Whether `ccxt.pro` async WebSocket is needed or if vanilla `websockets` suffices for Binance Spot user-stream.
4. Best library for technical indicator computation — `pandas-ta` selected, but `talipp` may be faster for incremental updates.
5. Notification channel for v1 — defer or include email?

---

**End of design document.**
