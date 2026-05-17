import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api/client";
function explainRejection(reason) {
    if (reason.startsWith("stop_loss must be"))
        return "El SL propuesto por el LLM estaba por encima del precio actual al momento de validar (el orderbook no tenía snapshot).";
    if (reason.startsWith("R:R ratio"))
        return "La relación riesgo/beneficio entre el SL y el TP no alcanzó el mínimo configurado (1.3:1).";
    if (reason.startsWith("SL distance"))
        return "La distancia del stop-loss al precio de entrada fue menor al 0.3× ATR(1h), considerado demasiado ajustado.";
    if (reason.startsWith("max_simultaneous"))
        return "Ya hay el máximo de posiciones simultáneas abiertas.";
    if (reason.startsWith("daily P&L"))
        return "El P&L del día alcanzó el límite de pérdida diaria configurado.";
    if (reason.startsWith("kill_switch"))
        return "El kill switch está activado. Solo se permiten ventas para cerrar posiciones.";
    if (reason.startsWith("BUY requires stop_loss"))
        return "El LLM no incluyó un stop_loss en la respuesta. Todo BUY lo requiere obligatoriamente.";
    if (reason.startsWith("parse_error"))
        return "El LLM devolvió una respuesta que no pudo parsearse como JSON válido.";
    if (reason.startsWith("llm_error"))
        return "Todos los providers LLM fallaron (rate limit o error) y no se obtuvo decisión.";
    return "";
}
export function Decisions() {
    const [items, setItems] = useState([]);
    const [agent, setAgent] = useState("");
    const [selected, setSelected] = useState(null);
    useEffect(() => {
        api.decisions(agent ? { agent } : undefined).then(setItems).catch(() => { });
    }, [agent]);
    const out = (d) => d.output;
    const isBuyRejected = (d) => out(d).action === "BUY" && !d.executed;
    const rejectionLabel = (reason) => {
        if (reason.startsWith("stop_loss must be"))
            return "SL > precio actual";
        if (reason.startsWith("R:R ratio"))
            return reason.replace("R:R ratio", "R:R");
        if (reason.startsWith("SL distance"))
            return "SL muy ajustado";
        if (reason.startsWith("max_simultaneous"))
            return "Máx. posiciones abiertas";
        if (reason.startsWith("daily P&L"))
            return "Stop diario alcanzado";
        if (reason.startsWith("kill_switch"))
            return "Kill switch activo";
        if (reason.startsWith("SELL requested"))
            return "Sin posición abierta";
        if (reason.startsWith("insufficient_data"))
            return reason;
        if (reason.startsWith("llm_error"))
            return "Error LLM";
        if (reason.startsWith("parse_error"))
            return "Error parsing LLM";
        return reason;
    };
    return (_jsxs("div", { className: "grid grid-cols-1 lg:grid-cols-3 gap-4", children: [_jsxs("div", { className: "lg:col-span-2 rounded-xl bg-zinc-900 p-5", children: [_jsxs("div", { className: "flex items-center justify-between mb-4", children: [_jsx("h2", { className: "text-lg font-semibold", children: "Decisiones (audit log)" }), _jsxs("select", { value: agent, onChange: e => setAgent(e.target.value), className: "rounded bg-zinc-800 px-2 py-1 text-sm border border-zinc-700", children: [_jsx("option", { value: "", children: "Todos" }), _jsx("option", { value: "decisor", children: "Decisor" }), _jsx("option", { value: "supervisor", children: "Supervisor" })] })] }), _jsxs("table", { className: "w-full text-sm", children: [_jsx("thead", { children: _jsxs("tr", { className: "text-xs uppercase text-zinc-500 border-b border-zinc-800", children: [_jsx("th", { className: "text-left py-2 pr-3", children: "TS" }), _jsx("th", { className: "text-left pr-3", children: "ID" }), _jsx("th", { className: "text-left pr-3", children: "Agente" }), _jsx("th", { className: "text-left pr-3", children: "Modelo" }), _jsx("th", { className: "text-left pr-3", children: "Acci\u00F3n" }), _jsx("th", { className: "text-right pr-3", children: "Conf" }), _jsx("th", { className: "text-left", children: "Estado / Motivo" })] }) }), _jsx("tbody", { children: items.map(d => (_jsxs("tr", { onClick: () => setSelected(d), className: `cursor-pointer border-t border-zinc-800 hover:bg-zinc-800/40 transition-colors ${selected?.id === d.id ? "bg-zinc-800" : ""}`, children: [_jsx("td", { className: "py-2 pr-3 text-zinc-400 text-xs whitespace-nowrap", children: new Date(d.ts).toLocaleString("es-AR", { hour12: false }) }), _jsx("td", { className: "pr-3 text-xs text-zinc-500 font-mono", children: d.id.substring(0, 8) }), _jsx("td", { className: "pr-3", children: d.agent }), _jsx("td", { className: "pr-3 text-xs text-zinc-400 font-mono", children: d.model }), _jsx("td", { className: "pr-3 font-semibold", children: d.agent === "supervisor"
                                                ? out(d).mode === "diagnostic"
                                                    ? _jsx("span", { className: "text-xs bg-amber-900/50 text-amber-300 px-2 py-0.5 rounded font-normal", children: "Diagn\u00F3stico" })
                                                    : _jsx("span", { className: "text-xs bg-blue-900/50 text-blue-300 px-2 py-0.5 rounded font-normal", children: "Normal" })
                                                : _jsx("span", { className: out(d).action === "BUY" ? "text-emerald-400" : out(d).action === "SELL" ? "text-red-400" : "text-zinc-400", children: out(d).action ?? "—" }) }), _jsxs("td", { className: "text-right pr-3", children: [((out(d).confidence ?? 0) * 100).toFixed(0), "%"] }), _jsx("td", { className: "py-1", children: d.executed
                                                ? _jsx("span", { className: "text-emerald-400", children: "\u2705 ejecutado" })
                                                : isBuyRejected(d) && d.rejected_reason
                                                    ? (_jsxs("span", { className: "inline-flex flex-col gap-0.5", children: [_jsx("span", { className: "text-amber-400 text-xs font-semibold", children: "\u26A0 BUY bloqueado" }), _jsx("span", { className: "text-red-400 text-xs font-mono", children: rejectionLabel(d.rejected_reason) })] }))
                                                    : d.rejected_reason
                                                        ? _jsxs("span", { className: "text-zinc-500 text-xs", children: ["\u274C ", rejectionLabel(d.rejected_reason)] })
                                                        : _jsx("span", { className: "text-zinc-600", children: "\u2014" }) })] }, d.id))) })] })] }), _jsx("div", { className: "rounded-xl bg-zinc-900 p-5 overflow-auto max-h-[80vh]", children: selected ? (_jsxs("div", { children: [_jsx("div", { className: "flex items-center gap-2 mb-1", children: selected.agent === "supervisor"
                                ? _jsxs(_Fragment, { children: [_jsx("h3", { className: "font-semibold text-lg text-zinc-300", children: "Supervisor" }), out(selected).mode === "diagnostic"
                                            ? _jsx("span", { className: "text-xs bg-amber-900/50 text-amber-300 px-2 py-0.5 rounded", children: "Diagn\u00F3stico" })
                                            : _jsx("span", { className: "text-xs bg-blue-900/50 text-blue-300 px-2 py-0.5 rounded", children: "Normal" })] })
                                : _jsxs(_Fragment, { children: [_jsx("h3", { className: `font-semibold text-lg ${out(selected).action === "BUY" ? "text-emerald-400" : out(selected).action === "SELL" ? "text-red-400" : "text-zinc-300"}`, children: out(selected).action ?? "—" }), selected.executed
                                            ? _jsx("span", { className: "text-xs bg-emerald-900/50 text-emerald-300 px-2 py-0.5 rounded", children: "ejecutado" })
                                            : selected.rejected_reason
                                                ? _jsx("span", { className: "text-xs bg-red-900/50 text-red-300 px-2 py-0.5 rounded", children: "bloqueado" })
                                                : null] }) }), _jsxs("div", { className: "mb-3 space-y-1 text-xs text-zinc-500", children: [_jsx("p", { className: "font-mono", children: selected.model }), _jsxs("p", { children: ["ID: ", _jsx("span", { className: "text-zinc-400", children: selected.id })] }), _jsx("p", { children: new Date(selected.ts).toLocaleString("es-AR", { hour12: false }) })] }), _jsx("p", { className: "text-sm text-zinc-300 mb-4 leading-relaxed", children: out(selected).reasoning ?? "" }), (out(selected).confluences ?? []).length > 0 && (_jsxs("div", { className: "mb-4", children: [_jsx("p", { className: "text-xs text-zinc-500 uppercase mb-1", children: "Confluencias detectadas" }), _jsx("ul", { className: "space-y-1", children: (out(selected).confluences ?? []).map((c, i) => (_jsxs("li", { className: "text-xs text-emerald-400 flex items-center gap-1", children: [_jsx("span", { className: "text-zinc-600", children: "\u2022" }), " ", c] }, i))) })] })), out(selected).action === "BUY" && (_jsxs("div", { className: "mb-4 rounded-lg bg-zinc-800 p-3 space-y-1.5", children: [_jsx("p", { className: "text-xs text-zinc-500 uppercase mb-2", children: "Par\u00E1metros de la orden" }), _jsxs("div", { className: "flex justify-between text-xs", children: [_jsx("span", { className: "text-zinc-400", children: "Stop Loss" }), _jsx("span", { className: "font-mono text-red-400", children: out(selected).stop_loss ? `$${out(selected).stop_loss.toFixed(2)}` : "—" })] }), _jsxs("div", { className: "flex justify-between text-xs", children: [_jsx("span", { className: "text-zinc-400", children: "Take Profit" }), _jsx("span", { className: "font-mono text-emerald-400", children: out(selected).take_profit ? `$${out(selected).take_profit.toFixed(2)}` : "—" })] }), _jsxs("div", { className: "flex justify-between text-xs", children: [_jsx("span", { className: "text-zinc-400", children: "Size" }), _jsx("span", { className: "font-mono text-zinc-300", children: out(selected).position_size_pct != null ? `${(out(selected).position_size_pct * 100).toFixed(0)}% del capital` : "—" })] })] })), isBuyRejected(selected) && selected.rejected_reason && (_jsxs("div", { className: "mb-4 rounded-lg bg-amber-950/40 border border-amber-800/50 p-3", children: [_jsx("p", { className: "text-xs text-amber-400 font-semibold mb-1", children: "\u26A0 Por qu\u00E9 no se ejecut\u00F3" }), _jsx("p", { className: "text-xs text-red-300 font-mono", children: selected.rejected_reason }), _jsx("p", { className: "text-xs text-zinc-500 mt-1", children: explainRejection(selected.rejected_reason) })] })), !isBuyRejected(selected) && selected.rejected_reason && (_jsx("div", { className: "mb-4 rounded-lg bg-zinc-800 p-3", children: _jsxs("p", { className: "text-xs text-red-400", children: ["Rechazada: ", selected.rejected_reason] }) })), _jsxs("details", { className: "mb-2", children: [_jsx("summary", { className: "cursor-pointer text-xs text-zinc-500 hover:text-zinc-300", children: "Output JSON" }), _jsx("pre", { className: "mt-2 text-xs bg-zinc-950 p-3 rounded overflow-auto", children: JSON.stringify(selected.output, null, 2) })] }), _jsxs("details", { children: [_jsx("summary", { className: "cursor-pointer text-xs text-zinc-500 hover:text-zinc-300", children: "Input JSON" }), _jsx("pre", { className: "mt-2 text-xs bg-zinc-950 p-3 rounded overflow-auto max-h-64", children: JSON.stringify(selected.input, null, 2) })] })] })) : _jsx("p", { className: "text-zinc-500 text-sm", children: "Seleccion\u00E1 una fila para ver el detalle." }) })] }));
}
