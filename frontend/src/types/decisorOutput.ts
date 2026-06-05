/** Campos de confianza en `decisions.output` del agente decisor. */
export interface ConfidenceMeta {
  confluence_count?: number;
  confluences_counted?: string[];
  confluences_dropped?: string[];
  extended_confluence_weight?: number;
  quality_factor?: number;
  regime_factor?: number;
  conf_base_table_value?: number;
  confidence_base_computed?: number;
  confidence_llm_factor?: number;
  confidence_adjustment?: number;
  subjective_adj_max?: number;
  confidence?: number;
}

export interface DecisorOutputFields {
  action?: string;
  regime?: string;
  confidence?: number;
  confidence_base?: number;
  confidence_llm_factor?: number;
  confidence_adjustment?: number;
  confidence_meta?: ConfidenceMeta;
  reasoning?: string;
  confluences?: string[];
  stop_loss?: number;
  take_profit?: number;
  position_size_pct?: number;
  mode?: string;
  llm_error_tried?: { provider: string; rate_limited: boolean; too_large: boolean }[];
}

export function asDecisorOutput(output: Record<string, unknown>): DecisorOutputFields {
  return output as DecisorOutputFields;
}

export function fmtConfidencePct(value: number | null | undefined): string {
  const n = typeof value === "number" ? value : 0;
  return `${(n * 100).toLocaleString("es-AR", { maximumFractionDigits: 0 })}%`;
}
