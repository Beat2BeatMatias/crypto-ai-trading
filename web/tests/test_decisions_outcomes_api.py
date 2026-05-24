from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


async def test_get_outcomes_returns_recent_classifications(client, app_with_db):
    from shared.db.models import Decision, DecisionOutcome

    factory = app_with_db.state.session_factory
    async with factory() as s:
        now = datetime.now(tz=timezone.utc)
        decision = Decision(
            id=uuid4(),
            ts=now - timedelta(hours=2), agent="decisor", model="m",
            input={"price": 100.0}, output={"action": "HOLD", "confidence": 0.55},
            executed=False,
        )
        s.add(decision)
        await s.commit()
        s.add(DecisionOutcome(
            decision_id=decision.id, horizon_min=240, matured=True,
            forward_return_pct=Decimal("0.5"), mfe_pct=Decimal("0.5"),
            mae_pct=Decimal("-0.05"), time_to_mfe_min=15, time_to_mae_min=1,
            sl_dist_pct=Decimal("0.3"), tp_target_pct=Decimal("0.39"),
            classification="MISSED_OPPORTUNITY",
            computed_at=now - timedelta(hours=1),
        ))
        await s.commit()

    resp = await client.get("/api/decisions/outcomes?since_hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    assert item["classification"] == "MISSED_OPPORTUNITY"
    assert item["mfe_pct"] == pytest.approx(0.5)
    assert item["action"] == "HOLD"


async def test_get_outcomes_filter_by_classification(client, app_with_db):
    from shared.db.models import Decision, DecisionOutcome

    factory = app_with_db.state.session_factory
    async with factory() as s:
        now = datetime.now(tz=timezone.utc)
        d1 = Decision(id=uuid4(), ts=now - timedelta(hours=2), agent="decisor", model="m",
                      input={}, output={"action": "HOLD"}, executed=False)
        d2 = Decision(id=uuid4(), ts=now - timedelta(hours=3), agent="decisor", model="m",
                      input={}, output={"action": "HOLD"}, executed=False)
        s.add_all([d1, d2])
        await s.commit()
        s.add(DecisionOutcome(decision_id=d1.id, horizon_min=240, matured=True,
                              classification="MISSED_OPPORTUNITY",
                              computed_at=now - timedelta(hours=1)))
        s.add(DecisionOutcome(decision_id=d2.id, horizon_min=240, matured=True,
                              classification="GOOD_HOLD",
                              computed_at=now - timedelta(hours=2)))
        await s.commit()

    resp = await client.get(
        "/api/decisions/outcomes?since_hours=24&classification=MISSED_OPPORTUNITY"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["classification"] == "MISSED_OPPORTUNITY"


async def test_get_outcomes_include_lessons_when_requested(client, app_with_db):
    from shared.db.models import Decision, DecisionOutcome

    factory = app_with_db.state.session_factory
    async with factory() as s:
        now = datetime.now(tz=timezone.utc)
        decision = Decision(
            id=uuid4(),
            ts=now - timedelta(hours=2), agent="decisor", model="m",
            input={"price": 100.0}, output={"action": "BUY", "confidence": 0.7},
            executed=True,
        )
        s.add(decision)
        await s.commit()
        lesson = {"summary": "test lesson", "root_cause_tag": "x"}
        s.add(DecisionOutcome(
            decision_id=decision.id, horizon_min=240, matured=True,
            classification="BAD_BUY",
            computed_at=now - timedelta(hours=1),
            postmortem_status="completed",
            lesson_raw=lesson,
            postmortem_at=now - timedelta(hours=1),
        ))
        await s.commit()

    resp_default = await client.get("/api/decisions/outcomes?since_hours=24")
    assert resp_default.status_code == 200
    assert resp_default.json()[0].get("lesson_raw") is None

    resp = await client.get("/api/decisions/outcomes?since_hours=24&include_lessons=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["postmortem_status"] == "completed"
    assert body[0]["lesson_raw"]["summary"] == "test lesson"
