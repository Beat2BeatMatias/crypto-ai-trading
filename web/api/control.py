from fastapi import APIRouter

router = APIRouter()

@router.post("/kill-switch")
async def toggle_kill_switch():
    return {"ok": True}

@router.post("/mode")
async def set_mode():
    return {"ok": True}
