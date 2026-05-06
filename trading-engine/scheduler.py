from __future__ import annotations
from typing import Awaitable, Callable
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger()

class EngineScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def add_decisor(self, fn: Callable[[], Awaitable[None]], *, interval_min: int) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(minutes=interval_min),
                                id="decisor", replace_existing=True)

    def add_supervisor(self, fn: Callable[[], Awaitable[None]], *, cron: str) -> None:
        self._scheduler.add_job(fn, CronTrigger.from_crontab(cron, timezone="UTC"),
                                id="supervisor", replace_existing=True)

    def add_fee_refresh(self, fn: Callable[[], Awaitable[None]], *, hours: int = 24) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(hours=hours), id="fees", replace_existing=True)

    def add_position_refresh(self, fn: Callable[[], Awaitable[None]], *, seconds: int = 30) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(seconds=seconds),
                                id="positions", replace_existing=True)

    def add_order_tracker(self, fn: Callable[[], Awaitable[None]], *, seconds: int = 30) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(seconds=seconds),
                                id="order_tracker", replace_existing=True)

    def start(self) -> None:
        self._scheduler.start()
        logger.info("scheduler.started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")
