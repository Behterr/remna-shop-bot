"""Redis-backed throttling middleware to protect bot handlers from flood."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Rate-limit user messages and callback queries using Redis NX keys."""

    def __init__(self, redis: Redis | None, rate_limit_seconds: float = 0.6) -> None:
        self.redis = redis
        self.rate_limit_ms = max(0, int(rate_limit_seconds * 1000))
        self._fallback_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self.rate_limit_ms <= 0 or isinstance(event, PreCheckoutQuery):
            return await handler(event, data)

        # Never throttle successful payment notifications from Telegram
        if isinstance(event, Message) and event.successful_payment is not None:
            return await handler(event, data)

        tg_user = data.get("event_from_user") or getattr(event, "from_user", None)
        if tg_user is None:
            return await handler(event, data)

        allowed = True
        if self.redis is not None:
            key = f"throttle:{tg_user.id}"
            try:
                created = await self.redis.set(key, "1", px=self.rate_limit_ms, nx=True)
                allowed = bool(created)
            except Exception as exc:
                logger.warning("Redis throttling fallback triggered: %s", exc)
                allowed = self._check_memory(tg_user.id)
        else:
            allowed = self._check_memory(tg_user.id)

        if not allowed:
            if isinstance(event, CallbackQuery):
                await event.answer("⏳ Слишком частые запросы, подождите секунду.", show_alert=False)
            return None

        return await handler(event, data)

    def _check_memory(self, user_id: int) -> bool:
        now = time.monotonic()
        last = self._fallback_seen.get(user_id, 0.0)
        if (now - last) * 1000 < self.rate_limit_ms:
            return False
        self._fallback_seen[user_id] = now
        return True
