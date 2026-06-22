from __future__ import annotations
from typing import Any, Awaitable, Callable
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger()

class EngineScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._decisor_interval_min: int | None = None

    def add_decisor(self, fn: Callable[[], Awaitable[None]], *, interval_min: int) -> None:
        self._decisor_interval_min = interval_min
        self._scheduler.add_job(fn, IntervalTrigger(minutes=interval_min),
                                id="decisor", replace_existing=True)

    def update_decisor_interval(self, interval_min: int) -> bool:
        if self._decisor_interval_min == interval_min:
            return False
        self._decisor_interval_min = interval_min
        self._scheduler.reschedule_job(
            "decisor",
            trigger=IntervalTrigger(minutes=interval_min),
        )
        logger.info("scheduler.decisor_interval_updated", interval_min=interval_min)
        return True

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

    def add_balance_refresh(self, fn: Callable[[], Awaitable[None]], *, seconds: int = 60) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(seconds=seconds),
                                id="balance_refresh", replace_existing=True)

    def add_outcome_attribution(self, fn: Callable[[], Awaitable[None]], *, interval_min: int = 60) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(minutes=interval_min),
                                id="outcome_attribution", replace_existing=True)

    def add_calibration(self, fn: Callable[[], Awaitable[None]], *, hours: int = 6) -> None:
        self._scheduler.add_job(fn, IntervalTrigger(hours=hours),
                                id="calibration", replace_existing=True)

    def get_job(self, job_id: str) -> Any | None:
        return self._scheduler.get_job(job_id)

    add_listener = lambda self, fn, mask: self._scheduler.add_listener(fn, mask)

    def start(self) -> None:
        self._scheduler.start()
        logger.info("scheduler.started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")
