from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Decision

router = APIRouter()

class DecisionOut(BaseModel):
    id: UUID
    ts: datetime
    agent: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    input: dict
    output: dict
    outcome: dict | None
    trade_id: UUID | None
    executed: bool
    rejected_reason: str | None


class SupervisorRunOut(BaseModel):
    """Proyección ligera de una ejecución del Supervisor para el frontend."""
    ts: datetime
    ratified: bool
    ratify_reason: str | None
    force_regen_reason: str | None
    mode: str
    new_version: int | None
    playbook_age_days: int | None
    playbook_win_rate_baseline: float | None


async def _session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as s:
        yield s

@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(session: Annotated[AsyncSession, Depends(_session)],
                          agent: str | None = Query(None),
                          executed: bool | None = Query(None),
                          limit: int = Query(100, le=500)):
    stmt = select(Decision).order_by(desc(Decision.ts)).limit(limit)
    if agent:
        stmt = stmt.where(Decision.agent == agent)
    if executed is not None:
        stmt = stmt.where(Decision.executed == executed)
    rows = (await session.execute(stmt)).scalars().all()
    return [DecisionOut.model_validate(r, from_attributes=True) for r in rows]


class RiskGateBreakdown(BaseModel):
    total_rejections: int
    by_rule: dict[str, int]


class CoherenceBreakdown(BaseModel):
    total_warnings: int
    decisions_with_warnings: int
    two_pass_triggered: int
    by_rule: dict[str, int]


class ConfidenceBucket(BaseModel):
    range: str
    count: int


class SizingBucket(BaseModel):
    range: str
    count: int


class DecisionStatsOut(BaseModel):
    window_hours: int
    total_decisions: int
    by_action: dict[str, int]
    executed: int
    risk_gate: RiskGateBreakdown
    coherence: CoherenceBreakdown
    confidence_distribution: list[ConfidenceBucket]
    sizing_distribution: list[SizingBucket]


@router.get("/decisions/stats", response_model=DecisionStatsOut)
async def decisions_stats(
    session: Annotated[AsyncSession, Depends(_session)],
    window: int = Query(24, ge=1, le=168, description="Ventana en horas (1–168)"),
):
    """Estadísticas del decisor: rechazos por rule_id, warnings de CoherenceChecker,
    distribución de confianza y sizing — para monitoreo durante el rollout."""
    since = datetime.now(tz=timezone.utc) - timedelta(hours=window)
    rows = (await session.execute(
        select(Decision)
        .where(Decision.ts >= since, Decision.agent == "decisor")
        .order_by(desc(Decision.ts))
    )).scalars().all()

    by_action: Counter[str] = Counter()
    risk_rejections: Counter[str] = Counter()
    coherence_by_rule: Counter[str] = Counter()
    confidence_buckets: Counter[str] = Counter()
    sizing_buckets: Counter[str] = Counter()
    decisions_with_warnings = 0
    two_pass_count = 0

    _conf_ranges = [
        ("<0.50", 0.0, 0.50),
        ("0.50–0.59", 0.50, 0.60),
        ("0.60–0.69", 0.60, 0.70),
        ("0.70–0.79", 0.70, 0.80),
        ("0.80–0.89", 0.80, 0.90),
        ("≥0.90", 0.90, 1.01),
    ]
    _size_ranges = [
        ("0%", 0.0, 0.001),
        ("0–2%", 0.001, 0.02),
        ("2–5%", 0.02, 0.05),
        ("5–8%", 0.05, 0.08),
        ("8–10%", 0.08, 0.10),
        (">10%", 0.10, 1.01),
    ]

    for d in rows:
        out = d.output or {}
        action = out.get("action", "UNKNOWN")
        by_action[action] += 1

        # Risk gate rejections por rule_id
        reason = d.rejected_reason or ""
        if reason.startswith("risk_gate:") or reason.startswith("R"):
            rule_id = out.get("risk_gate_rule_id") or reason.split(":")[0]
            risk_rejections[rule_id] += 1
        elif reason.startswith("coherence_strict:"):
            # extraer reglas de "coherence_strict: ['C1', 'C3']"
            import re as _re
            ids = _re.findall(r"C\d+", reason)
            for cid in ids:
                risk_rejections[f"coherence_strict_{cid}"] += 1

        # Coherence warnings
        warnings = out.get("coherence_warnings", [])
        if warnings:
            decisions_with_warnings += 1
        for w in warnings:
            coherence_by_rule[w.get("rule_id", "?")] += 1
        if out.get("two_pass_triggered"):
            two_pass_count += 1

        # Confidence distribution
        conf = out.get("confidence", 0.0) or 0.0
        for label, lo, hi in _conf_ranges:
            if lo <= conf < hi:
                confidence_buckets[label] += 1
                break

        # Sizing distribution
        size = out.get("position_size_pct", 0.0) or 0.0
        for label, lo, hi in _size_ranges:
            if lo <= size < hi:
                sizing_buckets[label] += 1
                break

    executed = sum(1 for d in rows if d.executed)
    total_risk_rejections = sum(risk_rejections.values())

    return DecisionStatsOut(
        window_hours=window,
        total_decisions=len(rows),
        by_action=dict(by_action),
        executed=executed,
        risk_gate=RiskGateBreakdown(
            total_rejections=total_risk_rejections,
            by_rule=dict(risk_rejections),
        ),
        coherence=CoherenceBreakdown(
            total_warnings=sum(coherence_by_rule.values()),
            decisions_with_warnings=decisions_with_warnings,
            two_pass_triggered=two_pass_count,
            by_rule=dict(coherence_by_rule),
        ),
        confidence_distribution=[
            ConfidenceBucket(range=r, count=confidence_buckets.get(r, 0))
            for r, *_ in _conf_ranges
        ],
        sizing_distribution=[
            SizingBucket(range=r, count=sizing_buckets.get(r, 0))
            for r, *_ in _size_ranges
        ],
    )


@router.get("/supervisor/runs", response_model=list[SupervisorRunOut])
async def list_supervisor_runs(
    session: Annotated[AsyncSession, Depends(_session)],
    limit: int = Query(30, le=200),
):
    """Historial de ejecuciones del Supervisor (ratificaciones + regeneraciones).

    Proyección ligera de `decisions` con `agent="supervisor"`, extrayendo
    los campos de ratificación del JSONB `output` para que el frontend no
    tenga que parsear el payload completo.
    """
    rows = (await session.execute(
        select(Decision)
        .where(Decision.agent == "supervisor")
        .order_by(desc(Decision.ts))
        .limit(limit)
    )).scalars().all()

    result: list[SupervisorRunOut] = []
    for r in rows:
        out = r.output or {}
        result.append(SupervisorRunOut(
            ts=r.ts,
            ratified=bool(out.get("ratified", False)),
            ratify_reason=out.get("ratify_reason"),
            force_regen_reason=out.get("force_regen_reason"),
            mode=str(out.get("mode") or "normal"),
            new_version=out.get("new_version"),
            playbook_age_days=out.get("playbook_age_days"),
            playbook_win_rate_baseline=out.get("playbook_win_rate_baseline"),
        ))
    return result
