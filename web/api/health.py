from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()

@router.get("/health")
async def health(request: Request) -> dict:
    factory = request.app.state.session_factory
    try:
        async with factory() as s:
            await s.execute(text("SELECT 1"))
        return {"ok": True, "db": "up"}
    except Exception as e:
        return {"ok": False, "db": str(e)}

@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
