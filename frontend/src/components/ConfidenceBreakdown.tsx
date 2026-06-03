import type { ConfidenceMeta } from "../types/decisorOutput";
import { fmtConfidencePct } from "../types/decisorOutput";

type Props = {
  confidence?: number;
  confidenceBase?: number;
  confidenceAdjustment?: number;
  meta?: ConfidenceMeta;
  compact?: boolean;
};

export default function ConfidenceBreakdown({
  confidence,
  confidenceBase,
  confidenceAdjustment,
  meta,
  compact = false,
}: Props) {
  const final = confidence ?? meta?.confidence;
  const base = confidenceBase ?? meta?.confidence_base_computed;
  const adj = confidenceAdjustment ?? meta?.confidence_adjustment ?? 0;
  const hasBase = typeof base === "number";
  const hasMeta = meta && (meta.confluence_count != null || meta.quality_factor != null);

  if (final == null && !hasBase && !hasMeta) {
    return <span className="text-zinc-500">—</span>;
  }

  const adjLabel =
    adj > 0 ? `+${fmtConfidencePct(adj)}` : adj < 0 ? fmtConfidencePct(adj) : null;

  if (compact) {
    return (
      <span className="text-zinc-300">
        {fmtConfidencePct(final)}
        {hasBase && (
          <span className="text-zinc-500 text-xs ml-1">
            (base {fmtConfidencePct(base)}
            {adjLabel ? ` ${adjLabel}` : ""})
          </span>
        )}
      </span>
    );
  }

  return (
    <div className="rounded-lg bg-zinc-800 p-3 space-y-2">
      <p className="text-xs text-zinc-500 uppercase">Confianza</p>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-semibold text-white">{fmtConfidencePct(final)}</span>
        {hasBase && (
          <span className="text-xs text-zinc-400">
            = base {fmtConfidencePct(base)}
            {adjLabel ? ` ${adjLabel}` : ""}
          </span>
        )}
      </div>

      {hasMeta && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
          {meta.confluence_count != null && (
            <>
              <dt className="text-zinc-500">Confluencias (conteo)</dt>
              <dd className="text-zinc-300 font-mono">{meta.confluence_count}</dd>
            </>
          )}
          {meta.quality_factor != null && (
            <>
              <dt className="text-zinc-500">Factor calidad (F/G)</dt>
              <dd className="text-zinc-300 font-mono">×{meta.quality_factor.toFixed(2)}</dd>
            </>
          )}
          {meta.regime_factor != null && (
            <>
              <dt className="text-zinc-500">Factor régimen</dt>
              <dd className="text-zinc-300 font-mono">×{meta.regime_factor.toFixed(2)}</dd>
            </>
          )}
          {meta.conf_base_table_value != null && (
            <>
              <dt className="text-zinc-500">Tabla conf_base_N</dt>
              <dd className="text-zinc-300 font-mono">{meta.conf_base_table_value.toFixed(2)}</dd>
            </>
          )}
        </dl>
      )}

      {(meta?.confluences_counted?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs text-zinc-500 mb-1">Contadas en la base</p>
          <p className="text-xs font-mono text-emerald-400/90">
            {meta!.confluences_counted!.join(", ")}
          </p>
        </div>
      )}

      {(meta?.confluences_dropped?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs text-zinc-500 mb-1">Descartadas (inactivas / inválidas)</p>
          <p className="text-xs font-mono text-amber-400/90">
            {meta!.confluences_dropped!.join(", ")}
          </p>
        </div>
      )}

      {meta?.extended_confluence_weight != null && (
        <p className="text-[10px] text-zinc-600">
          Peso I–Z en conteo: {meta.extended_confluence_weight}× (servidor)
        </p>
      )}
    </div>
  );
}
