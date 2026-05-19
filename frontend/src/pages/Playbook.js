import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
function computeDiff(a, b) {
    const aLines = a.split("\n");
    const bLines = b.split("\n");
    const m = aLines.length;
    const n = bLines.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = 1; i <= m; i++)
        for (let j = 1; j <= n; j++)
            dp[i][j] = aLines[i - 1] === bLines[j - 1]
                ? dp[i - 1][j - 1] + 1
                : Math.max(dp[i - 1][j], dp[i][j - 1]);
    const result = [];
    let i = m, j = n;
    while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && aLines[i - 1] === bLines[j - 1]) {
            result.unshift({ type: "equal", line: aLines[i - 1] });
            i--;
            j--;
        }
        else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
            result.unshift({ type: "add", line: bLines[j - 1] });
            j--;
        }
        else {
            result.unshift({ type: "remove", line: aLines[i - 1] });
            i--;
        }
    }
    return result;
}
// ── Diff Viewer ───────────────────────────────────────────────────────────────
function DiffViewer({ base, compare, labelBase, labelCompare }) {
    const lines = useMemo(() => computeDiff(base, compare), [base, compare]);
    const added = lines.filter(l => l.type === "add").length;
    const removed = lines.filter(l => l.type === "remove").length;
    return (_jsxs("div", { className: "flex flex-col gap-2", children: [_jsxs("div", { className: "flex items-center gap-4 text-xs text-zinc-500 px-1", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-sm bg-emerald-800/80" }), "+", added, " l\u00EDneas agregadas"] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx("span", { className: "inline-block w-2 h-2 rounded-sm bg-red-900/80" }), "-", removed, " l\u00EDneas eliminadas"] }), _jsxs("span", { className: "ml-auto text-zinc-600", children: [labelCompare, " \u2192 ", labelBase] })] }), _jsx("div", { className: "rounded-lg border border-zinc-800 overflow-auto max-h-[70vh] font-mono text-xs leading-5", children: _jsx("table", { className: "w-full border-collapse", children: _jsx("tbody", { children: lines.map((dl, idx) => (_jsxs("tr", { className: dl.type === "add" ? "bg-emerald-950/50"
                                : dl.type === "remove" ? "bg-red-950/50"
                                    : "", children: [_jsx("td", { className: "w-6 text-center text-zinc-700 select-none border-r border-zinc-800 px-1 sticky left-0 bg-zinc-900", children: dl.type === "add" ? _jsx("span", { className: "text-emerald-500", children: "+" })
                                        : dl.type === "remove" ? _jsx("span", { className: "text-red-500", children: "-" })
                                            : _jsx("span", { className: "text-zinc-700", children: " " }) }), _jsx("td", { className: `px-3 py-0.5 whitespace-pre-wrap break-all ${dl.type === "add" ? "text-emerald-300"
                                        : dl.type === "remove" ? "text-red-300 line-through decoration-red-800"
                                            : "text-zinc-400"}`, children: dl.line || " " })] }, idx))) }) }) }), added === 0 && removed === 0 && (_jsx("p", { className: "text-center text-xs text-zinc-500 py-2", children: "Las versiones son id\u00E9nticas." }))] }));
}
// ── MD Components (reutilizable) ──────────────────────────────────────────────
const mdComponents = {
    h1: ({ children }) => _jsx("h1", { className: "text-xl font-bold text-zinc-100 mt-4 mb-2", children: children }),
    h2: ({ children }) => _jsx("h2", { className: "text-base font-semibold text-zinc-200 mt-4 mb-1.5 border-b border-zinc-700 pb-1", children: children }),
    h3: ({ children }) => _jsx("h3", { className: "text-sm font-semibold text-zinc-300 mt-3 mb-1", children: children }),
    p: ({ children }) => _jsx("p", { className: "text-sm text-zinc-300 leading-relaxed mb-2", children: children }),
    ul: ({ children }) => _jsx("ul", { className: "list-disc ml-4 mb-2 space-y-0.5", children: children }),
    ol: ({ children }) => _jsx("ol", { className: "list-decimal ml-4 mb-2 space-y-0.5", children: children }),
    li: ({ children }) => _jsx("li", { className: "text-sm text-zinc-300", children: children }),
    strong: ({ children }) => _jsx("strong", { className: "font-semibold text-zinc-100", children: children }),
    em: ({ children }) => _jsx("em", { className: "italic text-zinc-400", children: children }),
    code: ({ className, children }) => {
        const isBlock = !!className;
        return isBlock
            ? _jsx("code", { className: "block bg-zinc-800 rounded p-3 text-xs font-mono text-emerald-300 my-2 overflow-x-auto", children: children })
            : _jsx("code", { className: "bg-zinc-800 rounded px-1 py-0.5 text-xs font-mono text-emerald-300", children: children });
    },
    pre: ({ children }) => _jsx("pre", { className: "bg-zinc-800 rounded p-3 overflow-x-auto my-2 text-xs", children: children }),
    hr: () => _jsx("hr", { className: "border-zinc-700 my-3" }),
};
// ── Supervisor Status Bar ─────────────────────────────────────────────────────
function playbookAgeLabel(tsGenerated) {
    const now = Date.now();
    const then = new Date(tsGenerated).getTime();
    const diffDays = Math.floor((now - then) / 86_400_000);
    if (diffDays === 0)
        return "hoy";
    if (diffDays === 1)
        return "1 día";
    return `${diffDays} días`;
}
function SupervisorStatusBar({ active, lastRun }) {
    if (!active)
        return null;
    const age = playbookAgeLabel(active.ts_generated);
    const ratified = lastRun?.ratified ?? null;
    return (_jsxs("div", { className: "rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-2.5 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs", children: [_jsxs("span", { className: "text-zinc-400", children: [_jsx("span", { className: "text-zinc-500", children: "Playbook activo: " }), _jsxs("span", { className: "font-semibold text-zinc-200", children: ["v", active.version] }), _jsxs("span", { className: "ml-1.5 text-zinc-500", children: ["\u00B7 ", age] })] }), active.win_rate != null && (_jsxs("span", { className: "text-zinc-400", children: [_jsx("span", { className: "text-zinc-500", children: "WR baseline: " }), _jsxs("span", { className: `font-semibold ${active.win_rate >= 50 ? "text-emerald-400" : "text-red-400"}`, children: [active.win_rate.toFixed(1), "%"] })] })), lastRun && (_jsxs("span", { className: "flex items-center gap-1.5 ml-auto", children: [_jsx("span", { className: "text-zinc-500", children: "\u00DAltimo ciclo supervisor:" }), ratified ? (_jsx("span", { className: "inline-flex items-center gap-1 rounded-full px-2 py-0.5 bg-emerald-900/40 border border-emerald-800/40 text-emerald-400 font-medium", children: "\u2713 Ratificado" })) : (_jsx("span", { className: "inline-flex items-center gap-1 rounded-full px-2 py-0.5 bg-blue-900/40 border border-blue-800/40 text-blue-300 font-medium", children: "\u21BB Regenerado" })), _jsx("span", { className: "text-zinc-600", children: new Date(lastRun.ts).toLocaleString("es-AR", { hour12: false, hour: "2-digit", minute: "2-digit" }) })] }))] }));
}
// ── Supervisor Timeline ────────────────────────────────────────────────────────
function SupervisorTimeline({ runs }) {
    if (runs.length === 0) {
        return _jsx("p", { className: "text-xs text-zinc-500 py-2", children: "Sin ejecuciones registradas." });
    }
    return (_jsx("ul", { className: "space-y-1.5 max-h-[320px] overflow-y-auto pr-0.5", children: runs.map((r, i) => {
            const isRatified = r.ratified;
            const hasForce = !!r.force_regen_reason;
            const ts = new Date(r.ts).toLocaleString("es-AR", { hour12: false, dateStyle: "short", timeStyle: "short" });
            return (_jsxs("li", { className: `rounded-md border px-2.5 py-2 flex flex-col gap-0.5 text-xs transition-colors ${isRatified
                    ? "border-zinc-700/60 bg-zinc-800/40"
                    : "border-blue-800/40 bg-blue-950/20"}`, children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [isRatified ? (_jsx("span", { className: "text-emerald-400 font-medium shrink-0", children: "\u2713" })) : (_jsx("span", { className: "text-blue-400 font-medium shrink-0", children: "\u21BB" })), _jsx("span", { className: "font-medium text-zinc-300", children: isRatified ? "Ratificado" : `Regenerado${r.new_version ? ` → v${r.new_version}` : ""}` }), hasForce && (_jsx("span", { className: "ml-auto rounded-full px-1.5 py-0.5 bg-amber-900/30 border border-amber-800/30 text-amber-400 text-[10px]", children: "forzado" })), _jsx("span", { className: "ml-auto text-zinc-600 tabular-nums shrink-0", children: ts })] }), (r.ratify_reason || r.force_regen_reason) && (_jsx("p", { className: "text-zinc-500 leading-snug line-clamp-2 mt-0.5 pl-4", children: r.force_regen_reason ?? r.ratify_reason })), r.playbook_age_days != null && (_jsxs("span", { className: "text-zinc-600 pl-4", children: ["edad ", r.playbook_age_days, "d", r.playbook_win_rate_baseline != null && ` · WR base ${r.playbook_win_rate_baseline.toFixed(1)}%`] }))] }, i));
        }) }));
}
// ── Main Component ────────────────────────────────────────────────────────────
export function PlaybookPage() {
    const [active, setActive] = useState(null);
    const [history, setHistory] = useState([]);
    const [supervisorRuns, setSupervisorRuns] = useState([]);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState("");
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);
    const [diffVersion, setDiffVersion] = useState(null);
    const [resetting, setResetting] = useState(false);
    const lastRun = supervisorRuns[0] ?? null;
    const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
    const { last } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);
    useEffect(() => {
        if (!last)
            return;
        if (last.event === "supervisor_ran") {
            api.supervisorRuns(30).then(setSupervisorRuns).catch(() => { });
        }
        if (last.event === "playbook_updated") {
            api.playbookActive().then((v) => {
                setActive(v);
                if (!editing)
                    setDraft(v?.content ?? "");
            }).catch(() => { });
            api.playbookHistory().then(setHistory).catch(() => { });
        }
    }, [last]);
    const reload = () => {
        api.playbookActive().then((v) => {
            setActive(v);
            if (!editing)
                setDraft(v?.content ?? "");
        }).catch(() => { });
        api.playbookHistory().then(setHistory).catch(() => { });
        api.supervisorRuns(30).then(setSupervisorRuns).catch(() => { });
    };
    useEffect(reload, []);
    const v0 = useMemo(() => history.length > 0 ? history.reduce((a, b) => a.version < b.version ? a : b) : null, [history]);
    const onActivate = async (version) => {
        if (!confirm(`¿Activar v${version}? La versión actual quedará inactiva.`))
            return;
        await api.playbookActivate(version);
        setDiffVersion(null);
        reload();
    };
    const onResetToV0 = async () => {
        if (!v0)
            return;
        if (!confirm(`¿Restaurar al playbook v${v0.version} (original)?\n\nEsta acción desactivará el playbook actual y activará el v${v0.version}. El Decisor usará el original a partir del próximo ciclo.`))
            return;
        setResetting(true);
        try {
            await api.playbookActivate(v0.version);
            setMsg({ text: `Playbook restaurado a v${v0.version}.`, ok: true });
            setDiffVersion(null);
            reload();
        }
        catch {
            setMsg({ text: "Error al restaurar. Intentá de nuevo.", ok: false });
        }
        finally {
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
        if (!active)
            return;
        if (!draft.trim()) {
            setMsg({ text: "El contenido no puede estar vacío.", ok: false });
            return;
        }
        setSaving(true);
        try {
            await api.playbookEditContent(active.version, draft);
            setMsg({ text: `Playbook v${active.version} guardado.`, ok: true });
            setEditing(false);
            reload();
        }
        catch {
            setMsg({ text: "Error al guardar. Intentá de nuevo.", ok: false });
        }
        finally {
            setSaving(false);
        }
    };
    const toggleDiff = (v) => {
        setDiffVersion(prev => prev?.id === v.id ? null : v);
        setEditing(false);
    };
    const charCount = draft.length;
    const lineCount = draft.split("\n").length;
    const showDiff = diffVersion !== null && active !== null;
    return (_jsxs("div", { className: "flex flex-col gap-4", children: [_jsx(SupervisorStatusBar, { active: active, lastRun: lastRun }), _jsxs("div", { className: "grid grid-cols-1 lg:grid-cols-3 gap-4", children: [_jsxs("div", { className: "lg:col-span-2 rounded-xl bg-zinc-900 p-5 flex flex-col gap-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("h2", { className: "text-lg font-semibold", children: showDiff
                                            ? _jsxs(_Fragment, { children: ["Diff: ", _jsxs("span", { className: "text-zinc-500 font-normal", children: ["v", diffVersion.version] }), " vs ", _jsxs("span", { className: "text-zinc-500 font-normal", children: ["v", active.version, " (activa)"] })] })
                                            : _jsxs(_Fragment, { children: ["Playbook activo ", active && _jsxs("span", { className: "text-zinc-500 font-normal", children: ["(v", active.version, ")"] })] }) }), _jsx("div", { className: "flex gap-2", children: showDiff ? (_jsx("button", { onClick: () => setDiffVersion(null), className: "rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 transition-colors", children: "\u2715 Cerrar diff" })) : !editing ? (_jsxs(_Fragment, { children: [v0 && !active?.active && v0.version !== active?.version && (_jsx("button", { onClick: onResetToV0, disabled: resetting, className: "rounded bg-zinc-700 px-3 py-1.5 text-xs hover:bg-zinc-600 disabled:opacity-40 transition-colors text-amber-400 border border-amber-800/40", children: resetting ? "Restaurando..." : `↺ Reset a v${v0.version}` })), v0 && active && v0.version !== active.version && (_jsx("button", { onClick: onResetToV0, disabled: resetting, className: "rounded bg-zinc-700 px-3 py-1.5 text-xs hover:bg-zinc-600 disabled:opacity-40 transition-colors text-amber-400 border border-amber-800/40", children: resetting ? "Restaurando..." : `↺ Reset a v${v0.version}` })), _jsxs("button", { onClick: onEdit, disabled: !active, className: "rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 disabled:opacity-40 transition-colors flex items-center gap-1.5", children: [_jsx("span", { className: "text-zinc-400", children: "\u270E" }), " Editar"] })] })) : (_jsxs(_Fragment, { children: [_jsx("button", { onClick: onCancel, className: "rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 transition-colors", children: "Cancelar" }), _jsx("button", { onClick: onSave, disabled: saving, className: "rounded bg-emerald-600 px-3 py-1.5 text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50 transition-colors", children: saving ? "Guardando..." : "Guardar cambios" })] })) })] }), msg && (_jsx("div", { className: `rounded px-3 py-2 text-sm ${msg.ok
                                    ? "bg-emerald-900/40 text-emerald-300 border border-emerald-800/40"
                                    : "bg-red-900/40 text-red-300 border border-red-800/40"}`, children: msg.text })), showDiff && (_jsx(DiffViewer, { base: active.content, compare: diffVersion.content, labelBase: `v${active.version} (activa)`, labelCompare: `v${diffVersion.version}` })), !showDiff && editing && (_jsxs("div", { className: "flex flex-col gap-2", children: [_jsxs("div", { className: "rounded border border-zinc-700 overflow-hidden", children: [_jsxs("div", { className: "bg-zinc-800/60 px-3 py-1.5 flex items-center justify-between border-b border-zinc-700", children: [_jsx("span", { className: "text-xs text-zinc-500 font-mono", children: "markdown" }), _jsxs("span", { className: "text-xs text-zinc-600", children: [lineCount, " l\u00EDneas \u00B7 ", charCount, " chars"] })] }), _jsx("textarea", { className: "w-full bg-zinc-900 text-sm text-zinc-200 font-mono leading-relaxed p-4 resize-none focus:outline-none", rows: 30, value: draft, onChange: e => setDraft(e.target.value), spellCheck: false })] }), _jsx("p", { className: "text-xs text-zinc-600", children: "El playbook es le\u00EDdo por el Decisor en cada ciclo. Los cambios se aplican en el pr\u00F3ximo tick." })] })), !showDiff && !editing && (active?.content ? (_jsx("div", { className: "text-sm text-zinc-300 leading-relaxed", children: _jsx(ReactMarkdown, { components: mdComponents, children: active.content }) })) : (_jsx("p", { className: "text-zinc-500 text-sm", children: "Sin playbook. El Supervisor generar\u00E1 uno al primer ciclo." })))] }), _jsxs("div", { className: "flex flex-col gap-4", children: [_jsxs("div", { className: "rounded-xl bg-zinc-900 p-5", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsx("h3", { className: "font-semibold text-sm", children: "Historial de versiones" }), v0 && active && v0.version !== active.version && (_jsx("button", { onClick: onResetToV0, disabled: resetting, title: `Restaurar al playbook original v${v0.version}`, className: "text-xs px-2 py-1 rounded bg-amber-900/30 border border-amber-800/40 text-amber-400 hover:bg-amber-900/50 disabled:opacity-40 transition-colors", children: resetting ? "..." : `↺ v${v0.version}` }))] }), _jsxs("ul", { className: "space-y-2", children: [history.map(v => (_jsx("li", { className: `rounded p-2 border transition-colors ${diffVersion?.id === v.id
                                                    ? "border-blue-700 bg-blue-900/20"
                                                    : "border-transparent bg-zinc-800"}`, children: _jsxs("div", { className: "flex items-center justify-between gap-1", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "text-sm flex items-center gap-1.5", children: ["v", v.version, v.active && _jsx("span", { className: "text-emerald-400 text-xs", children: "(activa)" }), v.version === v0?.version && !v.active && (_jsx("span", { className: "text-amber-600 text-xs", children: "(original)" }))] }), _jsxs("div", { className: "text-xs text-zinc-500 truncate", children: [new Date(v.ts_generated).toLocaleString("es-AR", { hour12: false }), v.win_rate != null && ` · WR ${v.win_rate.toFixed(1)}%`, v.trades_analyzed != null && ` · ${v.trades_analyzed} trades`] })] }), _jsxs("div", { className: "flex gap-1 shrink-0", children: [active && !v.active && (_jsx("button", { onClick: () => toggleDiff(v), title: "Ver diferencias con la versi\u00F3n activa", className: `text-xs rounded px-1.5 py-1 transition-colors ${diffVersion?.id === v.id
                                                                        ? "bg-blue-800 text-blue-200"
                                                                        : "bg-zinc-700 text-zinc-400 hover:bg-zinc-600"}`, children: "diff" })), !v.active && (_jsx("button", { onClick: () => onActivate(v.version), className: "text-xs rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600 text-zinc-300", children: "Activar" }))] })] }) }, v.id))), history.length === 0 && _jsx("li", { className: "text-zinc-500 text-sm", children: "Sin versiones a\u00FAn." })] })] }), _jsxs("div", { className: "rounded-xl bg-zinc-900 p-5", children: [_jsx("h3", { className: "font-semibold text-sm mb-3", children: "Ciclos del Supervisor" }), _jsx(SupervisorTimeline, { runs: supervisorRuns })] })] })] })] }));
}
