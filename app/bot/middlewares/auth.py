"""Auto-registration, referral binding, and ban enforcement middleware."""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db.models import User
from app.services.referral_service import ReferralService

logger = logging.getLogger(__name__)


def _extract_referral_id(text: str | None) -> int | None:
    if not text or not text.startswith("/start"):
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    if arg.startswith("ref_"):
        raw_id = arg[4:].strip()
        if raw_id.isdigit():
            return int(raw_id)
    return None


class AuthAndBanMiddleware(BaseMiddleware):
    """Ensure every interacting Telegram user exists in PostgreSQL and is not banned."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user") or getattr(event, "from_user", None)
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        session: AsyncSession = data["session"]
        settings: Settings = data["settings"]

        stmt = (
            select(User)
            .where(User.telegram_id == tg_user.id)
            .options(selectinload(User.subscription), selectinload(User.pending_promo))
        )
        db_user = (await session.execute(stmt)).scalar_one_or_none()
        full_name = tg_user.full_name or str(tg_user.id)

        if db_user is None:
            db_user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                full_name=full_name[:255],
            )
            session.add(db_user)
            await session.flush()

            if isinstance(event, Message):
                ref_tg_id = _extract_referral_id(event.text)
                if ref_tg_id is not None:
                    await ReferralService.bind_referrer(session, db_user, ref_tg_id)

            # Reload relationships cleanly
            db_user = (
                await session.execute(
                    select(User)
                    .where(User.id == db_user.id)
                    .options(selectinload(User.subscription), selectinload(User.pending_promo))
                )
            ).scalar_one()
        else:
            changed = False
            if db_user.username != tg_user.username:
                db_user.username = tg_user.username
                changed = True
            if db_user.full_name != full_name[:255]:
                db_user.full_name = full_name[:255]
                changed = True
            if changed:
                await session.flush()

        data["db_user"] = db_user

        if db_user.is_banned and tg_user.id not in settings.ADMIN_IDS:
            if isinstance(event, PreCheckoutQuery):
                await event.answer(ok=False, error_message="Ваш аккаунт заблокирован.")
                return None
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Ваш аккаунт заблокирован.", show_alert=True)
                return None
            if isinstance(event, Message):
                await event.answer("⛔ Ваш аккаунт заблокирован. Обратитесь в поддержку.")
                return None
            return None

        return await handler(event, data)
