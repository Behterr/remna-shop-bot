"""Aiogram Bot and Dispatcher factory with Redis FSM storage and middlewares."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.bot.handlers.admin import admin_router
from app.bot.handlers.user import user_router
from app.bot.middlewares.auth import AuthAndBanMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.throttling import ThrottlingMiddleware
from app.config import Settings
from app.db.session import Database
from app.services.remnawave import RemnawaveClient


def create_bot(settings: Settings) -> Bot:
    """Create configured aiogram Bot instance with HTML parse mode."""
    return Bot(
        token=settings.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    *,
    settings: Settings,
    database: Database,
    remnawave: RemnawaveClient,
    redis: Redis | None = None,
) -> Dispatcher:
    """Assemble aiogram Dispatcher with RedisStorage, middlewares, and routers."""
    storage = RedisStorage(redis=redis) if redis is not None else MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp["settings"] = settings
    dp["database"] = database
    dp["remnawave"] = remnawave

    throttler = ThrottlingMiddleware(redis=redis, rate_limit_seconds=settings.THROTTLE_RATE_LIMIT)
    db_mw = DbSessionMiddleware(database=database)
    auth_mw = AuthAndBanMiddleware()

    dp.update.outer_middleware(throttler)
    dp.update.middleware(db_mw)
    dp.update.middleware(auth_mw)

    dp.include_router(admin_router)
    dp.include_router(user_router)
    return dp
