import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Decision } from "../types";

export function Decisions() {
  const [items, setItems] = useState<Decision[]>([]);
  const [agent, setAgent] = useState("");
  const [selected, setSelected] = useState<Decision | null>(null);

  useEffect(() => {
    api.decisions(agent ? { agent } : undefined).then(setItems).catch(() => {});
  }, [agent]);

  const out = (d: Decision) => d.output as { action?: string; confidence?: number; reasoning?: string };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Decisiones (audit log)</h2>
          <select value={agent} onChange={e => setAgent(e.target.value)}
            className="rounded bg-zinc-800 px-2 py-1 text-sm border border-zinc-700">
            <option value="">Todos</option>
            <option value="decisor">Decisor</option>
            <option value="supervisor">Supervisor</option>
          </select>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
              <th className="text-left py-2 pr-3">TS</th>
              <th className="text-left pr-3">Agente</th>
              <th className="text-left pr-3">Modelo</th>
              <th className="text-left pr-3">Acción</th>
              <th className="text-right pr-3">Conf</th>
              <th className="text-left">Estado</th>
            </tr>
          </thead>
          <tbody>
            {items.map(d => (
              <tr key={d.id} onClick={() => setSelected(d)}
                className={`cursor-pointer border-t border-zinc-800 hover:bg-zinc-800/40 transition-colors ${selected?.id === d.id ? "bg-zinc-800" : ""}`}>
                <td className="py-2 pr-3 text-zinc-400 text-xs">{new Date(d.ts).toLocaleString("es-AR")}</td>
                <td className="pr-3">{d.agent}</td>
                <td className="pr-3 text-xs text-zinc-400 font-mono">{d.model}</td>
                <td className={`pr-3 font-semibold ${out(d).action === "BUY" ? "text-emerald-400" : out(d).action === "SELL" ? "text-red-400" : "text-zinc-400"}`}>
                  {out(d).action ?? "—"}
                </td>
                <td className="text-right pr-3">{((out(d).confidence ?? 0) * 100).toFixed(0)}%</td>
                <td>{d.executed ? "✅" : d.rejected_reason ? "❌" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rounded-xl bg-zinc-900 p-5 overflow-auto max-h-[80vh]">
        {selected ? (
          <div>
            <h3 className="font-semibold mb-1">{out(selected).action ?? "—"}</h3>
            <p className="text-xs text-zinc-500 font-mono mb-3">{selected.model}</p>
            <p className="text-sm text-zinc-300 mb-4">{out(selected).reasoning ?? ""}</p>
            {selected.rejected_reason && (
              <p className="text-xs text-red-400 mb-3">Rechazada: {selected.rejected_reason}</p>
            )}
            <details className="mb-2">
              <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-300">Output JSON</summary>
              <pre className="mt-2 text-xs bg-zinc-950 p-3 rounded overflow-auto">{JSON.stringify(selected.output, null, 2)}</pre>
            </details>
            <details>
              <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-300">Input JSON</summary>
              <pre className="mt-2 text-xs bg-zinc-950 p-3 rounded overflow-auto max-h-64">{JSON.stringify(selected.input, null, 2)}</pre>
            </details>
          </div>
        ) : <p className="text-zinc-500 text-sm">Seleccioná una fila para ver el detalle.</p>}
      </div>
    </div>
  );
}
