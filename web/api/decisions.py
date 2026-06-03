from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import Decision, DecisionOutcome
from shared.config_store import default_list_since
from shared.confidence_calibration import compute_calibration

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
                          action: str | None = Query(None),
                          executed: bool | None = Query(None),
                          since: datetime | None = Query(None),
                          include_paper: bool = Query(False),
                          limit: int = Query(100, le=500)):
    stmt = select(Decision).order_by(desc(Decision.ts)).limit(limit)
    if agent:
        stmt = stmt.where(Decision.agent == agent)
    if action:
        stmt = stmt.where(Decision.output["action"].as_string() == action)
    if executed is not None:
        stmt = stmt.where(Decision.executed == executed)
    effective_since = since
    if effective_since is None and not include_paper:
        effective_since = await default_list_since(session)
    if effective_since is not None:
        stmt = stmt.where(Decision.ts >= effective_since)
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


class CalibrationBucketOut(BaseModel):
    range: str
    count: int
    success_count: int
    success_rate: float | None
    avg_confidence: float | None


class ConfidenceCalibrationOut(BaseModel):
    window_hours: int
    sample_size: int
    buckets: list[CalibrationBucketOut]
    brier_score: float | None
    expected_calibration_error: float | None
    discriminates: bool | None
    low_bucket_success_rate: float | None
    high_bucket_success_rate: float | None
    recommendation: str


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


@router.get("/decisions/calibration", response_model=ConfidenceCalibrationOut)
async def decisions_calibration(
    session: Annotated[AsyncSession, Depends(_session)],
    window: int = Query(168, ge=24, le=336, description="Ventana en horas (24–336)"),
):
    """Curva de calibración: confidence vs tasa de éxito por bucket (outcomes maduros)."""
    since = datetime.now(tz=timezone.utc) - timedelta(hours=window)
    rows = (await session.execute(
        select(Decision, DecisionOutcome)
        .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(Decision.ts >= since, Decision.agent == "decisor")
        .order_by(desc(Decision.ts))
    )).all()

    samples: list[tuple[float, str]] = []
    for decision, outcome in rows:
        if outcome.classification in ("PENDING", "UNKNOWN"):
            continue
        conf = float((decision.output or {}).get("confidence") or 0.0)
        samples.append((conf, outcome.classification))

    report = compute_calibration(samples, window_hours=window)
    return ConfidenceCalibrationOut(
        window_hours=report.window_hours,
        sample_size=report.sample_size,
        buckets=[
            CalibrationBucketOut(
                range=b.range,
                count=b.count,
                success_count=b.success_count,
                success_rate=b.success_rate,
                avg_confidence=b.avg_confidence,
            )
            for b in report.buckets
        ],
        brier_score=report.brier_score,
        expected_calibration_error=report.expected_calibration_error,
        discriminates=report.discriminates,
        low_bucket_success_rate=report.low_bucket_success_rate,
        high_bucket_success_rate=report.high_bucket_success_rate,
        recommendation=report.recommendation,
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


class DecisionOutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    decision_id: UUID
    ts: datetime
    action: str | None
    confidence: float | None
    regime: str | None
    executed: bool
    rejected_reason: str | None

    horizon_min: int
    matured: bool
    forward_return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    time_to_mfe_min: int | None
    time_to_mae_min: int | None
    sl_dist_pct: float | None
    tp_target_pct: float | None
    classification: str
    computed_at: datetime
    postmortem_status: str | None = None
    lesson_raw: dict | None = None
    lesson_normalized: dict | None = None
    postmortem_at: datetime | None = None


@router.get("/decisions/outcomes", response_model=list[DecisionOutcomeOut])
async def list_outcomes(
    session: Annotated[AsyncSession, Depends(_session)],
    since_hours: int = Query(24, ge=1, le=168),
    classification: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    include_lessons: bool = Query(False),
) -> list[DecisionOutcomeOut]:
    since = datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(Decision, DecisionOutcome)
        .join(DecisionOutcome, Decision.id == DecisionOutcome.decision_id)
        .where(Decision.ts >= since)
        .order_by(Decision.ts.desc())
        .limit(limit)
    )
    if classification:
        stmt = stmt.where(DecisionOutcome.classification == classification)
    rows = (await session.execute(stmt)).all()
    out = []
    for d, o in rows:
        out.append(DecisionOutcomeOut(
            decision_id=d.id,
            ts=d.ts,
            action=(d.output or {}).get("action"),
            confidence=(d.output or {}).get("confidence"),
            regime=(d.output or {}).get("regime"),
            executed=d.executed,
            rejected_reason=d.rejected_reason,
            horizon_min=o.horizon_min,
            matured=o.matured,
            forward_return_pct=float(o.forward_return_pct) if o.forward_return_pct is not None else None,
            mfe_pct=float(o.mfe_pct) if o.mfe_pct is not None else None,
            mae_pct=float(o.mae_pct) if o.mae_pct is not None else None,
            time_to_mfe_min=o.time_to_mfe_min,
            time_to_mae_min=o.time_to_mae_min,
            sl_dist_pct=float(o.sl_dist_pct) if o.sl_dist_pct is not None else None,
            tp_target_pct=float(o.tp_target_pct) if o.tp_target_pct is not None else None,
            classification=o.classification,
            computed_at=o.computed_at,
            postmortem_status=o.postmortem_status if include_lessons else None,
            lesson_raw=o.lesson_raw if include_lessons else None,
            lesson_normalized=o.lesson_normalized if include_lessons else None,
            postmortem_at=o.postmortem_at if include_lessons else None,
        ))
    return out
