import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
import type { Playbook } from "../types";

export function PlaybookPage() {
  const [active, setActive] = useState<Playbook | null>(null);
  const [history, setHistory] = useState<Playbook[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const reload = () => {
    api.playbookActive().then(v => { setActive(v); if (!editing) setDraft(v?.content ?? ""); }).catch(() => {});
    api.playbookHistory().then(setHistory).catch(() => {});
  };
  useEffect(reload, []);

  const onActivate = async (version: number) => {
    if (!confirm(`¿Activar v${version}? La versión actual quedará inactiva.`)) return;
    await api.playbookActivate(version);
    reload();
  };

  const onEdit = () => {
    setDraft(active?.content ?? "");
    setEditing(true);
    setMsg(null);
  };

  const onCancel = () => {
    setEditing(false);
    setDraft(active?.content ?? "");
    setMsg(null);
  };

  const onSave = async () => {
    if (!active) return;
    if (!draft.trim()) { setMsg({ text: "El contenido no puede estar vacío.", ok: false }); return; }
    setSaving(true);
    try {
      await api.playbookEditContent(active.version, draft);
      setMsg({ text: `Playbook v${active.version} guardado.`, ok: true });
      setEditing(false);
      reload();
    } catch {
      setMsg({ text: "Error al guardar. Intentá de nuevo.", ok: false });
    } finally {
      setSaving(false);
    }
  };

  const charCount = draft.length;
  const lineCount = draft.split("\n").length;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Panel principal */}
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            Playbook activo {active && <span className="text-zinc-500 font-normal">(v{active.version})</span>}
          </h2>
          <div className="flex gap-2">
            {!editing ? (
              <button
                onClick={onEdit}
                disabled={!active}
                className="rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 disabled:opacity-40 transition-colors flex items-center gap-1.5">
                <span className="text-zinc-400">✎</span> Editar
              </button>
            ) : (
              <>
                <button onClick={onCancel}
                  className="rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 transition-colors">
                  Cancelar
                </button>
                <button onClick={onSave} disabled={saving}
                  className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50 transition-colors">
                  {saving ? "Guardando..." : "Guardar cambios"}
                </button>
              </>
            )}
          </div>
        </div>

        {msg && (
          <div className={`rounded px-3 py-2 text-sm ${msg.ok ? "bg-emerald-900/40 text-emerald-300 border border-emerald-800/40" : "bg-red-900/40 text-red-300 border border-red-800/40"}`}>
            {msg.text}
          </div>
        )}

        {editing ? (
          <div className="flex flex-col gap-2">
            <div className="rounded border border-zinc-700 overflow-hidden">
              <div className="bg-zinc-800/60 px-3 py-1.5 flex items-center justify-between border-b border-zinc-700">
                <span className="text-xs text-zinc-500 font-mono">markdown</span>
                <span className="text-xs text-zinc-600">{lineCount} líneas · {charCount} chars</span>
              </div>
              <textarea
                className="w-full bg-zinc-900 text-sm text-zinc-200 font-mono leading-relaxed p-4 resize-none focus:outline-none"
                rows={30}
                value={draft}
                onChange={e => setDraft(e.target.value)}
                spellCheck={false}
              />
            </div>
            <p className="text-xs text-zinc-600">
              El playbook es leído por el Decisor en cada ciclo. Los cambios se aplican en el próximo tick.
            </p>
          </div>
        ) : active?.content ? (
          <div className="text-sm text-zinc-300 leading-relaxed [&>h1]:text-xl [&>h1]:font-bold [&>h1]:text-zinc-100 [&>h1]:mt-4 [&>h1]:mb-2 [&>h2]:text-base [&>h2]:font-semibold [&>h2]:text-zinc-200 [&>h2]:mt-4 [&>h2]:mb-1.5 [&>h2]:border-b [&>h2]:border-zinc-700 [&>h2]:pb-1 [&>h3]:text-sm [&>h3]:font-semibold [&>h3]:text-zinc-300 [&>h3]:mt-3 [&>h3]:mb-1 [&>p]:mb-2 [&>ul]:list-disc [&>ul]:ml-4 [&>ul]:mb-2 [&>ul]:space-y-0.5 [&>ol]:list-decimal [&>ol]:ml-4 [&>ol]:mb-2 [&>ol]:space-y-0.5">
            <ReactMarkdown
              components={{
                h1: ({ children }) => <h1 className="text-xl font-bold text-zinc-100 mt-4 mb-2">{children}</h1>,
                h2: ({ children }) => <h2 className="text-base font-semibold text-zinc-200 mt-4 mb-1.5 border-b border-zinc-700 pb-1">{children}</h2>,
                h3: ({ children }) => <h3 className="text-sm font-semibold text-zinc-300 mt-3 mb-1">{children}</h3>,
                p: ({ children }) => <p className="text-sm text-zinc-300 leading-relaxed mb-2">{children}</p>,
                ul: ({ children }) => <ul className="list-disc ml-4 mb-2 space-y-0.5">{children}</ul>,
                ol: ({ children }) => <ol className="list-decimal ml-4 mb-2 space-y-0.5">{children}</ol>,
                li: ({ children }) => <li className="text-sm text-zinc-300">{children}</li>,
                strong: ({ children }) => <strong className="font-semibold text-zinc-100">{children}</strong>,
                em: ({ children }) => <em className="italic text-zinc-400">{children}</em>,
                code: ({ className, children }) => {
                  const isBlock = !!className;
                  return isBlock
                    ? <code className="block bg-zinc-800 rounded p-3 text-xs font-mono text-emerald-300 my-2 overflow-x-auto">{children}</code>
                    : <code className="bg-zinc-800 rounded px-1 py-0.5 text-xs font-mono text-emerald-300">{children}</code>;
                },
                pre: ({ children }) => <pre className="bg-zinc-800 rounded p-3 overflow-x-auto my-2 text-xs">{children}</pre>,
                hr: () => <hr className="border-zinc-700 my-3" />,
              }}
            >
              {active.content}
            </ReactMarkdown>
          </div>
        ) : (
          <p className="text-zinc-500 text-sm">Sin playbook. El Supervisor generará uno al primer ciclo.</p>
        )}
      </div>

      {/* Historial */}
      <div className="rounded-xl bg-zinc-900 p-5">
        <h3 className="font-semibold mb-3">Historial de versiones</h3>
        <ul className="space-y-2">
          {history.map(v => (
            <li key={v.id} className="rounded bg-zinc-800 p-2">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm">
                    v{v.version} {v.active && <span className="text-emerald-400 text-xs ml-1">(activa)</span>}
                  </div>
                  <div className="text-xs text-zinc-500">
                    {new Date(v.ts_generated).toLocaleString("es-AR", { hour12: false })}
                    {v.win_rate != null && ` · WR ${v.win_rate.toFixed(1)}%`}
                  </div>
                </div>
                {!v.active && (
                  <button onClick={() => onActivate(v.version)}
                    className="text-xs rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600">
                    Activar
                  </button>
                )}
              </div>
            </li>
          ))}
          {history.length === 0 && <li className="text-zinc-500 text-sm">Sin versiones aún.</li>}
        </ul>
      </div>
    </div>
  );
}
