"""Database session middleware injecting AsyncSession per Telegram update."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.session import Database


class DbSessionMiddleware(BaseMiddleware):
    """Provide an AsyncSession in handler data and commit/rollback around the update."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.database.session() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                if session.in_transaction():
                    await session.commit()
                return result
            except Exception:
                if session.in_transaction():
                    await session.rollback()
                raise
