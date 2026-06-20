export type TradingProduct = "spot" | "futures";

export type RuntimeMismatchReason =
  | "api_permissions"
  | "insufficient_margin"
  | "restart_required"
  | "unknown";

export interface TradingContext {
  mode: string;
  trading_product: TradingProduct;
  effective_trading_product: TradingProduct;
  runtime_mismatch: boolean;
  runtime_mismatch_reason?: RuntimeMismatchReason | null;
  runtime_mismatch_detail?: string | null;
  binance_testnet: boolean;
  chart_market?: "spot" | "futures";
  chart_label?: string;
  chart_symbol?: string;
}

export type DecisorAction = "BUY" | "SHORT" | "SELL" | "HOLD";
export type PositionSide = "LONG" | "SHORT";

export type MarketRegime =
  | "TRENDING_UP" | "TRENDING_DOWN" | "RANGE" | "HIGH_VOLATILITY";

export interface Trade {
  id: string; decision_id: string | null;
  ts_open: string; ts_close: string | null;
  side: string; quantity_btc: number; entry_price: number;
  exit_price: number | null; pnl_usdt: number | null; pnl_pct: number | null;
  status: "open" | "closed" | "cancelled";
  stop_loss: number | null; take_profit: number | null;
  close_reason: string | null; fees_usdt: number | null;
  close_requested: boolean;
  order_id_open: string | null;
  order_id_close: string | null;
  order_id_sl: string | null;
  order_id_tp: string | null;
  current_price?: number | null;
  unrealized_pnl_usdt?: number | null;
  unrealized_pnl_pct?: number | null;
  sl_pnl_usdt?: number | null;
  sl_pnl_pct?: number | null;
  tp_pnl_usdt?: number | null;
  tp_pnl_pct?: number | null;
  position_side?: PositionSide;
  leverage?: number | null;
  liquidation_price?: number | null;
  margin_mode?: string | null;
}

export interface Position {
  id: string; trade_id: string | null; symbol: string;
  quantity_btc: number; entry_price: number;
  current_price: number | null; unrealized_pnl: number | null;
  unrealized_pct: number | null; status: string;
  opened_at: string; updated_at: string | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  sl_pnl_usdt?: number | null;
  sl_pnl_pct?: number | null;
  tp_pnl_usdt?: number | null;
  tp_pnl_pct?: number | null;
  position_side?: PositionSide;
  leverage?: number | null;
  liquidation_price?: number | null;
  order_id_sl?: string | null;
  order_id_tp?: string | null;
}

export interface Decision {
  id: string; ts: string; agent: "decisor" | "supervisor"; model: string;
  tokens_in: number | null; tokens_out: number | null; latency_ms: number | null;
  input: Record<string, unknown>; output: Record<string, unknown>;
  outcome: Record<string, unknown> | null;
  trade_id: string | null; executed: boolean; rejected_reason: string | null;
}

export type OutcomeClassification =
  | "MISSED_OPPORTUNITY"
  | "GOOD_HOLD"
  | "BLOCKED_GOOD_TRADE"
  | "CORRECTLY_BLOCKED"
  | "GOOD_BUY"
  | "BAD_BUY"
  | "GOOD_SELL"
  | "BAD_SELL"
  | "GOOD_SHORT"
  | "BAD_SHORT"
  | "PENDING"
  | "UNKNOWN";

export interface DecisionOutcome {
  decision_id: string;
  ts: string;
  action: string | null;
  confidence: number | null;
  regime: string | null;
  executed: boolean;
  rejected_reason: string | null;
  horizon_min: number;
  matured: boolean;
  forward_return_pct: number | null;
  mfe_pct: number | null;
  mae_pct: number | null;
  time_to_mfe_min: number | null;
  time_to_mae_min: number | null;
  sl_dist_pct: number | null;
  tp_target_pct: number | null;
  classification: OutcomeClassification;
  computed_at: string;
}

export interface ConfigEntry {
  key: string; value: string;
  value_type: "int" | "float" | "string" | "bool" | "json";
  description: string | null;
  updated_at: string | null;
  last_changed_by: string | null;
}

export interface DailyStats {
  trades_open: number; trades_closed: number; trades_won: number; trades_lost: number;
  pnl_realized: number; pnl_unrealized: number; fees_total: number;
  decisions_total: number; decisions_buy: number; decisions_sell: number;
  decisions_hold: number; decisions_executed: number; decisions_blocked: number;
}

export interface FuturesBalance {
  available_margin: number | null;
  margin_balance: number | null;
  margin_locked: number | null;
  source: "live" | "snapshot" | "unavailable" | string;
  fetched_at: string | null;
}

export interface Balance {
  usdt: number;
  usdt_locked: number;
  usdt_total: number;
  btc_exchange: number;
  btc_locked: number;
  btc_exchange_total: number;
  btc_in_positions: number;
  open_positions: number;
  balance_ts: string | null;
  balance_source: string | null;
  realized_pnl_today: number;
  margin_balance?: number | null;
  available_margin?: number | null;
  futures?: FuturesBalance | null;
}

export interface ScheduledProcess {
  id: string;
  name: string;
  next_run_at: string | null;
  last_run_at: string | null;
  interval_desc: string;
}

export interface Playbook {
  id: string; version: number; ts_generated: string;
  content: string; model: string | null;
  trades_analyzed: number | null; win_rate: number | null; active: boolean;
}

/** Ejecución del Supervisor persistida en `decisions` (agent="supervisor"). */
export interface SupervisorRun {
  ts: string;
  ratified: boolean;
  ratify_reason: string | null;
  force_regen_reason: string | null;
  mode: "normal" | "diagnostic" | string;
  new_version: number | null;
  playbook_age_days: number | null;
  playbook_win_rate_baseline: number | null;
}

/** Payload del evento WebSocket `supervisor_ran`. */
export interface SupervisorRanEvent {
  event: "supervisor_ran";
  data: SupervisorRun;
}

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h";

export interface Candle {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

export interface ConfluenceCandidate {
  id: string;
  pattern_tag: string;
  proposed_code: string | null;
  title: string;
  definition_md: string;
  verify_spec: Record<string, unknown>;
  occurrence_count: number;
  first_seen_at: string;
  last_seen_at: string;
  source_decision_ids: string[];
  status: "open" | "promoted" | "rejected" | "merged" | string;
  promoted_at: string | null;
  reject_reason: string | null;
}

export interface ConfluenceRegistryEntry {
  code: string;
  slug: string;
  title: string;
  definition_md: string;
  verify_spec: Record<string, unknown>;
  active: boolean;
  promoted_from: string | null;
  created_at: string;
  deactivated_at: string | null;
}
