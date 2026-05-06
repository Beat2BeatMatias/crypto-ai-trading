import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConfigEntry, ConfigSuggestions } from "../types";

const ALL_PROVIDERS = [
  "groq-llama-3.3-70b",
  "groq-compound-beta",
  "groq-compound-mini",
  "groq-llama-4-scout",
  "groq-gpt-oss-120b",
  "groq-gpt-oss-20b",
  "groq-qwen3-32b",
  "groq-llama-3.1-8b",
  "gemini-2.5-flash",
  "gemini-2.5-pro",
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

function FallbackChain({ label, configKey, currentValue, onSave }: {
  label: string;
  configKey: string;
  currentValue: string;
  onSave: (key: string, value: string) => void;
}) {
  const initial = currentValue.split(",").map(s => s.trim()).filter(Boolean);
  const [selected, setSelected] = useState<string[]>(initial);
  const [dirty, setDirty] = useState(false);

  const toggle = (provider: string) => {
    setDirty(true);
    setSelected(prev =>
      prev.includes(provider)
        ? prev.filter(p => p !== provider)
        : [...prev, provider]
    );
  };

  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...selected];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    setSelected(next);
    setDirty(true);
  };

  const moveDown = (idx: number) => {
    if (idx === selected.length - 1) return;
    const next = [...selected];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    setSelected(next);
    setDirty(true);
  };

  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="font-semibold text-sm">{label}</h3>
          <p className="text-xs text-zinc-500 mt-0.5">
            Se intentan en orden. Si el primero falla (rate limit/error), pasa al siguiente.
          </p>
        </div>
        {dirty && (
          <button
            onClick={() => { onSave(configKey, selected.join(",")); setDirty(false); }}
            className="rounded bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-500 shrink-0">
            Guardar cadena
          </button>
        )}
      </div>

      {/* Cadena activa ordenada */}
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

      {/* Providers disponibles no seleccionados */}
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

export function Config() {
  const [entries, setEntries] = useState<ConfigEntry[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [liveModal, setLiveModal] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [supRunning, setSupRunning] = useState(false);
  const [suggestions, setSuggestions] = useState<ConfigSuggestions | null>(null);
  const [appliedKeys, setAppliedKeys] = useState<Set<string>>(new Set());

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
    setMsg(`Guardado: ${key}`);
    setTimeout(() => setMsg(""), 3000);
  };

  const onLive = async () => {
    try {
      await api.setMode("LIVE", liveConfirm);
      setLiveModal(false); setLiveConfirm("");
      reload();
      setMsg("Modo LIVE activado.");
    } catch {
      setMsg("Confirmación incorrecta. Escribe exactamente: CONFIRMO TRADING REAL");
    }
  };

  const onRunSupervisor = async () => {
    setSupRunning(true);
    try {
      await api.runSupervisor();
      setMsg("Supervisor encolado. Se ejecutará en el próximo tick del decisor (máx. 5 min).");
    } catch {
      setMsg("Error al encolar el supervisor.");
    }
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
  const regularEntries = entries.filter(e => !FALLBACK_KEYS.has(e.key));
  const fallbackDecissor = entries.find(e => e.key === "fallback_providers");
  const fallbackSupervisor = entries.find(e => e.key === "supervisor_fallback_providers");

  return (
    <div className="space-y-4">
      {msg && <div className="rounded bg-zinc-800 px-4 py-2 text-sm text-emerald-400">{msg}</div>}

      <div className="flex gap-3">
        {modeEntry?.value === "PAPER_TRADING" && (
          <button onClick={() => setLiveModal(true)}
            className="rounded bg-amber-600 px-4 py-2 text-sm font-semibold hover:bg-amber-500">
            Cambiar a LIVE (trading real) →
          </button>
        )}
        <button onClick={onRunSupervisor} disabled={supRunning}
          className="rounded bg-indigo-600 px-4 py-2 text-sm font-semibold hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed">
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
                Generado {new Date(suggestions.generated_at).toLocaleString("es-AR")}
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
                        <>
                          <span className="text-zinc-600 text-xs">→</span>
                          <span className="font-mono text-xs text-indigo-300 font-semibold">{String(s.suggested)}</span>
                        </>
                      )}
                      {unchanged && <span className="text-xs text-zinc-600 italic">sin cambio</span>}
                    </div>
                    <p className="text-xs text-zinc-500">{s.reason}</p>
                  </div>
                  {!unchanged && (
                    <button
                      onClick={() => applySuggestion(s.key, s.suggested)}
                      disabled={applied}
                      className="shrink-0 rounded px-3 py-1 text-xs font-semibold bg-indigo-700 hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-default transition-colors">
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
          Podés ejecutarlo manualmente con el botón de arriba.
        </div>
      )}

      {/* Cadenas de fallback */}
      {fallbackDecissor && (
        <FallbackChain
          label="Cadena de fallback — Decisor"
          configKey="fallback_providers"
          currentValue={fallbackDecissor.value}
          onSave={onSave}
        />
      )}
      {fallbackSupervisor && (
        <FallbackChain
          label="Cadena de fallback — Supervisor"
          configKey="supervisor_fallback_providers"
          currentValue={fallbackSupervisor.value}
          onSave={onSave}
        />
      )}

      {/* Resto de configuraciones */}
      <div className="rounded-xl bg-zinc-900 p-5">
        <h2 className="text-lg font-semibold mb-4">Configuración runtime</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
              <th className="text-left py-2 pr-4 w-64">Key</th>
              <th className="text-left pr-4">Valor</th>
              <th className="text-left w-24">Tipo</th>
              <th className="w-24"></th>
            </tr>
          </thead>
          <tbody>
            {regularEntries.map(e => (
              <tr key={e.key} className="border-t border-zinc-800">
                <td className="py-2 pr-4 align-top">
                  <div className="font-mono text-zinc-200">{e.key}</div>
                  {e.description && <div className="text-xs text-zinc-500 mt-0.5">{e.description}</div>}
                </td>
                <td className="pr-4">
                  {SELECT_OPTIONS[e.key] ? (
                    <select
                      className="w-full rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm text-zinc-100 cursor-pointer"
                      value={edits[e.key] ?? e.value}
                      onChange={ev => setEdits(p => ({ ...p, [e.key]: ev.target.value }))}
                    >
                      {SELECT_OPTIONS[e.key].map(opt => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="w-full rounded bg-zinc-800 border border-zinc-700 px-2 py-1 font-mono text-sm"
                      value={edits[e.key] ?? e.value}
                      onChange={ev => setEdits(p => ({ ...p, [e.key]: ev.target.value }))}
                    />
                  )}
                </td>
                <td className="pr-4 text-zinc-500 text-xs">{e.value_type}</td>
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
