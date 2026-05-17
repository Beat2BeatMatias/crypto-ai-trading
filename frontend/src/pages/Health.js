import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
function StatusRow({ label, ok, detail }) {
    return (_jsxs("div", { className: "flex items-center justify-between rounded bg-zinc-800 p-3", children: [_jsx("span", { className: "font-medium", children: label }), _jsxs("span", { className: "flex items-center gap-2 text-sm", children: [_jsx("span", { className: `size-2 rounded-full ${ok ? "bg-emerald-400" : "bg-red-400"}` }), _jsx("span", { className: "text-zinc-400", children: detail })] })] }));
}
export function Health() {
    const [data, setData] = useState(null);
    const refresh = () => fetch("/api/health").then(r => r.json()).then(setData).catch(() => { });
    useEffect(() => {
        refresh();
        const id = setInterval(refresh, 5000);
        return () => clearInterval(id);
    }, []);
    return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-5 max-w-xl", children: [_jsx("h2", { className: "text-lg font-semibold mb-4", children: "Estado del sistema" }), _jsxs("div", { className: "space-y-2", children: [_jsx(StatusRow, { label: "Web API", ok: data?.ok ?? false, detail: data ? `DB: ${data.db}` : "Verificando..." }), _jsx(StatusRow, { label: "Trading Engine", ok: data?.engine?.ok ?? false, detail: data?.engine?.detail ?? "Verificando..." }), _jsx(StatusRow, { label: "Binance", ok: data?.binance?.ok ?? false, detail: data?.binance?.detail ?? "Verificando..." })] })] }));
}
