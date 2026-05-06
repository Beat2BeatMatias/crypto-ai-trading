import { useEffect, useState } from "react";
import type React from "react";
import { api } from "../api/client";
import type { Trade } from "../types";

function fmt(n: number | null | undefined, decimals = 2, prefix = "$"): string {
  if (n == null) return "—";
  return `${prefix}${n.toFixed(decimals)}`;
}

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

export function Trades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const { closing, requestClose } = useCloseTrade(setTrades);
  useEffect(() => { api.trades().then(setTrades).catch(() => {}); }, []);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Historial de trades</h2>
        <span className="text-xs text-zinc-500">{trades.length} trade{trades.length !== 1 ? "s" : ""}</span>
      </div>

      {trades.length === 0 && (
        <div className="rounded-xl bg-zinc-900 p-8 text-center text-zinc-500 text-sm">
          Sin trades aún.
        </div>
      )}

      {trades.map(t => {
        const valueUsdt = t.quantity_btc * t.entry_price;
        const pnlPositive = (t.pnl_usdt ?? 0) >= 0;
        const pnlColor = t.pnl_usdt == null ? "text-zinc-400" : pnlPositive ? "text-emerald-400" : "text-red-400";
        const isOpen = t.status === "open";

        return (
          <div key={t.id} className="rounded-xl bg-zinc-900 p-4">

            {/* ── Header ── */}
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                t.side === "BUY" ? "bg-emerald-900/60 text-emerald-300" : "bg-red-900/60 text-red-300"
              }`}>{t.side}</span>

              <span className={`text-xs px-2 py-0.5 rounded ${
                isOpen ? "bg-blue-900/50 text-blue-300" :
                t.status === "closed" ? "bg-zinc-800 text-zinc-400" :
                "bg-zinc-800 text-zinc-600"
              }`}>{t.status.toUpperCase()}</span>

              <span className="text-xs text-zinc-500">
                {new Date(t.ts_open).toLocaleString("es-AR")}
              </span>
              {t.ts_close && (
                <span className="text-xs text-zinc-600">
                  → {new Date(t.ts_close).toLocaleString("es-AR")}
                </span>
              )}
              {t.close_reason && (
                <span className="text-xs text-zinc-600 italic ml-auto">{t.close_reason}</span>
              )}
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
                <div className="font-mono text-sm">{fmt(t.exit_price)}</div>
              </div>
            </div>

            {/* ── Fila 2: Gestión de riesgo ── */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-3 border-t border-zinc-800">
              <div>
                <div className="text-xs text-zinc-500 mb-1">Stop Loss</div>
                <div className="font-mono text-sm text-red-400">{fmt(t.stop_loss)}</div>
                <div className="text-xs text-zinc-600 mt-0.5">
                  {slDistance(t.entry_price, t.stop_loss)} abajo
                </div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 mb-1">Take Profit</div>
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

            {/* ── P&L (solo si hay resultado) ── */}
            {(t.pnl_usdt != null || isOpen) && (
              <div className="mt-3 pt-3 border-t border-zinc-800 flex items-center justify-between">
                <span className="text-xs text-zinc-500">
                  {isOpen ? "P&L no realizado estimado" : "P&L realizado"}
                </span>
                <span className={`font-mono font-semibold text-sm ${pnlColor}`}>
                  {t.pnl_usdt != null
                    ? `${t.pnl_usdt >= 0 ? "+" : ""}${fmt(t.pnl_usdt)}${pct(t.pnl_pct)}`
                    : isOpen ? "pendiente" : "—"}
                </span>
              </div>
            )}

            {/* ── Cierre manual (solo trades abiertos) ── */}
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
