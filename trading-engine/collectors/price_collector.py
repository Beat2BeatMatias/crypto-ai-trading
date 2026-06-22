"""PriceCollector: fetches OHLCV via CCXT and persists with indicators to Postgres."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import Ohlcv, Indicators
from collectors.indicators import compute_indicators

logger = structlog.get_logger()

TIMEFRAMES_DEFAULT = ("1m", "5m", "15m", "1h", "4h")
LIMIT_DEFAULT = 250


class PriceCollector:
    def __init__(
        self,
        exchange: Any,
        session: AsyncSession,
        *,
        symbol: str,
        market: str = "spot",
        timeframes: tuple[str, ...] = TIMEFRAMES_DEFAULT,
        limit: int = LIMIT_DEFAULT,
    ):
        self.exchange = exchange
        self.session = session
        self.symbol = symbol
        self.market = market if market in ("spot", "futures") else "spot"
        self.timeframes = timeframes
        self.limit = limit

    async def _detect_dialect(self) -> str:
        """Detect DB dialect from the active async connection."""
        try:
            conn = await self.session.connection()
            return conn.dialect.name
        except Exception:
            return "postgresql"

    async def fetch_and_persist(self, *, timeframe: str) -> int:
        """Fetch latest candles for *timeframe* and upsert. Returns count written."""
        raw = await self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=self.limit)
        if not raw:
            return 0

        dialect = await self._detect_dialect()

        for candle in raw:
            ts_ms, open_, high, low, close, volume = candle
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as _insert
                stmt = _insert(Ohlcv).values(
                    time=ts,
                    timeframe=timeframe,
                    market=self.market,
                    open=Decimal(str(open_)),
                    high=Decimal(str(high)),
                    low=Decimal(str(low)),
                    close=Decimal(str(close)),
                    volume=Decimal(str(volume)),
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["time", "timeframe", "market"],
                )
            else:
                from sqlalchemy.dialects.postgresql import insert as _insert  # noqa: F811
                stmt = _insert(Ohlcv).values(
                    time=ts,
                    timeframe=timeframe,
                    market=self.market,
                    open=Decimal(str(open_)),
                    high=Decimal(str(high)),
                    low=Decimal(str(low)),
                    close=Decimal(str(close)),
                    volume=Decimal(str(volume)),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["time", "timeframe", "market"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                    },
                )
            await self.session.execute(stmt)

        await self.session.commit()
        logger.info(
            "ohlcv.persisted",
            symbol=self.symbol,
            market=self.market,
            timeframe=timeframe,
            n=len(raw),
        )
        return len(raw)

    async def compute_and_persist_indicators(self) -> None:
        """Compute indicators for all timeframes and persist one snapshot row."""
        from sqlalchemy import select

        all_indicators: dict[str, dict[str, Any]] = {}
        for tf in self.timeframes:
            rows = (
                await self.session.execute(
                    select(Ohlcv)
                    .where(Ohlcv.timeframe == tf, Ohlcv.market == self.market)
                    .order_by(Ohlcv.time.desc())
                    .limit(self.limit)
                )
            ).scalars().all()
            rows.reverse()
            if not rows:
                continue
            df = pd.DataFrame(
                {
                    "open": [float(r.open) for r in rows],
                    "high": [float(r.high) for r in rows],
                    "low": [float(r.low) for r in rows],
                    "close": [float(r.close) for r in rows],
                    "volume": [float(r.volume) for r in rows],
                },
                index=pd.DatetimeIndex([r.time for r in rows], tz="UTC"),
            )
            all_indicators[tf] = compute_indicators(df, timeframe=tf)

        now = datetime.now(tz=timezone.utc).replace(microsecond=0)
        dialect = await self._detect_dialect()

        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as _insert
            stmt = _insert(Indicators).values(time=now, data=all_indicators)
            stmt = stmt.on_conflict_do_update(
                index_elements=["time"],
                set_={"data": stmt.excluded.data},
            )
        else:
            from sqlalchemy.dialects.postgresql import insert as _insert  # noqa: F811
            stmt = _insert(Indicators).values(time=now, data=all_indicators)
            stmt = stmt.on_conflict_do_update(
                index_elements=["time"],
                set_={"data": stmt.excluded.data},
            )

        await self.session.execute(stmt)
        await self.session.commit()
        logger.info(
            "indicators.persisted",
            time=now.isoformat(),
            tfs=list(all_indicators.keys()),
        )
