"""Unified application entrypoint: Telegram Bot (polling/webhook) + FastAPI + APScheduler."""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from redis.asyncio import Redis

from app.bot.dispatcher import create_bot, create_dispatcher
from app.config import get_settings
from app.db.session import init_database
from app.logging_config import setup_logging
from app.services.remnawave import RemnawaveClient
from app.tasks.scheduler import create_scheduler
from app.web.app import create_web_app

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    database = init_database(settings.DATABASE_URL)
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=False)
    remnawave = RemnawaveClient(
        base_url=settings.REMNAWAVE_URL,
        api_token=settings.REMNAWAVE_API_TOKEN.get_secret_value(),
        timeout=settings.REMNAWAVE_API_TIMEOUT,
        max_retries=settings.REMNAWAVE_API_RETRIES,
    )
    bot = create_bot(settings)
    dp = create_dispatcher(
        settings=settings,
        database=database,
        remnawave=remnawave,
        redis=redis,
    )

    try:
        me = await bot.get_me()
        if me.username and not settings.BOT_USERNAME:
            settings.BOT_USERNAME = me.username
        logger.info("Authorized Telegram bot @%s (id=%s)", me.username, me.id)
    except Exception as exc:
        logger.warning("Could not fetch bot profile on startup: %s", exc)

    web_app = create_web_app(
        settings=settings,
        database=database,
        redis=redis,
        remnawave=remnawave,
        bot=bot,
        dispatcher=dp,
    )
    uvicorn_config = uvicorn.Config(
        app=web_app,
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        access_log=False,
    )
    web_server = uvicorn.Server(uvicorn_config)

    scheduler = create_scheduler(
        settings=settings,
        database=database,
        remnawave=remnawave,
        bot=bot,
    )
    scheduler.start()
    logger.info("APScheduler started with %d job(s)", len(scheduler.get_jobs()))

    try:
        if settings.BOT_MODE == "webhook" and settings.telegram_webhook_url:
            await bot.set_webhook(
                url=settings.telegram_webhook_url,
                secret_token=settings.BOT_WEBHOOK_SECRET or None,
                allowed_updates=dp.resolve_used_update_types(),
            )
            logger.info("Running in WEBHOOK mode at %s", settings.telegram_webhook_url)
            await web_server.serve()
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            logger.info("Running in POLLING mode + FastAPI on %s:%s", settings.WEB_HOST, settings.WEB_PORT)
            polling_task = asyncio.create_task(
                dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
                name="aiogram_polling",
            )
            web_task = asyncio.create_task(web_server.serve(), name="uvicorn_web")
            done, pending = await asyncio.wait(
                {polling_task, web_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
    finally:
        logger.info("Shutting down services...")
        scheduler.shutdown(wait=False)
        await dp.storage.close()
        await bot.session.close()
        await remnawave.aclose()
        await redis.aclose()
        await database.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
