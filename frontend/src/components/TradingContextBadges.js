import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
function ModeBadge({ mode }) {
    const isLive = mode === "LIVE";
    return (_jsx("span", { className: `rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${isLive
            ? "bg-red-900/80 text-red-300 ring-1 ring-red-700 animate-pulse"
            : "bg-amber-900/50 text-amber-300 ring-1 ring-amber-800"}`, title: isLive ? "Modo LIVE — trading real (según config)" : "Modo paper — sin dinero real en config", children: isLive ? "LIVE" : "Paper" }));
}
function ExchangeBadge({ testnet }) {
    return (_jsx("span", { className: `rounded px-2 py-0.5 text-[11px] font-semibold ${testnet
            ? "bg-zinc-800 text-zinc-400 ring-1 ring-zinc-700"
            : "bg-orange-950/60 text-orange-300 ring-1 ring-orange-800"}`, title: testnet
            ? "BINANCE_TESTNET=true — órdenes van a testnet de Binance"
            : "BINANCE_TESTNET=false — órdenes van a mainnet de Binance", children: testnet ? "Testnet" : "Mainnet" }));
}
function ProductBadge({ product, variant, }) {
    const isFutures = product === "futures";
    const label = isFutures ? "Futuros" : "Spot";
    const prefix = variant === "runtime" ? "Runtime: " : "";
    return (_jsxs("span", { className: `rounded px-2 py-0.5 text-[11px] font-semibold ${isFutures
            ? "bg-violet-900/50 text-violet-200 ring-1 ring-violet-700"
            : "bg-sky-950/50 text-sky-200 ring-1 ring-sky-800"}`, title: variant === "config"
            ? `trading_product=${product} en configuración`
            : `Producto efectivo del engine en este ciclo`, children: [prefix, label] }));
}
export function TradingContextBadges({ ctx }) {
    if (!ctx) {
        return (_jsx("span", { className: "text-[11px] text-zinc-600", children: "Cargando modo\u2026" }));
    }
    const configProduct = ctx.trading_product === "futures" ? "futures" : "spot";
    const effective = ctx.effective_trading_product === "futures" ? "futures" : "spot";
    const mismatch = ctx.runtime_mismatch ?? configProduct !== effective;
    const mismatchTitle = (() => {
        if (!mismatch)
            return "";
        if (ctx.runtime_mismatch_detail)
            return ctx.runtime_mismatch_detail;
        const labels = {
            api_permissions: "API sin permiso de futuros o IP no autorizada",
            insufficient_margin: "Margen futuros insuficiente para min_notional",
            restart_required: "Reiniciá trading-engine para aplicar futuros",
        };
        const r = ctx.runtime_mismatch_reason;
        return (r && labels[r]) || "Config futuros pero runtime spot";
    })();
    return (_jsxs("div", { className: "flex items-center gap-1.5 flex-wrap", "aria-label": "Modo de trading", children: [_jsx(ModeBadge, { mode: ctx.mode }), _jsx(ExchangeBadge, { testnet: ctx.binance_testnet }), _jsx(ProductBadge, { product: configProduct, variant: "config" }), mismatch && (_jsxs(_Fragment, { children: [_jsx(ProductBadge, { product: effective, variant: "runtime" }), _jsx("span", { className: "text-[10px] text-amber-400 max-w-[11rem] leading-tight", title: mismatchTitle, children: "\u26A0 Config futuros \u00B7 runtime spot" })] }))] }));
}
