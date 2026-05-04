import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Playbook } from "../types";

export function PlaybookPage() {
  const [active, setActive] = useState<Playbook | null>(null);
  const [history, setHistory] = useState<Playbook[]>([]);

  const reload = () => {
    api.playbookActive().then(setActive).catch(() => {});
    api.playbookHistory().then(setHistory).catch(() => {});
  };
  useEffect(reload, []);

  const onActivate = async (version: number) => {
    if (!confirm(`¿Activar v${version}? La versión actual quedará inactiva.`)) return;
    await api.playbookActivate(version);
    reload();
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5">
        <h2 className="text-lg font-semibold mb-3">
          Playbook activo {active && <span className="text-zinc-500">(v{active.version})</span>}
        </h2>
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 leading-relaxed">
          {active?.content ?? "Sin playbook. El Supervisor generará uno al primer ciclo."}
        </pre>
      </div>
      <div className="rounded-xl bg-zinc-900 p-5">
        <h3 className="font-semibold mb-3">Historial de versiones</h3>
        <ul className="space-y-2">
          {history.map(v => (
            <li key={v.id} className="flex items-center justify-between rounded bg-zinc-800 p-2">
              <div>
                <div className="text-sm">
                  v{v.version} {v.active && <span className="text-emerald-400 text-xs ml-1">(activa)</span>}
                </div>
                <div className="text-xs text-zinc-500">
                  {new Date(v.ts_generated).toLocaleString("es-AR")}
                  {v.win_rate != null && ` · WR ${v.win_rate.toFixed(1)}%`}
                </div>
              </div>
              {!v.active && (
                <button onClick={() => onActivate(v.version)}
                  className="text-xs rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600">
                  Activar
                </button>
              )}
            </li>
          ))}
          {history.length === 0 && <li className="text-zinc-500 text-sm">Sin versiones aún.</li>}
        </ul>
      </div>
    </div>
  );
}
