export type DecisorAction = "BUY" | "SELL" | "HOLD";

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
}

export interface Position {
  id: string; trade_id: string | null; symbol: string;
  quantity_btc: number; entry_price: number;
  current_price: number | null; unrealized_pnl: number | null;
  unrealized_pct: number | null; status: string;
  opened_at: string; updated_at: string | null;
}

export interface Decision {
  id: string; ts: string; agent: "decisor" | "supervisor"; model: string;
  tokens_in: number | null; tokens_out: number | null; latency_ms: number | null;
  input: Record<string, unknown>; output: Record<string, unknown>;
  outcome: Record<string, unknown> | null;
  trade_id: string | null; executed: boolean; rejected_reason: string | null;
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

export interface Balance {
  usdt: number;
  btc_exchange: number;
  btc_in_positions: number;
  open_positions: number;
  balance_ts: string | null;
  balance_source: string | null;
  realized_pnl_today: number;
}

export interface Playbook {
  id: string; version: number; ts_generated: string;
  content: string; model: string | null;
  trades_analyzed: number | null; win_rate: number | null; active: boolean;
}
