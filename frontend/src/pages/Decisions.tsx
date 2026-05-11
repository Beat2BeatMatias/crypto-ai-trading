import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Decision } from "../types";

function explainRejection(reason: string): string {
  if (reason.startsWith("stop_loss must be"))
    return "El SL propuesto por el LLM estaba por encima del precio actual al momento de validar (el orderbook no tenía snapshot).";
  if (reason.startsWith("R:R ratio"))
    return "La relación riesgo/beneficio entre el SL y el TP no alcanzó el mínimo configurado (1.3:1).";
  if (reason.startsWith("SL distance"))
    return "La distancia del stop-loss al precio de entrada fue menor al 0.3× ATR(1h), considerado demasiado ajustado.";
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
  return "";
}

export function Decisions() {
  const [items, setItems] = useState<Decision[]>([]);
  const [agent, setAgent] = useState("");
  const [selected, setSelected] = useState<Decision | null>(null);

  useEffect(() => {
    api.decisions(agent ? { agent } : undefined).then(setItems).catch(() => {});
  }, [agent]);

  const out = (d: Decision) => d.output as {
    action?: string; confidence?: number; reasoning?: string;
    stop_loss?: number; take_profit?: number; position_size_pct?: number; confluences?: string[];
    mode?: string;
  };

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
    return reason;
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 rounded-xl bg-zinc-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Decisiones (audit log)</h2>
          <select value={agent} onChange={e => setAgent(e.target.value)}
            className="rounded bg-zinc-800 px-2 py-1 text-sm border border-zinc-700">
            <option value="">Todos</option>
            <option value="decisor">Decisor</option>
            <option value="supervisor">Supervisor</option>
          </select>
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
                    : <span className={out(d).action === "BUY" ? "text-emerald-400" : out(d).action === "SELL" ? "text-red-400" : "text-zinc-400"}>
                        {out(d).action ?? "—"}
                      </span>
                  }
                </td>
                <td className="text-right pr-3">{((out(d).confidence ?? 0) * 100).toFixed(0)}%</td>
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

            {/* Razonamiento del LLM */}
            <p className="text-sm text-zinc-300 mb-4 leading-relaxed">{out(selected).reasoning ?? ""}</p>

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
              <div className="mb-4 rounded-lg bg-zinc-800 p-3">
                <p className="text-xs text-red-400">Rechazada: {selected.rejected_reason}</p>
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
