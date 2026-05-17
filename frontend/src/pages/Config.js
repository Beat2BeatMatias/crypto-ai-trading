import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api/client";
/** Keys that the Supervisor agent can modify automatically via config suggestions. */
const SUPERVISOR_MANAGED_KEYS = new Set([
    "atr_timeframe",
    "sl_atr_multiplier",
    "sl_atr_max_multiplier",
    "min_rr_ratio",
    "decisor_interval_min",
    "max_position_pct",
    "conf_threshold_trending_up",
    "conf_threshold_range",
    "conf_threshold_high_vol",
    "min_confluences_buy",
    "min_fees_to_tp_ratio",
    "cooldown_after_sell_min",
    "rsi_overbought_1h",
    "expected_holding_max_min",
]);
const ALL_PROVIDERS = [
    "groq-llama-3.3-70b", "groq-compound-beta", "groq-compound-mini",
    "groq-llama-4-scout", "groq-gpt-oss-120b", "groq-gpt-oss-20b",
    "groq-qwen3-32b", "groq-llama-3.1-8b", "gemini-2.5-flash", "gemini-2.5-pro",
];
const PROVIDER_RPD = {
    "groq-llama-3.3-70b": "1K RPD · 12K TPM",
    "groq-compound-beta": "250 RPD · 70K TPM",
    "groq-compound-mini": "250 RPD · 70K TPM",
    "groq-llama-4-scout": "1K RPD · 30K TPM",
    "groq-gpt-oss-120b": "1K RPD · 8K TPM",
    "groq-gpt-oss-20b": "1K RPD · 8K TPM",
    "groq-qwen3-32b": "1K RPD",
    "groq-llama-3.1-8b": "14.4K RPD · 6K TPM",
    "gemini-2.5-flash": "20 RPD (free)",
    "gemini-2.5-pro": "5 RPD (free)",
};
const SELECT_OPTIONS = {
    decisor_provider: ["groq-llama-3.3-70b", "groq-compound-beta", "groq-compound-mini",
        "groq-llama-4-scout", "groq-gpt-oss-120b", "gemini-2.5-flash"],
    supervisor_provider: ["gemini-2.5-pro", "groq-llama-3.3-70b", "groq-compound-beta",
        "groq-llama-4-scout", "groq-gpt-oss-120b"],
    mode: ["PAPER_TRADING", "LIVE"],
    atr_timeframe: ["5m", "15m", "1h"],
};
const FALLBACK_KEYS = new Set(["fallback_providers", "supervisor_fallback_providers"]);
const fmt2 = (v) => v.toFixed(2);
const fmt1 = (v) => v.toFixed(1);
const fmtPct1 = (v) => `${(v * 100).toFixed(0)}%`;
const FIELD_DEFS = {
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
        description: "Porcentaje máximo del capital total que puede usarse en una sola entrada.",
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
    // ── Umbrales de decisión ──────────────────────────────────────────────────
    sl_atr_max_multiplier: {
        label: "Multiplicador SL máximo (ATR)",
        description: "Distancia máxima del Stop Loss en múltiplos del ATR. SL más amplio que este es rechazado.",
        type: "slider", min: 0.5, max: 3.0, step: 0.1, unit: "× ATR",
        format: fmt1, parse: parseFloat,
    },
    rsi_overbought_1h: {
        label: "RSI sobrecompra — 1h",
        description: "Si RSI 1h supera este umbral, se cancelan señales alcistas de timeframes menores.",
        type: "slider", min: 60, max: 85, step: 1, unit: "",
        format: v => String(v), parse: parseInt,
    },
    conf_threshold_trending_up: {
        label: "Umbral confianza — TRENDING_UP",
        description: "Confianza mínima para ejecutar BUY en régimen alcista. Menor valor = más trades.",
        type: "slider", min: 0.40, max: 0.85, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    conf_threshold_range: {
        label: "Umbral confianza — RANGE",
        description: "Confianza mínima para ejecutar BUY en régimen lateral. Más exigente que TRENDING_UP.",
        type: "slider", min: 0.50, max: 0.90, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    conf_threshold_high_vol: {
        label: "Umbral confianza — HIGH_VOLATILITY",
        description: "Confianza mínima para ejecutar BUY en alta volatilidad. Más exigente que RANGE.",
        type: "slider", min: 0.60, max: 0.95, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    // ── Fórmula de confianza — base confluencias ──────────────────────────────
    conf_base_0: {
        label: "Base — 0 confluencias",
        description: "Confianza base cuando no hay ninguna confluencia activa del playbook.",
        type: "slider", min: 0.10, max: 0.50, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    conf_base_1: {
        label: "Base — 1 confluencia",
        description: "Confianza base con 1 confluencia activa.",
        type: "slider", min: 0.30, max: 0.65, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    conf_base_2: {
        label: "Base — 2 confluencias",
        description: "Confianza base con 2 confluencias activas.",
        type: "slider", min: 0.45, max: 0.80, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    conf_base_3: {
        label: "Base — 3 confluencias",
        description: "Confianza base con 3 confluencias activas.",
        type: "slider", min: 0.60, max: 0.90, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    conf_base_4plus: {
        label: "Base — 4+ confluencias",
        description: "Confianza base con 4 o más confluencias activas.",
        type: "slider", min: 0.75, max: 1.00, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    // ── Fórmula de confianza — pesos timeframe ────────────────────────────────
    peso_timeframe_partial: {
        label: "Peso timeframe — solo 15m",
        description: "Multiplicador cuando solo el 15m confirma la dirección y el 1h es neutral.",
        type: "slider", min: 0.50, max: 1.00, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    peso_timeframe_minimal: {
        label: "Peso timeframe — solo 5m",
        description: "Multiplicador cuando solo el 5m confirma, con 15m y 1h discordantes.",
        type: "slider", min: 0.40, max: 0.90, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    // ── Fórmula de confianza — pesos régimen ─────────────────────────────────
    peso_regime_range: {
        label: "Peso régimen — RANGE",
        description: "Multiplicador de confianza base cuando el mercado está en rango lateral.",
        type: "slider", min: 0.50, max: 1.00, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    peso_regime_high_vol: {
        label: "Peso régimen — HIGH_VOLATILITY",
        description: "Multiplicador de confianza base en alta volatilidad. Generalmente menor que RANGE.",
        type: "slider", min: 0.40, max: 0.90, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    // ── Fórmula de confianza — ajustes ────────────────────────────────────────
    adj_volume_boost: {
        label: "Ajuste — boost por volumen",
        description: "Se suma a la confianza cuando el volumen del 5m supera el ratio configurado × la media.",
        type: "slider", min: 0.00, max: 0.15, step: 0.01, unit: "",
        format: fmt2, parse: parseFloat,
    },
    adj_volume_ratio: {
        label: "Ratio de volumen (boost)",
        description: "Múltiplo de la media de volumen 5m requerido para activar el boost de confianza.",
        type: "slider", min: 1.0, max: 3.0, step: 0.1, unit: "× avg",
        format: fmt1, parse: parseFloat,
    },
    adj_antipattern_penalty: {
        label: "Ajuste — penalización anti-patrón",
        description: "Se resta a la confianza cuando se detecta un anti-patrón (FOMO, overtrading, etc.).",
        type: "slider", min: -0.25, max: 0.00, step: 0.01, unit: "",
        format: fmt2, parse: parseFloat,
    },
    adj_spread_penalty: {
        label: "Ajuste — penalización spread",
        description: "Se resta a la confianza cuando el spread supera el umbral configurado.",
        type: "slider", min: -0.15, max: 0.00, step: 0.01, unit: "",
        format: fmt2, parse: parseFloat,
    },
    adj_spread_threshold_pct: {
        label: "Umbral spread (penalización)",
        description: "Spread máximo (% del precio) antes de aplicar la penalización de confianza.",
        type: "slider", min: 0.01, max: 0.20, step: 0.01, unit: "%",
        format: v => `${(v * 100).toFixed(2)}%`, parse: parseFloat,
    },
    adj_orderbook_penalty: {
        label: "Ajuste — penalización order book",
        description: "Se resta a la confianza cuando el bid_wall supera el ratio sobre ask_wall en zona contraria.",
        type: "slider", min: -0.15, max: 0.00, step: 0.01, unit: "",
        format: fmt2, parse: parseFloat,
    },
    adj_orderbook_ratio: {
        label: "Ratio bid/ask wall (penalización)",
        description: "Múltiplo bid_wall / ask_wall que activa la penalización de order book.",
        type: "slider", min: 2.0, max: 10.0, step: 0.5, unit: "× ask",
        format: fmt1, parse: parseFloat,
    },
    subjective_adj_max: {
        label: "Ajuste subjetivo máximo",
        description: "Límite del ajuste de confianza que el LLM puede aplicar por criterio propio (±). Valor más bajo = mayor control.",
        type: "slider", min: 0.00, max: 0.20, step: 0.01, unit: "",
        format: fmt2, parse: parseFloat,
    },
    confluence_weak_factor: {
        label: "Factor confluencia débil",
        description: "Multiplicador aplicado a una confluencia débil respecto a una sólida en el cálculo de confianza. 1.0 = igual peso.",
        type: "slider", min: 0.0, max: 1.0, step: 0.05, unit: "",
        format: fmt2, parse: parseFloat,
    },
    // ── Decisor v2 — controles operacionales ─────────────────────────────────
    min_fees_to_tp_ratio: {
        label: "Ratio mínimo TP / fees",
        description: "El movimiento al TP debe ser al menos este múltiplo del costo de fees ida y vuelta. Filtra trades con TP demasiado pequeño.",
        type: "slider", min: 1.5, max: 6.0, step: 0.1, unit: "× fees",
        format: fmt1, parse: parseFloat,
    },
    min_confluences_buy: {
        label: "Confluencias mínimas para BUY",
        description: "Cantidad mínima de confluencias del playbook requeridas para autorizar una entrada.",
        type: "slider", min: 1, max: 4, step: 1, unit: "confluencias",
        format: v => String(v), parse: parseInt,
    },
    cooldown_after_sell_min: {
        label: "Cooldown post-SELL",
        description: "Minutos de espera obligatoria tras un SELL antes de permitir una nueva entrada BUY.",
        type: "slider", min: 0, max: 120, step: 5, unit: "min",
        format: v => String(v), parse: parseInt,
    },
    expected_holding_max_min: {
        label: "Holding máximo esperado",
        description: "Tiempo máximo en minutos que se espera mantener una posición. Superar este tiempo activa la detección de trade zombie.",
        type: "slider", min: 30, max: 1440, step: 30, unit: "min",
        format: v => String(v), parse: parseInt,
    },
    // ── Sizing factors ────────────────────────────────────────────────────────
    factor_conf_60: {
        label: "Factor sizing — confianza 0.60-0.69",
        description: "Fracción del max_position_pct usada cuando la confianza está en el rango mínimo.",
        type: "slider", min: 0.20, max: 0.80, step: 0.05, unit: "",
        format: fmtPct1, parse: parseFloat,
    },
    factor_conf_70: {
        label: "Factor sizing — confianza 0.70-0.79",
        description: "Fracción del max_position_pct usada con confianza media.",
        type: "slider", min: 0.30, max: 0.90, step: 0.05, unit: "",
        format: fmtPct1, parse: parseFloat,
    },
    factor_conf_80: {
        label: "Factor sizing — confianza 0.80-0.89",
        description: "Fracción del max_position_pct usada con confianza alta.",
        type: "slider", min: 0.50, max: 1.00, step: 0.05, unit: "",
        format: fmtPct1, parse: parseFloat,
    },
    factor_conf_90: {
        label: "Factor sizing — confianza 0.90+",
        description: "Fracción del max_position_pct usada con confianza máxima.",
        type: "slider", min: 0.70, max: 1.00, step: 0.05, unit: "",
        format: fmtPct1, parse: parseFloat,
    },
    factor_regime_non_trending: {
        label: "Factor sizing — RANGE | HIGH_VOLATILITY",
        description: "Fracción del sizing base aplicada en regímenes no tendenciales (RANGE o alta volatilidad).",
        type: "slider", min: 0.20, max: 0.80, step: 0.05, unit: "",
        format: fmtPct1, parse: parseFloat,
    },
};
const GROUPS = [
    {
        title: "Gestión de riesgo",
        color: "amber",
        keys: ["sl_atr_multiplier", "sl_atr_max_multiplier", "min_rr_ratio", "default_rr_ratio",
            "max_position_pct", "max_simultaneous_trades", "daily_stop_pct", "max_drawdown_pct",
            "max_slippage_pct", "atr_timeframe"],
    },
    {
        title: "Motor de decisiones",
        color: "emerald",
        keys: ["decisor_interval_min", "orderbook_levels", "kill_switch"],
    },
    {
        title: "Umbrales de confianza",
        color: "sky",
        keys: ["rsi_overbought_1h", "conf_threshold_trending_up", "conf_threshold_range", "conf_threshold_high_vol"],
    },
    {
        title: "Fórmula de confianza",
        color: "violet",
        keys: [
            "conf_base_0", "conf_base_1", "conf_base_2", "conf_base_3", "conf_base_4plus",
            "peso_timeframe_partial", "peso_timeframe_minimal",
            "peso_regime_range", "peso_regime_high_vol",
            "adj_volume_boost", "adj_volume_ratio",
            "adj_antipattern_penalty",
            "adj_spread_penalty", "adj_spread_threshold_pct",
            "adj_orderbook_penalty", "adj_orderbook_ratio",
            "subjective_adj_max", "confluence_weak_factor",
        ],
    },
    {
        title: "Decisor v2 — Controles",
        color: "teal",
        keys: ["min_fees_to_tp_ratio", "min_confluences_buy", "cooldown_after_sell_min", "expected_holding_max_min"],
    },
    {
        title: "Sizing de posición",
        color: "rose",
        keys: ["factor_conf_60", "factor_conf_70", "factor_conf_80", "factor_conf_90", "factor_regime_non_trending"],
    },
    {
        title: "Modelos LLM",
        color: "indigo",
        keys: ["decisor_provider", "supervisor_provider", "llm_timeout_sec", "llm_max_retries"],
    },
    {
        title: "Scheduler",
        color: "zinc",
        keys: ["supervisor_cron"],
    },
];
function SupervisorHint({ entry }) {
    if (!entry)
        return null;
    const isManaged = SUPERVISOR_MANAGED_KEYS.has(entry.key);
    const bySupervisor = entry.last_changed_by === "supervisor";
    if (!isManaged && !bySupervisor)
        return null;
    const dateStr = entry.updated_at
        ? new Date(entry.updated_at).toLocaleString("es-AR", {
            hour12: false, day: "2-digit", month: "2-digit",
            year: "numeric", hour: "2-digit", minute: "2-digit",
        })
        : null;
    return (_jsxs("div", { className: "flex items-center gap-2 mt-1.5", children: [isManaged && (_jsx("span", { className: "inline-flex items-center gap-1 rounded-full bg-indigo-950 border border-indigo-800/50 px-1.5 py-0.5 text-[10px] text-indigo-400 font-medium leading-none", children: "\u26A1 auto-supervisor" })), bySupervisor && dateStr && (_jsxs("span", { className: "text-[10px] text-indigo-400/60 leading-none", children: ["Modificado por Supervisor \u00B7 ", dateStr] }))] }));
}
function SliderField({ fieldKey, def, value, entry, onSave }) {
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
    const handleChange = (v) => {
        setLocal(v);
        setDirty(v !== current);
    };
    return (_jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("div", { className: "font-medium text-sm text-zinc-100", children: def.label }), _jsx("div", { className: "text-xs text-zinc-500 mt-0.5", children: def.description })] }), _jsxs("div", { className: "flex items-center gap-2 ml-4 shrink-0", children: [_jsxs("span", { className: "font-mono text-sm font-semibold text-zinc-100 w-20 text-right", children: [fmt(local), " ", !def.format && def.unit ? def.unit : ""] }), dirty && (_jsx("button", { onClick: () => { onSave(fieldKey, String(local)); setDirty(false); }, className: "rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500 transition-colors whitespace-nowrap", children: "Guardar" }))] })] }), _jsxs("div", { className: "relative", children: [_jsx("input", { type: "range", min: def.min, max: def.max, step: def.step, value: local, onChange: e => handleChange(parse(e.target.value)), className: "w-full h-1.5 rounded-full appearance-none cursor-pointer bg-zinc-700", style: {
                            background: `linear-gradient(to right, #10b981 ${pct}%, #3f3f46 ${pct}%)`
                        } }), _jsxs("div", { className: "flex justify-between text-xs text-zinc-600 mt-1", children: [_jsx("span", { children: fmt(def.min) }), _jsx("span", { children: fmt(def.max) })] })] }), _jsx(SupervisorHint, { entry: entry })] }));
}
function ConfigField({ fieldKey, def, value, entry, onSave }) {
    const [local, setLocal] = useState(value);
    const [dirty, setDirty] = useState(false);
    useEffect(() => { setLocal(value); setDirty(false); }, [value]);
    if (def.type === "slider") {
        return _jsx(SliderField, { fieldKey: fieldKey, def: def, value: value, entry: entry, onSave: onSave });
    }
    if (def.type === "toggle") {
        const isOn = value === "true";
        return (_jsxs("div", { children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("div", { className: "font-medium text-sm text-zinc-100", children: def.label }), _jsx("div", { className: "text-xs text-zinc-500 mt-0.5", children: def.description })] }), _jsx("button", { onClick: () => onSave(fieldKey, isOn ? "false" : "true"), className: `relative inline-flex h-6 w-11 items-center rounded-full transition-colors ml-4 shrink-0 ${isOn ? "bg-red-600" : "bg-zinc-700"}`, children: _jsx("span", { className: `inline-block h-4 w-4 rounded-full bg-white transition-transform ${isOn ? "translate-x-6" : "translate-x-1"}` }) })] }), _jsx(SupervisorHint, { entry: entry })] }));
    }
    if (def.type === "select") {
        return (_jsxs("div", { children: [_jsxs("div", { className: "flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1", children: [_jsx("div", { className: "font-medium text-sm text-zinc-100", children: def.label }), _jsx("div", { className: "text-xs text-zinc-500 mt-0.5", children: def.description })] }), _jsx("select", { className: "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm text-zinc-100 cursor-pointer shrink-0", value: local, onChange: e => { setLocal(e.target.value); onSave(fieldKey, e.target.value); }, children: (SELECT_OPTIONS[fieldKey] ?? []).map(opt => (_jsx("option", { value: opt, children: opt }, opt))) })] }), _jsx(SupervisorHint, { entry: entry })] }));
    }
    return (_jsxs("div", { children: [_jsxs("div", { className: "flex items-start justify-between gap-4", children: [_jsxs("div", { className: "flex-1", children: [_jsx("div", { className: "font-medium text-sm text-zinc-100", children: def.label }), _jsx("div", { className: "text-xs text-zinc-500 mt-0.5", children: def.description })] }), _jsxs("div", { className: "flex items-center gap-2 shrink-0", children: [_jsx("input", { className: "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm w-40", value: local, onChange: e => { setLocal(e.target.value); setDirty(e.target.value !== value); } }), dirty && (_jsx("button", { onClick: () => { onSave(fieldKey, local); setDirty(false); }, className: "rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500", children: "Guardar" }))] })] }), _jsx(SupervisorHint, { entry: entry })] }));
}
function FallbackChain({ label, configKey, currentValue, onSave }) {
    const initial = currentValue.split(",").map(s => s.trim()).filter(Boolean);
    const [selected, setSelected] = useState(initial);
    const [dirty, setDirty] = useState(false);
    const toggle = (p) => {
        setDirty(true);
        setSelected(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p]);
    };
    const moveUp = (idx) => {
        if (idx === 0)
            return;
        const next = [...selected];
        [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
        setSelected(next);
        setDirty(true);
    };
    const moveDown = (idx) => {
        if (idx === selected.length - 1)
            return;
        const next = [...selected];
        [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
        setSelected(next);
        setDirty(true);
    };
    return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-5", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsxs("div", { children: [_jsx("h3", { className: "font-semibold text-sm", children: label }), _jsx("p", { className: "text-xs text-zinc-500 mt-0.5", children: "Se intentan en orden. Si el primero falla, pasa al siguiente." })] }), dirty && (_jsx("button", { onClick: () => { onSave(configKey, selected.join(",")); setDirty(false); }, className: "rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500 shrink-0", children: "Guardar cadena" }))] }), selected.length > 0 && (_jsxs("div", { className: "mb-3 space-y-1", children: [_jsx("p", { className: "text-xs text-zinc-500 uppercase mb-1", children: "Orden de fallback" }), selected.map((p, idx) => (_jsxs("div", { className: "flex items-center gap-2 rounded bg-zinc-800 px-3 py-1.5 text-xs", children: [_jsxs("span", { className: "text-zinc-500 w-4 shrink-0", children: [idx + 1, "."] }), _jsx("span", { className: "font-mono flex-1", children: p }), _jsx("span", { className: "text-zinc-600 text-xs", children: PROVIDER_RPD[p] ?? "" }), _jsx("button", { onClick: () => moveUp(idx), disabled: idx === 0, className: "text-zinc-500 hover:text-zinc-200 disabled:opacity-20 px-1", children: "\u2191" }), _jsx("button", { onClick: () => moveDown(idx), disabled: idx === selected.length - 1, className: "text-zinc-500 hover:text-zinc-200 disabled:opacity-20 px-1", children: "\u2193" }), _jsx("button", { onClick: () => toggle(p), className: "text-red-500 hover:text-red-300 px-1", children: "\u2715" })] }, p)))] })), _jsx("p", { className: "text-xs text-zinc-500 uppercase mb-1", children: "Agregar provider" }), _jsx("div", { className: "flex flex-wrap gap-2", children: ALL_PROVIDERS.filter(p => !selected.includes(p)).map(p => (_jsxs("button", { onClick: () => toggle(p), className: "rounded border border-zinc-700 px-2 py-1 text-xs font-mono hover:border-emerald-500 hover:text-emerald-400 transition-colors", children: ["+ ", p, PROVIDER_RPD[p] && _jsxs("span", { className: "text-zinc-600 ml-1", children: ["(", PROVIDER_RPD[p], ")"] })] }, p))) })] }));
}
const COLOR_CLASSES = {
    amber: { border: "border-amber-800/40", title: "text-amber-300", dot: "bg-amber-400" },
    emerald: { border: "border-emerald-800/40", title: "text-emerald-300", dot: "bg-emerald-400" },
    sky: { border: "border-sky-800/40", title: "text-sky-300", dot: "bg-sky-400" },
    violet: { border: "border-violet-800/40", title: "text-violet-300", dot: "bg-violet-400" },
    teal: { border: "border-teal-800/40", title: "text-teal-300", dot: "bg-teal-400" },
    rose: { border: "border-rose-800/40", title: "text-rose-300", dot: "bg-rose-400" },
    indigo: { border: "border-indigo-800/40", title: "text-indigo-300", dot: "bg-indigo-400" },
    zinc: { border: "border-zinc-700", title: "text-zinc-300", dot: "bg-zinc-400" },
};
export function Config() {
    const [entries, setEntries] = useState([]);
    const [edits, setEdits] = useState({});
    const [liveModal, setLiveModal] = useState(false);
    const [liveConfirm, setLiveConfirm] = useState("");
    const [drawdownResetModal, setDrawdownResetModal] = useState(false);
    const [drawdownResetting, setDrawdownResetting] = useState(false);
    const [msg, setMsg] = useState("");
    const [supRunning, setSupRunning] = useState(false);
    const entryMap = Object.fromEntries(entries.map(e => [e.key, e]));
    const reload = () => api.config().then(setEntries).catch(() => { });
    useEffect(() => { reload(); }, []);
    const onSave = async (key, valueOverride) => {
        const value = valueOverride ?? edits[key];
        if (value === undefined)
            return;
        await api.setConfig(key, value);
        if (!valueOverride)
            setEdits(p => { const { [key]: _, ...rest } = p; return rest; });
        reload();
        setMsg(`Guardado: ${key} = ${value}`);
        setTimeout(() => setMsg(""), 3000);
    };
    const onLive = async () => {
        try {
            await api.setMode("LIVE", liveConfirm);
            setLiveModal(false);
            setLiveConfirm("");
            reload();
            setMsg("Modo LIVE activado.");
        }
        catch {
            setMsg("Confirmación incorrecta. Escribe exactamente: CONFIRMO TRADING REAL");
        }
    };
    const onRunSupervisor = async () => {
        setSupRunning(true);
        try {
            await api.runSupervisor();
            setMsg("Supervisor encolado. Se ejecutará en el próximo tick del decisor (máx. 15 min).");
        }
        catch {
            setMsg("Error al encolar el supervisor.");
        }
        setTimeout(() => { setMsg(""); setSupRunning(false); }, 6000);
    };
    const onResetDrawdown = async () => {
        setDrawdownResetting(true);
        try {
            const res = await api.resetDrawdown();
            setDrawdownResetModal(false);
            setMsg(`Pico de drawdown reseteado. El engine medirá desde ${new Date(res.reset_at).toLocaleString("es-AR", { hour12: false })}.`);
        }
        catch {
            setMsg("Error al resetear el drawdown.");
        }
        setTimeout(() => { setMsg(""); setDrawdownResetting(false); }, 6000);
    };
    const modeEntry = entries.find(e => e.key === "mode");
    const fallbackDecissor = entries.find(e => e.key === "fallback_providers");
    const fallbackSupervisor = entries.find(e => e.key === "supervisor_fallback_providers");
    const knownKeys = new Set([
        ...GROUPS.flatMap(g => g.keys),
        ...Array.from(FALLBACK_KEYS),
        "mode",
        // keys internas / legacy — no se exponen en el UI
        "drawdown_reset_ts",
        "supervisor_run_now",
        "pending_execute",
        "fallback_provider",
    ]);
    const otherEntries = entries.filter(e => !knownKeys.has(e.key));
    return (_jsxs("div", { className: "space-y-4", children: [msg && (_jsx("div", { className: "rounded bg-zinc-800 px-4 py-2 text-sm text-emerald-400 border border-emerald-800/40", children: msg })), _jsxs("div", { className: "flex flex-wrap gap-3", children: [modeEntry?.value === "PAPER_TRADING" && (_jsx("button", { onClick: () => setLiveModal(true), className: "rounded bg-amber-600 px-4 py-2 text-sm font-semibold hover:bg-amber-500", children: "Cambiar a LIVE (trading real) \u2192" })), _jsx("button", { onClick: onRunSupervisor, disabled: supRunning, className: "rounded bg-indigo-600 px-4 py-2 text-sm font-semibold hover:bg-indigo-500 disabled:opacity-50", children: supRunning ? "Encolando..." : "Ejecutar Supervisor ahora" }), _jsx("button", { onClick: () => setDrawdownResetModal(true), className: "rounded bg-amber-700 px-4 py-2 text-sm font-semibold hover:bg-amber-600", children: "Resetear pico de drawdown" })] }), GROUPS.map(group => {
                const c = COLOR_CLASSES[group.color];
                const groupEntries = group.keys
                    .map(k => entryMap[k])
                    .filter((e) => e !== undefined);
                if (groupEntries.length === 0)
                    return null;
                return (_jsxs("div", { className: `rounded-xl bg-zinc-900 p-5 border ${c.border}`, children: [_jsxs("div", { className: "flex items-center gap-2 mb-5", children: [_jsx("span", { className: `w-2 h-2 rounded-full ${c.dot}` }), _jsx("h2", { className: `text-sm font-semibold uppercase tracking-wide ${c.title}`, children: group.title })] }), _jsx("div", { className: "space-y-6", children: groupEntries.map(e => {
                                const def = FIELD_DEFS[e.key];
                                if (!def)
                                    return null;
                                return (_jsx("div", { children: _jsx(ConfigField, { fieldKey: e.key, def: def, value: e.value, entry: e, onSave: (k, v) => onSave(k, v) }) }, e.key));
                            }) })] }, group.title));
            }), fallbackDecissor && (_jsx(FallbackChain, { label: "Cadena de fallback \u2014 Decisor", configKey: "fallback_providers", currentValue: fallbackDecissor.value, onSave: onSave })), fallbackSupervisor && (_jsx(FallbackChain, { label: "Cadena de fallback \u2014 Supervisor", configKey: "supervisor_fallback_providers", currentValue: fallbackSupervisor.value, onSave: onSave })), otherEntries.length > 0 && (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-5 border border-zinc-800", children: [_jsx("h2", { className: "text-sm font-semibold text-zinc-500 uppercase tracking-wide mb-4", children: "Otros par\u00E1metros" }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { children: _jsxs("tr", { className: "text-xs uppercase text-zinc-600 border-b border-zinc-800", children: [_jsx("th", { className: "text-left py-2 pr-4 w-64", children: "Key" }), _jsx("th", { className: "text-left pr-4", children: "Valor" }), _jsx("th", { className: "w-24" })] }) }), _jsx("tbody", { children: otherEntries.map(e => (_jsxs("tr", { className: "border-t border-zinc-800", children: [_jsx("td", { className: "py-2 pr-4 font-mono text-zinc-400 text-xs align-top pt-3", children: e.key }), _jsxs("td", { className: "pr-4 align-top pt-2", children: [SELECT_OPTIONS[e.key] ? (_jsx("select", { className: "rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm text-zinc-100", value: edits[e.key] ?? e.value, onChange: ev => { setEdits(p => ({ ...p, [e.key]: ev.target.value })); onSave(e.key, ev.target.value); }, children: SELECT_OPTIONS[e.key].map(opt => _jsx("option", { value: opt, children: opt }, opt)) })) : (_jsx("input", { className: "w-full rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm", value: edits[e.key] ?? e.value, onChange: ev => setEdits(p => ({ ...p, [e.key]: ev.target.value })) })), _jsx(SupervisorHint, { entry: e })] }), _jsx("td", { className: "align-top pt-2", children: edits[e.key] !== undefined && edits[e.key] !== e.value && (_jsx("button", { onClick: () => onSave(e.key), className: "rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500", children: "Guardar" })) })] }, e.key))) })] })] })), drawdownResetModal && (_jsx("div", { className: "fixed inset-0 bg-black/70 flex items-center justify-center z-50", children: _jsxs("div", { className: "rounded-xl bg-zinc-900 border border-amber-700/50 p-6 max-w-sm w-full", children: [_jsx("h3", { className: "text-lg font-semibold mb-2 text-amber-300", children: "\u26A0\uFE0F Resetear pico de drawdown" }), _jsx("p", { className: "text-sm text-zinc-300 mb-4", children: "El engine dejar\u00E1 de considerar el historial anterior como referencia del pico m\u00E1ximo. A partir del pr\u00F3ximo tick, el drawdown se medir\u00E1 desde el balance actual." }), _jsx("p", { className: "text-xs text-zinc-500 mb-4", children: "No se eliminan datos. El historial queda intacto y el reset puede rehacerse en cualquier momento." }), _jsxs("div", { className: "flex gap-2 justify-end", children: [_jsx("button", { onClick: () => setDrawdownResetModal(false), className: "rounded bg-zinc-700 px-4 py-2 text-sm hover:bg-zinc-600", children: "Cancelar" }), _jsx("button", { onClick: onResetDrawdown, disabled: drawdownResetting, className: "rounded bg-amber-600 px-4 py-2 text-sm font-semibold hover:bg-amber-500 disabled:opacity-50", children: drawdownResetting ? "Reseteando..." : "Confirmar reset" })] })] }) })), liveModal && (_jsx("div", { className: "fixed inset-0 bg-black/70 flex items-center justify-center z-50", children: _jsxs("div", { className: "rounded-xl bg-zinc-900 border border-zinc-700 p-6 max-w-sm w-full", children: [_jsx("h3", { className: "text-lg font-semibold mb-2", children: "\u26A0\uFE0F Confirmar modo LIVE" }), _jsxs("p", { className: "text-sm text-zinc-300 mb-3", children: ["Esto activa trading con dinero real en Binance. Escribe exactamente:", _jsx("code", { className: "block mt-2 bg-zinc-800 px-3 py-2 rounded text-amber-300", children: "CONFIRMO TRADING REAL" })] }), _jsx("input", { className: "w-full rounded bg-zinc-800 border border-zinc-700 px-2 py-1 mb-3", value: liveConfirm, onChange: e => setLiveConfirm(e.target.value) }), _jsxs("div", { className: "flex gap-2 justify-end", children: [_jsx("button", { onClick: () => setLiveModal(false), className: "rounded bg-zinc-700 px-4 py-2 text-sm", children: "Cancelar" }), _jsx("button", { onClick: onLive, className: "rounded bg-red-600 px-4 py-2 text-sm hover:bg-red-500", children: "Activar LIVE" })] })] }) }))] }));
}
