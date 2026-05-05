import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
import type { Position, Decision } from "../types";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h3 className="text-sm font-semibold text-zinc-400 mb-3 uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  );
}

export function Dashboard() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [lastDecision, setLastDecision] = useState<Decision | null>(null);
  const [killSwitchOn, setKillSwitchOn] = useState(false);
  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const { last, connected } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);

  useEffect(() => {
    api.positions().then(setPositions).catch(() => {});
    api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null)).catch(() => {});
    api.config().then(cfg => {
      const ks = cfg.find(c => c.key === "kill_switch");
      setKillSwitchOn(ks?.value === "true");
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!last) return;
    if (last.event === "positions") setPositions(last.data as Position[]);
    if (last.event === "decision") {
      api.decisions({ agent: "decisor" }).then(d => setLastDecision(d[0] ?? null)).catch(() => {});
    }
  }, [last]);

  const onKillSwitch = async () => {
    if (!confirm("¿Activar kill switch? Cierra posiciones y desactiva el bot.")) return;
    await api.killSwitch(true);
    setKillSwitchOn(true);
  };

  const out = lastDecision?.output as { action?: string; confidence?: number; reasoning?: string; regime?: string } | undefined;
  const actionColor = out?.action === "BUY" ? "text-emerald-400" : out?.action === "SELL" ? "text-red-400" : "text-zinc-400";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl bg-zinc-900 p-4">
        <div className="flex items-center gap-3">
          <span className={`size-3 rounded-full ${connected ? "bg-emerald-400 animate-pulse" : "bg-zinc-600"}`} />
          <span className="text-sm">{connected ? "Engine conectado" : "Desconectado — reconectando..."}</span>
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
          <p className="text-zinc-500 text-sm">P&L detallado disponible en /trades.</p>
          <div className="mt-3 text-xs text-zinc-600">Proximas métricas: v1.1</div>
        </Card>
      </div>
    </div>
  );
}
