"""Admin authorization filter based on ADMIN_IDS."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings


class IsAdminFilter(BaseFilter):
    """Allow update only when sender ID is present in Settings.ADMIN_IDS."""

    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        user = getattr(event, "from_user", None)
        if user is None and isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        if user is None:
            return False
        return user.id in settings.ADMIN_IDS
