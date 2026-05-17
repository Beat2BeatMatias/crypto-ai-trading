from datetime import datetime, timezone
import os
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

            # Risk gate — tasa de rechazo y breakdown por regla (últimas 24h)
            rg_rows = (await s.execute(select(Decision).where(
                Decision.ts >= datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=24),
                Decision.agent == "decisor",
            ))).scalars().all()

            from collections import Counter as _Counter
            import re as _re
            rg_by_rule: _Counter[str] = _Counter()
            coh_by_rule: _Counter[str] = _Counter()
            decisions_with_warnings = 0
            two_pass_count = 0
            for _d in rg_rows:
                _out = _d.output or {}
                _reason = _d.rejected_reason or ""
                if _reason.startswith("risk_gate:") or _reason.startswith("R"):
                    _rid = _reason.split(":")[0]
                    rg_by_rule[_rid] += 1
                _warnings = _out.get("coherence_warnings", [])
                if _warnings:
                    decisions_with_warnings += 1
                for _w in _warnings:
                    coh_by_rule[_w.get("rule_id", "?")] += 1
                if _out.get("two_pass_triggered"):
                    two_pass_count += 1

            _dec_total = len(rg_rows)
            _rg_total = sum(rg_by_rule.values())
            _coh_total = sum(coh_by_rule.values())

            # LLM latency percentiles (last 24h, decisor only)
            latency_row = (await s.execute(text("""
                SELECT
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
                    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99
                FROM decisions
                WHERE ts >= NOW() - INTERVAL '24 hours'
                  AND agent = 'decisor'
                  AND latency_ms IS NOT NULL
            """))).first()

            # Postgres: row counts and DB size
            table_counts_rows = (await s.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM decisions)        AS decisions,
                    (SELECT COUNT(*) FROM trades)           AS trades,
                    (SELECT COUNT(*) FROM ohlcv)            AS ohlcv,
                    (SELECT COUNT(*) FROM indicators)       AS indicators,
                    (SELECT COUNT(*) FROM balance_snapshots) AS balance_snapshots
            """))).first()

            db_size_row = (await s.execute(text("""
                SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size,
                       pg_database_size(current_database()) AS db_bytes
            """))).first()

            # Binance WS: age of last OHLCV 1m (proxy for WS liveness)
            ws_row = (await s.execute(text(
                "SELECT time FROM ohlcv WHERE timeframe = '1m' ORDER BY time DESC LIMIT 1"
            ))).first()
            if ws_row:
                ws_age_sec = (datetime.now(timezone.utc) - ws_row.time).total_seconds()
                binance_ws_ok = ws_age_sec < 120
                binance_ws_detail = f"último 1m hace {int(ws_age_sec)}s"
            else:
                binance_ws_ok = False
                binance_ws_detail = "sin datos 1m"

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
                "ws": {
                    "ok": binance_ws_ok,
                    "detail": binance_ws_detail,
                },
            },
            "llm": {
                "ok": llm_ok,
                "decisor_total_24h": decisor_total,
                "decisor_executed_24h": int(llm_stats.decisor_executed) if llm_stats else 0,
                "parse_errors_24h": parse_errors,
                "llm_errors_24h": llm_errors,
                "supervisor_runs_24h": int(llm_stats.supervisor_total) if llm_stats else 0,
                "latency_ms": {
                    "p50": int(latency_row.p50) if latency_row and latency_row.p50 else None,
                    "p95": int(latency_row.p95) if latency_row and latency_row.p95 else None,
                    "p99": int(latency_row.p99) if latency_row and latency_row.p99 else None,
                },
            },
            "playbook": {
                "version": pb_row.version if pb_row else None,
                "ts_generated": pb_row.ts_generated.isoformat() if pb_row else None,
                "model": pb_row.model if pb_row else None,
                "win_rate": float(pb_row.win_rate) if pb_row and pb_row.win_rate else None,
            },
            "recent_rejections_1h": int(rejected_row.cnt) if rejected_row else 0,
            "risk_gate": {
                "rejection_rate_24h": round(_rg_total / _dec_total, 4) if _dec_total else 0.0,
                "total_rejections_24h": _rg_total,
                "by_rule_24h": dict(rg_by_rule),
            },
            "coherence": {
                "warning_rate_24h": round(decisions_with_warnings / _dec_total, 4) if _dec_total else 0.0,
                "total_warnings_24h": _coh_total,
                "decisions_with_warnings_24h": decisions_with_warnings,
                "two_pass_triggered_24h": two_pass_count,
                "by_rule_24h": dict(coh_by_rule),
            },
            "postgres": {
                "table_counts": {
                    "decisions": int(table_counts_rows.decisions) if table_counts_rows else None,
                    "trades": int(table_counts_rows.trades) if table_counts_rows else None,
                    "ohlcv": int(table_counts_rows.ohlcv) if table_counts_rows else None,
                    "indicators": int(table_counts_rows.indicators) if table_counts_rows else None,
                    "balance_snapshots": int(table_counts_rows.balance_snapshots) if table_counts_rows else None,
                },
                "db_size": db_size_row.db_size if db_size_row else None,
                "db_bytes": int(db_size_row.db_bytes) if db_size_row else None,
            },
        }
    except Exception as e:
        return {
            "ok": False, "db": str(e),
            "kill_switch": None,
            "circuit_breaker": {"triggered": None, "reason": None},
            "engine": {"ok": False, "detail": "error de DB", "last_decision_age_min": None},
            "binance": {"ok": False, "detail": "error de DB", "ws": {"ok": False, "detail": "error de DB"}},
            "llm": None,
            "playbook": None,
            "recent_rejections_1h": None,
            "risk_gate": None,
            "coherence": None,
            "postgres": {"table_counts": None, "db_size": None, "db_bytes": None},
        }


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
