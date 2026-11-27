"""FastAPI application factory."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import Settings
from app.db.session import Database
from app.services.remnawave import RemnawaveClient
from app.web.routes import connect, health, telegram, webhooks


def create_web_app(
    *,
    settings: Settings,
    database: Database,
    redis: Redis | None,
    remnawave: RemnawaveClient,
    bot: Bot,
    dispatcher: Dispatcher,
) -> FastAPI:
    """Create FastAPI app wired with shared bot, database, Redis, and Remnawave state."""
    app = FastAPI(title="RemnaShop Bot API", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.database = database
    app.state.redis = redis
    app.state.remnawave = remnawave
    app.state.bot = bot
    app.state.dispatcher = dispatcher

    app.include_router(health.router)
    app.include_router(connect.router)
    app.include_router(webhooks.router)
    app.include_router(telegram.router)
    return app
