import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useWebSocket } from "../hooks/useWebSocket";
import { PriceChart } from "../components/chart/PriceChart";
import type { Position, Decision, DailyStats, Balance, ScheduledProcess } from "../types";
import ConfidenceBreakdown from "../components/ConfidenceBreakdown";
import ReasoningBlock from "../components/ReasoningBlock";
import { asDecisorOutput } from "../types/decisorOutput";
import { PnlRow } from "../components/PnlRow";
import {
  actionBadgeClass,
  computePnlPctDirectional,
  computePnlUsdtDirectional,
  positionDirection,
  sideBadgeClass,
} from "../lib/pnl";
import type { TradingContext } from "../types";
import { RuntimeMismatchBanner } from "../components/RuntimeMismatchBanner";
import { TradingContextBadges } from "../components/TradingContextBadges";
import { FuturesBalanceCard } from "../components/FuturesBalanceCard";

interface EngineHealth {
  ok: boolean;
  detail: string;
  last_decision_age_min: number | null;
  decisor_interval_min: number | null;
  next_execution_in_min: number | null;
  scheduled_processes?: ScheduledProcess[];
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

function useCountdown(engineHealth: EngineHealth | null): number | null {
  const [secsLeft, setSecsLeft] = useState<number | null>(null);

  useEffect(() => {
    if (engineHealth?.next_execution_in_min == null) {
      setSecsLeft(null);
      return;
    }
    setSecsLeft(engineHealth.next_execution_in_min * 60);
    const timer = setInterval(() => {
      setSecsLeft(prev => {
        if (prev == null || prev <= 0) return 0;
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [engineHealth?.next_execution_in_min, engineHealth?.last_decision_age_min]);

  return secsLeft;
}

function formatCountdown(secs: number): string {
  if (secs <= 0) return "ejecutando...";
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return `${h}h ${m}m`;
}

function countdownColor(secs: number | null): string {
  if (secs == null) return "text-zinc-500";
  if (secs === 0) return "text-emerald-400 animate-pulse";
  if (secs <= 60) return "text-amber-400";
  return "text-zinc-300";
}

function useProcessCountdowns(
  processes: ScheduledProcess[] | undefined,
): Record<string, number | null> {
  const [countdowns, setCountdowns] = useState<Record<string, number | null>>({});

  useEffect(() => {
    if (!processes?.length) {
      setCountdowns({});
      return;
    }
    const tick = () => {
      const now = Date.now();
      const next: Record<string, number | null> = {};
      for (const p of processes) {
        if (!p.next_run_at) {
          next[p.id] = null;
        } else {
          const diff = Math.max(0, Math.floor((new Date(p.next_run_at).getTime() - now) / 1000));
          next[p.id] = diff;
        }
      }
      setCountdowns(next);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [processes]);

  return countdowns;
}

function ScheduledProcessesTable({
  processes,
  countdowns,
}: {
  processes: ScheduledProcess[];
  countdowns: Record<string, number | null>;
}) {
  if (!processes.length) return null;

  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h3 className="text-sm font-semibold text-zinc-400 mb-3 uppercase tracking-wide">
        Procesos programados
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-700 text-left text-xs text-zinc-500 uppercase">
              <th className="pb-2 pr-4">Proceso</th>
              <th className="pb-2 pr-4">Próxima ejecución</th>
              <th className="pb-2 pr-4">Intervalo</th>
              <th className="pb-2">Última ejecución</th>
            </tr>
          </thead>
          <tbody>
            {processes.map(p => {
              const secs = countdowns[p.id] ?? null;
              return (
                <tr key={p.id} className="border-b border-zinc-800 last:border-0">
                  <td className="py-2.5 pr-4 font-medium text-zinc-200">{p.name}</td>
                  <td className={`py-2.5 pr-4 font-mono ${countdownColor(secs)}`}>
                    {secs != null ? formatCountdown(secs) : "—"}
                  </td>
                  <td className="py-2.5 pr-4 text-zinc-400">{p.interval_desc}</td>
                  <td className="py-2.5 text-zinc-500">
                    {p.last_run_at
                      ? new Date(p.last_run_at).toLocaleString("es-AR")
                      : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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
  const [balance, setBalance] = useState<Balance | null>(null);
  const [tradingCtx, setTradingCtx] = useState<TradingContext | null>(null);
  const [scheduledProcesses, setScheduledProcesses] = useState<ScheduledProcess[]>([]);
  const countdownSecs = useCountdown(engineHealth);
  const processCountdowns = useProcessCountdowns(scheduledProcesses);
  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const { last, connected } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);

  const loadStats = () => api.dailyStats().then(setStats).catch(() => {});
  const loadHealth = () =>
    fetch("/api/health").then(r => r.json())
      .then(d => {
        setEngineHealth(d?.engine ?? null);
        setTradingCtx(d?.trading ?? null);
        setScheduledProcesses(d?.scheduled_processes ?? []);
      })
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
    api.balance().then(setBalance).catch(() => {});
    const id = setInterval(loadHealth, 15_000);
    const id2 = setInterval(() => api.balance().then(setBalance).catch(() => {}), 30_000);
    return () => { clearInterval(id); clearInterval(id2); };
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

  const out = lastDecision ? asDecisorOutput(lastDecision.output) : undefined;
  const actionColor = actionBadgeClass(out?.action ?? "HOLD");

  const pnlTotal = (stats?.pnl_realized ?? 0) + (stats?.pnl_unrealized ?? 0);
  const pnlColor = pnlTotal > 0 ? "text-emerald-400" : pnlTotal < 0 ? "text-red-400" : "text-zinc-400";
  const isFuturesMode = tradingCtx?.trading_product === "futures";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl bg-zinc-900 p-4 flex-wrap gap-3">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="sm:hidden">
            <TradingContextBadges ctx={tradingCtx} />
          </div>
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

      {tradingCtx?.runtime_mismatch && (
        <RuntimeMismatchBanner ctx={tradingCtx} />
      )}

      <ScheduledProcessesTable
        processes={scheduledProcesses}
        countdowns={processCountdowns}
      />

      <PriceChart
        tradingProduct={tradingCtx?.trading_product}
        chartLabel={tradingCtx?.chart_label}
      />

      <div className="grid gap-4 lg:grid-cols-4">
        {isFuturesMode ? (
          <FuturesBalanceCard futures={balance?.futures} />
        ) : (
          <Card title="Balance Binance">
            {!balance ? (
              <p className="text-zinc-500 text-sm">Cargando...</p>
            ) : (
              <div className="space-y-1">
                <StatRow
                  label="USDT libre"
                  value={`$${balance.usdt.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                  valueClass="text-emerald-400"
                />
                {balance.usdt_locked > 0 && (
                  <StatRow
                    label="USDT bloqueado (órdenes)"
                    value={`$${balance.usdt_locked.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                    valueClass="text-yellow-500"
                  />
                )}
                <StatRow
                  label="BTC libre en exchange"
                  value={`${balance.btc_exchange.toFixed(6)} BTC`}
                  valueClass="text-amber-400"
                />
                {balance.btc_locked > 0 && (
                  <StatRow
                    label="BTC bloqueado (órdenes)"
                    value={`${balance.btc_locked.toFixed(6)} BTC`}
                    valueClass="text-yellow-500"
                  />
                )}
                <StatRow
                  label="BTC en posiciones"
                  value={`${balance.btc_in_positions.toFixed(6)} BTC`}
                  valueClass="text-zinc-300"
                />
                {ticker?.price != null && (
                  <StatRow
                    label="Total en USD (real)"
                    value={`$${(balance.usdt_total + balance.btc_exchange_total * ticker.price).toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                    valueClass="text-white font-semibold"
                  />
                )}
                <div className="pt-2 text-xs text-zinc-600">
                  {balance.balance_ts
                    ? `Actualizado ${new Date(balance.balance_ts).toLocaleTimeString("es-AR")}`
                    : "Sin datos de Binance"}
                  {balance.balance_source === "binance" ? "" : " (fallback DB)"}
                </div>
              </div>
            )}
          </Card>
        )}

        <Card title="Posiciones abiertas">
          {positions.length === 0
            ? <p className="text-zinc-500 text-sm">Ninguna posición abierta.</p>
            : positions.map(p => {
                const dir = positionDirection(p);
                const liveCurrentPrice = ticker?.price ?? p.current_price;
                const currentPnlUsdt = liveCurrentPrice != null
                  ? computePnlUsdtDirectional(p.entry_price, p.quantity_btc, liveCurrentPrice, dir)
                  : p.unrealized_pnl;
                const currentPnlPct = liveCurrentPrice != null
                  ? computePnlPctDirectional(p.entry_price, liveCurrentPrice, dir)
                  : p.unrealized_pct;

                return (
                  <div key={p.id} className="rounded-lg bg-zinc-800 p-3 mb-2 last:mb-0">
                    <div className="flex flex-wrap justify-between items-center gap-2 text-sm mb-2">
                      <div className="flex items-center gap-2">
                        <span>{p.symbol}</span>
                        <span className={`text-xs font-bold px-2 py-0.5 rounded ${sideBadgeClass(dir)}`}>
                          {dir}
                        </span>
                        {p.leverage != null && (
                          <span className="text-xs text-zinc-500">{p.leverage}x</span>
                        )}
                      </div>
                      <span className="text-xs text-zinc-500">
                        qty {p.quantity_btc.toFixed(6)} · entry ${p.entry_price.toFixed(2)}
                      </span>
                    </div>
                    <div className="text-xs text-zinc-500 mb-2">
                      {liveCurrentPrice != null
                        ? `Precio actual $${liveCurrentPrice.toFixed(2)}`
                        : "Precio actual —"}
                      {p.stop_loss != null && (
                        <>
                          {` · SL $${p.stop_loss.toFixed(2)}`}
                          {!p.order_id_sl && (
                            <span className="text-amber-400/90" title="SL no está en Binance; solo guardian software">
                              {" "}(guardian)
                            </span>
                          )}
                        </>
                      )}
                      {p.take_profit != null && (
                        <>
                          {` · TP $${p.take_profit.toFixed(2)}`}
                          {!p.order_id_tp && (
                            <span className="text-amber-400/90" title="TP no está en Binance; solo guardian software">
                              {" "}(guardian)
                            </span>
                          )}
                        </>
                      )}
                      {p.liquidation_price != null && (
                        <span className="text-orange-400/90"> · Liq ${p.liquidation_price.toFixed(2)}</span>
                      )}
                    </div>
                    <div className="space-y-1.5 pt-2 border-t border-zinc-700">
                      <PnlRow
                        label="P&L al precio actual"
                        pnlUsdt={currentPnlUsdt}
                        pnlPct={currentPnlPct}
                      />
                      <PnlRow
                        label="P&L si cierra en SL"
                        pnlUsdt={p.sl_pnl_usdt}
                        pnlPct={p.sl_pnl_pct}
                        labelClass="text-red-400/70"
                      />
                      <PnlRow
                        label="P&L si cierra en TP"
                        pnlUsdt={p.tp_pnl_usdt}
                        pnlPct={p.tp_pnl_pct}
                        labelClass="text-emerald-400/70"
                      />
                    </div>
                  </div>
                );
              })
          }
        </Card>

        <Card title="Última decisión">
          {!lastDecision
            ? <p className="text-zinc-500 text-sm">Sin decisiones aún.</p>
            : (
              <div>
                <div className={`text-4xl font-bold mb-2 ${actionColor}`}>{out?.action ?? "—"}</div>
                <div className="text-sm text-zinc-400 mb-2">
                  <ConfidenceBreakdown
                    compact
                    confidence={out?.confidence}
                    confidenceBase={out?.confidence_base}
                    confidenceAdjustment={out?.confidence_adjustment}
                    meta={out?.confidence_meta}
                  />
                  {out?.regime && <span className="ml-1 text-zinc-500">{out.regime}</span>}
                </div>
                {out?.reasoning && <ReasoningBlock reasoning={out.reasoning} compact />}
                <div className="mt-3 text-xs text-zinc-600">
                  {new Date(lastDecision.ts).toLocaleString("es-AR")}
                </div>
                {countdownSecs != null && (
                  <div className="mt-2 flex items-center gap-2 rounded-lg bg-zinc-800 px-3 py-2">
                    <span className="text-xs text-zinc-500">Próxima ejecución</span>
                    <span className={`ml-auto font-mono text-sm font-semibold ${
                      countdownSecs === 0
                        ? "text-emerald-400 animate-pulse"
                        : countdownSecs <= 60
                        ? "text-amber-400"
                        : "text-zinc-300"
                    }`}>
                      {countdownSecs === 0
                        ? "ejecutando..."
                        : countdownSecs < 60
                        ? `${countdownSecs}s`
                        : `${Math.floor(countdownSecs / 60)}m ${countdownSecs % 60}s`}
                    </span>
                  </div>
                )}
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
                <StatRow label="Abiertos ahora / Cerrados hoy" value={`${stats.trades_open} / ${stats.trades_closed}`} />
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

