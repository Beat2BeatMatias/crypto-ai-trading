import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
import type { Playbook } from "../types";

// ── Diff engine (LCS line-based) ──────────────────────────────────────────────

type DiffLine = { type: "equal" | "remove" | "add"; line: string };

function computeDiff(a: string, b: string): DiffLine[] {
  const aLines = a.split("\n");
  const bLines = b.split("\n");
  const m = aLines.length;
  const n = bLines.length;

  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = aLines[i - 1] === bLines[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);

  const result: DiffLine[] = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && aLines[i - 1] === bLines[j - 1]) {
      result.unshift({ type: "equal", line: aLines[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ type: "add", line: bLines[j - 1] });
      j--;
    } else {
      result.unshift({ type: "remove", line: aLines[i - 1] });
      i--;
    }
  }
  return result;
}

// ── Diff Viewer ───────────────────────────────────────────────────────────────

function DiffViewer({ base, compare, labelBase, labelCompare }: {
  base: string; compare: string;
  labelBase: string; labelCompare: string;
}) {
  const lines = useMemo(() => computeDiff(base, compare), [base, compare]);
  const added = lines.filter(l => l.type === "add").length;
  const removed = lines.filter(l => l.type === "remove").length;

  return (
    <div className="flex flex-col gap-2">
      {/* Leyenda */}
      <div className="flex items-center gap-4 text-xs text-zinc-500 px-1">
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 rounded-sm bg-emerald-800/80" />
          +{added} líneas agregadas
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 rounded-sm bg-red-900/80" />
          -{removed} líneas eliminadas
        </span>
        <span className="ml-auto text-zinc-600">{labelCompare} → {labelBase}</span>
      </div>

      {/* Tabla de diff */}
      <div className="rounded-lg border border-zinc-800 overflow-auto max-h-[70vh] font-mono text-xs leading-5">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((dl, idx) => (
              <tr key={idx} className={
                dl.type === "add" ? "bg-emerald-950/50"
                : dl.type === "remove" ? "bg-red-950/50"
                : ""
              }>
                <td className="w-6 text-center text-zinc-700 select-none border-r border-zinc-800 px-1 sticky left-0 bg-zinc-900">
                  {dl.type === "add" ? <span className="text-emerald-500">+</span>
                    : dl.type === "remove" ? <span className="text-red-500">-</span>
                    : <span className="text-zinc-700"> </span>}
                </td>
                <td className={`px-3 py-0.5 whitespace-pre-wrap break-all ${
                  dl.type === "add" ? "text-emerald-300"
                  : dl.type === "remove" ? "text-red-300 line-through decoration-red-800"
                  : "text-zinc-400"
                }`}>
                  {dl.line || " "}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {added === 0 && removed === 0 && (
        <p className="text-center text-xs text-zinc-500 py-2">Las versiones son idénticas.</p>
      )}
    </div>
  );
}

// ── MD Components (reutilizable) ──────────────────────────────────────────────

const mdComponents = {
  h1: ({ children }: { children?: React.ReactNode }) =>
    <h1 className="text-xl font-bold text-zinc-100 mt-4 mb-2">{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) =>
    <h2 className="text-base font-semibold text-zinc-200 mt-4 mb-1.5 border-b border-zinc-700 pb-1">{children}</h2>,
  h3: ({ children }: { children?: React.ReactNode }) =>
    <h3 className="text-sm font-semibold text-zinc-300 mt-3 mb-1">{children}</h3>,
  p: ({ children }: { children?: React.ReactNode }) =>
    <p className="text-sm text-zinc-300 leading-relaxed mb-2">{children}</p>,
  ul: ({ children }: { children?: React.ReactNode }) =>
    <ul className="list-disc ml-4 mb-2 space-y-0.5">{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) =>
    <ol className="list-decimal ml-4 mb-2 space-y-0.5">{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) =>
    <li className="text-sm text-zinc-300">{children}</li>,
  strong: ({ children }: { children?: React.ReactNode }) =>
    <strong className="font-semibold text-zinc-100">{children}</strong>,
  em: ({ children }: { children?: React.ReactNode }) =>
    <em className="italic text-zinc-400">{children}</em>,
  code: ({ className, children }: { className?: string; children?: React.ReactNode }) => {
    const isBlock = !!className;
    return isBlock
      ? <code className="block bg-zinc-800 rounded p-3 text-xs font-mono text-emerald-300 my-2 overflow-x-auto">{children}</code>
      : <code className="bg-zinc-800 rounded px-1 py-0.5 text-xs font-mono text-emerald-300">{children}</code>;
  },
  pre: ({ children }: { children?: React.ReactNode }) =>
    <pre className="bg-zinc-800 rounded p-3 overflow-x-auto my-2 text-xs">{children}</pre>,
  hr: () => <hr className="border-zinc-700 my-3" />,
};

// ── Main Component ────────────────────────────────────────────────────────────

export function PlaybookPage() {
  const [active, setActive] = useState<Playbook | null>(null);
  const [history, setHistory] = useState<Playbook[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [diffVersion, setDiffVersion] = useState<Playbook | null>(null);
  const [resetting, setResetting] = useState(false);

  const reload = () => {
    api.playbookActive().then(v => {
      setActive(v);
      if (!editing) setDraft(v?.content ?? "");
    }).catch(() => {});
    api.playbookHistory().then(setHistory).catch(() => {});
  };
  useEffect(reload, []);

  const v0 = useMemo(
    () => history.length > 0 ? history.reduce((a, b) => a.version < b.version ? a : b) : null,
    [history]
  );

  const onActivate = async (version: number) => {
    if (!confirm(`¿Activar v${version}? La versión actual quedará inactiva.`)) return;
    await api.playbookActivate(version);
    setDiffVersion(null);
    reload();
  };

  const onResetToV0 = async () => {
    if (!v0) return;
    if (!confirm(
      `¿Restaurar al playbook v${v0.version} (original)?\n\nEsta acción desactivará el playbook actual y activará el v${v0.version}. El Decisor usará el original a partir del próximo ciclo.`
    )) return;
    setResetting(true);
    try {
      await api.playbookActivate(v0.version);
      setMsg({ text: `Playbook restaurado a v${v0.version}.`, ok: true });
      setDiffVersion(null);
      reload();
    } catch {
      setMsg({ text: "Error al restaurar. Intentá de nuevo.", ok: false });
    } finally {
      setResetting(false);
    }
  };

  const onEdit = () => {
    setDraft(active?.content ?? "");
    setEditing(true);
    setDiffVersion(null);
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

  const toggleDiff = (v: Playbook) => {
    setDiffVersion(prev => prev?.id === v.id ? null : v);
    setEditing(false);
  };

  const charCount = draft.length;
  const lineCount = draft.split("\n").length;

  const showDiff = diffVersion !== null && active !== null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

      {/* ── Panel principal ── */}
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">
            {showDiff
              ? <>Diff: <span className="text-zinc-500 font-normal">v{diffVersion.version}</span> vs <span className="text-zinc-500 font-normal">v{active.version} (activa)</span></>
              : <>Playbook activo {active && <span className="text-zinc-500 font-normal">(v{active.version})</span>}</>
            }
          </h2>

          <div className="flex gap-2">
            {showDiff ? (
              <button onClick={() => setDiffVersion(null)}
                className="rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 transition-colors">
                ✕ Cerrar diff
              </button>
            ) : !editing ? (
              <>
                {v0 && !active?.active && v0.version !== active?.version && (
                  <button onClick={onResetToV0} disabled={resetting}
                    className="rounded bg-zinc-700 px-3 py-1.5 text-xs hover:bg-zinc-600 disabled:opacity-40 transition-colors text-amber-400 border border-amber-800/40">
                    {resetting ? "Restaurando..." : `↺ Reset a v${v0.version}`}
                  </button>
                )}
                {v0 && active && v0.version !== active.version && (
                  <button onClick={onResetToV0} disabled={resetting}
                    className="rounded bg-zinc-700 px-3 py-1.5 text-xs hover:bg-zinc-600 disabled:opacity-40 transition-colors text-amber-400 border border-amber-800/40">
                    {resetting ? "Restaurando..." : `↺ Reset a v${v0.version}`}
                  </button>
                )}
                <button onClick={onEdit} disabled={!active}
                  className="rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 disabled:opacity-40 transition-colors flex items-center gap-1.5">
                  <span className="text-zinc-400">✎</span> Editar
                </button>
              </>
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
          <div className={`rounded px-3 py-2 text-sm ${msg.ok
            ? "bg-emerald-900/40 text-emerald-300 border border-emerald-800/40"
            : "bg-red-900/40 text-red-300 border border-red-800/40"}`}>
            {msg.text}
          </div>
        )}

        {/* Diff viewer */}
        {showDiff && (
          <DiffViewer
            base={active.content}
            compare={diffVersion.content}
            labelBase={`v${active.version} (activa)`}
            labelCompare={`v${diffVersion.version}`}
          />
        )}

        {/* Editor */}
        {!showDiff && editing && (
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
        )}

        {/* Vista markdown */}
        {!showDiff && !editing && (
          active?.content ? (
            <div className="text-sm text-zinc-300 leading-relaxed">
              <ReactMarkdown components={mdComponents as Parameters<typeof ReactMarkdown>[0]["components"]}>
                {active.content}
              </ReactMarkdown>
            </div>
          ) : (
            <p className="text-zinc-500 text-sm">Sin playbook. El Supervisor generará uno al primer ciclo.</p>
          )
        )}
      </div>

      {/* ── Historial ── */}
      <div className="rounded-xl bg-zinc-900 p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">Historial de versiones</h3>
          {v0 && active && v0.version !== active.version && (
            <button
              onClick={onResetToV0}
              disabled={resetting}
              title={`Restaurar al playbook original v${v0.version}`}
              className="text-xs px-2 py-1 rounded bg-amber-900/30 border border-amber-800/40 text-amber-400 hover:bg-amber-900/50 disabled:opacity-40 transition-colors"
            >
              {resetting ? "..." : `↺ v${v0.version}`}
            </button>
          )}
        </div>

        <ul className="space-y-2">
          {history.map(v => (
            <li key={v.id} className={`rounded p-2 border transition-colors ${
              diffVersion?.id === v.id
                ? "border-blue-700 bg-blue-900/20"
                : "border-transparent bg-zinc-800"
            }`}>
              <div className="flex items-center justify-between gap-1">
                <div className="min-w-0">
                  <div className="text-sm flex items-center gap-1.5">
                    v{v.version}
                    {v.active && <span className="text-emerald-400 text-xs">(activa)</span>}
                    {v.version === v0?.version && !v.active && (
                      <span className="text-amber-600 text-xs">(original)</span>
                    )}
                  </div>
                  <div className="text-xs text-zinc-500 truncate">
                    {new Date(v.ts_generated).toLocaleString("es-AR", { hour12: false })}
                    {v.win_rate != null && ` · WR ${v.win_rate.toFixed(1)}%`}
                    {v.trades_analyzed != null && ` · ${v.trades_analyzed} trades`}
                  </div>
                </div>

                <div className="flex gap-1 shrink-0">
                  {active && !v.active && (
                    <button
                      onClick={() => toggleDiff(v)}
                      title="Ver diferencias con la versión activa"
                      className={`text-xs rounded px-1.5 py-1 transition-colors ${
                        diffVersion?.id === v.id
                          ? "bg-blue-800 text-blue-200"
                          : "bg-zinc-700 text-zinc-400 hover:bg-zinc-600"
                      }`}
                    >
                      diff
                    </button>
                  )}
                  {!v.active && (
                    <button onClick={() => onActivate(v.version)}
                      className="text-xs rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600 text-zinc-300">
                      Activar
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
          {history.length === 0 && <li className="text-zinc-500 text-sm">Sin versiones aún.</li>}
        </ul>
      </div>
    </div>
  );
}
