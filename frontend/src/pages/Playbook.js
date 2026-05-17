import { jsxs as _jsxs, jsx as _jsx, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
export function PlaybookPage() {
    const [active, setActive] = useState(null);
    const [history, setHistory] = useState([]);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState("");
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);
    const reload = () => {
        api.playbookActive().then(v => { setActive(v); if (!editing)
            setDraft(v?.content ?? ""); }).catch(() => { });
        api.playbookHistory().then(setHistory).catch(() => { });
    };
    useEffect(reload, []);
    const onActivate = async (version) => {
        if (!confirm(`¿Activar v${version}? La versión actual quedará inactiva.`))
            return;
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
    const charCount = draft.length;
    const lineCount = draft.split("\n").length;
    return (_jsxs("div", { className: "grid grid-cols-1 lg:grid-cols-3 gap-4", children: [_jsxs("div", { className: "lg:col-span-2 rounded-xl bg-zinc-900 p-5 flex flex-col gap-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("h2", { className: "text-lg font-semibold", children: ["Playbook activo ", active && _jsxs("span", { className: "text-zinc-500 font-normal", children: ["(v", active.version, ")"] })] }), _jsx("div", { className: "flex gap-2", children: !editing ? (_jsxs("button", { onClick: onEdit, disabled: !active, className: "rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 disabled:opacity-40 transition-colors flex items-center gap-1.5", children: [_jsx("span", { className: "text-zinc-400", children: "\u270E" }), " Editar"] })) : (_jsxs(_Fragment, { children: [_jsx("button", { onClick: onCancel, className: "rounded bg-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-600 transition-colors", children: "Cancelar" }), _jsx("button", { onClick: onSave, disabled: saving, className: "rounded bg-emerald-600 px-3 py-1.5 text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50 transition-colors", children: saving ? "Guardando..." : "Guardar cambios" })] })) })] }), msg && (_jsx("div", { className: `rounded px-3 py-2 text-sm ${msg.ok ? "bg-emerald-900/40 text-emerald-300 border border-emerald-800/40" : "bg-red-900/40 text-red-300 border border-red-800/40"}`, children: msg.text })), editing ? (_jsxs("div", { className: "flex flex-col gap-2", children: [_jsxs("div", { className: "rounded border border-zinc-700 overflow-hidden", children: [_jsxs("div", { className: "bg-zinc-800/60 px-3 py-1.5 flex items-center justify-between border-b border-zinc-700", children: [_jsx("span", { className: "text-xs text-zinc-500 font-mono", children: "markdown" }), _jsxs("span", { className: "text-xs text-zinc-600", children: [lineCount, " l\u00EDneas \u00B7 ", charCount, " chars"] })] }), _jsx("textarea", { className: "w-full bg-zinc-900 text-sm text-zinc-200 font-mono leading-relaxed p-4 resize-none focus:outline-none", rows: 30, value: draft, onChange: e => setDraft(e.target.value), spellCheck: false })] }), _jsx("p", { className: "text-xs text-zinc-600", children: "El playbook es le\u00EDdo por el Decisor en cada ciclo. Los cambios se aplican en el pr\u00F3ximo tick." })] })) : active?.content ? (_jsx("div", { className: "text-sm text-zinc-300 leading-relaxed [&>h1]:text-xl [&>h1]:font-bold [&>h1]:text-zinc-100 [&>h1]:mt-4 [&>h1]:mb-2 [&>h2]:text-base [&>h2]:font-semibold [&>h2]:text-zinc-200 [&>h2]:mt-4 [&>h2]:mb-1.5 [&>h2]:border-b [&>h2]:border-zinc-700 [&>h2]:pb-1 [&>h3]:text-sm [&>h3]:font-semibold [&>h3]:text-zinc-300 [&>h3]:mt-3 [&>h3]:mb-1 [&>p]:mb-2 [&>ul]:list-disc [&>ul]:ml-4 [&>ul]:mb-2 [&>ul]:space-y-0.5 [&>ol]:list-decimal [&>ol]:ml-4 [&>ol]:mb-2 [&>ol]:space-y-0.5", children: _jsx(ReactMarkdown, { components: {
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
                            }, children: active.content }) })) : (_jsx("p", { className: "text-zinc-500 text-sm", children: "Sin playbook. El Supervisor generar\u00E1 uno al primer ciclo." }))] }), _jsxs("div", { className: "rounded-xl bg-zinc-900 p-5", children: [_jsx("h3", { className: "font-semibold mb-3", children: "Historial de versiones" }), _jsxs("ul", { className: "space-y-2", children: [history.map(v => (_jsx("li", { className: "rounded bg-zinc-800 p-2", children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsxs("div", { className: "text-sm", children: ["v", v.version, " ", v.active && _jsx("span", { className: "text-emerald-400 text-xs ml-1", children: "(activa)" })] }), _jsxs("div", { className: "text-xs text-zinc-500", children: [new Date(v.ts_generated).toLocaleString("es-AR", { hour12: false }), v.win_rate != null && ` · WR ${v.win_rate.toFixed(1)}%`] })] }), !v.active && (_jsx("button", { onClick: () => onActivate(v.version), className: "text-xs rounded bg-zinc-700 px-2 py-1 hover:bg-zinc-600", children: "Activar" }))] }) }, v.id))), history.length === 0 && _jsx("li", { className: "text-zinc-500 text-sm", children: "Sin versiones a\u00FAn." })] })] })] }));
}
