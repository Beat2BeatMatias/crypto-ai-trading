import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConfigEntry, ConfigSuggestions } from "../types";

const ALL_PROVIDERS = [
  "groq-llama-3.3-70b", "groq-compound-beta", "groq-compound-mini",
  "groq-llama-4-scout", "groq-gpt-oss-120b", "groq-gpt-oss-20b",
  "groq-qwen3-32b", "groq-llama-3.1-8b", "gemini-2.5-flash", "gemini-2.5-pro",
];

const PROVIDER_RPD: Record<string, string> = {
  "groq-llama-3.3-70b":  "1K RPD · 12K TPM",
  "groq-compound-beta":  "250 RPD · 70K TPM",
  "groq-compound-mini":  "250 RPD · 70K TPM",
  "groq-llama-4-scout":  "1K RPD · 30K TPM",
  "groq-gpt-oss-120b":   "1K RPD · 8K TPM",
  "groq-gpt-oss-20b":    "1K RPD · 8K TPM",
  "groq-qwen3-32b":      "1K RPD",
  "groq-llama-3.1-8b":   "14.4K RPD · 6K TPM",
  "gemini-2.5-flash":    "20 RPD (free)",
  "gemini-2.5-pro":      "5 RPD (free)",
};

const SELECT_OPTIONS: Record<string, string[]> = {
  decisor_provider:    ["groq-llama-3.3-70b", "groq-compound-beta", "groq-compound-mini",
                        "groq-llama-4-scout", "groq-gpt-oss-120b", "gemini-2.5-flash"],
  supervisor_provider: ["gemini-2.5-pro", "groq-llama-3.3-70b", "groq-compound-beta",
                        "groq-llama-4-scout", "groq-gpt-oss-120b"],
  mode:                ["PAPER_TRADING", "LIVE"],
  atr_timeframe:       ["5m", "15m", "1h"],
};

const FALLBACK_KEYS = new Set(["fallback_providers", "supervisor_fallback_providers"]);

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
};

const GROUPS: { title: string; keys: string[]; color: string }[] = [
  {
    title: "Gestión de riesgo",
    color: "amber",
    keys: ["sl_atr_multiplier", "min_rr_ratio", "max_position_pct", "max_simultaneous_trades",
           "daily_stop_pct", "max_drawdown_pct", "max_slippage_pct", "atr_timeframe"],
  },
  {
    title: "Motor de decisiones",
    color: "emerald",
    keys: ["decisor_interval_min", "kill_switch"],
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
    );
  }

  if (def.type === "select") {
    return (
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
    );
  }

  return (
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
  emerald: { border: "border-emerald-800/40", title: "text-emerald-300", dot: "bg-emerald-400" },
  indigo:  { border: "border-indigo-800/40",  title: "text-indigo-300",  dot: "bg-indigo-400" },
  zinc:    { border: "border-zinc-700",        title: "text-zinc-300",    dot: "bg-zinc-400" },
};

export function Config() {
  const [entries, setEntries] = useState<ConfigEntry[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [liveModal, setLiveModal] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [supRunning, setSupRunning] = useState(false);
  const [suggestions, setSuggestions] = useState<ConfigSuggestions | null>(null);
  const [appliedKeys, setAppliedKeys] = useState<Set<string>>(new Set());

  const entryMap = Object.fromEntries(entries.map(e => [e.key, e.value]));

  const reload = () => api.config().then(setEntries).catch(() => {});
  useEffect(() => {
    reload();
    api.configSuggestions().then(setSuggestions).catch(() => {});
  }, []);

  const onSave = async (key: string, valueOverride?: string) => {
    const value = valueOverride ?? edits[key];
    if (value === undefined) return;
    await api.setConfig(key, value);
    if (!valueOverride) setEdits(p => { const { [key]: _, ...rest } = p; return rest; });
    reload();
    setMsg(`Guardado: ${key} = ${value}`);
    setTimeout(() => setMsg(""), 3000);
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

  const applySuggestion = async (key: string, value: string | number) => {
    await api.setConfig(key, String(value));
    setAppliedKeys(prev => new Set(prev).add(key));
    reload();
    setMsg(`Sugerencia aplicada: ${key} = ${value}`);
    setTimeout(() => setMsg(""), 3000);
  };

  const modeEntry = entries.find(e => e.key === "mode");
  const fallbackDecissor = entries.find(e => e.key === "fallback_providers");
  const fallbackSupervisor = entries.find(e => e.key === "supervisor_fallback_providers");

  const knownKeys = new Set([
    ...GROUPS.flatMap(g => g.keys),
    ...Array.from(FALLBACK_KEYS),
    "mode",
  ]);
  const otherEntries = entries.filter(e => !knownKeys.has(e.key));

  return (
    <div className="space-y-4">
      {msg && (
        <div className="rounded bg-zinc-800 px-4 py-2 text-sm text-emerald-400 border border-emerald-800/40">
          {msg}
        </div>
      )}

      {/* Acciones rápidas */}
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
      </div>

      {/* Sugerencias del Supervisor */}
      {suggestions ? (
        <div className="rounded-xl bg-zinc-900 p-5 border border-indigo-800/40">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-indigo-300 uppercase tracking-wide mb-1">
                Sugerencias del Supervisor
              </h2>
              <p className="text-xs text-zinc-500">
                Generado {new Date(suggestions.generated_at).toLocaleString("es-AR", { hour12: false })}
              </p>
            </div>
          </div>
          {suggestions.summary && (
            <p className="text-sm text-zinc-300 mb-4 leading-relaxed border-l-2 border-indigo-700 pl-3">
              {suggestions.summary}
            </p>
          )}
          <div className="space-y-2">
            {suggestions.suggestions.map(s => {
              const applied = appliedKeys.has(s.key);
              const unchanged = String(s.current) === String(s.suggested);
              return (
                <div key={s.key} className="rounded-lg bg-zinc-800 px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-mono text-xs text-zinc-300">{s.key}</span>
                      <span className="text-zinc-600 text-xs">{String(s.current)}</span>
                      {!unchanged && (
                        <><span className="text-zinc-600 text-xs">→</span>
                        <span className="font-mono text-xs text-indigo-300 font-semibold">{String(s.suggested)}</span></>
                      )}
                      {unchanged && <span className="text-xs text-zinc-600 italic">sin cambio</span>}
                    </div>
                    <p className="text-xs text-zinc-500">{s.reason}</p>
                  </div>
                  {!unchanged && (
                    <button onClick={() => applySuggestion(s.key, s.suggested)} disabled={applied}
                      className="shrink-0 rounded px-3 py-1 text-xs font-semibold bg-indigo-700 hover:bg-indigo-600 disabled:opacity-40 transition-colors">
                      {applied ? "✓ Aplicado" : "Aplicar"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="rounded-xl bg-zinc-900 p-4 border border-zinc-800 text-xs text-zinc-500">
          Sin sugerencias aún — el Supervisor las genera automáticamente al analizar el histórico de trades (mínimo 5 trades cerrados).
        </div>
      )}

      {/* Grupos de configuración */}
      {GROUPS.map(group => {
        const c = COLOR_CLASSES[group.color];
        const groupEntries = group.keys
          .map(k => ({ key: k, value: entryMap[k] }))
          .filter(e => e.value !== undefined);
        if (groupEntries.length === 0) return null;
        return (
          <div key={group.title} className={`rounded-xl bg-zinc-900 p-5 border ${c.border}`}>
            <div className="flex items-center gap-2 mb-5">
              <span className={`w-2 h-2 rounded-full ${c.dot}`} />
              <h2 className={`text-sm font-semibold uppercase tracking-wide ${c.title}`}>{group.title}</h2>
            </div>
            <div className="space-y-6">
              {groupEntries.map(e => {
                const def = FIELD_DEFS[e.key];
                if (!def) return null;
                return (
                  <div key={e.key}>
                    <ConfigField
                      fieldKey={e.key}
                      def={def}
                      value={e.value}
                      onSave={(k, v) => onSave(k, v)}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {/* Cadenas de fallback */}
      {fallbackDecissor && (
        <FallbackChain label="Cadena de fallback — Decisor" configKey="fallback_providers"
          currentValue={fallbackDecissor.value} onSave={onSave} />
      )}
      {fallbackSupervisor && (
        <FallbackChain label="Cadena de fallback — Supervisor" configKey="supervisor_fallback_providers"
          currentValue={fallbackSupervisor.value} onSave={onSave} />
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
                  <td className="py-2 pr-4 font-mono text-zinc-400 text-xs">{e.key}</td>
                  <td className="pr-4">
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
                  <td>
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
