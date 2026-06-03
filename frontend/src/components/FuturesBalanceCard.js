import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
function fmtUsd(n) {
    if (n == null || Number.isNaN(n))
        return "—";
    return `$${n.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function sourceLabel(source) {
    if (source === "live")
        return "Binance (en vivo)";
    if (source === "snapshot")
        return "Último ciclo del engine";
    return "No disponible";
}
function StatRow({ label, value, valueClass = "text-zinc-200" }) {
    return (_jsxs("div", { className: "flex justify-between items-baseline py-1 border-b border-zinc-800 last:border-0", children: [_jsx("span", { className: "text-xs text-zinc-500", children: label }), _jsx("span", { className: `text-sm font-mono font-semibold ${valueClass}`, children: value })] }));
}
export function FuturesBalanceCard({ futures }) {
    const unavailable = !futures || futures.source === "unavailable";
    const hasValues = futures?.available_margin != null || futures?.margin_balance != null;
    return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-5 border border-violet-900/40", children: [_jsxs("div", { className: "flex items-center justify-between mb-3", children: [_jsx("h3", { className: "text-sm font-semibold text-violet-300 uppercase tracking-wide", children: "Balance Futuros USDT-M" }), futures && (_jsx("span", { className: `text-[10px] uppercase px-1.5 py-0.5 rounded ${futures.source === "live"
                            ? "bg-violet-900/60 text-violet-200"
                            : futures.source === "snapshot"
                                ? "bg-zinc-800 text-zinc-400"
                                : "bg-amber-950 text-amber-400"}`, children: sourceLabel(futures.source) }))] }), unavailable && !hasValues ? (_jsx("p", { className: "text-sm text-zinc-500", children: "No se pudo leer la wallet de futuros. Verific\u00E1 API keys con permiso de derivados y que haya USDT en la cuenta Futures de Binance." })) : (_jsxs("div", { className: "space-y-1", children: [_jsx(StatRow, { label: "Margen disponible", value: fmtUsd(futures?.available_margin), valueClass: "text-emerald-400" }), futures?.margin_locked != null && futures.margin_locked > 0 && (_jsx(StatRow, { label: "Margen bloqueado (\u00F3rdenes)", value: fmtUsd(futures.margin_locked), valueClass: "text-yellow-500" })), _jsx(StatRow, { label: "Margen total (wallet)", value: fmtUsd(futures?.margin_balance), valueClass: "text-violet-200" }), _jsx("div", { className: "pt-2 text-xs text-zinc-600", children: futures?.fetched_at
                            ? `Actualizado ${new Date(futures.fetched_at).toLocaleTimeString("es-AR")}`
                            : futures?.source === "snapshot"
                                ? "Desde snapshot del engine"
                                : "" })] }))] }));
}
