import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { Routes, Route, NavLink, BrowserRouter } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { Trades } from "./pages/Trades";
import { Decisions } from "./pages/Decisions";
import { PlaybookPage } from "./pages/Playbook";
import { Config } from "./pages/Config";
import { Health } from "./pages/Health";
import { ConfluencePage } from "./pages/Confluence";
import { Help } from "./pages/Help";
import { TradingContextBadges } from "./components/TradingContextBadges";
function NavBar() {
    const [tradingCtx, setTradingCtx] = useState(null);
    useEffect(() => {
        const load = () => fetch("/api/health")
            .then(r => r.json())
            .then(d => setTradingCtx(d?.trading ?? null))
            .catch(() => setTradingCtx(null));
        load();
        const id = setInterval(load, 30_000);
        return () => clearInterval(id);
    }, []);
    const base = "px-3 py-2 text-sm text-zinc-400 hover:text-white";
    const active = "!text-white border-b-2 border-emerald-400";
    return (_jsxs("nav", { className: "flex items-center border-b border-zinc-800 bg-zinc-900 h-12 gap-2", children: [_jsx("span", { className: "px-4 font-semibold text-emerald-400 shrink-0", children: "\u26A1 Crypto AI Trading" }), _jsx("div", { className: "hidden sm:flex px-2 border-l border-zinc-800", children: _jsx(TradingContextBadges, { ctx: tradingCtx }) }), _jsx("div", { className: "flex flex-1 min-w-0", children: [
                    ["/", "Dashboard"], ["/trades", "Trades"],
                    ["/decisions", "Decisiones"], ["/confluence", "Confluencias"], ["/playbook", "Playbook"],
                    ["/config", "Config"], ["/health", "Health"], ["/help", "Ayuda"],
                ].map(([to, label]) => (_jsx(NavLink, { to: to, end: to === "/", className: ({ isActive }) => `${base} ${isActive ? active : ""}`, children: label }, to))) })] }));
}
export function App() {
    return (_jsxs(BrowserRouter, { children: [_jsx(NavBar, {}), _jsx("main", { className: "p-6", children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Dashboard, {}) }), _jsx(Route, { path: "/trades", element: _jsx(Trades, {}) }), _jsx(Route, { path: "/decisions", element: _jsx(Decisions, {}) }), _jsx(Route, { path: "/confluence", element: _jsx(ConfluencePage, {}) }), _jsx(Route, { path: "/playbook", element: _jsx(PlaybookPage, {}) }), _jsx(Route, { path: "/config", element: _jsx(Config, {}) }), _jsx(Route, { path: "/health", element: _jsx(Health, {}) }), _jsx(Route, { path: "/help", element: _jsx(Help, {}) })] }) })] }));
}
