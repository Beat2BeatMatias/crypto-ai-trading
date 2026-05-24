import { useEffect, useMemo, useState } from "react";
import type React from "react";
import { api } from "../api/client";
import type { Trade } from "../types";
import { PnlRow } from "../components/PnlRow";
import { computePnlPct, computePnlUsdt } from "../lib/pnl";

// ── Close reason labels ───────────────────────────────────────────────────────

const CLOSE_REASON_LABELS: Record<string, { label: string; className: string }> = {
  sl_triggered:          { label: "Stop Loss",             className: "text-red-400" },
  tp_triggered:          { label: "Take Profit",           className: "text-green-400" },
  bracket_fill:          { label: "Bracket ejecutado",     className: "text-blue-400" },
  manual_close:          { label: "Cierre manual",         className: "text-yellow-400" },
  force_closed_notional: { label: "⚠ Cierre forzado — posición por debajo del mínimo NOTIONAL de Binance", className: "text-orange-400 font-semibold" },
};

function closeReasonBadge(reason: string | null) {
  if (!reason) return null;
  const def = CLOSE_REASON_LABELS[reason];
  return (
    <span className={`text-xs italic ml-auto ${def ? def.className : "text-zinc-600"}`}>
      {def ? def.label : reason}
    </span>
  );
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimals = 2, prefix = "$"): string {
  if (n == null) return "—";
  return `${prefix}${n.toLocaleString("es-AR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}

function pct(n: number | null | undefined): string {
  if (n == null) return "";
  return ` (${n >= 0 ? "+" : ""}${n.toFixed(2)}%)`;
}

function slDistance(entry: number, sl: number | null): string {
  if (!sl) return "—";
  return `${(((entry - sl) / entry) * 100).toFixed(2)}%`;
}

function tpDistance(entry: number, tp: number | null): string {
  if (!tp) return "—";
  return `+${(((tp - entry) / entry) * 100).toFixed(2)}%`;
}

function rrRatio(entry: number, sl: number | null, tp: number | null): string {
  if (!sl || !tp) return "—";
  const risk = entry - sl;
  const reward = tp - entry;
  if (risk <= 0) return "—";
  return `${(reward / risk).toFixed(2)}:1`;
}

// ── CSV Export ────────────────────────────────────────────────────────────────

function exportCSV(trades: Trade[]) {
  const cols = [
    "id", "decision_id", "ts_open", "ts_close", "side", "status",
    "quantity_btc", "entry_price", "exit_price",
    "stop_loss", "take_profit",
    "pnl_usdt", "pnl_pct", "fees_usdt", "close_reason",
    "order_id_open", "order_id_close",
  ] as const;

  const header = cols.join(",");
  const rows = trades.map(t =>
    cols.map(c => {
      const v = t[c];
      if (v == null) return "";
      if (typeof v === "string" && v.includes(",")) return `"${v}"`;
      return String(v);
    }).join(",")
  );

  const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Close hook ────────────────────────────────────────────────────────────────

function useCloseTrade(setTrades: React.Dispatch<React.SetStateAction<Trade[]>>) {
  const [closing, setClosing] = useState<string | null>(null);

  async function requestClose(trade: Trade) {
    if (!window.confirm(
      `¿Cerrar manualmente este trade?\n\nEntrada: $${trade.entry_price.toFixed(2)}\nCantidad: ${trade.quantity_btc.toFixed(6)} BTC\n\nEl engine ejecutará la orden de venta al precio de mercado en el próximo ciclo (~30s).`
    )) return;

    setClosing(trade.id);
    try {
      const updated = await api.closeTrade(trade.id);
      setTrades(prev => prev.map(t => t.id === updated.id ? updated : t));
    } catch {
      alert("Error al solicitar el cierre del trade.");
    } finally {
      setClosing(null);
    }
  }

  return { closing, requestClose };
}

// ── Sort types ────────────────────────────────────────────────────────────────

type SortKey = "ts_open" | "pnl_usdt" | "entry_price" | "quantity_btc";
type SortDir = "asc" | "desc";

// ── Summary Footer ────────────────────────────────────────────────────────────

function SummaryFooter({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => t.status === "closed");
  if (closed.length === 0) return null;

  const totalPnl = closed.reduce((s, t) => s + (t.pnl_usdt ?? 0), 0);
  const totalFees = trades.reduce((s, t) => s + (t.fees_usdt ?? 0), 0);
  const wins = closed.filter(t => (t.pnl_usdt ?? 0) > 0).length;
  const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0;
  const pnlColor = totalPnl >= 0 ? "text-emerald-400" : "text-red-400";

  return (
    <div className="rounded-xl bg-zinc-900 border border-zinc-700 p-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
      <div>
        <div className="text-xs text-zinc-500 mb-1">P&L realizado total</div>
        <div className={`font-mono font-semibold text-sm ${pnlColor}`}>
          {totalPnl >= 0 ? "+" : ""}{fmt(totalPnl)}
        </div>
      </div>
      <div>
        <div className="text-xs text-zinc-500 mb-1">Win rate</div>
        <div className="font-mono text-sm text-zinc-300">
          {winRate.toFixed(1)}% <span className="text-zinc-500">({wins}/{closed.length})</span>
        </div>
      </div>
      <div>
        <div className="text-xs text-zinc-500 mb-1">Fees totales</div>
        <div className="font-mono text-sm text-zinc-400">{fmt(totalFees)}</div>
      </div>
      <div>
        <div className="text-xs text-zinc-500 mb-1">Trades mostrados</div>
        <div className="font-mono text-sm text-zinc-300">
          {trades.length} <span className="text-zinc-500">({closed.length} cerrados)</span>
        </div>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function Trades() {
  const [allTrades, setAllTrades] = useState<Trade[]>([]);
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "closed">("all");
  const [resultFilter, setResultFilter] = useState<"all" | "win" | "loss">("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("ts_open");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const { closing, requestClose } = useCloseTrade(setAllTrades);

  useEffect(() => {
    const status = statusFilter === "all" ? undefined : statusFilter;
    api.trades(status).then(setAllTrades).catch(() => {});
    const id = setInterval(() => {
      api.trades(status).then(setAllTrades).catch(() => {});
    }, 30_000);
    return () => clearInterval(id);
  }, [statusFilter]);

  useEffect(() => {
    const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${wsProtocol}://${window.location.host}/ws`);
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.event === "ticker" && msg.data?.price != null) {
          setLivePrice(msg.data.price);
        }
      } catch {
        // ignore malformed WS payloads
      }
    };
    return () => ws.close();
  }, []);

  const trades = useMemo(() => {
    let list = [...allTrades];

    if (resultFilter === "win")
      list = list.filter(t => (t.pnl_usdt ?? 0) > 0);
    else if (resultFilter === "loss")
      list = list.filter(t => (t.pnl_usdt ?? 0) < 0);

    if (dateFrom)
      list = list.filter(t => new Date(t.ts_open) >= new Date(dateFrom));
    if (dateTo)
      list = list.filter(t => new Date(t.ts_open) <= new Date(dateTo + "T23:59:59Z"));

    list.sort((a, b) => {
      let av: number, bv: number;
      if (sortKey === "ts_open") {
        av = new Date(a.ts_open).getTime();
        bv = new Date(b.ts_open).getTime();
      } else {
        av = (a[sortKey] as number | null) ?? -Infinity;
        bv = (b[sortKey] as number | null) ?? -Infinity;
      }
      return sortDir === "asc" ? av - bv : bv - av;
    });

    return list;
  }, [allTrades, resultFilter, dateFrom, dateTo, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("desc"); }
  }

  const sortIcon = (key: SortKey) =>
    sortKey !== key ? <span className="text-zinc-700">⇅</span>
    : sortDir === "desc" ? <span className="text-blue-400">↓</span>
    : <span className="text-blue-400">↑</span>;

  const btnCls = (active: boolean) =>
    `text-xs px-3 py-1.5 rounded transition-colors ${
      active
        ? "bg-blue-900 text-blue-200 font-semibold"
        : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
    }`;

  return (
    <div className="space-y-3">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Historial de trades</h2>
        <button
          onClick={() => exportCSV(trades)}
          disabled={trades.length === 0}
          className="text-xs px-3 py-1.5 rounded bg-zinc-800 text-zinc-400 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          ↓ CSV
        </button>
      </div>

      {/* ── Filtros ── */}
      <div className="flex flex-wrap gap-3 items-end">
        {/* Status */}
        <div className="flex gap-1">
          {(["all", "open", "closed"] as const).map(s => (
            <button key={s} onClick={() => setStatusFilter(s)} className={btnCls(statusFilter === s)}>
              {s === "all" ? "Todos" : s === "open" ? "Abiertos" : "Cerrados"}
            </button>
          ))}
        </div>

        {/* Resultado */}
        <div className="flex gap-1">
          <button onClick={() => setResultFilter("all")} className={btnCls(resultFilter === "all")}>Win+Loss</button>
          <button onClick={() => setResultFilter("win")} className={btnCls(resultFilter === "win")}>
            <span className="text-emerald-400">▲</span> Win
          </button>
          <button onClick={() => setResultFilter("loss")} className={btnCls(resultFilter === "loss")}>
            <span className="text-red-400">▼</span> Loss
          </button>
        </div>

        {/* Date range */}
        <div className="flex gap-2 items-center ml-auto">
          <input
            type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 focus:outline-none focus:border-zinc-500"
          />
          <span className="text-zinc-600 text-xs">—</span>
          <input
            type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 focus:outline-none focus:border-zinc-500"
          />
          {(dateFrom || dateTo) && (
            <button onClick={() => { setDateFrom(""); setDateTo(""); }}
              className="text-xs text-zinc-500 hover:text-zinc-300">✕</button>
          )}
        </div>
      </div>

      {/* ── Sort bar ── */}
      <div className="flex gap-3 text-xs text-zinc-500">
        <span>Ordenar:</span>
        {([
          ["ts_open", "Fecha"],
          ["pnl_usdt", "P&L"],
          ["entry_price", "Precio entrada"],
          ["quantity_btc", "Cantidad BTC"],
        ] as [SortKey, string][]).map(([k, label]) => (
          <button key={k} onClick={() => toggleSort(k)}
            className={`flex items-center gap-1 hover:text-zinc-300 transition-colors ${sortKey === k ? "text-blue-300" : ""}`}>
            {label} {sortIcon(k)}
          </button>
        ))}
      </div>

      {/* ── Resumen ── */}
      <SummaryFooter trades={trades} />

      {/* ── Vacío ── */}
      {trades.length === 0 && (
        <div className="rounded-xl bg-zinc-900 p-8 text-center text-zinc-500 text-sm">
          Sin trades que coincidan con los filtros.
        </div>
      )}

      {/* ── Listado ── */}
      {trades.map(t => {
        const valueUsdt = t.quantity_btc * t.entry_price;
        const isOpen = t.status === "open";
        const currentPrice = isOpen ? (livePrice ?? t.current_price ?? null) : null;
        const unrealizedPnlUsdt = isOpen
          ? (livePrice != null
            ? computePnlUsdt(t.entry_price, t.quantity_btc, livePrice, t.side)
            : t.unrealized_pnl_usdt)
          : t.pnl_usdt;
        const unrealizedPnlPct = isOpen
          ? (livePrice != null
            ? computePnlPct(t.entry_price, livePrice, t.side)
            : t.unrealized_pnl_pct)
          : t.pnl_pct;
        const pnlPositive = (unrealizedPnlUsdt ?? 0) >= 0;
        const pnlColor = unrealizedPnlUsdt == null ? "text-zinc-400" : pnlPositive ? "text-emerald-400" : "text-red-400";

        return (
          <div key={t.id} className="rounded-xl bg-zinc-900 p-4">

            {/* ── Header ── */}
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                t.side === "BUY" ? "bg-emerald-900/60 text-emerald-300" : "bg-red-900/60 text-red-300"
              }`}>{t.side}</span>

              <span className={`text-xs px-2 py-0.5 rounded ${
                isOpen ? "bg-blue-900/50 text-blue-300" :
                t.pnl_usdt != null && t.pnl_usdt > 0 ? "bg-emerald-900/40 text-emerald-400" :
                t.pnl_usdt != null && t.pnl_usdt < 0 ? "bg-red-900/40 text-red-400" :
                "bg-zinc-800 text-zinc-400"
              }`}>
                {isOpen ? "ABIERTO" : t.pnl_usdt != null && t.pnl_usdt > 0 ? "WIN" : t.pnl_usdt != null && t.pnl_usdt < 0 ? "LOSS" : t.status.toUpperCase()}
              </span>

              <button
                type="button"
                title={`Trade: ${t.id}\nClick para copiar`}
                onClick={() => navigator.clipboard.writeText(t.id)}
                className="font-mono text-xs text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer select-none"
              >
                trade #{t.id.slice(0, 8)}
              </button>

              {t.decision_id ? (
                <button
                  type="button"
                  title={`Decisión: ${t.decision_id}\nClick para copiar`}
                  onClick={() => navigator.clipboard.writeText(t.decision_id!)}
                  className="font-mono text-xs text-violet-400/80 hover:text-violet-300 transition-colors cursor-pointer select-none"
                >
                  decisión #{t.decision_id.slice(0, 8)}
                </button>
              ) : (
                <span className="text-xs text-zinc-600 italic">sin decisión</span>
              )}

              <span className="text-xs text-zinc-500">
                {new Date(t.ts_open).toLocaleString("es-AR", { hour12: false })}
              </span>
              {t.ts_close && (
                <span className="text-xs text-zinc-600">
                  → {new Date(t.ts_close).toLocaleString("es-AR", { hour12: false })}
                </span>
              )}
              {closeReasonBadge(t.close_reason)}
            </div>

            {/* ── Fila 1: Ejecución ── */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
              <div>
                <div className="text-xs text-zinc-500 mb-1">Cantidad</div>
                <div className="font-mono text-sm">{t.quantity_btc.toFixed(6)} BTC</div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">Valor USDT</div>
                <div className="font-mono text-sm">{fmt(valueUsdt)}</div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">Precio entrada</div>
                <div className="font-mono text-sm">{fmt(t.entry_price)}</div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">Precio salida</div>
                <div className="font-mono text-sm">
                  {isOpen && currentPrice != null ? fmt(currentPrice) : fmt(t.exit_price)}
                </div>
                {isOpen && currentPrice != null && (
                  <div className="text-xs text-zinc-600 mt-0.5">precio actual</div>
                )}
              </div>
            </div>

            {/* ── Fila 2: Gestión de riesgo ── */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-3 border-t border-zinc-800">
              <div>
                <div className="text-xs text-zinc-500 mb-1">
                  Stop Loss
                  {t.stop_loss && !t.order_id_sl && (
                    <span
                      title="Orden SL no colocada en Binance — cubierto por SL Guardian (software)"
                      className="ml-1 text-amber-400 cursor-help"
                    >⚠ guardian</span>
                  )}
                </div>
                <div className="font-mono text-sm text-red-400">{fmt(t.stop_loss)}</div>
                <div className="text-xs text-zinc-600 mt-0.5">
                  {slDistance(t.entry_price, t.stop_loss)} abajo
                </div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">
                  Take Profit
                  {t.take_profit && !t.order_id_tp && (
                    <span
                      title="Orden TP no colocada en Binance — cubierto por TP Guardian (software)"
                      className="ml-1 text-amber-400 cursor-help"
                    >⚠ guardian</span>
                  )}
                </div>
                <div className="font-mono text-sm text-emerald-400">{fmt(t.take_profit)}</div>
                <div className="text-xs text-zinc-600 mt-0.5">
                  {tpDistance(t.entry_price, t.take_profit)} arriba
                </div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">R:R ratio</div>
                <div className="font-mono text-sm text-zinc-300">
                  {rrRatio(t.entry_price, t.stop_loss, t.take_profit)}
                </div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">Fees pagadas</div>
                <div className="font-mono text-sm text-zinc-400">{fmt(t.fees_usdt)}</div>
              </div>
            </div>

            {/* ── P&L ── */}
            {(unrealizedPnlUsdt != null || isOpen) && (
              <div className="mt-3 pt-3 border-t border-zinc-800 space-y-2">
                {isOpen ? (
                  <>
                    <PnlRow
                      label="P&L al precio actual"
                      pnlUsdt={unrealizedPnlUsdt}
                      pnlPct={unrealizedPnlPct}
                    />
                    <PnlRow
                      label="P&L si cierra en SL"
                      pnlUsdt={t.sl_pnl_usdt}
                      pnlPct={t.sl_pnl_pct}
                      labelClass="text-red-400/70"
                    />
                    <PnlRow
                      label="P&L si cierra en TP"
                      pnlUsdt={t.tp_pnl_usdt}
                      pnlPct={t.tp_pnl_pct}
                      labelClass="text-emerald-400/70"
                    />
                  </>
                ) : (
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-zinc-500">P&L realizado</span>
                    <span className={`font-mono font-semibold text-sm ${pnlColor}`}>
                      {unrealizedPnlUsdt != null
                        ? `${unrealizedPnlUsdt >= 0 ? "+" : ""}${fmt(unrealizedPnlUsdt)}${pct(unrealizedPnlPct)}`
                        : "—"}
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* ── Order IDs ── */}
            {(t.order_id_open || t.order_id_close || t.order_id_sl || t.order_id_tp) && (
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600">
                {t.order_id_open && (
                  <span>Apertura: <span className="font-mono">{t.order_id_open}</span></span>
                )}
                {t.order_id_close && (
                  <span>Cierre: <span className="font-mono">{t.order_id_close}</span></span>
                )}
                {t.order_id_sl ? (
                  <span>SL bracket: <span className="font-mono">{t.order_id_sl}</span></span>
                ) : t.stop_loss ? (
                  <span className="text-amber-500/70">SL: guardian software</span>
                ) : null}
                {t.order_id_tp ? (
                  <span>TP bracket: <span className="font-mono">{t.order_id_tp}</span></span>
                ) : t.take_profit ? (
                  <span className="text-amber-500/70">TP: guardian software</span>
                ) : null}
              </div>
            )}

            {/* ── Cierre manual ── */}
            {isOpen && (
              <div className="mt-3 pt-3 border-t border-zinc-800 flex items-center justify-between">
                <span className="text-xs text-zinc-500">
                  {t.close_requested
                    ? "Cierre solicitado — el engine ejecutará la venta en el próximo ciclo"
                    : "Cierre manual a precio de mercado"}
                </span>
                <button
                  disabled={t.close_requested || closing === t.id}
                  onClick={() => requestClose(t)}
                  className={`text-xs px-3 py-1 rounded font-semibold transition-colors ${
                    t.close_requested
                      ? "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                      : closing === t.id
                      ? "bg-orange-900/50 text-orange-400 cursor-wait"
                      : "bg-red-900/60 text-red-300 hover:bg-red-800/70 cursor-pointer"
                  }`}
                >
                  {t.close_requested ? "Cierre pendiente..." : closing === t.id ? "Enviando..." : "Cerrar ahora"}
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
