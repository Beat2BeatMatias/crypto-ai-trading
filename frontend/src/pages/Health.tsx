import { useEffect, useState } from "react";

interface ServiceStatus { ok: boolean; detail: string; last_decision_age_min?: number | null; }
interface CircuitBreakerStatus { triggered: boolean | null; reason: string | null; }
interface LlmStatus {
  ok: boolean;
  decisor_total_24h: number;
  decisor_executed_24h: number;
  parse_errors_24h: number;
  llm_errors_24h: number;
  supervisor_runs_24h: number;
}
interface PlaybookStatus {
  version: number | null;
  ts_generated: string | null;
  model: string | null;
  win_rate: number | null;
}
interface HealthData {
  ok: boolean;
  db: string;
  kill_switch: boolean | null;
  circuit_breaker: CircuitBreakerStatus;
  engine: ServiceStatus;
  binance: ServiceStatus;
  llm: LlmStatus | null;
  playbook: PlaybookStatus | null;
  recent_rejections_1h: number | null;
}

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

function AlertBanner({ color, icon, title, detail }: {
  color: "red" | "orange";
  icon: string;
  title: string;
  detail?: string | null;
}) {
  const bg = color === "red" ? "bg-red-950 border-red-700" : "bg-orange-950 border-orange-700";
  const text = color === "red" ? "text-red-300" : "text-orange-300";
  const subtext = color === "red" ? "text-red-400" : "text-orange-400";
  return (
    <div className={`flex items-start gap-3 rounded-lg border p-4 ${bg}`}>
      <span className="text-xl">{icon}</span>
      <div>
        <p className={`font-semibold ${text}`}>{title}</p>
        {detail && <p className={`text-sm mt-0.5 ${subtext}`}>{detail}</p>}
      </div>
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

  const cbTriggered = data?.circuit_breaker?.triggered === true;
  const killActive = data?.kill_switch === true;

  return (
    <div className="space-y-4 max-w-2xl">

      {/* Banners de alerta — circuit breaker y kill switch */}
      {cbTriggered && (
        <AlertBanner
          color="red"
          icon="🔴"
          title="Circuit Breaker activado — motor pausado"
          detail={data?.circuit_breaker?.reason ?? undefined}
        />
      )}
      {killActive && (
        <AlertBanner
          color="orange"
          icon="🟠"
          title="Kill Switch activo — solo se permiten cierres"
        />
      )}

      <div className="rounded-xl bg-zinc-900 p-5">
        <h2 className="text-lg font-semibold mb-4">Estado del sistema</h2>
        <div className="space-y-2">
          <StatusRow label="Web API"
            ok={data?.ok ?? false}
            detail={data ? `DB: ${data.db}` : "Verificando..."} />
          <StatusRow label="Trading Engine"
            ok={(data?.engine?.ok ?? false) && !cbTriggered}
            detail={cbTriggered ? "Pausado por circuit breaker" : (data?.engine?.detail ?? "Verificando...")} />
          <StatusRow label="Binance"
            ok={data?.binance?.ok ?? false}
            detail={data?.binance?.detail ?? "Verificando..."} />

          {/* Kill switch row */}
          <div className={`flex items-center justify-between rounded p-3 ${killActive ? "bg-orange-950 border border-orange-700" : "bg-zinc-800"}`}>
            <span className="font-medium">Kill Switch</span>
            <span className="flex items-center gap-2 text-sm">
              <span className={`size-2 rounded-full ${killActive ? "bg-orange-400 animate-pulse" : "bg-zinc-500"}`} />
              <span className={killActive ? "text-orange-300 font-semibold" : "text-zinc-400"}>
                {killActive ? "ACTIVO" : "inactivo"}
              </span>
            </span>
          </div>

          {/* Circuit breaker row */}
          <div className={`flex items-center justify-between rounded p-3 ${cbTriggered ? "bg-red-950 border border-red-700" : "bg-zinc-800"}`}>
            <span className="font-medium">Circuit Breaker</span>
            <span className="flex items-center gap-2 text-sm">
              <span className={`size-2 rounded-full ${cbTriggered ? "bg-red-400 animate-pulse" : "bg-zinc-500"}`} />
              <span className={cbTriggered ? "text-red-300 font-semibold" : "text-zinc-400"}>
                {cbTriggered ? "PAUSADO" : "operativo"}
              </span>
            </span>
          </div>
        </div>
      </div>

      {/* Stats LLM */}
      {data?.llm && (
        <div className="rounded-xl bg-zinc-900 p-5">
          <h2 className="text-lg font-semibold mb-3">LLM — últimas 24h</h2>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div className="bg-zinc-800 rounded p-3">
              <p className="text-zinc-400">Decisiones</p>
              <p className="text-lg font-semibold">{data.llm.decisor_total_24h}</p>
            </div>
            <div className="bg-zinc-800 rounded p-3">
              <p className="text-zinc-400">Ejecutadas</p>
              <p className="text-lg font-semibold text-emerald-400">{data.llm.decisor_executed_24h}</p>
            </div>
            <div className={`rounded p-3 ${data.llm.parse_errors_24h > 0 ? "bg-red-950" : "bg-zinc-800"}`}>
              <p className="text-zinc-400">Errores parseo</p>
              <p className={`text-lg font-semibold ${data.llm.parse_errors_24h > 0 ? "text-red-400" : ""}`}>
                {data.llm.parse_errors_24h}
              </p>
            </div>
            <div className={`rounded p-3 ${data.llm.llm_errors_24h > 0 ? "bg-red-950" : "bg-zinc-800"}`}>
              <p className="text-zinc-400">Errores LLM</p>
              <p className={`text-lg font-semibold ${data.llm.llm_errors_24h > 0 ? "text-red-400" : ""}`}>
                {data.llm.llm_errors_24h}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Rechazos recientes */}
      {data !== null && (
        <div className="rounded-xl bg-zinc-900 p-4 flex items-center justify-between">
          <span className="text-zinc-400 text-sm">Decisiones rechazadas (última hora)</span>
          <span className={`font-semibold ${(data.recent_rejections_1h ?? 0) > 0 ? "text-orange-400" : "text-zinc-300"}`}>
            {data.recent_rejections_1h ?? "—"}
          </span>
        </div>
      )}
    </div>
  );
}
