"""Diagnóstico de config futuros vs runtime spot para /api/health."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from binance_futures_balance import fetch_futures_margin_balance
from shared.runtime_mismatch import (
    RuntimeMismatchReason,
    classify_downgrade_reason,
    diagnose_from_live_margin,
    mismatch_detail_es,
)

_DEFAULT_MIN_NOTIONAL = 100.0


async def _config_float(session: AsyncSession, key: str, default: float) -> float:
    row = (await session.execute(
        text("SELECT value FROM config WHERE key = :k"),
        {"k": key},
    )).first()
    if not row or row.value is None:
        return default
    return float(row.value)


async def diagnose_futures_runtime_mismatch(
    session: AsyncSession,
) -> tuple[str, str] | tuple[None, None]:
    """
    Returns (reason_code, detail_es) when config=futures but effective=spot, else (None, None).
    """
    tp_row = (await session.execute(
        text("SELECT value FROM config WHERE key = 'trading_product'"),
    )).first()
    trading_product = tp_row.value if tp_row else "spot"
    if trading_product != "futures":
        return None, None

    from shared.db.models import BalanceSnapshot
    from sqlalchemy import desc, select

    snap = (await session.execute(
        select(BalanceSnapshot).order_by(desc(BalanceSnapshot.ts)).limit(1),
    )).scalar_one_or_none()
    if snap and snap.margin_balance is not None:
        return None, None

    dr_row = (await session.execute(
        text("SELECT value FROM config WHERE key = 'futures_runtime_downgrade_reason'"),
    )).first()
    stored = (dr_row.value if dr_row else "") or ""

    classified = classify_downgrade_reason(stored)
    if classified is not None:
        return classified.value, mismatch_detail_es(classified, stored_reason=stored)

    live = await fetch_futures_margin_balance()
    if live is None:
        return (
            RuntimeMismatchReason.API_PERMISSIONS.value,
            mismatch_detail_es(RuntimeMismatchReason.API_PERMISSIONS),
        )

    max_pos = await _config_float(session, "max_position_pct", 0.10)
    max_lev_row = (await session.execute(
        text("SELECT value FROM config WHERE key = 'max_leverage'"),
    )).first()
    max_lev = int(float(max_lev_row.value)) if max_lev_row else 1
    available = float(live["available_margin"])
    max_trade = available * max_pos * max_lev

    reason = diagnose_from_live_margin(
        available_margin=available,
        max_position_pct=max_pos,
        max_leverage=max_lev,
        min_notional_estimate=_DEFAULT_MIN_NOTIONAL,
    )
    return reason.value, mismatch_detail_es(
        reason,
        available_margin=available,
        max_trade_notional=max_trade,
    )
