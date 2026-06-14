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
import type { TradingContext } from "./types";

function NavBar() {
  const [tradingCtx, setTradingCtx] = useState<TradingContext | null>(null);

  useEffect(() => {
    const load = () =>
      fetch("/api/health")
        .then(r => r.json())
        .then(d => setTradingCtx(d?.trading ?? null))
        .catch(() => setTradingCtx(null));
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const base = "px-3 py-2 text-sm text-zinc-400 hover:text-white";
  const active = "!text-white border-b-2 border-emerald-400";
  return (
    <nav className="flex items-center border-b border-zinc-800 bg-zinc-900 h-12 gap-2">
      <span className="px-4 font-semibold text-emerald-400 shrink-0">⚡ Crypto AI Trading</span>
      <div className="hidden sm:flex px-2 border-l border-zinc-800">
        <TradingContextBadges ctx={tradingCtx} />
      </div>
      <div className="flex flex-1 min-w-0">
        {[
          ["/", "Dashboard"], ["/trades", "Trades"],
          ["/decisions", "Decisiones"], ["/confluence", "Confluencias"], ["/playbook", "Playbook"],
          ["/config", "Config"], ["/health", "Health"], ["/help", "Ayuda"],
        ].map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `${base} ${isActive ? active : ""}`}>
            {label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <NavBar />
      <main className="p-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/decisions" element={<Decisions />} />
          <Route path="/confluence" element={<ConfluencePage />} />
          <Route path="/playbook" element={<PlaybookPage />} />
          <Route path="/config" element={<Config />} />
          <Route path="/health" element={<Health />} />
          <Route path="/help" element={<Help />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
