"""APScheduler wiring for periodic server jobs."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from pulse_server.config import Settings
from pulse_server.logging import get_logger
from pulse_server.services import (
    alert_engine,
    boost_service,
    iperf3_orchestrator,
    rollup_service,
    webhook_dispatcher,
)

log = get_logger(__name__)


def _make_minute_rollup(sessionmaker: async_sessionmaker, settings: Settings):
    async def _job() -> None:
        async with sessionmaker() as db:
            summary = await rollup_service.rollup_minute(db)
            if summary.aggregates_written:
                log.info(
                    "rollup.minute",
                    buckets=summary.buckets_rolled,
                    written=summary.aggregates_written,
                )
        # Alert evaluation runs after rollup in a fresh session so the two tx don't
        # fight for the writer.
        async with sessionmaker() as db:
            eval_summary = await alert_engine.evaluate(db, settings)
            if eval_summary.transitions:
                log.info(
                    "alert.transitions",
                    count=eval_summary.transitions,
                    pairs=eval_summary.pairs_evaluated,
                )

    return _job


def _make_webhook_dispatch(sessionmaker: async_sessionmaker):
    async def _job() -> None:
        async with sessionmaker() as db:
            summary = await webhook_dispatcher.dispatch_due(db)
            if summary.attempted:
                log.info(
                    "webhook.dispatch",
                    attempted=summary.attempted,
                    delivered=summary.delivered,
                    failed=summary.failed,
                    dead=summary.marked_dead,
                )

    return _job


def _make_hour_rollup(sessionmaker: async_sessionmaker):
    async def _job() -> None:
        async with sessionmaker() as db:
            summary = await rollup_service.rollup_hour(db)
            if summary.aggregates_written:
                log.info(
                    "rollup.hour",
                    buckets=summary.buckets_rolled,
                    written=summary.aggregates_written,
                )

    return _job


def _make_iperf_watchdog(sessionmaker: async_sessionmaker):
    async def _job() -> None:
        async with sessionmaker() as db:
            summary = await iperf3_orchestrator.run_watchdog(db)
            if summary.timed_out:
                log.info("iperf.watchdog_timed_out", count=summary.timed_out)

    return _job


def _make_boost_prune(sessionmaker: async_sessionmaker):
    async def _job() -> None:
        async with sessionmaker() as db:
            n = await boost_service.prune_expired(db)
            await db.commit()
            if n:
                log.info("boost.expired", count=n)

    return _job


def _make_prune(sessionmaker: async_sessionmaker, settings: Settings):
    async def _job() -> None:
        async with sessionmaker() as db:
            summary = await rollup_service.prune(db, settings)
            if (
                summary.raw_deleted
                or summary.minute_deleted
                or summary.wireless_deleted
                or summary.scan_deleted
            ):
                log.info(
                    "rollup.prune",
                    raw=summary.raw_deleted,
                    minute=summary.minute_deleted,
                    wireless=summary.wireless_deleted,
                    scan=summary.scan_deleted,
                )

    return _job


def build_scheduler(
    settings: Settings, sessionmaker: async_sessionmaker
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _make_minute_rollup(sessionmaker, settings),
        IntervalTrigger(seconds=60),
        id="rollup_minute",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _make_webhook_dispatch(sessionmaker),
        IntervalTrigger(seconds=10),
        id="webhook_dispatch",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _make_hour_rollup(sessionmaker),
        CronTrigger(minute=2),  # every hour at :02
        id="rollup_hour",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _make_iperf_watchdog(sessionmaker),
        IntervalTrigger(seconds=5),
        id="iperf_watchdog",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _make_boost_prune(sessionmaker),
        IntervalTrigger(seconds=10),
        id="boost_prune",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _make_prune(sessionmaker, settings),
        CronTrigger(minute=15),  # every hour at :15
        id="retention_prune",
        coalesce=True,
        max_instances=1,
    )

    log.info("scheduler.built jobs=%d", len(scheduler.get_jobs()))
    return scheduler
