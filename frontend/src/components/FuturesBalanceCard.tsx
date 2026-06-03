import type { ReactNode } from "react";
import type { FuturesBalance } from "../types";

function fmtUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${n.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function sourceLabel(source: string): string {
  if (source === "live") return "Binance (en vivo)";
  if (source === "snapshot") return "Último ciclo del engine";
  return "No disponible";
}

function StatRow({ label, value, valueClass = "text-zinc-200" }: {
  label: string;
  value: ReactNode;
  valueClass?: string;
}) {
  return (
    <div className="flex justify-between items-baseline py-1 border-b border-zinc-800 last:border-0">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className={`text-sm font-mono font-semibold ${valueClass}`}>{value}</span>
    </div>
  );
}

export function FuturesBalanceCard({ futures }: { futures: FuturesBalance | null | undefined }) {
  const unavailable = !futures || futures.source === "unavailable";
  const hasValues =
    futures?.available_margin != null || futures?.margin_balance != null;

  return (
    <div className="rounded-xl bg-zinc-900 p-5 border border-violet-900/40">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-violet-300 uppercase tracking-wide">
          Balance Futuros USDT-M
        </h3>
        {futures && (
          <span
            className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
              futures.source === "live"
                ? "bg-violet-900/60 text-violet-200"
                : futures.source === "snapshot"
                ? "bg-zinc-800 text-zinc-400"
                : "bg-amber-950 text-amber-400"
            }`}
          >
            {sourceLabel(futures.source)}
          </span>
        )}
      </div>

      {unavailable && !hasValues ? (
        <p className="text-sm text-zinc-500">
          No se pudo leer la wallet de futuros. Verificá API keys con permiso de derivados y que
          haya USDT en la cuenta Futures de Binance.
        </p>
      ) : (
        <div className="space-y-1">
          <StatRow
            label="Margen disponible"
            value={fmtUsd(futures?.available_margin)}
            valueClass="text-emerald-400"
          />
          {futures?.margin_locked != null && futures.margin_locked > 0 && (
            <StatRow
              label="Margen bloqueado (órdenes)"
              value={fmtUsd(futures.margin_locked)}
              valueClass="text-yellow-500"
            />
          )}
          <StatRow
            label="Margen total (wallet)"
            value={fmtUsd(futures?.margin_balance)}
            valueClass="text-violet-200"
          />
          <div className="pt-2 text-xs text-zinc-600">
            {futures?.fetched_at
              ? `Actualizado ${new Date(futures.fetched_at).toLocaleTimeString("es-AR")}`
              : futures?.source === "snapshot"
              ? "Desde snapshot del engine"
              : ""}
          </div>
        </div>
      )}
    </div>
  );
}
