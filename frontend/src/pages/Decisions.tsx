import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Decision } from "../types";
import ConfidenceBreakdown from "../components/ConfidenceBreakdown";
import ReasoningBlock from "../components/ReasoningBlock";
import { asDecisorOutput, fmtConfidencePct } from "../types/decisorOutput";
import { cutoffFromDateInput } from "../lib/liveSince";
import { useLiveSinceFilter } from "../hooks/useLiveSinceFilter";

function explainRejection(reason: string): string {
  if (reason.startsWith("stop_loss must be"))
    return "El SL propuesto por el LLM estaba por encima del precio actual al momento de validar (el orderbook no tenía snapshot).";

  // Parsear threshold real del mensaje del backend: "R:R ratio 0.88 <= 1.5"
  const rrMatch = reason.match(/^R:R ratio [\d.]+ <= ([\d.]+)/);
  if (rrMatch)
    return `La relación riesgo/beneficio entre el SL y el TP no alcanzó el mínimo configurado (${rrMatch[1]}:1).`;

  // Parsear threshold real: "SL distance 12.00 < 0.2×ATR 30.00" o "SL distance 12.00 > 1.5×ATR 30.00"
  const slMatch = reason.match(/^SL distance [\d.]+ [<>] ([\d.]+)×ATR/);
  if (slMatch)
    return `La distancia del stop-loss al precio de entrada quedó fuera de la banda permitida (${slMatch[1]}× ATR).`;

  if (reason.startsWith("SL distance"))
    return "La distancia del stop-loss al precio de entrada quedó fuera de la banda permitida por la config de ATR.";
  if (reason.startsWith("max_simultaneous"))
    return "Ya hay el máximo de posiciones simultáneas abiertas.";
  if (reason.startsWith("daily P&L"))
    return "El P&L del día alcanzó el límite de pérdida diaria configurado.";
  if (reason.startsWith("kill_switch"))
    return "El kill switch está activado. Solo se permiten ventas para cerrar posiciones.";
  if (reason.startsWith("BUY requires stop_loss"))
    return "El LLM no incluyó un stop_loss en la respuesta. Todo BUY lo requiere obligatoriamente.";
  if (reason.startsWith("parse_error"))
    return "El LLM devolvió una respuesta que no pudo parsearse como JSON válido.";
  if (reason.startsWith("llm_error"))
    return "Todos los providers LLM fallaron (rate limit o error) y no se obtuvo decisión.";
  if (reason.startsWith("brackets_failed")) {
    if (reason.includes("-4120") || reason.toLowerCase().includes("algo order"))
      return "La posición se abrió en Binance, pero falló colocar SL/TP en el exchange. Revisá órdenes condicionales en Binance o cerrá manualmente; el guardian software sigue activo.";
    return "La posición se abrió, pero no se pudieron colocar las órdenes SL/TP en Binance. Ver detalle técnico abajo.";
  }
  if (reason.startsWith("execution_error")) {
    if (reason.includes("NOTIONAL"))
      return "Binance rechazó la orden porque el monto a operar es menor al mínimo permitido (filtro NOTIONAL). Verificá el balance USDT disponible o el porcentaje de posición configurado.";
    if (reason.includes("insufficient balance"))
      return "Balance insuficiente en la cuenta de Binance para ejecutar la orden.";
    return "Binance rechazó la orden al intentar ejecutarla. Ver el detalle técnico abajo.";
  }
  return "";
}

export function Decisions() {
  const [allItems, setAllItems] = useState<Decision[]>([]);
  const [agent, setAgent] = useState("");
  const [actionFilter, setActionFilter] = useState<"" | "BUY" | "SHORT" | "SELL" | "HOLD">("");
  const [confMin, setConfMin] = useState(0);
  const [confMax, setConfMax] = useState(100);
  const {
    liveSinceIso,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    includePaper,
    setIncludePaper,
    clearDateFilters,
    hasCustomDateFilter,
  } = useLiveSinceFilter();
  const [selected, setSelected] = useState<Decision | null>(null);

  useEffect(() => {
    api.decisions({
      agent: agent || undefined,
      includePaper,
    }).then(setAllItems).catch(() => {});
  }, [agent, includePaper]);

  const items = useMemo(() => {
    let list = [...allItems];
    if (actionFilter) {
      list = list.filter(d => {
        const o = d.output as { action?: string };
        return o.action === actionFilter;
      });
    }
    list = list.filter(d => {
      const o = d.output as { confidence?: number };
      const conf = (o.confidence ?? 0) * 100;
      return conf >= confMin && conf <= confMax;
    });
    if (dateFrom) {
      const cutoff = cutoffFromDateInput(dateFrom, liveSinceIso);
      list = list.filter(d => new Date(d.ts) >= cutoff);
    }
    if (dateTo)
      list = list.filter(d => new Date(d.ts) <= new Date(dateTo + "T23:59:59Z"));
    return list;
  }, [allItems, actionFilter, confMin, confMax, dateFrom, dateTo, liveSinceIso]);

  const out = (d: Decision) => asDecisorOutput(d.output);

  const isBuyRejected = (d: Decision) =>
    out(d).action === "BUY" && !d.executed;

  const rejectionLabel = (reason: string): string => {
    if (reason.startsWith("stop_loss must be")) return "SL > precio actual";
    if (reason.startsWith("R:R ratio")) return reason.replace("R:R ratio", "R:R");
    if (reason.startsWith("SL distance")) return "SL muy ajustado";
    if (reason.startsWith("max_simultaneous")) return "Máx. posiciones abiertas";
    if (reason.startsWith("daily P&L")) return "Stop diario alcanzado";
    if (reason.startsWith("kill_switch")) return "Kill switch activo";
    if (reason.startsWith("SELL requested")) return "Sin posición abierta";
    if (reason.startsWith("insufficient_data")) return reason;
    if (reason.startsWith("llm_error")) return "Error LLM";
    if (reason.startsWith("parse_error")) return "Error parsing LLM";
    if (reason.startsWith("brackets_failed")) return "SL/TP no en exchange";
    if (reason.startsWith("execution_error")) {
      if (reason.includes("NOTIONAL")) return "Error NOTIONAL (monto < mínimo Binance)";
      if (reason.includes("insufficient balance")) return "Balance insuficiente";
      return "Error al ejecutar en exchange";
    }
    return reason;
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Decisiones (audit log)</h2>
          <span className="text-xs text-zinc-500">{items.length} resultado{items.length !== 1 ? "s" : ""}</span>
        </div>

        {/* ── Filtros ── */}
        <div className="space-y-2 mb-4">
          <div className="flex flex-wrap gap-2 items-center">
            {/* Agente */}
            <select value={agent} onChange={e => setAgent(e.target.value)}
              className="rounded bg-zinc-800 px-2 py-1 text-xs border border-zinc-700 text-zinc-300">
              <option value="">Todos los agentes</option>
              <option value="decisor">Decisor</option>
              <option value="supervisor">Supervisor</option>
            </select>

            {/* Acción */}
            {(["", "BUY", "SHORT", "SELL", "HOLD"] as const).map(a => (
              <button key={a} onClick={() => setActionFilter(a)}
                className={`text-xs px-2.5 py-1 rounded transition-colors ${
                  actionFilter === a
                    ? a === "BUY" ? "bg-emerald-900/70 text-emerald-300 font-semibold"
                    : a === "SELL" ? "bg-red-900/70 text-red-300 font-semibold"
                    : a === "HOLD" ? "bg-zinc-700 text-zinc-300 font-semibold"
                    : "bg-blue-900 text-blue-200 font-semibold"
                    : "bg-zinc-800 text-zinc-400 hover:bg-zinc-700"
                }`}
              >
                {a === "" ? "Todas las acciones" : a}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap gap-3 items-center">
            {/* Confidence range */}
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <span>Conf:</span>
              <input type="range" min={0} max={100} step={5} value={confMin}
                onChange={e => setConfMin(Number(e.target.value))}
                className="w-20 accent-blue-500" />
              <span className="text-zinc-300 w-8">{confMin}%</span>
              <span>–</span>
              <input type="range" min={0} max={100} step={5} value={confMax}
                onChange={e => setConfMax(Number(e.target.value))}
                className="w-20 accent-blue-500" />
              <span className="text-zinc-300 w-8">{confMax}%</span>
            </div>

            {/* Date range */}
            <div className="flex gap-2 items-center ml-auto">
              <input type="date" value={dateFrom} onChange={e => { setDateFrom(e.target.value); setIncludePaper(false); }}
                className="text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 focus:outline-none" />
              <span className="text-zinc-600 text-xs">—</span>
              <input type="date" value={dateTo} onChange={e => { setDateTo(e.target.value); setIncludePaper(false); }}
                className="text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300 focus:outline-none" />
              {hasCustomDateFilter && (
                <button onClick={clearDateFilters}
                  className="text-xs text-zinc-500 hover:text-zinc-300">✕</button>
              )}
            </div>
          </div>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
              <th className="text-left py-2 pr-3">TS</th>
              <th className="text-left pr-3">ID</th>
              <th className="text-left pr-3">Agente</th>
              <th className="text-left pr-3">Modelo</th>
              <th className="text-left pr-3">Acción</th>
              <th className="text-right pr-3">Conf</th>
              <th className="text-left">Estado / Motivo</th>
            </tr>
          </thead>
          <tbody>
            {items.map(d => (
              <tr key={d.id} onClick={() => setSelected(d)}
                className={`cursor-pointer border-t border-zinc-800 hover:bg-zinc-800/40 transition-colors ${selected?.id === d.id ? "bg-zinc-800" : ""}`}>
                <td className="py-2 pr-3 text-zinc-400 text-xs whitespace-nowrap">{new Date(d.ts).toLocaleString("es-AR", { hour12: false })}</td>
                <td className="pr-3 text-xs text-zinc-500 font-mono">{d.id.substring(0, 8)}</td>
                <td className="pr-3">{d.agent}</td>
                <td className="pr-3 text-xs text-zinc-400 font-mono">{d.model}</td>
                <td className="pr-3 font-semibold">
                  {d.agent === "supervisor"
                    ? out(d).mode === "diagnostic"
                      ? <span className="text-xs bg-amber-900/50 text-amber-300 px-2 py-0.5 rounded font-normal">Diagnóstico</span>
                      : <span className="text-xs bg-blue-900/50 text-blue-300 px-2 py-0.5 rounded font-normal">Normal</span>
                    : <span className={
                        out(d).action === "BUY" ? "text-emerald-400"
                        : out(d).action === "SHORT" ? "text-amber-400"
                        : out(d).action === "SELL" ? "text-red-400"
                        : "text-zinc-400"
                      }>
                        {out(d).action ?? "—"}
                      </span>
                  }
                </td>
                <td className="text-right pr-3">
                  {d.agent === "decisor" ? (
                    <div className="leading-tight">
                      <div>{fmtConfidencePct(out(d).confidence)}</div>
                      {typeof out(d).confidence_base === "number" && (
                        <div className="text-[10px] text-zinc-500 font-mono">
                          b {fmtConfidencePct(out(d).confidence_base)}
                          {(out(d).confidence_adjustment ?? 0) !== 0 && (
                            <> a {(out(d).confidence_adjustment! > 0 ? "+" : "")}
                            {fmtConfidencePct(out(d).confidence_adjustment)}</>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-zinc-500">—</span>
                  )}
                </td>
                <td className="py-1">
                  {d.executed
                    ? <span className="text-emerald-400">✅ ejecutado</span>
                    : isBuyRejected(d) && d.rejected_reason
                      ? (
                        <span className="inline-flex flex-col gap-0.5">
                          <span className="text-amber-400 text-xs font-semibold">⚠ BUY bloqueado</span>
                          <span className="text-red-400 text-xs font-mono">{rejectionLabel(d.rejected_reason)}</span>
                        </span>
                      )
                    : d.rejected_reason
                      ? <span className="text-zinc-500 text-xs">❌ {rejectionLabel(d.rejected_reason)}</span>
                      : <span className="text-zinc-600">—</span>
                  }
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rounded-xl bg-zinc-900 p-5 overflow-auto max-h-[80vh]">
        {selected ? (
          <div>
            <div className="flex items-center gap-2 mb-1">
              {selected.agent === "supervisor"
                ? <>
                    <h3 className="font-semibold text-lg text-zinc-300">Supervisor</h3>
                    {out(selected).mode === "diagnostic"
                      ? <span className="text-xs bg-amber-900/50 text-amber-300 px-2 py-0.5 rounded">Diagnóstico</span>
                      : <span className="text-xs bg-blue-900/50 text-blue-300 px-2 py-0.5 rounded">Normal</span>
                    }
                  </>
                : <>
                    <h3 className={`font-semibold text-lg ${out(selected).action === "BUY" ? "text-emerald-400" : out(selected).action === "SELL" ? "text-red-400" : "text-zinc-300"}`}>
                      {out(selected).action ?? "—"}
                    </h3>
                    {selected.executed
                      ? <span className="text-xs bg-emerald-900/50 text-emerald-300 px-2 py-0.5 rounded">ejecutado</span>
                      : selected.rejected_reason
                        ? <span className="text-xs bg-red-900/50 text-red-300 px-2 py-0.5 rounded">bloqueado</span>
                        : null
                    }
                  </>
              }
            </div>
            <div className="mb-3 space-y-1 text-xs text-zinc-500">
              <p className="font-mono">{selected.model}</p>
              <p>ID: <span className="text-zinc-400">{selected.id}</span></p>
              <p>{new Date(selected.ts).toLocaleString("es-AR", { hour12: false })}</p>
            </div>

            {selected.agent === "decisor" && (
              <div className="mb-4">
                <ConfidenceBreakdown
                  confidence={out(selected).confidence}
                  confidenceBase={out(selected).confidence_base}
                  confidenceAdjustment={out(selected).confidence_adjustment}
                  meta={out(selected).confidence_meta}
                />
              </div>
            )}

            {/* Razonamiento del LLM */}
            {out(selected).reasoning && (
              <div className="mb-4">
                <ReasoningBlock reasoning={out(selected).reasoning!} />
              </div>
            )}

            {/* Confluencias */}
            {(out(selected).confluences ?? []).length > 0 && (
              <div className="mb-4">
                <p className="text-xs text-zinc-500 uppercase mb-1">Confluencias detectadas</p>
                <ul className="space-y-1">
                  {(out(selected).confluences ?? []).map((c, i) => (
                    <li key={i} className="text-xs text-emerald-400 flex items-center gap-1">
                      <span className="text-zinc-600">•</span> {c}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Parámetros de la orden para BUY */}
            {out(selected).action === "BUY" && (
              <div className="mb-4 rounded-lg bg-zinc-800 p-3 space-y-1.5">
                <p className="text-xs text-zinc-500 uppercase mb-2">Parámetros de la orden</p>
                <div className="flex justify-between text-xs">
                  <span className="text-zinc-400">Stop Loss</span>
                  <span className="font-mono text-red-400">
                    {out(selected).stop_loss ? `$${out(selected).stop_loss!.toFixed(2)}` : "—"}
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-zinc-400">Take Profit</span>
                  <span className="font-mono text-emerald-400">
                    {out(selected).take_profit ? `$${out(selected).take_profit!.toFixed(2)}` : "—"}
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-zinc-400">Size</span>
                  <span className="font-mono text-zinc-300">
                    {out(selected).position_size_pct != null ? `${(out(selected).position_size_pct! * 100).toFixed(0)}% del capital` : "—"}
                  </span>
                </div>
              </div>
            )}

            {/* Motivo de bloqueo para BUY no ejecutados */}
            {isBuyRejected(selected) && selected.rejected_reason && (
              <div className="mb-4 rounded-lg bg-amber-950/40 border border-amber-800/50 p-3">
                <p className="text-xs text-amber-400 font-semibold mb-1">⚠ Por qué no se ejecutó</p>
                <p className="text-xs text-red-300 font-mono">{selected.rejected_reason}</p>
                <p className="text-xs text-zinc-500 mt-1">{explainRejection(selected.rejected_reason)}</p>
              </div>
            )}

            {/* Motivo de bloqueo genérico (no BUY) */}
            {!isBuyRejected(selected) && selected.rejected_reason && (
              <div className="mb-4 rounded-lg bg-zinc-800 p-3 space-y-2">
                <p className="text-xs text-red-400 font-mono">{selected.rejected_reason}</p>
                {explainRejection(selected.rejected_reason) && (
                  <p className="text-xs text-zinc-500">{explainRejection(selected.rejected_reason)}</p>
                )}
                {out(selected).llm_error_tried && (
                  <div className="mt-2">
                    <p className="text-xs text-zinc-500 uppercase mb-1">Providers intentados</p>
                    <div className="space-y-1">
                      {out(selected).llm_error_tried!.map((t, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs font-mono">
                          <span className="text-zinc-600">{i + 1}.</span>
                          <span className="text-zinc-300 w-44">{t.provider}</span>
                          {t.rate_limited
                            ? <span className="text-amber-400">429 rate limit</span>
                            : t.too_large
                              ? <span className="text-orange-400">413 prompt muy grande</span>
                              : <span className="text-red-400">error</span>
                          }
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            <details className="mb-2">
              <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-300">Output JSON</summary>
              <pre className="mt-2 text-xs bg-zinc-950 p-3 rounded overflow-auto">{JSON.stringify(selected.output, null, 2)}</pre>
            </details>
            <details>
              <summary className="cursor-pointer text-xs text-zinc-500 hover:text-zinc-300">Input JSON</summary>
              <pre className="mt-2 text-xs bg-zinc-950 p-3 rounded overflow-auto max-h-64">{JSON.stringify(selected.input, null, 2)}</pre>
            </details>
          </div>
        ) : <p className="text-zinc-500 text-sm">Seleccioná una fila para ver el detalle.</p>}
      </div>
    </div>
  );
}
