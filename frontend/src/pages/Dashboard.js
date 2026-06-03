import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
import { PriceChart } from "../components/chart/PriceChart";
import ConfidenceBreakdown from "../components/ConfidenceBreakdown";
import ReasoningBlock from "../components/ReasoningBlock";
import { asDecisorOutput } from "../types/decisorOutput";
import { PnlRow } from "../components/PnlRow";
import { actionBadgeClass, computePnlPctDirectional, computePnlUsdtDirectional, sideBadgeClass, } from "../lib/pnl";
import { RuntimeMismatchBanner } from "../components/RuntimeMismatchBanner";
import { TradingContextBadges } from "../components/TradingContextBadges";
import { FuturesBalanceCard } from "../components/FuturesBalanceCard";
function Card({ title, children }) {
    return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-5", children: [_jsx("h3", { className: "text-sm font-semibold text-zinc-400 mb-3 uppercase tracking-wide", children: title }), children] }));
}
function StatRow({ label, value, valueClass = "text-zinc-200" }) {
    return (_jsxs("div", { className: "flex justify-between items-baseline py-1 border-b border-zinc-800 last:border-0", children: [_jsx("span", { className: "text-xs text-zinc-500", children: label }), _jsx("span", { className: `text-sm font-mono font-semibold ${valueClass}`, children: value })] }));
}
function EngineStatusPill({ health }) {
    if (!health)
        return null;
    const age = health.last_decision_age_min;
    const paused = !health.ok || (age !== null && age > 30);
    const slow = !paused && age !== null && age > 15;
    const dot = paused
        ? "bg-red-400"
        : slow
            ? "bg-amber-400 animate-pulse"
            : "bg-emerald-400 animate-pulse";
    const label = paused ? "Engine pausado" : slow ? "Engine lento" : "Engine activo";
    return (_jsxs("div", { className: "flex items-center gap-2 border-l border-zinc-700 pl-4", children: [_jsx("span", { className: `size-2.5 rounded-full ${dot}` }), _jsx("span", { className: "text-sm text-zinc-300", children: label }), _jsxs("span", { className: "text-xs text-zinc-500", children: ["(", health.detail, ")"] })] }));
}
function useCountdown(engineHealth) {
    const [secsLeft, setSecsLeft] = useState(null);
    useEffect(() => {
        if (engineHealth?.next_execution_in_min == null) {
            setSecsLeft(null);
            return;
        }
        setSecsLeft(engineHealth.next_execution_in_min * 60);
        const timer = setInterval(() => {
            setSecsLeft(prev => {
                if (prev == null || prev <= 0)
                    return 0;
                return prev - 1;
            });
        }, 1000);
        return () => clearInterval(timer);
    }, [engineHealth?.next_execution_in_min, engineHealth?.last_decision_age_min]);
    return secsLeft;
}
export function Dashboard() {
    const [positions, setPositions] = useState([]);
    const [lastDecision, setLastDecision] = useState(null);
    const [killSwitchOn, setKillSwitchOn] = useState(false);
    const [stats, setStats] = useState(null);
    const [ticker, setTicker] = useState(null);
    const [engineHealth, setEngineHealth] = useState(null);
    const [balance, setBalance] = useState(null);
    const [tradingCtx, setTradingCtx] = useState(null);
    const countdownSecs = useCountdown(engineHealth);
    const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
    const { last, connected } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);
    const loadStats = () => api.dailyStats().then(setStats).catch(() => { });
    const loadHealth = () => fetch("/api/health").then(r => r.json())
        .then(d => {
        setEngineHealth(d?.engine ?? null);
        setTradingCtx(d?.trading ?? null);
    })
        .catch(() => { });
    useEffect(() => {
        api.positions().then(setPositions).catch(() => { });
        api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null)).catch(() => { });
        api.config().then(cfg => {
            const ks = cfg.find(c => c.key === "kill_switch");
            setKillSwitchOn(ks?.value === "true");
        }).catch(() => { });
        loadStats();
        loadHealth();
        api.balance().then(setBalance).catch(() => { });
        const id = setInterval(loadHealth, 15_000);
        const id2 = setInterval(() => api.balance().then(setBalance).catch(() => { }), 30_000);
        return () => { clearInterval(id); clearInterval(id2); };
    }, []);
    useEffect(() => {
        if (!last)
            return;
        if (last.event === "positions")
            setPositions(last.data);
        if (last.event === "ticker")
            setTicker(last.data);
        if (last.event === "decision") {
            api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null)).catch(() => { });
            loadStats();
        }
    }, [last]);
    const onKillSwitch = async () => {
        if (!confirm("¿Activar kill switch? Cierra posiciones y desactiva el bot."))
            return;
        await api.killSwitch(true);
        setKillSwitchOn(true);
    };
    const out = lastDecision ? asDecisorOutput(lastDecision.output) : undefined;
    const actionColor = actionBadgeClass(out?.action ?? "HOLD");
    const pnlTotal = (stats?.pnl_realized ?? 0) + (stats?.pnl_unrealized ?? 0);
    const pnlColor = pnlTotal > 0 ? "text-emerald-400" : pnlTotal < 0 ? "text-red-400" : "text-zinc-400";
    const isFuturesMode = tradingCtx?.trading_product === "futures";
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center justify-between rounded-xl bg-zinc-900 p-4 flex-wrap gap-3", children: [_jsxs("div", { className: "flex items-center gap-4 flex-wrap", children: [_jsx("div", { className: "sm:hidden", children: _jsx(TradingContextBadges, { ctx: tradingCtx }) }), _jsxs("div", { className: "flex items-center gap-3", children: [_jsx("span", { className: `size-3 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-zinc-600"}` }), _jsx("span", { className: "text-sm", children: connected ? "WS conectado" : "Desconectado — reconectando..." })] }), _jsx(EngineStatusPill, { health: engineHealth }), ticker && (_jsxs("div", { className: "flex items-center gap-2 border-l border-zinc-700 pl-4", children: [_jsx("span", { className: "text-sm font-semibold text-zinc-400", children: ticker.symbol }), _jsx("span", { className: "text-lg font-bold text-white", children: ticker.price != null
                                            ? `$${ticker.price.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                                            : "—" })] }))] }), _jsxs("button", { onClick: onKillSwitch, className: `rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${killSwitchOn ? "bg-red-800 cursor-default" : "bg-red-600 hover:bg-red-500"}`, children: ["\uD83D\uDEA8 ", killSwitchOn ? "Kill Switch ACTIVO" : "Activar Kill Switch"] })] }), tradingCtx?.runtime_mismatch && (_jsx(RuntimeMismatchBanner, { ctx: tradingCtx })), _jsx(PriceChart, { tradingProduct: tradingCtx?.trading_product, chartLabel: tradingCtx?.chart_label }), _jsxs("div", { className: "grid gap-4 lg:grid-cols-4", children: [isFuturesMode ? (_jsx(FuturesBalanceCard, { futures: balance?.futures })) : (_jsx(Card, { title: "Balance Binance", children: !balance ? (_jsx("p", { className: "text-zinc-500 text-sm", children: "Cargando..." })) : (_jsxs("div", { className: "space-y-1", children: [_jsx(StatRow, { label: "USDT libre", value: `$${balance.usdt.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, valueClass: "text-emerald-400" }), balance.usdt_locked > 0 && (_jsx(StatRow, { label: "USDT bloqueado (\u00F3rdenes)", value: `$${balance.usdt_locked.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, valueClass: "text-yellow-500" })), _jsx(StatRow, { label: "BTC libre en exchange", value: `${balance.btc_exchange.toFixed(6)} BTC`, valueClass: "text-amber-400" }), balance.btc_locked > 0 && (_jsx(StatRow, { label: "BTC bloqueado (\u00F3rdenes)", value: `${balance.btc_locked.toFixed(6)} BTC`, valueClass: "text-yellow-500" })), _jsx(StatRow, { label: "BTC en posiciones", value: `${balance.btc_in_positions.toFixed(6)} BTC`, valueClass: "text-zinc-300" }), ticker?.price != null && (_jsx(StatRow, { label: "Total en USD (real)", value: `$${(balance.usdt_total + balance.btc_exchange_total * ticker.price).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, valueClass: "text-white font-semibold" })), _jsxs("div", { className: "pt-2 text-xs text-zinc-600", children: [balance.balance_ts
                                            ? `Actualizado ${new Date(balance.balance_ts).toLocaleTimeString("es-AR")}`
                                            : "Sin datos de Binance", balance.balance_source === "binance" ? "" : " (fallback DB)"] })] })) })), _jsx(Card, { title: "Posiciones abiertas", children: positions.length === 0
                            ? _jsx("p", { className: "text-zinc-500 text-sm", children: "Ninguna posici\u00F3n abierta." })
                            : positions.map(p => {
                                const dir = p.position_side ?? "LONG";
                                const liveCurrentPrice = ticker?.price ?? p.current_price;
                                const currentPnlUsdt = liveCurrentPrice != null
                                    ? computePnlUsdtDirectional(p.entry_price, p.quantity_btc, liveCurrentPrice, dir)
                                    : p.unrealized_pnl;
                                const currentPnlPct = liveCurrentPrice != null
                                    ? computePnlPctDirectional(p.entry_price, liveCurrentPrice, dir)
                                    : p.unrealized_pct;
                                return (_jsxs("div", { className: "rounded-lg bg-zinc-800 p-3 mb-2 last:mb-0", children: [_jsxs("div", { className: "flex flex-wrap justify-between items-center gap-2 text-sm mb-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { children: p.symbol }), _jsx("span", { className: `text-xs font-bold px-2 py-0.5 rounded ${sideBadgeClass(dir)}`, children: dir }), p.leverage != null && (_jsxs("span", { className: "text-xs text-zinc-500", children: [p.leverage, "x"] }))] }), _jsxs("span", { className: "text-xs text-zinc-500", children: ["qty ", p.quantity_btc.toFixed(6), " \u00B7 entry $", p.entry_price.toFixed(2)] })] }), _jsxs("div", { className: "text-xs text-zinc-500 mb-2", children: [liveCurrentPrice != null
                                                    ? `Precio actual $${liveCurrentPrice.toFixed(2)}`
                                                    : "Precio actual —", p.stop_loss != null && ` · SL $${p.stop_loss.toFixed(2)}`, p.take_profit != null && ` · TP $${p.take_profit.toFixed(2)}`, p.liquidation_price != null && (_jsxs("span", { className: "text-orange-400/90", children: [" \u00B7 Liq $", p.liquidation_price.toFixed(2)] }))] }), _jsxs("div", { className: "space-y-1.5 pt-2 border-t border-zinc-700", children: [_jsx(PnlRow, { label: "P&L al precio actual", pnlUsdt: currentPnlUsdt, pnlPct: currentPnlPct }), _jsx(PnlRow, { label: "P&L si cierra en SL", pnlUsdt: p.sl_pnl_usdt, pnlPct: p.sl_pnl_pct, labelClass: "text-red-400/70" }), _jsx(PnlRow, { label: "P&L si cierra en TP", pnlUsdt: p.tp_pnl_usdt, pnlPct: p.tp_pnl_pct, labelClass: "text-emerald-400/70" })] })] }, p.id));
                            }) }), _jsx(Card, { title: "\u00DAltima decisi\u00F3n", children: !lastDecision
                            ? _jsx("p", { className: "text-zinc-500 text-sm", children: "Sin decisiones a\u00FAn." })
                            : (_jsxs("div", { children: [_jsx("div", { className: `text-4xl font-bold mb-2 ${actionColor}`, children: out?.action ?? "—" }), _jsxs("div", { className: "text-sm text-zinc-400 mb-2", children: [_jsx(ConfidenceBreakdown, { compact: true, confidence: out?.confidence, confidenceBase: out?.confidence_base, confidenceAdjustment: out?.confidence_adjustment, meta: out?.confidence_meta }), out?.regime && _jsx("span", { className: "ml-1 text-zinc-500", children: out.regime })] }), out?.reasoning && _jsx(ReasoningBlock, { reasoning: out.reasoning, compact: true }), _jsx("div", { className: "mt-3 text-xs text-zinc-600", children: new Date(lastDecision.ts).toLocaleString("es-AR") }), countdownSecs != null && (_jsxs("div", { className: "mt-2 flex items-center gap-2 rounded-lg bg-zinc-800 px-3 py-2", children: [_jsx("span", { className: "text-xs text-zinc-500", children: "Pr\u00F3xima ejecuci\u00F3n" }), _jsx("span", { className: `ml-auto font-mono text-sm font-semibold ${countdownSecs === 0
                                                    ? "text-emerald-400 animate-pulse"
                                                    : countdownSecs <= 60
                                                        ? "text-amber-400"
                                                        : "text-zinc-300"}`, children: countdownSecs === 0
                                                    ? "ejecutando..."
                                                    : countdownSecs < 60
                                                        ? `${countdownSecs}s`
                                                        : `${Math.floor(countdownSecs / 60)}m ${countdownSecs % 60}s` })] }))] })) }), _jsx(Card, { title: "Estado del d\u00EDa", children: !stats ? (_jsx("p", { className: "text-zinc-500 text-sm", children: "Cargando..." })) : (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { children: [_jsxs("div", { className: `text-2xl font-bold font-mono ${pnlColor}`, children: [pnlTotal >= 0 ? "+" : "", "$", pnlTotal.toFixed(2)] }), _jsx("div", { className: "text-xs text-zinc-500 mt-0.5", children: "P&L total del d\u00EDa" })] }), _jsxs("div", { children: [_jsx(StatRow, { label: "Realizado", value: `${stats.pnl_realized >= 0 ? "+" : ""}$${stats.pnl_realized.toFixed(2)}`, valueClass: stats.pnl_realized > 0 ? "text-emerald-400" : stats.pnl_realized < 0 ? "text-red-400" : "text-zinc-400" }), _jsx(StatRow, { label: "No realizado", value: `${stats.pnl_unrealized >= 0 ? "+" : ""}$${stats.pnl_unrealized.toFixed(2)}`, valueClass: stats.pnl_unrealized > 0 ? "text-emerald-400" : stats.pnl_unrealized < 0 ? "text-red-400" : "text-zinc-400" }), _jsx(StatRow, { label: "Fees pagadas", value: `$${stats.fees_total.toFixed(4)}`, valueClass: "text-zinc-500" })] }), _jsxs("div", { children: [_jsx("p", { className: "text-xs text-zinc-500 uppercase mb-1", children: "Trades" }), _jsx(StatRow, { label: "Abiertos ahora / Cerrados hoy", value: `${stats.trades_open} / ${stats.trades_closed}` }), stats.trades_closed > 0 && (_jsx(StatRow, { label: "Win / Loss", value: `${stats.trades_won}W · ${stats.trades_lost}L`, valueClass: stats.trades_won > stats.trades_lost ? "text-emerald-400" : "text-red-400" }))] }), _jsxs("div", { children: [_jsx("p", { className: "text-xs text-zinc-500 uppercase mb-1", children: "Decisiones" }), _jsx(StatRow, { label: "Total", value: stats.decisions_total }), _jsx(StatRow, { label: "BUY / SELL / HOLD", value: `${stats.decisions_buy} / ${stats.decisions_sell} / ${stats.decisions_hold}` }), _jsx(StatRow, { label: "Ejecutadas", value: stats.decisions_executed, valueClass: "text-emerald-400" }), _jsx(StatRow, { label: "Bloqueadas", value: stats.decisions_blocked, valueClass: stats.decisions_blocked > 0 ? "text-amber-400" : "text-zinc-400" })] })] })) })] })] }));
}
