import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
const REASON_LABELS = {
    api_permissions: "API / permisos Binance",
    insufficient_margin: "Margen insuficiente",
    restart_required: "Reinicio del engine",
    unknown: "Desajuste config / runtime",
};
export function RuntimeMismatchBanner({ ctx }) {
    const reason = ctx.runtime_mismatch_reason ?? "unknown";
    const label = REASON_LABELS[reason] ?? REASON_LABELS.unknown;
    const detail = ctx.runtime_mismatch_detail ??
        "Config en futuros pero el engine opera en spot. Revisá logs y reiniciá trading-engine.";
    return (_jsxs("div", { className: "rounded-lg border border-amber-800/50 bg-amber-950/30 px-4 py-3 text-sm text-amber-200", role: "alert", children: [_jsxs("p", { className: "font-medium text-amber-100", children: ["Config en ", _jsx("strong", { children: "futuros" }), ", runtime en ", _jsx("strong", { children: "spot" }), _jsx("span", { className: "ml-2 rounded bg-amber-900/60 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-300", children: label })] }), _jsx("p", { className: "mt-2 leading-relaxed", children: detail }), reason === "api_permissions" && (_jsxs("ul", { className: "mt-2 list-disc pl-5 text-amber-200/90 space-y-1", children: [_jsxs("li", { children: ["En Binance \u2192 API Management: habilit\u00E1 permiso ", _jsx("strong", { children: "Futures" }), "."] }), _jsx("li", { children: "Si us\u00E1s whitelist de IP, agreg\u00E1 la IP del servidor." }), _jsxs("li", { children: ["Luego:", " ", _jsx("code", { className: "text-amber-100", children: "docker-compose restart trading-engine" })] })] })), reason === "restart_required" && (_jsx("p", { className: "mt-2 text-amber-200/90", children: "Guardar config no alcanza: el producto efectivo se fija al arrancar el proceso." }))] }));
}
