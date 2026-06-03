import type { TradingContext } from "../types";

const REASON_LABELS: Record<string, string> = {
  api_permissions: "API / permisos Binance",
  insufficient_margin: "Margen insuficiente",
  restart_required: "Reinicio del engine",
  unknown: "Desajuste config / runtime",
};

export function RuntimeMismatchBanner({ ctx }: { ctx: TradingContext }) {
  const reason = ctx.runtime_mismatch_reason ?? "unknown";
  const label = REASON_LABELS[reason] ?? REASON_LABELS.unknown;
  const detail =
    ctx.runtime_mismatch_detail ??
    "Config en futuros pero el engine opera en spot. Revisá logs y reiniciá trading-engine.";

  return (
    <div
      className="rounded-lg border border-amber-800/50 bg-amber-950/30 px-4 py-3 text-sm text-amber-200"
      role="alert"
    >
      <p className="font-medium text-amber-100">
        Config en <strong>futuros</strong>, runtime en <strong>spot</strong>
        <span className="ml-2 rounded bg-amber-900/60 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-300">
          {label}
        </span>
      </p>
      <p className="mt-2 leading-relaxed">{detail}</p>
      {reason === "api_permissions" && (
        <ul className="mt-2 list-disc pl-5 text-amber-200/90 space-y-1">
          <li>En Binance → API Management: habilitá permiso <strong>Futures</strong>.</li>
          <li>Si usás whitelist de IP, agregá la IP del servidor.</li>
          <li>
            Luego:{" "}
            <code className="text-amber-100">docker-compose restart trading-engine</code>
          </li>
        </ul>
      )}
      {reason === "restart_required" && (
        <p className="mt-2 text-amber-200/90">
          Guardar config no alcanza: el producto efectivo se fija al arrancar el proceso.
        </p>
      )}
    </div>
  );
}
