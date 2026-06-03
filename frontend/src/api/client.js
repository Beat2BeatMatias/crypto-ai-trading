const BASE = "/api";
async function get(path) {
    const r = await fetch(`${BASE}${path}`);
    if (!r.ok)
        throw new Error(`${r.status} ${path}`);
    return r.json();
}
async function put(path, body) {
    const r = await fetch(`${BASE}${path}`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!r.ok)
        throw new Error(`${r.status} ${path}`);
    return r.json();
}
async function post(path, body) {
    const r = await fetch(`${BASE}${path}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!r.ok)
        throw new Error(`${r.status} ${path}`);
    return r.json();
}
async function patch(path, body) {
    const r = await fetch(`${BASE}${path}`, {
        method: "PATCH", headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!r.ok)
        throw new Error(`${r.status} ${path}`);
    return r.json();
}
export const api = {
    trades: (p) => {
        const q = new URLSearchParams();
        if (p?.status)
            q.set("status", p.status);
        if (p?.includePaper)
            q.set("include_paper", "true");
        const qs = q.toString();
        return get(`/trades${qs ? `?${qs}` : ""}`);
    },
    closeTrade: (id) => post(`/trades/${id}/close`, {}),
    decisions: (p) => {
        const q = new URLSearchParams();
        if (p?.agent)
            q.set("agent", p.agent);
        if (p?.executed !== undefined)
            q.set("executed", String(p.executed));
        if (p?.includePaper)
            q.set("include_paper", "true");
        const qs = q.toString();
        return get(`/decisions${qs ? `?${qs}` : ""}`);
    },
    positions: () => get("/positions"),
    balance: () => get("/balance"),
    config: () => get("/config"),
    setConfig: (key, value) => put(`/config/${key}`, { value }),
    killSwitch: (enabled) => post("/kill-switch", { enabled }),
    runSupervisor: () => post("/supervisor/run", {}),
    resetDrawdown: () => post("/drawdown/reset", {}),
    setMode: (mode, confirmation) => post("/mode", { mode, confirmation }),
    playbookActive: () => get("/playbook/active"),
    playbookHistory: () => get("/playbook/history"),
    playbookActivate: (version) => post(`/playbook/${version}/activate`, {}),
    playbookEditContent: (version, content) => patch(`/playbook/${version}/content`, { content }),
    supervisorRuns: (limit = 30) => get(`/supervisor/runs?limit=${limit}`),
    outcomes: (sinceHours = 24, classification) => {
        const q = new URLSearchParams();
        q.set("since_hours", String(sinceHours));
        if (classification)
            q.set("classification", classification);
        return get(`/decisions/outcomes?${q.toString()}`);
    },
    dailyStats: () => get("/stats/daily"),
    ohlcv: (timeframe, limit = 300, market) => {
        const q = new URLSearchParams({ timeframe, limit: String(limit) });
        if (market)
            q.set("market", market);
        return get(`/ohlcv?${q}`);
    },
    confluenceCandidates: (status) => {
        const q = status ? `?status=${encodeURIComponent(status)}` : "";
        return get(`/confluence/candidates${q}`);
    },
    confluenceRegistry: (activeOnly = true) => get(`/confluence/registry?active_only=${activeOnly}`),
    promoteConfluenceCandidate: (id) => post(`/confluence/candidates/${id}/promote`, {}),
    rejectConfluenceCandidate: (id, reason) => post(`/confluence/candidates/${id}/reject`, { reason }),
    deactivateConfluence: (code) => post(`/confluence/registry/${code}/deactivate`, {}),
};
