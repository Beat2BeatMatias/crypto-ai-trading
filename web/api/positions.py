from fastapi import APIRouter

router = APIRouter()

@router.get("/positions")
async def list_positions():
    return []
