import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
import type { Position, Decision, DailyStats } from "../types";

interface EngineHealth {
  ok: boolean;
  detail: string;
  last_decision_age_min: number | null;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h3 className="text-sm font-semibold text-zinc-400 mb-3 uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  );
}

function StatRow({ label, value, valueClass = "text-zinc-200" }: {
  label: string; value: React.ReactNode; valueClass?: string;
}) {
  return (
    <div className="flex justify-between items-baseline py-1 border-b border-zinc-800 last:border-0">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className={`text-sm font-mono font-semibold ${valueClass}`}>{value}</span>
    </div>
  );
}

function EngineStatusPill({ health }: { health: EngineHealth | null }) {
  if (!health) return null;
  const age = health.last_decision_age_min;
  const paused = !health.ok || (age !== null && age > 30);
  const slow = !paused && age !== null && age > 15;
  const dot = paused
    ? "bg-red-400"
    : slow
    ? "bg-amber-400 animate-pulse"
    : "bg-emerald-400 animate-pulse";
  const label = paused ? "Engine pausado" : slow ? "Engine lento" : "Engine activo";
  return (
    <div className="flex items-center gap-2 border-l border-zinc-700 pl-4">
      <span className={`size-2.5 rounded-full ${dot}`} />
      <span className="text-sm text-zinc-300">{label}</span>
      <span className="text-xs text-zinc-500">({health.detail})</span>
    </div>
  );
}

export function Dashboard() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [lastDecision, setLastDecision] = useState<Decision | null>(null);
  const [killSwitchOn, setKillSwitchOn] = useState(false);
  const [stats, setStats] = useState<DailyStats | null>(null);
  const [ticker, setTicker] = useState<{ symbol: string; price: number | null } | null>(null);
  const [engineHealth, setEngineHealth] = useState<EngineHealth | null>(null);
  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const { last, connected } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);

  const loadStats = () => api.dailyStats().then(setStats).catch(() => {});
  const loadHealth = () =>
    fetch("/api/health").then(r => r.json())
      .then(d => setEngineHealth(d?.engine ?? null))
      .catch(() => {});

  useEffect(() => {
    api.positions().then(setPositions).catch(() => {});
    api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null)).catch(() => {});
    api.config().then(cfg => {
      const ks = cfg.find(c => c.key === "kill_switch");
      setKillSwitchOn(ks?.value === "true");
    }).catch(() => {});
    loadStats();
    loadHealth();
    const id = setInterval(loadHealth, 15_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!last) return;
    if (last.event === "positions") setPositions(last.data as Position[]);
    if (last.event === "ticker") setTicker(last.data as { symbol: string; price: number | null });
    if (last.event === "decision") {
      api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null)).catch(() => {});
      loadStats();
    }
  }, [last]);

  const onKillSwitch = async () => {
    if (!confirm("¿Activar kill switch? Cierra posiciones y desactiva el bot.")) return;
    await api.killSwitch(true);
    setKillSwitchOn(true);
  };

  const out = lastDecision?.output as { action?: string; confidence?: number; reasoning?: string; regime?: string } | undefined;
  const actionColor = out?.action === "BUY" ? "text-emerald-400" : out?.action === "SELL" ? "text-red-400" : "text-zinc-400";

  const pnlTotal = (stats?.pnl_realized ?? 0) + (stats?.pnl_unrealized ?? 0);
  const pnlColor = pnlTotal > 0 ? "text-emerald-400" : pnlTotal < 0 ? "text-red-400" : "text-zinc-400";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl bg-zinc-900 p-4">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3">
            <span className={`size-3 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-zinc-600"}`} />
            <span className="text-sm">{connected ? "WS conectado" : "Desconectado — reconectando..."}</span>
          </div>
          <EngineStatusPill health={engineHealth} />
          {ticker && (
            <div className="flex items-center gap-2 border-l border-zinc-700 pl-4">
              <span className="text-sm font-semibold text-zinc-400">{ticker.symbol}</span>
              <span className="text-lg font-bold text-white">
                {ticker.price != null
                  ? `$${ticker.price.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : "—"}
              </span>
            </div>
          )}
        </div>
        <button onClick={onKillSwitch}
          className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${killSwitchOn ? "bg-red-800 cursor-default" : "bg-red-600 hover:bg-red-500"}`}>
          🚨 {killSwitchOn ? "Kill Switch ACTIVO" : "Activar Kill Switch"}
        </button>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Posiciones abiertas">
          {positions.length === 0
            ? <p className="text-zinc-500 text-sm">Ninguna posición abierta.</p>
            : positions.map(p => (
                <div key={p.id} className="rounded-lg bg-zinc-800 p-3 mb-2">
                  <div className="flex justify-between text-sm">
                    <span>{p.symbol}</span>
                    <span className={p.unrealized_pnl && p.unrealized_pnl > 0 ? "text-emerald-400" : "text-red-400"}>
                      {p.unrealized_pnl != null ? `$${p.unrealized_pnl.toFixed(2)}` : "—"}
                      {p.unrealized_pct != null ? ` (${p.unrealized_pct.toFixed(2)}%)` : ""}
                    </span>
                  </div>
                  <div className="text-xs text-zinc-500 mt-1">
                    qty {p.quantity_btc.toFixed(6)} | entry ${p.entry_price.toFixed(2)}
                    {p.current_price ? ` | actual $${p.current_price.toFixed(2)}` : ""}
                  </div>
                </div>
              ))
          }
        </Card>

        <Card title="Última decisión">
          {!lastDecision
            ? <p className="text-zinc-500 text-sm">Sin decisiones aún.</p>
            : (
              <div>
                <div className={`text-4xl font-bold mb-2 ${actionColor}`}>{out?.action ?? "—"}</div>
                <div className="text-sm text-zinc-400 mb-2">
                  Confianza: <span className="text-white">{((out?.confidence ?? 0) * 100).toFixed(0)}%</span>
                  {out?.regime && <span className="ml-3 text-zinc-500">{out.regime}</span>}
                </div>
                <p className="text-sm text-zinc-300 leading-relaxed">{out?.reasoning ?? ""}</p>
                <div className="mt-3 text-xs text-zinc-600">
                  {new Date(lastDecision.ts).toLocaleString("es-AR")}
                </div>
              </div>
            )
          }
        </Card>

        <Card title="Estado del día">
          {!stats ? (
            <p className="text-zinc-500 text-sm">Cargando...</p>
          ) : (
            <div className="space-y-3">
              <div>
                <div className={`text-2xl font-bold font-mono ${pnlColor}`}>
                  {pnlTotal >= 0 ? "+" : ""}${pnlTotal.toFixed(2)}
                </div>
                <div className="text-xs text-zinc-500 mt-0.5">P&L total del día</div>
              </div>

              <div>
                <StatRow
                  label="Realizado"
                  value={`${stats.pnl_realized >= 0 ? "+" : ""}$${stats.pnl_realized.toFixed(2)}`}
                  valueClass={stats.pnl_realized > 0 ? "text-emerald-400" : stats.pnl_realized < 0 ? "text-red-400" : "text-zinc-400"}
                />
                <StatRow
                  label="No realizado"
                  value={`${stats.pnl_unrealized >= 0 ? "+" : ""}$${stats.pnl_unrealized.toFixed(2)}`}
                  valueClass={stats.pnl_unrealized > 0 ? "text-emerald-400" : stats.pnl_unrealized < 0 ? "text-red-400" : "text-zinc-400"}
                />
                <StatRow label="Fees pagadas" value={`$${stats.fees_total.toFixed(4)}`} valueClass="text-zinc-500" />
              </div>

              <div>
                <p className="text-xs text-zinc-500 uppercase mb-1">Trades</p>
                <StatRow label="Abiertos / Cerrados" value={`${stats.trades_open} / ${stats.trades_closed}`} />
                {stats.trades_closed > 0 && (
                  <StatRow
                    label="Win / Loss"
                    value={`${stats.trades_won}W · ${stats.trades_lost}L`}
                    valueClass={stats.trades_won > stats.trades_lost ? "text-emerald-400" : "text-red-400"}
                  />
                )}
              </div>

              <div>
                <p className="text-xs text-zinc-500 uppercase mb-1">Decisiones</p>
                <StatRow label="Total" value={stats.decisions_total} />
                <StatRow label="BUY / SELL / HOLD" value={`${stats.decisions_buy} / ${stats.decisions_sell} / ${stats.decisions_hold}`} />
                <StatRow label="Ejecutadas" value={stats.decisions_executed} valueClass="text-emerald-400" />
                <StatRow
                  label="Bloqueadas"
                  value={stats.decisions_blocked}
                  valueClass={stats.decisions_blocked > 0 ? "text-amber-400" : "text-zinc-400"}
                />
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
