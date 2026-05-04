from fastapi import APIRouter

router = APIRouter()

@router.get("/playbook/active")
async def playbook_active():
    return None

@router.get("/playbook/history")
async def playbook_history():
    return []

@router.post("/playbook/{version}/activate")
async def playbook_activate(version: int):
    return {"ok": True, "version": version}
