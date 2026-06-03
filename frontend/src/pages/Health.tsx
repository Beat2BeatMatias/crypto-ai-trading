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
interface OutcomeStats {
  total: number;
  missed_opportunity: number;
  good_hold: number;
  bad_buy: number;
  good_buy: number;
  good_short: number;
  bad_short: number;
  good_sell: number;
  bad_sell: number;
  blocked_good_trade: number;
  correctly_blocked: number;
  pending: number;
  unknown: number;
}
interface OutcomeAttributionStatus {
  ok: boolean;
  last_run_age_min: number | null;
  interval_min: number;
  last_run_ts: string | null;
  stats_24h: OutcomeStats;
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
  outcome_attribution: OutcomeAttributionStatus | null;
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

interface CalibrationBucket {
  range: string;
  count: number;
  success_count: number;
  success_rate: number | null;
  avg_confidence: number | null;
}

interface CalibrationData {
  window_hours: number;
  sample_size: number;
  buckets: CalibrationBucket[];
  brier_score: number | null;
  expected_calibration_error: number | null;
  discriminates: boolean | null;
  recommendation: string;
}

export function Health() {
  const [data, setData] = useState<HealthData | null>(null);
  const [calibration, setCalibration] = useState<CalibrationData | null>(null);
  const [resetting, setResetting] = useState(false);

  const refresh = () => {
    fetch("/api/health").then(r => r.json()).then(setData).catch(() => {});
    fetch("/api/decisions/calibration?window=168")
      .then(r => r.ok ? r.json() : null)
      .then(setCalibration)
      .catch(() => setCalibration(null));
  };
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  const handleCircuitBreakerReset = async () => {
    if (!window.confirm("¿Confirmas el reset del Circuit Breaker? El motor retomará operaciones.")) return;
    setResetting(true);
    try {
      await fetch("/api/circuit-breaker/reset", { method: "POST" });
      await refresh();
    } finally {
      setResetting(false);
    }
  };

  const cbTriggered = data?.circuit_breaker?.triggered === true;
  const killActive = data?.kill_switch === true;

  return (
    <div className="space-y-4 max-w-2xl">

      {/* Banners de alerta — circuit breaker y kill switch */}
      {cbTriggered && (
        <div className="flex items-start gap-3 rounded-lg border p-4 bg-red-950 border-red-700">
          <span className="text-xl">🔴</span>
          <div className="flex-1">
            <p className="font-semibold text-red-300">Circuit Breaker activado — motor pausado</p>
            {data?.circuit_breaker?.reason && (
              <p className="text-sm mt-0.5 text-red-400">{data.circuit_breaker.reason}</p>
            )}
          </div>
          <button
            onClick={handleCircuitBreakerReset}
            disabled={resetting}
            className="shrink-0 rounded px-3 py-1.5 text-sm font-semibold bg-red-700 hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed text-white transition-colors"
          >
            {resetting ? "Reseteando…" : "Resetear"}
          </button>
        </div>
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
              {cbTriggered && (
                <button
                  onClick={handleCircuitBreakerReset}
                  disabled={resetting}
                  className="ml-2 rounded px-2 py-0.5 text-xs font-semibold bg-red-700 hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed text-white transition-colors"
                >
                  {resetting ? "…" : "Resetear"}
                </button>
              )}
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

      {/* Calibración de confianza */}
      {calibration && (
        <div className="rounded-xl bg-zinc-900 p-5">
          <h2 className="text-lg font-semibold mb-2">Calibración de confianza</h2>
          <p className="text-xs text-zinc-500 mb-3">
            Ventana {calibration.window_hours}h · {calibration.sample_size} decisiones con outcome
            {calibration.brier_score != null && ` · Brier ${calibration.brier_score}`}
            {calibration.expected_calibration_error != null &&
              ` · ECE ${calibration.expected_calibration_error}`}
          </p>
          <p className={`text-sm mb-3 ${
            calibration.discriminates === true ? "text-emerald-400"
              : calibration.discriminates === false ? "text-amber-400" : "text-zinc-400"
          }`}>
            {calibration.recommendation}
          </p>
          <div className="grid grid-cols-2 gap-1.5 text-xs">
            {calibration.buckets.filter(b => b.count > 0).map(b => (
              <div key={b.range} className="flex justify-between bg-zinc-800 rounded px-2.5 py-1.5">
                <span className="text-zinc-400">{b.range}</span>
                <span className="text-zinc-200">
                  n={b.count}
                  {b.success_rate != null && ` · ${(b.success_rate * 100).toFixed(0)}% ok`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Outcome Attribution */}
      {data?.outcome_attribution && (
        <div className="rounded-xl bg-zinc-900 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Outcome Attribution</h2>
            <span className="flex items-center gap-2 text-sm">
              <span className={`size-2 rounded-full ${data.outcome_attribution.ok ? "bg-emerald-400" : "bg-red-400"}`} />
              <span className="text-zinc-400">
                {data.outcome_attribution.last_run_age_min !== null
                  ? `último run hace ${data.outcome_attribution.last_run_age_min}m · intervalo ${data.outcome_attribution.interval_min}m`
                  : "sin runs aún"}
              </span>
            </span>
          </div>

          {/* Distribución de clasificaciones 24h */}
          {(() => {
            const s = data.outcome_attribution.stats_24h;
            const evaluated = s.total - s.pending - s.unknown;
            const missedRate = evaluated > 0 ? ((s.missed_opportunity / evaluated) * 100).toFixed(1) : "—";
            const badBuyRate = (s.good_buy + s.bad_buy) > 0
              ? ((s.bad_buy / (s.good_buy + s.bad_buy)) * 100).toFixed(1) : "—";
            const badShortRate = (s.good_short + s.bad_short) > 0
              ? ((s.bad_short / (s.good_short + s.bad_short)) * 100).toFixed(1) : "—";

            return (
              <div className="space-y-3">
                {/* Métricas clave */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                  <div className="bg-zinc-800 rounded p-3">
                    <p className="text-zinc-400 text-xs">Evaluadas (24h)</p>
                    <p className="text-lg font-semibold">{evaluated}</p>
                    <p className="text-xs text-zinc-500">de {s.total} totales</p>
                  </div>
                  <div className={`rounded p-3 ${s.missed_opportunity > 0 ? "bg-amber-950 border border-amber-800" : "bg-zinc-800"}`}>
                    <p className="text-zinc-400 text-xs">Miss rate</p>
                    <p className={`text-lg font-semibold ${s.missed_opportunity > 0 ? "text-amber-400" : ""}`}>
                      {missedRate}%
                    </p>
                    <p className="text-xs text-zinc-500">{s.missed_opportunity} missed</p>
                  </div>
                  <div className={`rounded p-3 ${s.bad_buy > 0 ? "bg-red-950" : "bg-zinc-800"}`}>
                    <p className="text-zinc-400 text-xs">Bad buy rate</p>
                    <p className={`text-lg font-semibold ${s.bad_buy > 0 ? "text-red-400" : ""}`}>
                      {badBuyRate}%
                    </p>
                    <p className="text-xs text-zinc-500">{s.bad_buy} bad / {s.good_buy} good</p>
                  </div>
                  <div className={`rounded p-3 ${s.bad_short > 0 ? "bg-red-950" : "bg-zinc-800"}`}>
                    <p className="text-zinc-400 text-xs">Bad short rate</p>
                    <p className={`text-lg font-semibold ${s.bad_short > 0 ? "text-red-400" : ""}`}>
                      {badShortRate}%
                    </p>
                    <p className="text-xs text-zinc-500">{s.bad_short} bad / {s.good_short} good</p>
                  </div>
                </div>

                {/* Distribución completa */}
                <div className="grid grid-cols-2 gap-1.5 text-xs">
                  {[
                    { label: "GOOD_HOLD",          value: s.good_hold,          color: "text-zinc-300" },
                    { label: "MISSED_OPPORTUNITY",  value: s.missed_opportunity, color: "text-amber-400" },
                    { label: "GOOD_BUY",            value: s.good_buy,           color: "text-emerald-400" },
                    { label: "BAD_BUY",             value: s.bad_buy,            color: "text-red-400" },
                    { label: "GOOD_SHORT",          value: s.good_short ?? 0,  color: "text-emerald-400" },
                    { label: "BAD_SHORT",           value: s.bad_short ?? 0,   color: "text-red-400" },
                    { label: "GOOD_SELL",           value: s.good_sell ?? 0,   color: "text-zinc-300" },
                    { label: "BAD_SELL",            value: s.bad_sell ?? 0,    color: "text-red-400" },
                    { label: "BLOCKED_GOOD_TRADE",  value: s.blocked_good_trade, color: "text-amber-300" },
                    { label: "CORRECTLY_BLOCKED",   value: s.correctly_blocked,  color: "text-zinc-400" },
                    { label: "PENDING",             value: s.pending,            color: "text-zinc-500" },
                    { label: "UNKNOWN",             value: s.unknown,            color: "text-zinc-600" },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="flex items-center justify-between bg-zinc-800 rounded px-2.5 py-1.5">
                      <span className="text-zinc-400 font-mono text-xs">{label}</span>
                      <span className={`font-semibold ${color}`}>{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
