import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConfigEntry } from "../types";

const SELECT_OPTIONS: Record<string, string[]> = {
  decisor_provider:    ["groq-llama-3.3-70b", "groq-compound-beta", "gemini-2.5-flash"],
  fallback_provider:   ["gemini-2.5-flash", "groq-llama-3.3-70b", "groq-compound-beta"],
  supervisor_provider: ["gemini-2.5-pro", "groq-llama-3.3-70b", "groq-compound-beta"],
  mode:                ["PAPER_TRADING", "LIVE"],
};

export function Config() {
  const [entries, setEntries] = useState<ConfigEntry[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [liveModal, setLiveModal] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [supRunning, setSupRunning] = useState(false);

  useEffect(() => { api.config().then(setEntries).catch(() => {}); }, []);

  const onSave = async (key: string) => {
    const value = edits[key];
    if (value === undefined) return;
    await api.setConfig(key, value);
    setEdits(p => { const { [key]: _, ...rest } = p; return rest; });
    api.config().then(setEntries);
    setMsg(`Guardado: ${key} = ${value}`);
    setTimeout(() => setMsg(""), 3000);
  };

  const onLive = async () => {
    try {
      await api.setMode("LIVE", liveConfirm);
      setLiveModal(false); setLiveConfirm("");
      api.config().then(setEntries);
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

  const modeEntry = entries.find(e => e.key === "mode");

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
            {entries.map(e => (
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
