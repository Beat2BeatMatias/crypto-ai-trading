from fastapi import APIRouter

router = APIRouter()

@router.get("/decisions")
async def list_decisions():
    return []
