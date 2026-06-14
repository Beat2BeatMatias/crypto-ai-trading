import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
const MIN_OCC_DEFAULT = 3;
function fmtTs(iso) {
    return new Date(iso).toLocaleString("es-AR", { hour12: false });
}
function statusBadge(status) {
    const map = {
        open: "bg-amber-900/40 text-amber-300 border-amber-800",
        promoted: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
        rejected: "bg-zinc-800 text-zinc-400 border-zinc-700",
        merged: "bg-blue-900/40 text-blue-300 border-blue-800",
    };
    return map[status] ?? "bg-zinc-800 text-zinc-400 border-zinc-700";
}
function VerifySpecPreview({ spec }) {
    const rules = [
        ...(spec.all ?? []),
        ...(spec.any ?? []),
    ];
    if (!rules.length)
        return _jsx("span", { className: "text-zinc-600", children: "\u2014" });
    return (_jsx("ul", { className: "text-xs text-zinc-400 space-y-0.5 font-mono", children: rules.slice(0, 4).map((r, i) => (_jsx("li", { children: JSON.stringify(r) }, i))) }));
}
export function ConfluencePage() {
    const [candidates, setCandidates] = useState([]);
    const [registry, setRegistry] = useState([]);
    const [statusFilter, setStatusFilter] = useState("open");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [busyId, setBusyId] = useState(null);
    const [rejectId, setRejectId] = useState(null);
    const [rejectReason, setRejectReason] = useState("");
    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [c, r] = await Promise.all([
                api.confluenceCandidates(statusFilter || undefined),
                api.confluenceRegistry(true),
            ]);
            setCandidates(c);
            setRegistry(r);
        }
        catch {
            setError("No se pudieron cargar los datos de confluencias.");
        }
        finally {
            setLoading(false);
        }
    }, [statusFilter]);
    useEffect(() => { load(); }, [load]);
    const openCount = useMemo(() => candidates.filter(c => c.status === "open").length, [candidates]);
    async function handlePromote(id) {
        if (!confirm("¿Promover este patrón al catálogo extendido (Q–Z)?"))
            return;
        setBusyId(id);
        setError(null);
        try {
            await api.promoteConfluenceCandidate(id);
            await load();
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Error al promover");
        }
        finally {
            setBusyId(null);
        }
    }
    async function handleReject(id) {
        setRejectId(id);
        setRejectReason("");
    }
    async function confirmReject() {
        if (!rejectId)
            return;
        setBusyId(rejectId);
        setError(null);
        try {
            await api.rejectConfluenceCandidate(rejectId, rejectReason);
            setRejectId(null);
            await load();
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Error al rechazar");
        }
        finally {
            setBusyId(null);
        }
    }
    async function handleDeactivate(code) {
        if (!confirm(`¿Desactivar la confluencia '${code}'? No se recicla la letra por 30 días.`))
            return;
        setBusyId(code);
        setError(null);
        try {
            await api.deactivateConfluence(code);
            await load();
        }
        catch (e) {
            setError(e instanceof Error ? e.message : "Error al desactivar");
        }
        finally {
            setBusyId(null);
        }
    }
    return (_jsxs("div", { className: "max-w-6xl mx-auto space-y-8", children: [_jsxs("header", { children: [_jsx("h1", { className: "text-xl font-semibold text-white", children: "Aprendizaje \u2014 Confluencias" }), _jsx("p", { className: "text-sm text-zinc-500 mt-1", children: "Patrones detectados por post-mortem. Promov\u00E9 candidatos al cat\u00E1logo I\u2013Z o rechazalos manualmente." })] }), error && (_jsx("div", { className: "rounded border border-red-900 bg-red-950/50 text-red-300 text-sm px-4 py-2", children: error })), _jsxs("section", { className: "rounded-lg border border-zinc-800 bg-zinc-900/50 p-4", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsx("h2", { className: "text-sm font-medium text-zinc-300 uppercase tracking-wide", children: "Cat\u00E1logo promovido (I\u2013Z)" }), _jsxs("span", { className: "text-xs text-zinc-500", children: [registry.length, " activas"] })] }), registry.length === 0 ? (_jsx("p", { className: "text-sm text-zinc-600", children: "Ninguna confluencia promovida activa." })) : (_jsx("div", { className: "overflow-x-auto", children: _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { children: _jsxs("tr", { className: "text-left text-xs text-zinc-500 border-b border-zinc-800", children: [_jsx("th", { className: "py-2 pr-3", children: "Letra" }), _jsx("th", { className: "py-2 pr-3", children: "T\u00EDtulo" }), _jsx("th", { className: "py-2 pr-3", children: "Definici\u00F3n" }), _jsx("th", { className: "py-2 pr-3", children: "Desde" }), _jsx("th", { className: "py-2" })] }) }), _jsx("tbody", { children: registry.map(r => (_jsxs("tr", { className: "border-b border-zinc-800/60", children: [_jsx("td", { className: "py-2 pr-3 font-mono text-emerald-400 text-lg", children: r.code }), _jsx("td", { className: "py-2 pr-3 text-zinc-200", children: r.title }), _jsx("td", { className: "py-2 pr-3 text-zinc-400 text-xs max-w-md", children: r.definition_md }), _jsx("td", { className: "py-2 pr-3 text-zinc-500 text-xs whitespace-nowrap", children: fmtTs(r.created_at) }), _jsx("td", { className: "py-2 text-right", children: _jsx("button", { type: "button", disabled: busyId === r.code, onClick: () => handleDeactivate(r.code), className: "text-xs text-red-400 hover:text-red-300 disabled:opacity-40", children: "Desactivar" }) })] }, r.code))) })] }) }))] }), _jsxs("section", { className: "rounded-lg border border-zinc-800 bg-zinc-900/50 p-4", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-3 mb-4", children: [_jsx("h2", { className: "text-sm font-medium text-zinc-300 uppercase tracking-wide", children: "Cola de candidatos" }), _jsxs("select", { value: statusFilter, onChange: e => setStatusFilter(e.target.value), className: "text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300", children: [_jsx("option", { value: "open", children: "Abiertos" }), _jsx("option", { value: "promoted", children: "Promovidos" }), _jsx("option", { value: "rejected", children: "Rechazados" }), _jsx("option", { value: "", children: "Todos" })] }), _jsx("button", { type: "button", onClick: load, className: "text-xs text-emerald-400 hover:text-emerald-300 ml-auto", children: "Actualizar" })] }), loading ? (_jsx("p", { className: "text-sm text-zinc-500", children: "Cargando\u2026" })) : candidates.length === 0 ? (_jsx("p", { className: "text-sm text-zinc-600", children: "Sin candidatos para el filtro seleccionado." })) : (_jsx("div", { className: "space-y-3", children: candidates.map(c => {
                            const eligible = c.status === "open" && c.occurrence_count >= MIN_OCC_DEFAULT;
                            return (_jsxs("div", { className: "rounded border border-zinc-800 bg-zinc-950/40 p-4", children: [_jsxs("div", { className: "flex flex-wrap items-start gap-2 mb-2", children: [_jsx("span", { className: `text-xs px-2 py-0.5 rounded border ${statusBadge(c.status)}`, children: c.status }), eligible && (_jsxs("span", { className: "text-xs px-2 py-0.5 rounded border bg-emerald-950/50 text-emerald-400 border-emerald-900", children: ["elegible (\u2265", MIN_OCC_DEFAULT, " ocurrencias)"] })), _jsx("span", { className: "text-xs text-zinc-500 font-mono ml-auto", children: c.pattern_tag })] }), _jsx("h3", { className: "text-white font-medium", children: c.title }), _jsx("p", { className: "text-sm text-zinc-400 mt-1", children: c.definition_md }), _jsxs("div", { className: "grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 text-xs text-zinc-500", children: [_jsxs("div", { children: [_jsx("span", { className: "block text-zinc-600", children: "Ocurrencias" }), _jsx("span", { className: "text-zinc-300 font-mono", children: c.occurrence_count })] }), _jsxs("div", { children: [_jsx("span", { className: "block text-zinc-600", children: "\u00DAltima vez" }), fmtTs(c.last_seen_at)] }), _jsxs("div", { children: [_jsx("span", { className: "block text-zinc-600", children: "Primera vez" }), fmtTs(c.first_seen_at)] }), _jsxs("div", { children: [_jsx("span", { className: "block text-zinc-600", children: "verify_spec" }), _jsx(VerifySpecPreview, { spec: c.verify_spec })] })] }), c.reject_reason && (_jsxs("p", { className: "text-xs text-red-400/80 mt-2", children: ["Motivo rechazo: ", c.reject_reason] })), c.proposed_code && (_jsxs("p", { className: "text-xs text-emerald-500 mt-1", children: ["Promovido como letra ", c.proposed_code] })), c.status === "open" && (_jsxs("div", { className: "flex gap-2 mt-4", children: [_jsx("button", { type: "button", disabled: busyId === c.id, onClick: () => handlePromote(c.id), className: "text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40", children: "Promover" }), _jsx("button", { type: "button", disabled: busyId === c.id, onClick: () => handleReject(c.id), className: "text-xs px-3 py-1.5 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 disabled:opacity-40", children: "Rechazar" })] }))] }, c.id));
                        }) })), statusFilter === "open" && !loading && (_jsxs("p", { className: "text-xs text-zinc-600 mt-3", children: [openCount, " candidato(s) abierto(s). El Supervisor tambi\u00E9n promueve autom\u00E1ticamente al cumplir umbrales."] }))] }), rejectId && (_jsx("div", { className: "fixed inset-0 bg-black/60 flex items-center justify-center p-4 z-50", children: _jsxs("div", { className: "bg-zinc-900 border border-zinc-700 rounded-lg p-5 max-w-md w-full shadow-xl", children: [_jsx("h3", { className: "text-white font-medium mb-2", children: "Rechazar candidato" }), _jsx("p", { className: "text-xs text-zinc-500 mb-3", children: "No se repropone en 7 d\u00EDas salvo nueva ocurrencia duplicada." }), _jsx("textarea", { value: rejectReason, onChange: e => setRejectReason(e.target.value), placeholder: "Motivo (opcional)", rows: 3, className: "w-full text-sm bg-zinc-950 border border-zinc-700 rounded p-2 text-zinc-300 mb-4" }), _jsxs("div", { className: "flex justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setRejectId(null), className: "text-xs px-3 py-1.5 text-zinc-400", children: "Cancelar" }), _jsx("button", { type: "button", onClick: confirmReject, className: "text-xs px-3 py-1.5 rounded bg-red-800 hover:bg-red-700 text-white", children: "Confirmar rechazo" })] })] }) }))] }));
}
