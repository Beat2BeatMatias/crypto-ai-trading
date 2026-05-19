import type { Trade, Position, Decision, ConfigEntry, Playbook, DailyStats, Balance, Candle, Timeframe, SupervisorRun, DecisionOutcome } from "../types";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "PATCH", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

export const api = {
  trades: (status?: string) => get<Trade[]>(`/trades${status ? `?status=${status}` : ""}`),
  closeTrade: (id: string) => post<Trade>(`/trades/${id}/close`, {}),
  decisions: (p?: { agent?: string; executed?: boolean }) => {
    const q = new URLSearchParams();
    if (p?.agent) q.set("agent", p.agent);
    if (p?.executed !== undefined) q.set("executed", String(p.executed));
    const qs = q.toString();
    return get<Decision[]>(`/decisions${qs ? `?${qs}` : ""}`);
  },
  positions: () => get<Position[]>("/positions"),
  balance: () => get<Balance>("/balance"),
  config: () => get<ConfigEntry[]>("/config"),
  setConfig: (key: string, value: string) => put(`/config/${key}`, { value }),
  killSwitch: (enabled: boolean) => post("/kill-switch", { enabled }),
  runSupervisor: () => post("/supervisor/run", {}),
  resetDrawdown: () => post<{ ok: boolean; reset_at: string }>("/drawdown/reset", {}),
  setMode: (mode: "PAPER_TRADING" | "LIVE", confirmation: string) =>
    post("/mode", { mode, confirmation }),
  playbookActive: () => get<Playbook | null>("/playbook/active"),
  playbookHistory: () => get<Playbook[]>("/playbook/history"),
  playbookActivate: (version: number) => post(`/playbook/${version}/activate`, {}),
  playbookEditContent: (version: number, content: string) =>
    patch(`/playbook/${version}/content`, { content }),
  supervisorRuns: (limit = 30) => get<SupervisorRun[]>(`/supervisor/runs?limit=${limit}`),
  outcomes: (sinceHours = 24, classification?: string) => {
    const q = new URLSearchParams();
    q.set("since_hours", String(sinceHours));
    if (classification) q.set("classification", classification);
    return get<DecisionOutcome[]>(`/decisions/outcomes?${q.toString()}`);
  },
  dailyStats: () => get<DailyStats>("/stats/daily"),
  ohlcv: (timeframe: Timeframe, limit = 300) =>
    get<Candle[]>(`/ohlcv?timeframe=${timeframe}&limit=${limit}`),
};
