import { useEffect, useState } from "react";

interface HealthData { ok: boolean; db: string; }

function StatusRow({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-center justify-between rounded bg-zinc-800 p-3">
      <span className="font-medium">{label}</span>
      <span className="flex items-center gap-2 text-sm">
        <span className={`size-2 rounded-full ${ok ? "bg-emerald-400" : "bg-red-400"}`} />
        <span className="text-zinc-400">{detail}</span>
      </span>
    </div>
  );
}

export function Health() {
  const [data, setData] = useState<HealthData | null>(null);

  const refresh = () => fetch("/api/health").then(r => r.json()).then(setData).catch(() => {});
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rounded-xl bg-zinc-900 p-5 max-w-xl">
      <h2 className="text-lg font-semibold mb-4">Estado del sistema</h2>
      <div className="space-y-2">
        <StatusRow label="Web API" ok={data?.ok ?? false}
          detail={data ? `DB: ${data.db}` : "Verificando..."} />
        <StatusRow label="Trading Engine" ok={false}
          detail="Heartbeat via DB — ver tabla daily_stats (v1.1)" />
        <StatusRow label="Binance" ok={false}
          detail="Conectividad — ver logs del engine" />
      </div>
      <div className="mt-4 text-xs text-zinc-600">
        Panel de salud ampliado disponible en v1.1 (heartbeat desde DB, latencia LLM, etc.)
      </div>
    </div>
  );
}
