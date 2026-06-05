"""Coloca SL/TP en Binance (Algo API) para trades abiertos sin order_id_sl/tp."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from config import get_settings
from exchange import build_binance_client
from execution.exchange_adapter import FuturesAdapter
from shared.config_store import ConfigKey, ConfigStore
from shared.db.base import create_engine_from_url, create_session_factory
from shared.db.models import Trade
from shared.schemas import Direction


async def main() -> None:
    engine = create_engine_from_url(get_settings().database_url)
    factory = create_session_factory(engine)
    async with factory() as session:
        store = ConfigStore(session)
        product = await store.get(ConfigKey.TRADING_PRODUCT)
        if product != "futures":
            print("trading_product no es futures; nada que hacer.")
            return
        symbol = "BTC/USDT:USDT"
        adapter = FuturesAdapter(client=build_binance_client(default_type="future"))
        rows = list((await session.execute(
            select(Trade).where(Trade.status == "open"),
        )).scalars().all())
        pending = [
            t for t in rows
            if (t.stop_loss or t.take_profit)
            and (not t.order_id_sl or not t.order_id_tp)
        ]
        if not pending:
            print("No hay trades abiertos sin brackets en exchange.")
            return
        for trade in pending:
            direction = Direction(getattr(trade, "position_side", "LONG") or "LONG")
            sl = float(trade.stop_loss) if trade.stop_loss else None
            tp = float(trade.take_profit) if trade.take_profit else None
            print(f"Trade {trade.id} {direction.value} qty={trade.quantity_btc} SL={sl} TP={tp}")
            brackets = await adapter.place_brackets(
                symbol=symbol,
                direction=direction,
                qty=float(trade.quantity_btc),
                stop_loss=sl,
                take_profit=tp,
            )
            if sl and not trade.order_id_sl and brackets.order_id_sl:
                trade.order_id_sl = brackets.order_id_sl
            if tp and not trade.order_id_tp and brackets.order_id_tp:
                trade.order_id_tp = brackets.order_id_tp
            print(f"  -> SL id={trade.order_id_sl} TP id={trade.order_id_tp}")
        await session.commit()
        print("Listo.")


if __name__ == "__main__":
    asyncio.run(main())
