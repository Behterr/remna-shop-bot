"""APScheduler AsyncIOScheduler configuration."""

from __future__ import annotations

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.db.session import Database
from app.services.remnawave import RemnawaveClient
from app.tasks.jobs import (
    cleanup_expired_users,
    process_expiry_and_autorenew,
    sync_traffic_and_notify,
)


def create_scheduler(
    *,
    settings: Settings,
    database: Database,
    remnawave: RemnawaveClient,
    bot: Bot,
) -> AsyncIOScheduler:
    """Configure and register periodic background jobs."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        process_expiry_and_autorenew,
        trigger="interval",
        minutes=max(1, settings.SCHEDULER_EXPIRY_INTERVAL_MINUTES),
        kwargs={
            "database": database,
            "remnawave": remnawave,
            "bot": bot,
            "settings": settings,
        },
        id="expiry_and_autorenew",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        sync_traffic_and_notify,
        trigger="interval",
        minutes=max(1, settings.SCHEDULER_TRAFFIC_INTERVAL_MINUTES),
        kwargs={
            "database": database,
            "remnawave": remnawave,
            "bot": bot,
        },
        id="traffic_sync",
        max_instances=1,
        coalesce=True,
    )

    if settings.ENABLE_EXPIRED_CLEANUP:
        scheduler.add_job(
            cleanup_expired_users,
            trigger="interval",
            hours=24,
            kwargs={
                "database": database,
                "remnawave": remnawave,
                "settings": settings,
            },
            id="expired_cleanup",
            max_instances=1,
            coalesce=True,
        )

    return scheduler
