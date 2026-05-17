import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
// ── Formatters ────────────────────────────────────────────────────────────────
function fmt(n, decimals = 2, prefix = "$") {
    if (n == null)
        return "—";
    return `${prefix}${n.toLocaleString("es-AR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}
function pct(n) {
    if (n == null)
        return "";
    return ` (${n >= 0 ? "+" : ""}${n.toFixed(2)}%)`;
}
function slDistance(entry, sl) {
    if (!sl)
        return "—";
    return `${(((entry - sl) / entry) * 100).toFixed(2)}%`;
}
function tpDistance(entry, tp) {
    if (!tp)
        return "—";
    return `+${(((tp - entry) / entry) * 100).toFixed(2)}%`;
}
function rrRatio(entry, sl, tp) {
    if (!sl || !tp)
        return "—";
    const risk = entry - sl;
    const reward = tp - entry;
    if (risk <= 0)
        return "—";
    return `${(reward / risk).toFixed(2)}:1`;
}
// ── CSV Export ────────────────────────────────────────────────────────────────
function exportCSV(trades) {
    const cols = [
        "id", "ts_open", "ts_close", "side", "status",
        "quantity_btc", "entry_price", "exit_price",
        "stop_loss", "take_profit",
        "pnl_usdt", "pnl_pct", "fees_usdt", "close_reason",
        "order_id_open", "order_id_close",
    ];
    const header = cols.join(",");
    const rows = trades.map(t => cols.map(c => {
        const v = t[c];
        if (v == null)
            return "";
        if (typeof v === "string" && v.includes(","))
            return `"${v}"`;
        return String(v);
    }).join(","));
    const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}
// ── Close hook ────────────────────────────────────────────────────────────────
function useCloseTrade(setTrades) {
    const [closing, setClosing] = useState(null);
    async function requestClose(trade) {
        if (!window.confirm(`¿Cerrar manualmente este trade?\n\nEntrada: $${trade.entry_price.toFixed(2)}\nCantidad: ${trade.quantity_btc.toFixed(6)} BTC\n\nEl engine ejecutará la orden de venta al precio de mercado en el próximo ciclo (~30s).`))
            return;
        setClosing(trade.id);
        try {
            const updated = await api.closeTrade(trade.id);
            setTrades(prev => prev.map(t => t.id === updated.id ? updated : t));
        }
        catch {
            alert("Error al solicitar el cierre del trade.");
        }
        finally {
            setClosing(null);
        }
    }
    return { closing, requestClose };
}
// ── Summary Footer ────────────────────────────────────────────────────────────
function SummaryFooter({ trades }) {
    const closed = trades.filter(t => t.status === "closed");
    if (closed.length === 0)
        return null;
    const totalPnl = closed.reduce((s, t) => s + (t.pnl_usdt ?? 0), 0);
    const totalFees = trades.reduce((s, t) => s + (t.fees_usdt ?? 0), 0);
    const wins = closed.filter(t => (t.pnl_usdt ?? 0) > 0).length;
    const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0;
    const pnlColor = totalPnl >= 0 ? "text-emerald-400" : "text-red-400";
    return (_jsxs("div", { className: "rounded-xl bg-zinc-900 border border-zinc-700 p-4 grid grid-cols-2 sm:grid-cols-4 gap-4", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "P&L realizado total" }), _jsxs("div", { className: `font-mono font-semibold text-sm ${pnlColor}`, children: [totalPnl >= 0 ? "+" : "", fmt(totalPnl)] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Win rate" }), _jsxs("div", { className: "font-mono text-sm text-zinc-300", children: [winRate.toFixed(1), "% ", _jsxs("span", { className: "text-zinc-500", children: ["(", wins, "/", closed.length, ")"] })] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Fees totales" }), _jsx("div", { className: "font-mono text-sm text-zinc-400", children: fmt(totalFees) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Trades mostrados" }), _jsxs("div", { className: "font-mono text-sm text-zinc-300", children: [trades.length, " ", _jsxs("span", { className: "text-zinc-500", children: ["(", closed.length, " cerrados)"] })] })] })] }));
}
// ── Main Component ────────────────────────────────────────────────────────────
export function Trades() {
    const [allTrades, setAllTrades] = useState([]);
    const [statusFilter, setStatusFilter] = useState("all");
    const [resultFilter, setResultFilter] = useState("all");
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [sortKey, setSortKey] = useState("ts_open");
    const [sortDir, setSortDir] = useState("desc");
    const { closing, requestClose } = useCloseTrade(setAllTrades);
    useEffect(() => {
        const status = statusFilter === "all" ? undefined : statusFilter;
        api.trades(status).then(setAllTrades).catch(() => { });
    }, [statusFilter]);
    const trades = useMemo(() => {
        let list = [...allTrades];
        if (resultFilter === "win")
            list = list.filter(t => (t.pnl_usdt ?? 0) > 0);
        else if (resultFilter === "loss")
            list = list.filter(t => (t.pnl_usdt ?? 0) < 0);
        if (dateFrom)
            list = list.filter(t => new Date(t.ts_open) >= new Date(dateFrom));
        if (dateTo)
            list = list.filter(t => new Date(t.ts_open) <= new Date(dateTo + "T23:59:59Z"));
        list.sort((a, b) => {
            let av, bv;
            if (sortKey === "ts_open") {
                av = new Date(a.ts_open).getTime();
                bv = new Date(b.ts_open).getTime();
            }
            else {
                av = a[sortKey] ?? -Infinity;
                bv = b[sortKey] ?? -Infinity;
            }
            return sortDir === "asc" ? av - bv : bv - av;
        });
        return list;
    }, [allTrades, resultFilter, dateFrom, dateTo, sortKey, sortDir]);
    function toggleSort(key) {
        if (sortKey === key)
            setSortDir(d => d === "asc" ? "desc" : "asc");
        else {
            setSortKey(key);
            setSortDir("desc");
        }
    }
    const sortIcon = (key) => sortKey !== key ? _jsx("span", { className: "text-zinc-700", children: "\u21C5" })
        : sortDir === "desc" ? _jsx("span", { className: "text-blue-400", children: "\u2193" })
            : _jsx("span", { className: "text-blue-400", children: "\u2191" });
    const btnCls = (active) => `text-xs px-3 py-1.5 rounded transition-colors ${active
        ? "bg-blue-900 text-blue-200 font-semibold"
        : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"}`;
    return (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("h2", { className: "text-lg font-semibold", children: "Historial de trades" }), _jsx("button", { onClick: () => exportCSV(trades), disabled: trades.length === 0, className: "text-xs px-3 py-1.5 rounded bg-zinc-800 text-zinc-400 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors", children: "\u2193 CSV" })] }), _jsxs("div", { className: "flex flex-wrap gap-3 items-end", children: [_jsx("div", { className: "flex gap-1", children: ["all", "open", "closed"].map(s => (_jsx("button", { onClick: () => setStatusFilter(s), className: btnCls(statusFilter === s), children: s === "all" ? "Todos" : s === "open" ? "Abiertos" : "Cerrados" }, s))) }), _jsxs("div", { className: "flex gap-1", children: [_jsx("button", { onClick: () => setResultFilter("all"), className: btnCls(resultFilter === "all"), children: "Win+Loss" }), _jsxs("button", { onClick: () => setResultFilter("win"), className: btnCls(resultFilter === "win"), children: [_jsx("span", { className: "text-emerald-400", children: "\u25B2" }), " Win"] }), _jsxs("button", { onClick: () => setResultFilter("loss"), className: btnCls(resultFilter === "loss"), children: [_jsx("span", { className: "text-red-400", children: "\u25BC" }), " Loss"] })] }), _jsxs("div", { className: "flex gap-2 items-center ml-auto", children: [_jsx("input", { type: "date", value: dateFrom, onChange: e => setDateFrom(e.target.value), className: "text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 focus:outline-none focus:border-zinc-500" }), _jsx("span", { className: "text-zinc-600 text-xs", children: "\u2014" }), _jsx("input", { type: "date", value: dateTo, onChange: e => setDateTo(e.target.value), className: "text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 focus:outline-none focus:border-zinc-500" }), (dateFrom || dateTo) && (_jsx("button", { onClick: () => { setDateFrom(""); setDateTo(""); }, className: "text-xs text-zinc-500 hover:text-zinc-300", children: "\u2715" }))] })] }), _jsxs("div", { className: "flex gap-3 text-xs text-zinc-500", children: [_jsx("span", { children: "Ordenar:" }), [
                        ["ts_open", "Fecha"],
                        ["pnl_usdt", "P&L"],
                        ["entry_price", "Precio entrada"],
                        ["quantity_btc", "Cantidad BTC"],
                    ].map(([k, label]) => (_jsxs("button", { onClick: () => toggleSort(k), className: `flex items-center gap-1 hover:text-zinc-300 transition-colors ${sortKey === k ? "text-blue-300" : ""}`, children: [label, " ", sortIcon(k)] }, k)))] }), _jsx(SummaryFooter, { trades: trades }), trades.length === 0 && (_jsx("div", { className: "rounded-xl bg-zinc-900 p-8 text-center text-zinc-500 text-sm", children: "Sin trades que coincidan con los filtros." })), trades.map(t => {
                const valueUsdt = t.quantity_btc * t.entry_price;
                const pnlPositive = (t.pnl_usdt ?? 0) >= 0;
                const pnlColor = t.pnl_usdt == null ? "text-zinc-400" : pnlPositive ? "text-emerald-400" : "text-red-400";
                const isOpen = t.status === "open";
                return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-4", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 mb-4", children: [_jsx("span", { className: `text-xs font-bold px-2 py-0.5 rounded ${t.side === "BUY" ? "bg-emerald-900/60 text-emerald-300" : "bg-red-900/60 text-red-300"}`, children: t.side }), _jsx("span", { className: `text-xs px-2 py-0.5 rounded ${isOpen ? "bg-blue-900/50 text-blue-300" :
                                        t.pnl_usdt != null && t.pnl_usdt > 0 ? "bg-emerald-900/40 text-emerald-400" :
                                            t.pnl_usdt != null && t.pnl_usdt < 0 ? "bg-red-900/40 text-red-400" :
                                                "bg-zinc-800 text-zinc-400"}`, children: isOpen ? "ABIERTO" : t.pnl_usdt != null && t.pnl_usdt > 0 ? "WIN" : t.pnl_usdt != null && t.pnl_usdt < 0 ? "LOSS" : t.status.toUpperCase() }), _jsxs("button", { type: "button", title: `ID: ${t.id}\nClick para copiar`, onClick: () => navigator.clipboard.writeText(t.id), className: "font-mono text-xs text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer select-none", children: ["#", t.id.slice(0, 8)] }), _jsx("span", { className: "text-xs text-zinc-500", children: new Date(t.ts_open).toLocaleString("es-AR", { hour12: false }) }), t.ts_close && (_jsxs("span", { className: "text-xs text-zinc-600", children: ["\u2192 ", new Date(t.ts_close).toLocaleString("es-AR", { hour12: false })] })), t.close_reason && (_jsx("span", { className: "text-xs text-zinc-600 italic ml-auto", children: t.close_reason }))] }), _jsxs("div", { className: "grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Cantidad" }), _jsxs("div", { className: "font-mono text-sm", children: [t.quantity_btc.toFixed(6), " BTC"] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Valor USDT" }), _jsx("div", { className: "font-mono text-sm", children: fmt(valueUsdt) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Precio entrada" }), _jsx("div", { className: "font-mono text-sm", children: fmt(t.entry_price) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Precio salida" }), _jsx("div", { className: "font-mono text-sm", children: fmt(t.exit_price) })] })] }), _jsxs("div", { className: "grid grid-cols-2 sm:grid-cols-4 gap-4 pt-3 border-t border-zinc-800", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Stop Loss" }), _jsx("div", { className: "font-mono text-sm text-red-400", children: fmt(t.stop_loss) }), _jsxs("div", { className: "text-xs text-zinc-600 mt-0.5", children: [slDistance(t.entry_price, t.stop_loss), " abajo"] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Take Profit" }), _jsx("div", { className: "font-mono text-sm text-emerald-400", children: fmt(t.take_profit) }), _jsxs("div", { className: "text-xs text-zinc-600 mt-0.5", children: [tpDistance(t.entry_price, t.take_profit), " arriba"] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "R:R ratio" }), _jsx("div", { className: "font-mono text-sm text-zinc-300", children: rrRatio(t.entry_price, t.stop_loss, t.take_profit) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Fees pagadas" }), _jsx("div", { className: "font-mono text-sm text-zinc-400", children: fmt(t.fees_usdt) })] })] }), (t.pnl_usdt != null || isOpen) && (_jsxs("div", { className: "mt-3 pt-3 border-t border-zinc-800 flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-zinc-500", children: isOpen ? "P&L no realizado estimado" : "P&L realizado" }), _jsx("span", { className: `font-mono font-semibold text-sm ${pnlColor}`, children: t.pnl_usdt != null
                                        ? `${t.pnl_usdt >= 0 ? "+" : ""}${fmt(t.pnl_usdt)}${pct(t.pnl_pct)}`
                                        : isOpen ? "pendiente" : "—" })] })), (t.order_id_open || t.order_id_close) && (_jsxs("div", { className: "mt-2 flex gap-4 text-xs text-zinc-600", children: [t.order_id_open && (_jsxs("span", { children: ["Order apertura: ", _jsx("span", { className: "font-mono", children: t.order_id_open })] })), t.order_id_close && (_jsxs("span", { children: ["Order cierre: ", _jsx("span", { className: "font-mono", children: t.order_id_close })] }))] })), isOpen && (_jsxs("div", { className: "mt-3 pt-3 border-t border-zinc-800 flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-zinc-500", children: t.close_requested
                                        ? "Cierre solicitado — el engine ejecutará la venta en el próximo ciclo"
                                        : "Cierre manual a precio de mercado" }), _jsx("button", { disabled: t.close_requested || closing === t.id, onClick: () => requestClose(t), className: `text-xs px-3 py-1 rounded font-semibold transition-colors ${t.close_requested
                                        ? "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                                        : closing === t.id
                                            ? "bg-orange-900/50 text-orange-400 cursor-wait"
                                            : "bg-red-900/60 text-red-300 hover:bg-red-800/70 cursor-pointer"}`, children: t.close_requested ? "Cierre pendiente..." : closing === t.id ? "Enviando..." : "Cerrar ahora" })] }))] }, t.id));
            })] }));
}
