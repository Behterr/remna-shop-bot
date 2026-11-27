"""Support section handler."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import support_kb
from app.bot.utils.formatting import escape_html
from app.config import Settings

router = Router(name="user_support")


@router.callback_query(F.data == "menu:support")
async def cb_support(
    callback: CallbackQuery,
    settings: Settings,
) -> None:
    text = (
        "🆘 <b>Поддержка</b>\n\n"
        f"{escape_html(settings.SUPPORT_TEXT)}\n\n"
        f"Контакт: {escape_html(settings.SUPPORT_URL)}"
    )
    await safe_edit_or_send(callback, text, support_kb(settings.SUPPORT_URL))
