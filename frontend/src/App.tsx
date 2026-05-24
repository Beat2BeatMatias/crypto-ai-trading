import { Routes, Route, NavLink, BrowserRouter } from "react-router-dom";
import { Dashboard } from "./pages/Dashboard";
import { Trades } from "./pages/Trades";
import { Decisions } from "./pages/Decisions";
import { PlaybookPage } from "./pages/Playbook";
import { Config } from "./pages/Config";
import { Health } from "./pages/Health";
import { ConfluencePage } from "./pages/Confluence";

function NavBar() {
  const base = "px-3 py-2 text-sm text-zinc-400 hover:text-white";
  const active = "!text-white border-b-2 border-emerald-400";
  return (
    <nav className="flex items-center border-b border-zinc-800 bg-zinc-900 h-12">
      <span className="px-4 font-semibold text-emerald-400">⚡ Crypto AI Trading</span>
      <div className="flex">
        {[
          ["/", "Dashboard"], ["/trades", "Trades"],
          ["/decisions", "Decisiones"], ["/confluence", "Confluencias"], ["/playbook", "Playbook"],
          ["/config", "Config"], ["/health", "Health"],
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
        </Routes>
      </main>
    </BrowserRouter>
  );
}
