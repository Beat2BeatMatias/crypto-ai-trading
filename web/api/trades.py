from fastapi import APIRouter

router = APIRouter()

@router.get("/trades")
async def list_trades():
    return []
