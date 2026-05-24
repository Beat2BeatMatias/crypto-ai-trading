import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { fmtPnlValue, pnlColorClass } from "../lib/pnl";
export function PnlRow({ label, pnlUsdt, pnlPct, labelClass = "text-zinc-500" }) {
    return (_jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsx("span", { className: `text-xs ${labelClass}`, children: label }), _jsx("span", { className: `font-mono text-sm font-semibold ${pnlColorClass(pnlUsdt)}`, children: fmtPnlValue(pnlUsdt, pnlPct) })] }));
}
