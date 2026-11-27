"""One-time trial activation handler."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import back_to_menu_kb
from app.bot.utils.formatting import (
    escape_html,
    format_device_limit,
    format_dt_utc,
    format_traffic_limit,
)
from app.config import Settings
from app.db.models import User
from app.services.remnawave import RemnawaveClient, RemnawaveError
from app.services.subscription_service import (
    SubscriptionService,
    TrialAlreadyUsedError,
    TrialDisabledError,
)

logger = logging.getLogger(__name__)
router = Router(name="user_trial")


@router.callback_query(F.data == "menu:trial")
async def cb_activate_trial(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    try:
        sub = await SubscriptionService.activate_trial(
            session=session,
            user=db_user,
            remnawave=remnawave,
            settings=settings,
        )
        await session.commit()
    except (TrialAlreadyUsedError, TrialDisabledError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    except RemnawaveError as exc:
        logger.exception("Remnawave error issuing trial for %s: %s", db_user.telegram_id, exc)
        await session.rollback()
        await callback.answer(
            "Не удалось выдать пробный период из-за временной ошибки VPN-панели. Попробуйте позже.",
            show_alert=True,
        )
        return

    text = (
        "🎁 <b>Пробный период активирован!</b>\n\n"
        f"⏳ <b>Действует до:</b> {format_dt_utc(sub.expires_at)}\n"
        f"📊 <b>Лимит трафика:</b> {format_traffic_limit(sub.traffic_limit_bytes)}\n"
        f"📱 <b>Лимит устройств:</b> {format_device_limit(sub.device_limit)}\n\n"
        f"🔗 <b>Ссылка подписки:</b>\n<code>{escape_html(sub.subscription_url)}</code>"
    )
    kb = back_to_menu_kb(
        extra_buttons=[
            InlineKeyboardButton(text="📱 Перейти к подписке и QR", callback_data="menu:sub"),
        ]
    )
    await safe_edit_or_send(callback, text, kb)
