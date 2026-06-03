import type { TradingContext } from "../types";

function ModeBadge({ mode }: { mode: string }) {
  const isLive = mode === "LIVE";
  return (
    <span
      className={`rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        isLive
          ? "bg-red-900/80 text-red-300 ring-1 ring-red-700 animate-pulse"
          : "bg-amber-900/50 text-amber-300 ring-1 ring-amber-800"
      }`}
      title={isLive ? "Modo LIVE — trading real (según config)" : "Modo paper — sin dinero real en config"}
    >
      {isLive ? "LIVE" : "Paper"}
    </span>
  );
}

function ExchangeBadge({ testnet }: { testnet: boolean }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-[11px] font-semibold ${
        testnet
          ? "bg-zinc-800 text-zinc-400 ring-1 ring-zinc-700"
          : "bg-orange-950/60 text-orange-300 ring-1 ring-orange-800"
      }`}
      title={
        testnet
          ? "BINANCE_TESTNET=true — órdenes van a testnet de Binance"
          : "BINANCE_TESTNET=false — órdenes van a mainnet de Binance"
      }
    >
      {testnet ? "Testnet" : "Mainnet"}
    </span>
  );
}

function ProductBadge({
  product,
  variant,
}: {
  product: "spot" | "futures";
  variant: "config" | "runtime";
}) {
  const isFutures = product === "futures";
  const label = isFutures ? "Futuros" : "Spot";
  const prefix = variant === "runtime" ? "Runtime: " : "";
  return (
    <span
      className={`rounded px-2 py-0.5 text-[11px] font-semibold ${
        isFutures
          ? "bg-violet-900/50 text-violet-200 ring-1 ring-violet-700"
          : "bg-sky-950/50 text-sky-200 ring-1 ring-sky-800"
      }`}
      title={
        variant === "config"
          ? `trading_product=${product} en configuración`
          : `Producto efectivo del engine en este ciclo`
      }
    >
      {prefix}
      {label}
    </span>
  );
}

export function TradingContextBadges({ ctx }: { ctx: TradingContext | null }) {
  if (!ctx) {
    return (
      <span className="text-[11px] text-zinc-600">Cargando modo…</span>
    );
  }

  const configProduct = ctx.trading_product === "futures" ? "futures" : "spot";
  const effective =
    ctx.effective_trading_product === "futures" ? "futures" : "spot";
  const mismatch = ctx.runtime_mismatch ?? configProduct !== effective;

  const mismatchTitle = (() => {
    if (!mismatch) return "";
    if (ctx.runtime_mismatch_detail) return ctx.runtime_mismatch_detail;
    const labels: Record<string, string> = {
      api_permissions: "API sin permiso de futuros o IP no autorizada",
      insufficient_margin: "Margen futuros insuficiente para min_notional",
      restart_required: "Reiniciá trading-engine para aplicar futuros",
    };
    const r = ctx.runtime_mismatch_reason;
    return (r && labels[r]) || "Config futuros pero runtime spot";
  })();

  return (
    <div className="flex items-center gap-1.5 flex-wrap" aria-label="Modo de trading">
      <ModeBadge mode={ctx.mode} />
      <ExchangeBadge testnet={ctx.binance_testnet} />
      <ProductBadge product={configProduct} variant="config" />
      {mismatch && (
        <>
          <ProductBadge product={effective} variant="runtime" />
          <span
            className="text-[10px] text-amber-400 max-w-[11rem] leading-tight"
            title={mismatchTitle}
          >
            ⚠ Config futuros · runtime spot
          </span>
        </>
      )}
    </div>
  );
}
