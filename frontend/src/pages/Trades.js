import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api/client";
function fmt(n, decimals = 2, prefix = "$") {
    if (n == null)
        return "—";
    return `${prefix}${n.toFixed(decimals)}`;
}
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
export function Trades() {
    const [trades, setTrades] = useState([]);
    const [filter, setFilter] = useState("all");
    const { closing, requestClose } = useCloseTrade(setTrades);
    useEffect(() => {
        const status = filter === "all" ? undefined : filter;
        api.trades(status).then(setTrades).catch(() => { });
    }, [filter]);
    return (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("h2", { className: "text-lg font-semibold", children: "Historial de trades" }), _jsxs("span", { className: "text-xs text-zinc-500", children: [trades.length, " trade", trades.length !== 1 ? "s" : ""] })] }), _jsxs("div", { className: "flex gap-2", children: [_jsx("button", { onClick: () => setFilter("all"), className: `text-xs px-3 py-1.5 rounded transition-colors ${filter === "all"
                            ? "bg-blue-900 text-blue-200 font-semibold"
                            : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"}`, children: "Todos" }), _jsx("button", { onClick: () => setFilter("open"), className: `text-xs px-3 py-1.5 rounded transition-colors ${filter === "open"
                            ? "bg-blue-900/70 text-blue-200 font-semibold"
                            : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"}`, children: "Abiertos" }), _jsx("button", { onClick: () => setFilter("closed"), className: `text-xs px-3 py-1.5 rounded transition-colors ${filter === "closed"
                            ? "bg-blue-900/70 text-blue-200 font-semibold"
                            : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"}`, children: "Cerrados" })] }), trades.length === 0 && (_jsx("div", { className: "rounded-xl bg-zinc-900 p-8 text-center text-zinc-500 text-sm", children: "Sin trades a\u00FAn." })), trades.map(t => {
                const valueUsdt = t.quantity_btc * t.entry_price;
                const pnlPositive = (t.pnl_usdt ?? 0) >= 0;
                const pnlColor = t.pnl_usdt == null ? "text-zinc-400" : pnlPositive ? "text-emerald-400" : "text-red-400";
                const isOpen = t.status === "open";
                return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-4", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 mb-4", children: [_jsx("span", { className: `text-xs font-bold px-2 py-0.5 rounded ${t.side === "BUY" ? "bg-emerald-900/60 text-emerald-300" : "bg-red-900/60 text-red-300"}`, children: t.side }), _jsx("span", { className: `text-xs px-2 py-0.5 rounded ${isOpen ? "bg-blue-900/50 text-blue-300" :
                                        t.status === "closed" ? "bg-zinc-800 text-zinc-400" :
                                            "bg-zinc-800 text-zinc-600"}`, children: t.status.toUpperCase() }), _jsx("span", { className: "text-xs text-zinc-500", children: new Date(t.ts_open).toLocaleString("es-AR", { hour12: false }) }), t.ts_close && (_jsxs("span", { className: "text-xs text-zinc-600", children: ["\u2192 ", new Date(t.ts_close).toLocaleString("es-AR", { hour12: false })] })), t.close_reason && (_jsx("span", { className: "text-xs text-zinc-600 italic ml-auto", children: t.close_reason }))] }), _jsxs("div", { className: "grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Cantidad" }), _jsxs("div", { className: "font-mono text-sm", children: [t.quantity_btc.toFixed(6), " BTC"] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Valor USDT" }), _jsx("div", { className: "font-mono text-sm", children: fmt(valueUsdt) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Precio entrada" }), _jsx("div", { className: "font-mono text-sm", children: fmt(t.entry_price) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Precio salida" }), _jsx("div", { className: "font-mono text-sm", children: fmt(t.exit_price) })] })] }), _jsxs("div", { className: "grid grid-cols-2 sm:grid-cols-4 gap-4 pt-3 border-t border-zinc-800", children: [_jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Stop Loss" }), _jsx("div", { className: "font-mono text-sm text-red-400", children: fmt(t.stop_loss) }), _jsxs("div", { className: "text-xs text-zinc-600 mt-0.5", children: [slDistance(t.entry_price, t.stop_loss), " abajo"] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Take Profit" }), _jsx("div", { className: "font-mono text-sm text-emerald-400", children: fmt(t.take_profit) }), _jsxs("div", { className: "text-xs text-zinc-600 mt-0.5", children: [tpDistance(t.entry_price, t.take_profit), " arriba"] })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "R:R ratio" }), _jsx("div", { className: "font-mono text-sm text-zinc-300", children: rrRatio(t.entry_price, t.stop_loss, t.take_profit) })] }), _jsxs("div", { children: [_jsx("div", { className: "text-xs text-zinc-500 mb-1", children: "Fees pagadas" }), _jsx("div", { className: "font-mono text-sm text-zinc-400", children: fmt(t.fees_usdt) })] })] }), (t.pnl_usdt != null || isOpen) && (_jsxs("div", { className: "mt-3 pt-3 border-t border-zinc-800 flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-zinc-500", children: isOpen ? "P&L no realizado estimado" : "P&L realizado" }), _jsx("span", { className: `font-mono font-semibold text-sm ${pnlColor}`, children: t.pnl_usdt != null
                                        ? `${t.pnl_usdt >= 0 ? "+" : ""}${fmt(t.pnl_usdt)}${pct(t.pnl_pct)}`
                                        : isOpen ? "pendiente" : "—" })] })), isOpen && (_jsxs("div", { className: "mt-3 pt-3 border-t border-zinc-800 flex items-center justify-between", children: [_jsx("span", { className: "text-xs text-zinc-500", children: t.close_requested
                                        ? "Cierre solicitado — el engine ejecutará la venta en el próximo ciclo"
                                        : "Cierre manual a precio de mercado" }), _jsx("button", { disabled: t.close_requested || closing === t.id, onClick: () => requestClose(t), className: `text-xs px-3 py-1 rounded font-semibold transition-colors ${t.close_requested
                                        ? "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                                        : closing === t.id
                                            ? "bg-orange-900/50 text-orange-400 cursor-wait"
                                            : "bg-red-900/60 text-red-300 hover:bg-red-800/70 cursor-pointer"}`, children: t.close_requested ? "Cierre pendiente..." : closing === t.id ? "Enviando..." : "Cerrar ahora" })] }))] }, t.id));
            })] }));
}
