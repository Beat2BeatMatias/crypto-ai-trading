import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Trade } from "../types";

export function Trades() {
  const [trades, setTrades] = useState<Trade[]>([]);
  useEffect(() => { api.trades().then(setTrades).catch(() => {}); }, []);

  return (
    <div className="rounded-xl bg-zinc-900 p-5">
      <h2 className="text-lg font-semibold mb-4">Historial de trades</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
              <th className="text-left py-2 pr-4">Apertura</th>
              <th className="text-left pr-4">Side</th>
              <th className="text-right pr-4">Qty BTC</th>
              <th className="text-right pr-4">Entry</th>
              <th className="text-right pr-4">Exit</th>
              <th className="text-right pr-4">P&L</th>
              <th className="text-right pr-4">Fees</th>
              <th className="text-left">Status</th>
            </tr>
          </thead>
          <tbody>
            {trades.length === 0 && (
              <tr><td colSpan={8} className="py-6 text-center text-zinc-500">Sin trades aún.</td></tr>
            )}
            {trades.map(t => (
              <tr key={t.id} className="border-t border-zinc-800 hover:bg-zinc-800/40 transition-colors">
                <td className="py-2 pr-4 text-zinc-400">{new Date(t.ts_open).toLocaleString("es-AR")}</td>
                <td className="pr-4">{t.side}</td>
                <td className="text-right pr-4 font-mono">{t.quantity_btc.toFixed(6)}</td>
                <td className="text-right pr-4 font-mono">${t.entry_price.toFixed(2)}</td>
                <td className="text-right pr-4 font-mono">{t.exit_price ? `$${t.exit_price.toFixed(2)}` : "—"}</td>
                <td className={`text-right pr-4 font-mono ${(t.pnl_usdt ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {t.pnl_usdt != null ? `$${t.pnl_usdt.toFixed(2)}` : "—"}
                </td>
                <td className="text-right pr-4 text-zinc-500">
                  {t.fees_usdt != null ? `$${t.fees_usdt.toFixed(2)}` : "—"}
                </td>
                <td className="text-zinc-400">{t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
