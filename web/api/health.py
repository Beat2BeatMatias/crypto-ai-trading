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

            row = (await s.execute(
                text("SELECT ts FROM decisions ORDER BY ts DESC LIMIT 1")
            )).first()
            if row:
                age_min = (datetime.now(timezone.utc) - row.ts).total_seconds() / 60
                engine_ok = age_min < 15
                engine_detail = f"última decisión hace {int(age_min)}m"
            else:
                engine_ok = False
                engine_detail = "sin decisiones aún"

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

        return {
            "ok": True, "db": "up",
            "engine": {"ok": engine_ok, "detail": engine_detail},
            "binance": {"ok": binance_ok, "detail": binance_detail},
        }
    except Exception as e:
        return {
            "ok": False, "db": str(e),
            "engine": {"ok": False, "detail": "error de DB"},
            "binance": {"ok": False, "detail": "error de DB"},
        }

@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
