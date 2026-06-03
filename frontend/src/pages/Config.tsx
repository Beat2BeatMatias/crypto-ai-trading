import { useEffect, useState } from "react";
import { api } from "../api/client";
import { RuntimeMismatchBanner } from "../components/RuntimeMismatchBanner";
import { TradingContextBadges } from "../components/TradingContextBadges";
import type { ConfigEntry, TradingContext } from "../types";

const ALL_PROVIDERS = [
  "groq-llama-3.3-70b", "groq-compound-beta", "groq-compound-mini",
  "groq-llama-4-scout", "groq-gpt-oss-120b", "groq-gpt-oss-20b",
  "groq-qwen3-32b", "groq-llama-3.1-8b", "gemini-2.5-flash", "gemini-2.5-pro",
  "ollama-deepseek-v3.2", "ollama-deepseek-v4-flash", "ollama-deepseek-v4-pro",
  "ollama-kimi-k2-thinking", "ollama-kimi-k2.6", "ollama-qwen3.5-32b",
  "ollama-qwen3.5-122b", "ollama-qwen3-next-80b", "ollama-gemma4-27b",
  "ollama-nemotron-3-super", "ollama-gpt-oss-20b", "ollama-gpt-oss-120b",
  "ollama-glm-5", "ollama-minimax-m2",
];

const PROVIDER_RPD: Record<string, string> = {
  "groq-llama-3.3-70b":  "1K RPD · 12K TPM",
  "groq-compound-beta":  "250 RPD · 70K TPM",
  "groq-compound-mini":  "250 RPD · 70K TPM",
  "groq-llama-4-scout":  "1K RPD · 30K TPM",
  "groq-gpt-oss-120b":   "1K RPD · 8K TPM · reasoning ✓",
  "groq-gpt-oss-20b":    "1K RPD · 8K TPM · reasoning ✓",
  "groq-qwen3-32b":      "1K RPD · reasoning ✓",
  "groq-llama-3.1-8b":   "14.4K RPD · 6K TPM",
  "gemini-2.5-flash":    "20 RPD (free)",
  "gemini-2.5-pro":      "5 RPD (free)",
  "ollama-deepseek-v3.2":       "cloud · thinking ✓",
  "ollama-deepseek-v4-flash":   "cloud · thinking ✓",
  "ollama-deepseek-v4-pro":     "cloud · thinking ✓",
  "ollama-kimi-k2-thinking":    "cloud · thinking ✓",
  "ollama-kimi-k2.6":           "cloud",
  "ollama-qwen3.5-32b":         "cloud · 32b",
  "ollama-qwen3.5-122b":        "cloud · 122b",
  "ollama-qwen3-next-80b":      "cloud · 80b",
  "ollama-gemma4-27b":          "cloud · 27b",
  "ollama-nemotron-3-super":    "cloud · 120b",
  "ollama-gpt-oss-20b":         "cloud · 20b",
  "ollama-gpt-oss-120b":        "cloud · 120b",
  "ollama-glm-5":               "cloud",
  "ollama-minimax-m2":          "cloud",
};

const SELECT_OPTIONS: Record<string, string[]> = {
  decisor_provider:    ["groq-llama-3.3-70b", "groq-compound-beta", "groq-compound-mini",
                        "groq-llama-4-scout", "groq-gpt-oss-120b", "gemini-2.5-flash",
                        "ollama-deepseek-v4-flash", "ollama-deepseek-v4-pro",
                        "ollama-kimi-k2-thinking", "ollama-qwen3.5-32b", "ollama-qwen3.5-122b"],
  supervisor_provider: ["gemini-2.5-pro", "groq-llama-3.3-70b", "groq-compound-beta",
                        "groq-llama-4-scout", "groq-gpt-oss-120b",
                        "ollama-deepseek-v4-pro", "ollama-kimi-k2-thinking",
                        "ollama-qwen3.5-122b", "ollama-nemotron-3-super"],
  postmortem_provider: ["gemini-2.5-flash", "gemini-2.5-pro", "groq-llama-3.3-70b",
                        "groq-compound-beta", "groq-compound-mini", "groq-llama-4-scout",
                        "groq-gpt-oss-120b", "groq-gpt-oss-20b", "groq-qwen3-32b", "groq-llama-3.1-8b",
                        "ollama-kimi-k2-thinking", "ollama-deepseek-v4-flash", "ollama-qwen3.5-32b"],
  mode:                ["PAPER_TRADING", "LIVE"],
  atr_timeframe:       ["5m", "15m", "1h"],
  trading_product:     ["spot", "futures"],
  margin_mode:         ["isolated", "cross"],
};

const FALLBACK_KEYS = new Set([
  "fallback_providers",
  "supervisor_fallback_providers",
  "postmortem_fallback_providers",
]);

type FieldDef = {
  label: string;
  description: string;
  type: "slider" | "select" | "text" | "toggle";
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  format?: (v: number) => string;
  parse?: (v: string) => number;
};

const fmt2 = (v: number) => v.toFixed(2);
const fmt1 = (v: number) => v.toFixed(1);

const FIELD_DEFS: Record<string, FieldDef> = {
  sl_atr_multiplier: {
    label: "Multiplicador SL (ATR)",
    description: "Distancia mínima del Stop Loss medida en múltiplos del ATR. Mayor valor = SL más amplio.",
    type: "slider", min: 0.1, max: 2.0, step: 0.1, unit: "× ATR",
    format: v => v.toFixed(1), parse: parseFloat,
  },
  min_rr_ratio: {
    label: "R:R mínimo",
    description: "Ratio Riesgo/Recompensa mínimo para aceptar una entrada. Ej: 1.8 = TP debe ser 1.8× el SL.",
    type: "slider", min: 1.0, max: 4.0, step: 0.1, unit: ":1",
    format: v => v.toFixed(1), parse: parseFloat,
  },
  default_rr_ratio: {
    label: "R:R por defecto (Take Profit)",
    description: "Ratio usado para calcular el nivel de Take Profit cuando el LLM no especifica uno. Ej: 2.0 = TP a 2× el SL.",
    type: "slider", min: 1.0, max: 5.0, step: 0.1, unit: ":1",
    format: fmt1, parse: parseFloat,
  },
  max_position_pct: {
    label: "Tamaño máximo de posición",
    description: "Tope de notional por trade (% del capital en spot o del margen disponible en futuros). Usado por R1 y el guard de min_notional.",
    type: "slider", min: 0.01, max: 0.20, step: 0.01, unit: "%",
    format: v => `${(v * 100).toFixed(0)}%`, parse: parseFloat,
  },
  max_simultaneous_trades: {
    label: "Posiciones simultáneas máximas",
    description: "Cantidad máxima de trades abiertos al mismo tiempo.",
    type: "slider", min: 1, max: 6, step: 1, unit: "trades",
    format: v => String(v), parse: parseInt,
  },
  decisor_interval_min: {
    label: "Intervalo de decisión",
    description: "Cada cuántos minutos el LLM evalúa si entrar o salir del mercado.",
    type: "slider", min: 5, max: 60, step: 5, unit: "min",
    format: v => String(v), parse: parseInt,
  },
  orderbook_levels: {
    label: "Niveles del order book",
    description: "Cantidad de niveles bid/ask incluidos en el contexto enviado al LLM. Más niveles = más contexto, mayor costo de tokens.",
    type: "slider", min: 5, max: 20, step: 1, unit: "niveles",
    format: v => String(v), parse: parseInt,
  },
  daily_stop_pct: {
    label: "Stop diario (máx. pérdida del día)",
    description: "Si el P&L del día cae a este porcentaje, el engine fuerza HOLD hasta las 00:00 UTC.",
    type: "slider", min: -0.10, max: -0.01, step: 0.01, unit: "%",
    format: v => `${(v * 100).toFixed(0)}%`, parse: parseFloat,
  },
  max_drawdown_pct: {
    label: "Drawdown máximo (circuit breaker)",
    description: "Si el drawdown total desde el pico supera este valor, activa el kill switch automáticamente.",
    type: "slider", min: -0.30, max: -0.05, step: 0.01, unit: "%",
    format: v => `${(v * 100).toFixed(0)}%`, parse: parseFloat,
  },
  max_slippage_pct: {
    label: "Slippage máximo aceptado",
    description: "Si el precio de ejecución difiere más de este porcentaje del precio esperado, la orden es rechazada.",
    type: "slider", min: 0.001, max: 0.010, step: 0.001, unit: "%",
    format: v => `${(v * 100).toFixed(2)}%`, parse: parseFloat,
  },
  atr_timeframe: {
    label: "Timeframe del ATR",
    description: "Timeframe usado para calcular el ATR de referencia en SL/TP y en el contexto del LLM.",
    type: "select",
  },
  decisor_provider: {
    label: "Proveedor LLM — Decisor",
    description: "Modelo primario que toma las decisiones de trading.",
    type: "select",
  },
  supervisor_provider: {
    label: "Proveedor LLM — Supervisor",
    description: "Modelo primario que genera el playbook diario.",
    type: "select",
  },
  postmortem_provider: {
    label: "Proveedor LLM — Post-mortem",
    description: "Modelo primario que analiza decisiones con outcome negativo y genera lecciones.",
    type: "select",
  },
  postmortem_enabled: {
    label: "Post-mortem habilitado",
    description: "Si está activo, tras outcome attribution se encadena el análisis LLM de malas decisiones.",
    type: "toggle",
  },
  postmortem_max_per_tick: {
    label: "Post-mortems por tick",
    description: "Máximo de análisis LLM por ejecución del job (cada intervalo de outcome attribution).",
    type: "slider", min: 1, max: 20, step: 1, unit: "análisis",
    format: v => String(v), parse: parseInt,
  },
  block_k_max_lines: {
    label: "Bloque K — máx. líneas",
    description: "Cantidad de lecciones post-mortem recientes inyectadas en el prompt del Decisor.",
    type: "slider", min: 1, max: 10, step: 1, unit: "líneas",
    format: v => String(v), parse: parseInt,
  },
  block_k_window_hours: {
    label: "Bloque K — ventana horaria",
    description: "Solo lecciones completadas en las últimas N horas aparecen en el Bloque K.",
    type: "slider", min: 24, max: 168, step: 24, unit: "h",
    format: v => `${v}h`, parse: parseInt,
  },
  llm_timeout_sec: {
    label: "Timeout LLM",
    description: "Segundos máximos de espera por respuesta del LLM antes de pasar al fallback.",
    type: "slider", min: 10, max: 120, step: 5, unit: "seg",
    format: v => String(v), parse: parseInt,
  },
  llm_max_retries: {
    label: "Reintentos LLM",
    description: "Cantidad de reintentos por provider antes de marcarlo como fallido.",
    type: "slider", min: 1, max: 5, step: 1, unit: "intentos",
    format: v => String(v), parse: parseInt,
  },
  kill_switch: {
    label: "Kill Switch",
    description: "Cuando está activo, el engine solo permite SELL para cerrar posiciones abiertas. No abre nuevas.",
    type: "toggle",
  },
  supervisor_cron: {
    label: "Cron del Supervisor",
    description: "Expresión cron que define cuándo corre el supervisor automáticamente (UTC).",
    type: "text",
  },
  outcome_attribution_interval_min: {
    label: "Intervalo del job de outcomes",
    description: "Cada cuántos minutos corre el job que clasifica decisiones pasadas (MFE/MAE). Valores más bajos actualizan los marcadores del chart más seguido pero añaden carga.",
    type: "slider", min: 15, max: 240, step: 15, unit: "min",
    format: v => String(v), parse: parseInt,
  },
  outcome_attribution_horizon_min: {
    label: "Horizonte de evaluación contrafactual",
    description: "Cuántos minutos hacia adelante se analizan las velas 1m después de cada decisión para calcular MFE/MAE. Debe ser mayor que el holding promedio esperado.",
    type: "slider", min: 60, max: 1440, step: 30, unit: "min",
    format: v => v >= 60 ? `${v / 60}h` : `${v}min`, parse: parseInt,
  },
  outcome_attribution_window_hours: {
    label: "Ventana de análisis (attribution + post-mortem)",
    description: "Solo decisiones de las últimas N horas entran al job de outcome attribution y a la cola post-mortem. Compartida entre ambos jobs. Default 25 h.",
    type: "slider", min: 12, max: 72, step: 1, unit: "h",
    format: v => `${v}h`, parse: parseInt,
  },
  outcome_coverage_threshold_pct: {
    label: "Umbral de cobertura OHLCV (máx. faltantes)",
    description: "Si más de este % de velas 1m faltan en la ventana de evaluación, la decisión se clasifica como UNKNOWN en lugar de arriesgarse a una clasificación incorrecta.",
    type: "slider", min: 5, max: 50, step: 5, unit: "%",
    format: v => `${v}%`, parse: parseInt,
  },

  // ── LLM-Centric (v1.3) ───────────────────────────────────────────────────
  min_position_size: {
    label: "Tamaño mínimo de posición",
    description: "Piso de sizing (% del capital o margen disponible en futuros). El servidor no ejecuta aperturas por debajo de este piso.",
    type: "slider", min: 0.001, max: 0.05, step: 0.001, unit: "%",
    format: v => `${(v * 100).toFixed(1)}%`, parse: parseFloat,
  },
  coherence_strict_mode: {
    label: "Modo estricto del CoherenceChecker",
    description: "Cuando está activo, inconsistencias factuales (C1–C3 y C1P–C3P en futuros) bloquean la ejecución forzando HOLD. Por defecto son solo advertencias.",
    type: "toggle",
  },
  two_pass_enabled: {
    label: "Two-pass habilitado",
    description: "Segunda llamada al LLM en el mismo ciclo si hay C1/C2/C3 o C1P/C2P/C3P (confluencias vs indicadores).",
    type: "toggle",
  },
  decisor_llm_temperature: {
    label: "Temperatura LLM — Decisor",
    description: "Aleatoriedad de las respuestas del Decisor. Valores bajos (≈0.1) reducen variación entre ciclos y facilitan decisiones más estables.",
    type: "slider", min: 0.0, max: 1.0, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  decisor_self_consistency_n: {
    label: "Self-consistency (muestras por ciclo)",
    description: "Llamadas LLM por ciclo con votación mayoritaria. 0 = una sola llamada. 3 = tres muestras; sin mayoría clara → HOLD por consenso incierto.",
    type: "slider", min: 0, max: 5, step: 1, unit: "muestras",
    format: v => (v === 0 ? "off" : String(v)), parse: parseInt,
  },
  risk_per_trade_pct: {
    label: "Riesgo por trade (% capital / margen)",
    description: "Fracción del capital (spot) o margen disponible (futuros) en riesgo si toca el SL. El servidor calcula position_size_pct = riesgo / distancia SL (tope max_position_pct).",
    type: "slider", min: 0.001, max: 0.02, step: 0.001, unit: "%",
    format: v => `${(v * 100).toFixed(2)}%`, parse: parseFloat,
  },
  supervisor_config_window_hours: {
    label: "Ventana métricas — auto-config",
    description: "Horas de historial para sugerencias y auto-apply de parámetros numéricos. El playbook diario sigue usando 24h.",
    type: "slider", min: 24, max: 336, step: 24, unit: "h",
    format: v => `${v}h`, parse: parseInt,
  },
  supervisor_config_min_evaluated_decisions: {
    label: "Mín. decisiones evaluadas (auto-config)",
    description: "Outcomes maduros mínimos en la ventana de config antes de permitir auto-apply de parámetros.",
    type: "slider", min: 10, max: 200, step: 5, unit: "decisiones",
    format: v => String(v), parse: parseInt,
  },
  supervisor_config_auto_apply: {
    label: "Auto-apply de parámetros (Supervisor)",
    description: "Si está desactivado, el Supervisor solo sugiere cambios numéricos sin aplicarlos (recomendado en paper hasta validar edge).",
    type: "toggle",
  },

  // ── Guías para el LLM (no enforcement, solo referencia en el prompt) ──────
  sl_atr_max_multiplier: {
    label: "Multiplicador SL máximo (ATR)",
    description: "Distancia máxima del Stop Loss en múltiplos del ATR. SL más amplio que este es rechazado por el Risk Gate (R4).",
    type: "slider", min: 0.5, max: 20.0, step: 0.1, unit: "× ATR",
    format: fmt1, parse: parseFloat,
  },
  rsi_overbought_1h: {
    label: "RSI sobrecompra — 1h (guía LLM)",
    description: "RSI 1h considerado sobrecomprado. El LLM lo usa como referencia contextual en su razonamiento. No bloquea ejecuciones.",
    type: "slider", min: 60, max: 85, step: 1, unit: "",
    format: v => String(v), parse: parseInt,
  },
  conf_threshold_trending_up: {
    label: "Confianza mínima sugerida — TRENDING_UP",
    description: "Guía LLM para entradas alcistas (BUY). En futuros, TRENDING_UP desincentiva SHORT.",
    type: "slider", min: 0.40, max: 0.85, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  conf_threshold_range: {
    label: "Confianza mínima sugerida — RANGE",
    description: "Guía para el LLM: confidence mínima recomendada para BUY en mercado lateral. El LLM tiene autonomía para desviarse con justificación.",
    type: "slider", min: 0.50, max: 0.90, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  conf_threshold_high_vol: {
    label: "Confianza mínima sugerida — HIGH_VOLATILITY",
    description: "Guía para el LLM: confidence mínima recomendada para BUY en alta volatilidad. El LLM tiene autonomía para desviarse con justificación.",
    type: "slider", min: 0.60, max: 0.95, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },

  // ── Calibración de confidence (guías para el LLM) ────────────────────────
  conf_base_0: {
    label: "Confidence base — 0 confluencias",
    description: "Guía LLM: nivel de referencia de confidence con 0 confluencias. El LLM lo usa para calibrar su propia confidence_base.",
    type: "slider", min: 0.10, max: 0.50, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  conf_base_1: {
    label: "Confidence base — 1 confluencia",
    description: "Guía LLM: nivel de referencia con 1 confluencia activa.",
    type: "slider", min: 0.30, max: 0.65, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  conf_base_2: {
    label: "Confidence base — 2 confluencias",
    description: "Guía LLM: nivel de referencia con 2 confluencias activas.",
    type: "slider", min: 0.45, max: 0.80, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  conf_base_3: {
    label: "Confidence base — 3 confluencias",
    description: "Guía LLM: nivel de referencia con 3 confluencias activas.",
    type: "slider", min: 0.60, max: 0.90, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  conf_base_4plus: {
    label: "Confidence base — 4+ confluencias",
    description: "Guía LLM: nivel de referencia con 4 o más confluencias activas (cap).",
    type: "slider", min: 0.75, max: 1.00, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  peso_regime_range: {
    label: "Factor régimen — RANGE (guía LLM)",
    description: "Guía LLM: multiplicador de confianza base sugerido para régimen lateral. Se inyecta en el system prompt como referencia.",
    type: "slider", min: 0.50, max: 1.00, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  peso_regime_high_vol: {
    label: "Factor régimen — HIGH_VOLATILITY (guía LLM)",
    description: "Guía LLM: multiplicador de confianza base sugerido para alta volatilidad. Generalmente menor que RANGE.",
    type: "slider", min: 0.40, max: 0.90, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },
  adj_volume_boost: {
    label: "Boost por volumen (guía LLM)",
    description: "Guía LLM: boost de confidence sugerido cuando el volumen supera adj_volume_ratio × la media. Referencia para confidence_adjustment.",
    type: "slider", min: 0.00, max: 0.15, step: 0.01, unit: "",
    format: fmt2, parse: parseFloat,
  },
  adj_volume_ratio: {
    label: "Ratio de volumen para boost (guía LLM)",
    description: "Guía LLM: múltiplo del volumen medio que activa el boost de confianza. Referencia inyectada en el prompt.",
    type: "slider", min: 1.0, max: 3.0, step: 0.1, unit: "× avg",
    format: fmt1, parse: parseFloat,
  },
  adj_spread_penalty: {
    label: "Penalización por spread (guía LLM)",
    description: "Guía LLM: penalización de confidence sugerida cuando el spread supera el umbral. Referencia para confidence_adjustment.",
    type: "slider", min: -0.15, max: 0.00, step: 0.01, unit: "",
    format: fmt2, parse: parseFloat,
  },
  adj_spread_threshold_pct: {
    label: "Umbral de spread (guía LLM)",
    description: "Spread (% del precio) que el LLM considera zona de mayor riesgo. Usado como referencia en el system prompt.",
    type: "slider", min: 0.01, max: 0.20, step: 0.01, unit: "%",
    format: v => `${(v * 100).toFixed(2)}%`, parse: parseFloat,
  },
  subjective_adj_max: {
    label: "Límite de ajuste subjetivo",
    description: "Máximo ajuste de confidence que el LLM puede declarar en confidence_adjustment (±). Enforced por Pydantic. Menor = mayor control sobre el ajuste.",
    type: "slider", min: 0.00, max: 0.20, step: 0.01, unit: "",
    format: fmt2, parse: parseFloat,
  },
  confluence_weak_factor: {
    label: "Factor confluencia débil (guía LLM)",
    description: "Guía LLM: peso relativo de una confluencia débil vs una sólida al calibrar confidence. 1.0 = igual peso. Referencia en el contexto.",
    type: "slider", min: 0.0, max: 1.0, step: 0.05, unit: "",
    format: fmt2, parse: parseFloat,
  },

  // ── Decisor — controles operacionales ────────────────────────────────────
  min_fees_to_tp_ratio: {
    label: "Ratio mínimo TP / fees",
    description: "El movimiento al TP debe ser al menos este múltiplo del costo de fees ida y vuelta. Filtra trades con TP demasiado pequeño.",
    type: "slider", min: 1.5, max: 6.0, step: 0.1, unit: "× fees",
    format: fmt1, parse: parseFloat,
  },
  min_confluences_buy: {
    label: "Confluencias mínimas (BUY / long)",
    description: "Mínimo de confluencias alcistas (A–H) para BUY. Guía en el prompt.",
    type: "slider", min: 1, max: 4, step: 1, unit: "confluencias",
    format: v => String(v), parse: parseInt,
  },
  min_confluences_short: {
    label: "Confluencias mínimas (SHORT)",
    description: "Mínimo de confluencias bajistas (I/J/F…) para SHORT en futuros. Guía en el prompt.",
    type: "slider", min: 1, max: 4, step: 1, unit: "confluencias",
    format: v => String(v), parse: parseInt,
  },
  cooldown_after_sell_min: {
    label: "Cooldown post-cierre",
    description: "Minutos tras un SELL (cerrar long o short) antes de recomendar otra apertura en el prompt.",
    type: "slider", min: 0, max: 120, step: 5, unit: "min",
    format: v => String(v), parse: parseInt,
  },
  expected_holding_max_min: {
    label: "Holding máximo esperado",
    description: "Tiempo máximo en minutos que se espera mantener una posición. Superar este tiempo activa la detección de trade zombie.",
    type: "slider", min: 30, max: 1440, step: 30, unit: "min",
    format: v => String(v), parse: parseInt,
  },

  trading_product: {
    label: "Producto de mercado",
    description: "spot = BTC/USDT Spot. futures = USDT-M perpetuo (BUY/SHORT). Tras cambiar, reiniciá trading-engine para aplicar adapter y guard de capital.",
    type: "select",
  },
  max_leverage: {
    label: "Apalancamiento máximo",
    description: "Tope R12. Default 1x. Subir solo con historial y buffer de liquidación validados.",
    type: "slider", min: 1, max: 5, step: 1, unit: "×",
    format: v => `${v}×`, parse: parseInt,
  },
  margin_mode: {
    label: "Modo de margen",
    description: "isolated recomendado. El engine llama setup_symbol al arrancar en futuros.",
    type: "select",
  },
  funding_rate_max_pct: {
    label: "Funding máximo (abs)",
    description: "R15: no abre si |funding| supera este umbral (fracción, ej. 0.05 = 5%).",
    type: "slider", min: 0.01, max: 0.15, step: 0.01, unit: "",
    format: v => `${(v * 100).toFixed(0)}%`, parse: parseFloat,
  },
  liquidation_buffer_atr: {
    label: "Buffer liquidación (× ATR)",
    description: "R13: distancia mínima entre SL y precio de liquidación estimado.",
    type: "slider", min: 1.0, max: 5.0, step: 0.5, unit: "× ATR",
    format: fmt1, parse: parseFloat,
  },
  min_roundtrip_fee_pct: {
    label: "Piso fees round-trip (%)",
    description: "Mínimo % usado en R10 y en el prompt cuando Binance reporta fees 0 (testnet). Equivalente LIVE ~0.20.",
    type: "slider", min: 0.0, max: 0.5, step: 0.05, unit: "%",
    format: v => `${v.toFixed(2)}%`, parse: parseFloat,
  },
};

type ConfigGroup = { title: string; keys: string[]; color: string; note?: string };

const GLOBAL_GROUPS: ConfigGroup[] = [
  {
    title: "Motor de decisiones",
    color: "emerald",
    keys: ["decisor_interval_min", "orderbook_levels", "kill_switch"],
  },
  {
    title: "LLM-Centric — Decisor autónomo",
    color: "sky",
    keys: [
      "coherence_strict_mode", "two_pass_enabled",
      "decisor_llm_temperature", "decisor_self_consistency_n",
    ],
    note: "Coherencia y two-pass aplican a BUY y SHORT. El sizing lo calcula el servidor (pestaña Mercado).",
  },
  {
    title: "Modelos LLM",
    color: "rose",
    keys: ["decisor_provider", "supervisor_provider", "llm_timeout_sec", "llm_max_retries"],
  },
  {
    title: "Supervisor — auto-config de parámetros",
    color: "cyan",
    keys: [
      "supervisor_config_window_hours",
      "supervisor_config_min_evaluated_decisions",
      "supervisor_config_auto_apply",
    ],
    note: "Playbook y diagnóstico usan 24h. Auto-apply de min_rr, sl_atr, etc. usa la ventana larga.",
  },
  {
    title: "Post-mortem — Aprendizaje",
    color: "orange",
    keys: [
      "postmortem_enabled",
      "postmortem_provider",
      "postmortem_max_per_tick",
      "block_k_max_lines",
      "block_k_window_hours",
    ],
    note: "Encadenado a Outcome Attribution. Ventana compartida con `outcome_attribution_window_hours`.",
  },
  {
    title: "Scheduler",
    color: "zinc",
    keys: ["supervisor_cron"],
  },
  {
    title: "Outcome Attribution",
    color: "amber",
    keys: [
      "outcome_attribution_interval_min",
      "outcome_attribution_horizon_min",
      "outcome_attribution_window_hours",
      "outcome_coverage_threshold_pct",
    ],
    note: "Clasificación MFE/MAE de decisiones (incluye SHORT). Efecto en el próximo tick del job.",
  },
];

const MARKET_GROUPS: ConfigGroup[] = [
  {
    title: "Gestión de riesgo",
    color: "amber",
    keys: [
      "sl_atr_multiplier", "sl_atr_max_multiplier", "min_rr_ratio", "default_rr_ratio",
      "max_position_pct", "min_position_size", "risk_per_trade_pct", "max_simultaneous_trades",
      "daily_stop_pct", "max_drawdown_pct", "max_slippage_pct", "atr_timeframe",
      "min_roundtrip_fee_pct",
    ],
    note: "Enforcement Risk Gate R1–R11 (spot) y R12–R15 (futuros). En perp, max_position_pct y risk aplican sobre margen disponible.",
  },
  {
    title: "Decisor — Controles operacionales",
    color: "teal",
    keys: [
      "min_fees_to_tp_ratio", "min_confluences_buy", "min_confluences_short",
      "cooldown_after_sell_min", "expected_holding_max_min",
    ],
    note: "min_fees_to_tp_ratio = R10. Confluencias y cooldown son guías en el prompt (BUY y SHORT).",
  },
  {
    title: "Guías LLM — Umbrales de confianza",
    color: "violet",
    keys: ["conf_threshold_trending_up", "conf_threshold_range", "conf_threshold_high_vol", "rsi_overbought_1h"],
    note: "Guías en el prompt; no bloquean ejecución. rsi_overbought_1h relevante para shorts (confluencia I).",
  },
  {
    title: "Guías LLM — Calibración de confidence",
    color: "indigo",
    keys: [
      "conf_base_0", "conf_base_1", "conf_base_2", "conf_base_3", "conf_base_4plus",
      "peso_regime_range", "peso_regime_high_vol",
      "adj_volume_boost", "adj_volume_ratio",
      "adj_spread_penalty", "adj_spread_threshold_pct",
      "subjective_adj_max", "confluence_weak_factor",
    ],
    note: "El servidor calcula confidence_base; regime_factor es direccional (TRENDING_DOWN favorece SHORT).",
  },
];

const FUTURES_GROUP: ConfigGroup = {
  title: "Derivados — Futuros USDT-M",
  color: "violet",
  keys: [
    "max_leverage", "margin_mode", "funding_rate_max_pct",
    "liquidation_buffer_atr",
  ],
  note: "Solo aplica con trading_product=futures. Reiniciá el engine tras cambios. Margen en wallet Futuros de Binance; si available×max_position_pct < min_notional, el engine hace downgrade a spot.",
};

const INTERNAL_KEYS = new Set([
  "drawdown_reset_ts", "live_since_ts", "supervisor_run_now",
  "engine_paused", "engine_pause_reason", "pending_execute", "fallback_provider",
  "postmortem_interval_min", "confluence_promotion_min_occurrences",
  "confluence_promotion_window_days", "confluence_registry_max_active",
  "peso_timeframe_partial", "peso_timeframe_minimal",
  "adj_antipattern_penalty", "adj_orderbook_penalty", "adj_orderbook_ratio",
  "factor_conf_60", "factor_conf_70", "factor_conf_80", "factor_conf_90",
  "factor_regime_non_trending", "mode",
]);

function allManagedKeys(): Set<string> {
  return new Set([
    ...GLOBAL_GROUPS.flatMap(g => g.keys),
    ...MARKET_GROUPS.flatMap(g => g.keys),
    ...FUTURES_GROUP.keys,
    "trading_product",
    ...Array.from(FALLBACK_KEYS),
  ]);
}

function ConfigGroupPanel({
  group,
  entryMap,
  onSave,
}: {
  group: ConfigGroup;
  entryMap: Record<string, ConfigEntry>;
  onSave: (key: string, value: string) => void;
}) {
  const c = COLOR_CLASSES[group.color] ?? COLOR_CLASSES.zinc;
  const groupEntries = group.keys
    .map(k => entryMap[k])
    .filter((e): e is ConfigEntry => e !== undefined);
  if (groupEntries.length === 0) return null;
  return (
    <div className={`rounded-xl bg-zinc-900 p-5 border ${c.border}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2 h-2 rounded-full ${c.dot}`} />
        <h2 className={`text-sm font-semibold uppercase tracking-wide ${c.title}`}>{group.title}</h2>
      </div>
      {group.note && (
        <p className="text-xs text-zinc-500 mb-4 leading-relaxed">{group.note}</p>
      )}
      <div className="space-y-6">
        {groupEntries.map(e => {
          const def = FIELD_DEFS[e.key];
          if (!def) return null;
          return (
            <div key={e.key}>
              <ConfigField fieldKey={e.key} def={def} value={e.value} onSave={onSave} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SliderField({ fieldKey, def, value, onSave }: {
  fieldKey: string;
  def: FieldDef;
  value: string;
  onSave: (key: string, value: string) => void;
}) {
  const parse = def.parse ?? parseFloat;
  const fmt = def.format ?? (v => String(v));
  const current = parse(value);
  const [local, setLocal] = useState(current);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setLocal(parse(value));
    setDirty(false);
  }, [value]);

  const pct = def.min !== undefined && def.max !== undefined
    ? ((local - def.min) / (def.max - def.min)) * 100
    : 50;

  const handleChange = (v: number) => {
    setLocal(v);
    setDirty(v !== current);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <div className="font-medium text-sm text-zinc-100">{def.label}</div>
          <div className="text-xs text-zinc-500 mt-0.5">{def.description}</div>
        </div>
        <div className="flex items-center gap-2 ml-4 shrink-0">
          <span className="font-mono text-sm font-semibold text-zinc-100 w-20 text-right">
            {fmt(local)} {!def.format && def.unit ? def.unit : ""}
          </span>
          {dirty && (
            <button
              onClick={() => { onSave(fieldKey, String(local)); setDirty(false); }}
              className="rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500 transition-colors whitespace-nowrap">
              Guardar
            </button>
          )}
        </div>
      </div>
      <div className="relative">
        <input
          type="range"
          min={def.min} max={def.max} step={def.step}
          value={local}
          onChange={e => handleChange(parse(e.target.value))}
          className="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-zinc-700"
          style={{
            background: `linear-gradient(to right, #10b981 ${pct}%, #3f3f46 ${pct}%)`
          }}
        />
        <div className="flex justify-between text-xs text-zinc-600 mt-1">
          <span>{fmt(def.min!)}</span>
          <span>{fmt(def.max!)}</span>
        </div>
      </div>
    </div>
  );
}

function ConfigField({ fieldKey, def, value, onSave }: {
  fieldKey: string;
  def: FieldDef;
  value: string;
  onSave: (key: string, value: string) => void;
}) {
  const [local, setLocal] = useState(value);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { setLocal(value); setDirty(false); }, [value]);

  if (def.type === "slider") {
    return <SliderField fieldKey={fieldKey} def={def} value={value} onSave={onSave} />;
  }

  if (def.type === "toggle") {
    const isOn = value === "true";
    return (
      <div>
        <div className="flex items-center justify-between">
          <div>
            <div className="font-medium text-sm text-zinc-100">{def.label}</div>
            <div className="text-xs text-zinc-500 mt-0.5">{def.description}</div>
          </div>
          <button
            onClick={() => onSave(fieldKey, isOn ? "false" : "true")}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ml-4 shrink-0 ${
              isOn ? "bg-red-600" : "bg-zinc-700"
            }`}>
            <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
              isOn ? "translate-x-6" : "translate-x-1"
            }`} />
          </button>
        </div>
      </div>
    );
  }

  if (def.type === "select") {
    return (
      <div>
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <div className="font-medium text-sm text-zinc-100">{def.label}</div>
            <div className="text-xs text-zinc-500 mt-0.5">{def.description}</div>
          </div>
          <select
            className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm text-zinc-100 cursor-pointer shrink-0"
            value={local}
            onChange={e => { setLocal(e.target.value); onSave(fieldKey, e.target.value); }}>
            {(SELECT_OPTIONS[fieldKey] ?? []).map(opt => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="font-medium text-sm text-zinc-100">{def.label}</div>
          <div className="text-xs text-zinc-500 mt-0.5">{def.description}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <input
            className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm w-40"
            value={local}
            onChange={e => { setLocal(e.target.value); setDirty(e.target.value !== value); }}
          />
          {dirty && (
            <button onClick={() => { onSave(fieldKey, local); setDirty(false); }}
              className="rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500">
              Guardar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function FallbackChain({ label, configKey, currentValue, onSave }: {
  label: string; configKey: string; currentValue: string;
  onSave: (key: string, value: string) => void;
}) {
  const initial = currentValue.split(",").map(s => s.trim()).filter(Boolean);
  const [selected, setSelected] = useState<string[]>(initial);
  const [dirty, setDirty] = useState(false);

  const toggle = (p: string) => {
    setDirty(true);
    setSelected(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]);
  };
  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...selected];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    setSelected(next); setDirty(true);
  };
  const moveDown = (idx: number) => {
    if (idx === selected.length - 1) return;
    const next = [...selected];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    setSelected(next); setDirty(true);
  };

  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="font-semibold text-sm">{label}</h3>
          <p className="text-xs text-zinc-500 mt-0.5">Se intentan en orden. Si el primero falla, pasa al siguiente.</p>
        </div>
        {dirty && (
          <button onClick={() => { onSave(configKey, selected.join(",")); setDirty(false); }}
            className="rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500 shrink-0">
            Guardar cadena
          </button>
        )}
      </div>
      {selected.length > 0 && (
        <div className="mb-3 space-y-1">
          <p className="text-xs text-zinc-500 uppercase mb-1">Orden de fallback</p>
          {selected.map((p, idx) => (
            <div key={p} className="flex items-center gap-2 rounded bg-zinc-800 px-3 py-1.5 text-xs">
              <span className="text-zinc-500 w-4 shrink-0">{idx + 1}.</span>
              <span className="font-mono flex-1">{p}</span>
              <span className="text-zinc-600 text-xs">{PROVIDER_RPD[p] ?? ""}</span>
              <button onClick={() => moveUp(idx)} disabled={idx === 0}
                className="text-zinc-500 hover:text-zinc-200 disabled:opacity-20 px-1">↑</button>
              <button onClick={() => moveDown(idx)} disabled={idx === selected.length - 1}
                className="text-zinc-500 hover:text-zinc-200 disabled:opacity-20 px-1">↓</button>
              <button onClick={() => toggle(p)} className="text-red-500 hover:text-red-300 px-1">✕</button>
            </div>
          ))}
        </div>
      )}
      <p className="text-xs text-zinc-500 uppercase mb-1">Agregar provider</p>
      <div className="flex flex-wrap gap-2">
        {ALL_PROVIDERS.filter(p => !selected.includes(p)).map(p => (
          <button key={p} onClick={() => toggle(p)}
            className="rounded border border-zinc-700 px-2 py-1 text-xs font-mono hover:border-emerald-500 hover:text-emerald-400 transition-colors">
            + {p}
            {PROVIDER_RPD[p] && <span className="text-zinc-600 ml-1">({PROVIDER_RPD[p]})</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

const COLOR_CLASSES: Record<string, { border: string; title: string; dot: string }> = {
  amber:   { border: "border-amber-800/40",   title: "text-amber-300",   dot: "bg-amber-400" },
  orange:  { border: "border-orange-800/40",  title: "text-orange-300",  dot: "bg-orange-400" },
  cyan:    { border: "border-cyan-800/40",    title: "text-cyan-300",    dot: "bg-cyan-400" },
  emerald: { border: "border-emerald-800/40", title: "text-emerald-300", dot: "bg-emerald-400" },
  sky:     { border: "border-sky-800/40",     title: "text-sky-300",     dot: "bg-sky-400" },
  violet:  { border: "border-violet-800/40",  title: "text-violet-300",  dot: "bg-violet-400" },
  teal:    { border: "border-teal-800/40",    title: "text-teal-300",    dot: "bg-teal-400" },
  rose:    { border: "border-rose-800/40",    title: "text-rose-300",    dot: "bg-rose-400" },
  indigo:  { border: "border-indigo-800/40",  title: "text-indigo-300",  dot: "bg-indigo-400" },
  zinc:    { border: "border-zinc-700",        title: "text-zinc-300",    dot: "bg-zinc-400" },
};

type ConfigTab = "global" | "market";

export function Config() {
  const [entries, setEntries] = useState<ConfigEntry[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [liveModal, setLiveModal] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState("");
  const [drawdownResetModal, setDrawdownResetModal] = useState(false);
  const [drawdownResetting, setDrawdownResetting] = useState(false);
  const [msg, setMsg] = useState("");
  const [supRunning, setSupRunning] = useState(false);
  const [tab, setTab] = useState<ConfigTab>("global");
  const [tradingCtx, setTradingCtx] = useState<TradingContext | null>(null);

  const entryMap = Object.fromEntries(entries.map(e => [e.key, e]));
  const tradingProduct = entryMap.trading_product?.value === "futures" ? "futures" : "spot";

  const reload = () => api.config().then(setEntries).catch(() => {});
  const loadTradingCtx = () =>
    fetch("/api/health")
      .then(r => r.json())
      .then(d => setTradingCtx(d?.trading ?? null))
      .catch(() => setTradingCtx(null));

  useEffect(() => {
    reload();
    loadTradingCtx();
    const id = setInterval(loadTradingCtx, 30_000);
    return () => clearInterval(id);
  }, []);

  const onSave = async (key: string, valueOverride?: string) => {
    const value = valueOverride ?? edits[key];
    if (value === undefined) return;
    await api.setConfig(key, value);
    if (!valueOverride) setEdits(p => { const { [key]: _, ...rest } = p; return rest; });
    reload();
    loadTradingCtx();
    const restartHint = key === "trading_product"
      ? " Reiniciá trading-engine para aplicar el producto."
      : "";
    setMsg(`Guardado: ${key} = ${value}.${restartHint}`);
    setTimeout(() => setMsg(""), 5000);
  };

  const onLive = async () => {
    try {
      await api.setMode("LIVE", liveConfirm);
      setLiveModal(false); setLiveConfirm(""); reload();
      setMsg("Modo LIVE activado.");
    } catch {
      setMsg("Confirmación incorrecta. Escribe exactamente: CONFIRMO TRADING REAL");
    }
  };

  const onRunSupervisor = async () => {
    setSupRunning(true);
    try {
      await api.runSupervisor();
      setMsg("Supervisor encolado. Se ejecutará en el próximo tick del decisor (máx. 15 min).");
    } catch { setMsg("Error al encolar el supervisor."); }
    setTimeout(() => { setMsg(""); setSupRunning(false); }, 6000);
  };

  const onResetDrawdown = async () => {
    setDrawdownResetting(true);
    try {
      const res = await api.resetDrawdown();
      setDrawdownResetModal(false);
      setMsg(`Pico de drawdown reseteado. El engine medirá desde ${new Date(res.reset_at).toLocaleString("es-AR", { hour12: false })}.`);
    } catch { setMsg("Error al resetear el drawdown."); }
    setTimeout(() => { setMsg(""); setDrawdownResetting(false); }, 6000);
  };

  const modeEntry = entries.find(e => e.key === "mode");
  const tradingProductEntry = entryMap.trading_product;
  const fallbackDecissor = entries.find(e => e.key === "fallback_providers");
  const fallbackSupervisor = entries.find(e => e.key === "supervisor_fallback_providers");
  const fallbackPostMortem = entries.find(e => e.key === "postmortem_fallback_providers");

  const managed = allManagedKeys();
  const otherEntries = entries.filter(
    e => !managed.has(e.key) && !INTERNAL_KEYS.has(e.key),
  );

  const tabBtn = (id: ConfigTab, label: string) => (
    <button
      type="button"
      onClick={() => setTab(id)}
      className={`px-4 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors ${
        tab === id
          ? "border-emerald-400 text-white bg-zinc-800"
          : "border-transparent text-zinc-500 hover:text-zinc-300"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-zinc-100">Configuración</h1>
        <TradingContextBadges ctx={tradingCtx} />
      </div>

      {tradingCtx?.runtime_mismatch && (
        <RuntimeMismatchBanner ctx={tradingCtx} />
      )}

      {msg && (
        <div className="rounded bg-zinc-800 px-4 py-2 text-sm text-emerald-400 border border-emerald-800/40">
          {msg}
        </div>
      )}

      <div className="flex flex-wrap gap-3">
        {modeEntry?.value === "PAPER_TRADING" && (
          <button onClick={() => setLiveModal(true)}
            className="rounded bg-amber-600 px-4 py-2 text-sm font-semibold hover:bg-amber-500">
            Cambiar a LIVE (trading real) →
          </button>
        )}
        <button onClick={onRunSupervisor} disabled={supRunning}
          className="rounded bg-indigo-600 px-4 py-2 text-sm font-semibold hover:bg-indigo-500 disabled:opacity-50">
          {supRunning ? "Encolando..." : "Ejecutar Supervisor ahora"}
        </button>
        <button onClick={() => setDrawdownResetModal(true)}
          className="rounded bg-amber-700 px-4 py-2 text-sm font-semibold hover:bg-amber-600">
          Resetear pico de drawdown
        </button>
      </div>

      <div className="rounded-xl bg-zinc-900 border border-zinc-800 p-4 flex flex-wrap items-end gap-6">
        {tradingProductEntry && FIELD_DEFS.trading_product && (
          <div className="min-w-[220px] flex-1">
            <ConfigField
              fieldKey="trading_product"
              def={FIELD_DEFS.trading_product}
              value={tradingProductEntry.value}
              onSave={onSave}
            />
          </div>
        )}
        <div className="text-xs text-zinc-500 max-w-md pb-1">
          <p>
            Modo paper/LIVE:{" "}
            <span className="text-zinc-300 font-medium">{modeEntry?.value ?? "—"}</span>
            {modeEntry?.value === "PAPER_TRADING" && (
              <span className="text-zinc-600"> · usá el botón abajo para pasar a LIVE</span>
            )}
          </p>
          <p className="mt-1">
            Testnet/mainnet viene de <code className="text-zinc-400">BINANCE_TESTNET</code> en{" "}
            <code className="text-zinc-400">.env</code>, no de esta pantalla.
          </p>
        </div>
      </div>

      <div className="flex border-b border-zinc-800 gap-1">
        {tabBtn("global", "Global")}
        {tabBtn("market", tradingProduct === "futures" ? "Mercado — Futuros" : "Mercado — Spot")}
      </div>

      {tab === "global" && (
        <div className="space-y-4">
          {GLOBAL_GROUPS.map(group => (
            <ConfigGroupPanel key={group.title} group={group} entryMap={entryMap} onSave={onSave} />
          ))}
        </div>
      )}

      {tab === "market" && (
        <div className="space-y-4">
          {tradingProduct === "spot" && (
            <div className="rounded-lg border border-sky-900/40 bg-sky-950/20 px-4 py-3 text-sm text-sky-200">
              Producto <strong>Spot</strong>: solo acciones BUY / SELL / HOLD. No aplica apalancamiento,
              funding ni buffer de liquidación.
            </div>
          )}
          {MARKET_GROUPS.map(group => (
            <ConfigGroupPanel key={group.title} group={group} entryMap={entryMap} onSave={onSave} />
          ))}
          {tradingProduct === "futures" && (
            <ConfigGroupPanel group={FUTURES_GROUP} entryMap={entryMap} onSave={onSave} />
          )}
        </div>
      )}

      {tab === "global" && fallbackDecissor && (
        <FallbackChain label="Cadena de fallback — Decisor" configKey="fallback_providers"
          currentValue={fallbackDecissor.value} onSave={onSave} />
      )}
      {tab === "global" && fallbackSupervisor && (
        <FallbackChain label="Cadena de fallback — Supervisor" configKey="supervisor_fallback_providers"
          currentValue={fallbackSupervisor.value} onSave={onSave} />
      )}
      {tab === "global" && fallbackPostMortem && (
        <FallbackChain label="Cadena de fallback — Post-mortem" configKey="postmortem_fallback_providers"
          currentValue={fallbackPostMortem.value} onSave={onSave} />
      )}

      {/* Parámetros sin UI dedicada */}
      {otherEntries.length > 0 && (
        <div className="rounded-xl bg-zinc-900 p-5 border border-zinc-800">
          <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wide mb-4">Otros parámetros</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase text-zinc-600 border-b border-zinc-800">
                <th className="text-left py-2 pr-4 w-64">Key</th>
                <th className="text-left pr-4">Valor</th>
                <th className="w-24"></th>
              </tr>
            </thead>
            <tbody>
              {otherEntries.map(e => (
                <tr key={e.key} className="border-t border-zinc-800">
                  <td className="py-2 pr-4 font-mono text-zinc-400 text-xs align-top pt-3">{e.key}</td>
                  <td className="pr-4 align-top pt-2">
                    {SELECT_OPTIONS[e.key] ? (
                      <select
                        className="rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm text-zinc-100"
                        value={edits[e.key] ?? e.value}
                        onChange={ev => { setEdits(p => ({ ...p, [e.key]: ev.target.value })); onSave(e.key, ev.target.value); }}>
                        {SELECT_OPTIONS[e.key].map(opt => <option key={opt} value={opt}>{opt}</option>)}
                      </select>
                    ) : (
                      <input
                        className="w-full rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm"
                        value={edits[e.key] ?? e.value}
                        onChange={ev => setEdits(p => ({ ...p, [e.key]: ev.target.value }))}
                      />
                    )}
                  </td>
                  <td className="align-top pt-2">
                    {edits[e.key] !== undefined && edits[e.key] !== e.value && (
                      <button onClick={() => onSave(e.key)}
                        className="rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500">
                        Guardar
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal Reset Drawdown */}
      {drawdownResetModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="rounded-xl bg-zinc-900 border border-amber-700/50 p-6 max-w-sm w-full">
            <h3 className="text-lg font-semibold mb-2 text-amber-300">⚠️ Resetear pico de drawdown</h3>
            <p className="text-sm text-zinc-300 mb-4">
              El engine dejará de considerar el historial anterior como referencia del pico máximo.
              A partir del próximo tick, el drawdown se medirá desde el balance actual.
            </p>
            <p className="text-xs text-zinc-500 mb-4">
              No se eliminan datos. El historial queda intacto y el reset puede rehacerse en cualquier momento.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setDrawdownResetModal(false)}
                className="rounded bg-zinc-700 px-4 py-2 text-sm hover:bg-zinc-600">
                Cancelar
              </button>
              <button
                onClick={onResetDrawdown}
                disabled={drawdownResetting}
                className="rounded bg-amber-600 px-4 py-2 text-sm font-semibold hover:bg-amber-500 disabled:opacity-50">
                {drawdownResetting ? "Reseteando..." : "Confirmar reset"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal LIVE */}
      {liveModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
          <div className="rounded-xl bg-zinc-900 border border-zinc-700 p-6 max-w-sm w-full">
            <h3 className="text-lg font-semibold mb-2">⚠️ Confirmar modo LIVE</h3>
            <p className="text-sm text-zinc-300 mb-3">
              Esto activa trading con dinero real en Binance. Escribe exactamente:
              <code className="block mt-2 bg-zinc-800 px-3 py-2 rounded text-amber-300">CONFIRMO TRADING REAL</code>
            </p>
            <input className="w-full rounded bg-zinc-800 border border-zinc-700 px-2 py-1 mb-3"
              value={liveConfirm} onChange={e => setLiveConfirm(e.target.value)} />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setLiveModal(false)} className="rounded bg-zinc-700 px-4 py-2 text-sm">Cancelar</button>
              <button onClick={onLive} className="rounded bg-red-600 px-4 py-2 text-sm hover:bg-red-500">Activar LIVE</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
