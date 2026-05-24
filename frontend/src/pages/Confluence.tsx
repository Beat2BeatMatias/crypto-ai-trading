import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ConfluenceCandidate, ConfluenceRegistryEntry } from "../types";

const MIN_OCC_DEFAULT = 3;

function fmtTs(iso: string) {
  return new Date(iso).toLocaleString("es-AR", { hour12: false });
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    open: "bg-amber-900/40 text-amber-300 border-amber-800",
    promoted: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
    rejected: "bg-zinc-800 text-zinc-400 border-zinc-700",
    merged: "bg-blue-900/40 text-blue-300 border-blue-800",
  };
  return map[status] ?? "bg-zinc-800 text-zinc-400 border-zinc-700";
}

function VerifySpecPreview({ spec }: { spec: Record<string, unknown> }) {
  const rules = [
    ...((spec.all as unknown[]) ?? []),
    ...((spec.any as unknown[]) ?? []),
  ];
  if (!rules.length) return <span className="text-zinc-600">—</span>;
  return (
    <ul className="text-xs text-zinc-400 space-y-0.5 font-mono">
      {rules.slice(0, 4).map((r, i) => (
        <li key={i}>{JSON.stringify(r)}</li>
      ))}
    </ul>
  );
}

export function ConfluencePage() {
  const [candidates, setCandidates] = useState<ConfluenceCandidate[]>([]);
  const [registry, setRegistry] = useState<ConfluenceRegistryEntry[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | "open" | "promoted" | "rejected">("open");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, r] = await Promise.all([
        api.confluenceCandidates(statusFilter || undefined),
        api.confluenceRegistry(true),
      ]);
      setCandidates(c);
      setRegistry(r);
    } catch {
      setError("No se pudieron cargar los datos de confluencias.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { load(); }, [load]);

  const openCount = useMemo(
    () => candidates.filter(c => c.status === "open").length,
    [candidates],
  );

  async function handlePromote(id: string) {
    if (!confirm("¿Promover este patrón al catálogo extendido (I–Z)?")) return;
    setBusyId(id);
    setError(null);
    try {
      await api.promoteConfluenceCandidate(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al promover");
    } finally {
      setBusyId(null);
    }
  }

  async function handleReject(id: string) {
    setRejectId(id);
    setRejectReason("");
  }

  async function confirmReject() {
    if (!rejectId) return;
    setBusyId(rejectId);
    setError(null);
    try {
      await api.rejectConfluenceCandidate(rejectId, rejectReason);
      setRejectId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al rechazar");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDeactivate(code: string) {
    if (!confirm(`¿Desactivar la confluencia '${code}'? No se recicla la letra por 30 días.`)) return;
    setBusyId(code);
    setError(null);
    try {
      await api.deactivateConfluence(code);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al desactivar");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      <header>
        <h1 className="text-xl font-semibold text-white">Aprendizaje — Confluencias</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Patrones detectados por post-mortem. Promové candidatos al catálogo I–Z o rechazalos manualmente.
        </p>
      </header>

      {error && (
        <div className="rounded border border-red-900 bg-red-950/50 text-red-300 text-sm px-4 py-2">
          {error}
        </div>
      )}

      {/* Registry activo */}
      <section className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-zinc-300 uppercase tracking-wide">
            Catálogo promovido (I–Z)
          </h2>
          <span className="text-xs text-zinc-500">{registry.length} activas</span>
        </div>
        {registry.length === 0 ? (
          <p className="text-sm text-zinc-600">Ninguna confluencia promovida activa.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800">
                  <th className="py-2 pr-3">Letra</th>
                  <th className="py-2 pr-3">Título</th>
                  <th className="py-2 pr-3">Definición</th>
                  <th className="py-2 pr-3">Desde</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {registry.map(r => (
                  <tr key={r.code} className="border-b border-zinc-800/60">
                    <td className="py-2 pr-3 font-mono text-emerald-400 text-lg">{r.code}</td>
                    <td className="py-2 pr-3 text-zinc-200">{r.title}</td>
                    <td className="py-2 pr-3 text-zinc-400 text-xs max-w-md">{r.definition_md}</td>
                    <td className="py-2 pr-3 text-zinc-500 text-xs whitespace-nowrap">{fmtTs(r.created_at)}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        disabled={busyId === r.code}
                        onClick={() => handleDeactivate(r.code)}
                        className="text-xs text-red-400 hover:text-red-300 disabled:opacity-40"
                      >
                        Desactivar
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Candidatos */}
      <section className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <h2 className="text-sm font-medium text-zinc-300 uppercase tracking-wide">
            Cola de candidatos
          </h2>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as typeof statusFilter)}
            className="text-xs bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-zinc-300"
          >
            <option value="open">Abiertos</option>
            <option value="promoted">Promovidos</option>
            <option value="rejected">Rechazados</option>
            <option value="">Todos</option>
          </select>
          <button
            type="button"
            onClick={load}
            className="text-xs text-emerald-400 hover:text-emerald-300 ml-auto"
          >
            Actualizar
          </button>
        </div>

        {loading ? (
          <p className="text-sm text-zinc-500">Cargando…</p>
        ) : candidates.length === 0 ? (
          <p className="text-sm text-zinc-600">Sin candidatos para el filtro seleccionado.</p>
        ) : (
          <div className="space-y-3">
            {candidates.map(c => {
              const eligible = c.status === "open" && c.occurrence_count >= MIN_OCC_DEFAULT;
              return (
                <div
                  key={c.id}
                  className="rounded border border-zinc-800 bg-zinc-950/40 p-4"
                >
                  <div className="flex flex-wrap items-start gap-2 mb-2">
                    <span className={`text-xs px-2 py-0.5 rounded border ${statusBadge(c.status)}`}>
                      {c.status}
                    </span>
                    {eligible && (
                      <span className="text-xs px-2 py-0.5 rounded border bg-emerald-950/50 text-emerald-400 border-emerald-900">
                        elegible (≥{MIN_OCC_DEFAULT} ocurrencias)
                      </span>
                    )}
                    <span className="text-xs text-zinc-500 font-mono ml-auto">{c.pattern_tag}</span>
                  </div>
                  <h3 className="text-white font-medium">{c.title}</h3>
                  <p className="text-sm text-zinc-400 mt-1">{c.definition_md}</p>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 text-xs text-zinc-500">
                    <div>
                      <span className="block text-zinc-600">Ocurrencias</span>
                      <span className="text-zinc-300 font-mono">{c.occurrence_count}</span>
                    </div>
                    <div>
                      <span className="block text-zinc-600">Última vez</span>
                      {fmtTs(c.last_seen_at)}
                    </div>
                    <div>
                      <span className="block text-zinc-600">Primera vez</span>
                      {fmtTs(c.first_seen_at)}
                    </div>
                    <div>
                      <span className="block text-zinc-600">verify_spec</span>
                      <VerifySpecPreview spec={c.verify_spec} />
                    </div>
                  </div>
                  {c.reject_reason && (
                    <p className="text-xs text-red-400/80 mt-2">Motivo rechazo: {c.reject_reason}</p>
                  )}
                  {c.proposed_code && (
                    <p className="text-xs text-emerald-500 mt-1">Promovido como letra {c.proposed_code}</p>
                  )}
                  {c.status === "open" && (
                    <div className="flex gap-2 mt-4">
                      <button
                        type="button"
                        disabled={busyId === c.id}
                        onClick={() => handlePromote(c.id)}
                        className="text-xs px-3 py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40"
                      >
                        Promover
                      </button>
                      <button
                        type="button"
                        disabled={busyId === c.id}
                        onClick={() => handleReject(c.id)}
                        className="text-xs px-3 py-1.5 rounded border border-zinc-700 text-zinc-400 hover:text-zinc-200 disabled:opacity-40"
                      >
                        Rechazar
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {statusFilter === "open" && !loading && (
          <p className="text-xs text-zinc-600 mt-3">
            {openCount} candidato(s) abierto(s). El Supervisor también promueve automáticamente al cumplir umbrales.
          </p>
        )}
      </section>

      {/* Modal rechazo */}
      {rejectId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center p-4 z-50">
          <div className="bg-zinc-900 border border-zinc-700 rounded-lg p-5 max-w-md w-full shadow-xl">
            <h3 className="text-white font-medium mb-2">Rechazar candidato</h3>
            <p className="text-xs text-zinc-500 mb-3">No se repropone en 7 días salvo nueva ocurrencia duplicada.</p>
            <textarea
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
              placeholder="Motivo (opcional)"
              rows={3}
              className="w-full text-sm bg-zinc-950 border border-zinc-700 rounded p-2 text-zinc-300 mb-4"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setRejectId(null)}
                className="text-xs px-3 py-1.5 text-zinc-400"
              >
                Cancelar
              </button>
              <button
                type="button"
                onClick={confirmReject}
                className="text-xs px-3 py-1.5 rounded bg-red-800 hover:bg-red-700 text-white"
              >
                Confirmar rechazo
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
