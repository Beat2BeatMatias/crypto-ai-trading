from fastapi import APIRouter

router = APIRouter()

@router.get("/config")
async def list_config():
    return []

@router.put("/config/{key}")
async def update_config(key: str):
    return {"ok": True, "key": key}
