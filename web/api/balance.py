from fastapi import APIRouter

router = APIRouter()

@router.get("/balance")
async def get_balance():
    return {"btc_held": 0, "open_positions": 0, "realized_pnl_today": 0}
