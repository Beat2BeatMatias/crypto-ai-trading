from datetime import datetime, timezone
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    factory = request.app.state.session_factory
    try:
        async with factory() as s:
            await s.execute(text("SELECT 1"))

            # Engine: last decision age
            row = (await s.execute(
                text("SELECT ts FROM decisions ORDER BY ts DESC LIMIT 1")
            )).first()
            if row:
                age_min = (datetime.now(timezone.utc) - row.ts).total_seconds() / 60
                engine_ok = age_min < 15
                engine_detail = f"última decisión hace {int(age_min)}m"
                last_decision_age_min = int(age_min)
            else:
                engine_ok = False
                engine_detail = "sin decisiones aún"
                last_decision_age_min = None

            # Binance: last OHLCV age
            row = (await s.execute(
                text("SELECT time FROM ohlcv WHERE timeframe = '1m' ORDER BY time DESC LIMIT 1")
            )).first()
            if row:
                age_min = (datetime.now(timezone.utc) - row.time).total_seconds() / 60
                binance_ok = age_min < 15
                binance_detail = f"último precio hace {int(age_min)}m"
            else:
                binance_ok = False
                binance_detail = "sin datos de precio"

            # LLM: last 24h stats
            llm_stats = (await s.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE agent = 'decisor')                          AS decisor_total,
                    COUNT(*) FILTER (WHERE agent = 'decisor' AND executed = true)       AS decisor_executed,
                    COUNT(*) FILTER (WHERE agent = 'decisor'
                                      AND rejected_reason LIKE 'parse_error%')         AS parse_errors,
                    COUNT(*) FILTER (WHERE agent = 'decisor'
                                      AND rejected_reason LIKE 'llm_error%')           AS llm_errors,
                    COUNT(*) FILTER (WHERE agent = 'supervisor')                        AS supervisor_total
                FROM decisions
                WHERE ts >= NOW() - INTERVAL '24 hours'
            """))).first()

            # Kill switch status
            ks_row = (await s.execute(
                text("SELECT value FROM config WHERE key = 'kill_switch'")
            )).first()
            kill_switch_active = ks_row.value.lower() in ("true", "1") if ks_row else False

            # Circuit breaker / engine paused
            cb_row = (await s.execute(
                text("SELECT value FROM config WHERE key = 'engine_paused'")
            )).first()
            engine_paused = cb_row.value.lower() in ("true", "1") if cb_row else False
            cb_reason_row = (await s.execute(
                text("SELECT value FROM config WHERE key = 'engine_pause_reason'")
            )).first()
            engine_pause_reason = cb_reason_row.value if cb_reason_row else ""

            # Last active playbook
            pb_row = (await s.execute(
                text("SELECT version, ts_generated, model, win_rate FROM playbook_versions WHERE active = true LIMIT 1")
            )).first()

            # Recent rejected decisions (last 1h)
            rejected_row = (await s.execute(text("""
                SELECT COUNT(*) AS cnt
                FROM decisions
                WHERE ts >= NOW() - INTERVAL '1 hour'
                  AND rejected_reason IS NOT NULL
                  AND agent = 'decisor'
            """))).first()

        parse_errors = int(llm_stats.parse_errors) if llm_stats else 0
        llm_errors = int(llm_stats.llm_errors) if llm_stats else 0
        decisor_total = int(llm_stats.decisor_total) if llm_stats else 0

        llm_ok = (parse_errors + llm_errors) == 0 or (
            decisor_total > 0 and (parse_errors + llm_errors) / decisor_total < 0.05
        )

        return {
            "ok": True,
            "db": "up",
            "kill_switch": kill_switch_active,
            "circuit_breaker": {
                "triggered": engine_paused,
                "reason": engine_pause_reason if engine_paused else None,
            },
            "engine": {
                "ok": engine_ok,
                "detail": engine_detail,
                "last_decision_age_min": last_decision_age_min,
            },
            "binance": {
                "ok": binance_ok,
                "detail": binance_detail,
            },
            "llm": {
                "ok": llm_ok,
                "decisor_total_24h": decisor_total,
                "decisor_executed_24h": int(llm_stats.decisor_executed) if llm_stats else 0,
                "parse_errors_24h": parse_errors,
                "llm_errors_24h": llm_errors,
                "supervisor_runs_24h": int(llm_stats.supervisor_total) if llm_stats else 0,
            },
            "playbook": {
                "version": pb_row.version if pb_row else None,
                "ts_generated": pb_row.ts_generated.isoformat() if pb_row else None,
                "model": pb_row.model if pb_row else None,
                "win_rate": float(pb_row.win_rate) if pb_row and pb_row.win_rate else None,
            },
            "recent_rejections_1h": int(rejected_row.cnt) if rejected_row else 0,
        }
    except Exception as e:
        return {
            "ok": False, "db": str(e),
            "kill_switch": None,
            "circuit_breaker": {"triggered": None, "reason": None},
            "engine": {"ok": False, "detail": "error de DB", "last_decision_age_min": None},
            "binance": {"ok": False, "detail": "error de DB"},
            "llm": None,
            "playbook": None,
            "recent_rejections_1h": None,
        }


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
