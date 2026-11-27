"""Start command and main menu rendering."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.user import main_menu_kb
from app.bot.utils.formatting import (
    escape_html,
    format_money,
    pluralize_days,
    remaining_days,
)
from app.config import Settings
from app.db.models import SubscriptionStatus, User
from app.services.settings_service import SettingsService

router = Router(name="user_start")


def _status_label(user: User) -> tuple[str, str]:
    sub = user.subscription
    if sub is None:
        return "⚪ Не оформлена", "—"

    now = datetime.now(timezone.utc)
    days_left = remaining_days(sub.expires_at, now)
    if sub.status == SubscriptionStatus.DISABLED:
        return "🔴 Отключена", "—"
    if sub.status == SubscriptionStatus.LIMITED:
        return "🟠 Исчерпан трафик", pluralize_days(days_left)
    if sub.status == SubscriptionStatus.EXPIRED or days_left <= 0:
        return "⌛ Истекла", "0 дней"
    prefix = "🎁 Пробная (активна)" if sub.is_trial else "🟢 Активна"
    return prefix, pluralize_days(days_left)


async def build_main_menu(
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> tuple[str, object]:
    trial_cfg = await SettingsService.get_trial_config(session, settings)
    show_trial = trial_cfg.enabled and not db_user.trial_used
    status_text, days_text = _status_label(db_user)

    promo_line = ""
    if db_user.pending_promo is not None and db_user.pending_promo.is_active:
        promo_line = (
            f"\n🏷 <b>Активная скидка:</b> {int(db_user.pending_promo.value)}% "
            f"(промокод <code>{escape_html(db_user.pending_promo.code)}</code>)"
        )

    text = (
        f"👋 Добро пожаловать, <b>{escape_html(db_user.full_name)}</b>!\n\n"
        f"🛡 <b>Статус подписки:</b> {status_text}\n"
        f"⏳ <b>Осталось дней:</b> {days_text}\n"
        f"💰 <b>Баланс:</b> {format_money(db_user.balance)}"
        f"{promo_line}\n\n"
        "Выберите нужный раздел ниже:"
    )
    kb = main_menu_kb(
        show_trial=show_trial,
        is_admin=db_user.telegram_id in settings.ADMIN_IDS,
    )
    return text, kb


async def safe_edit_or_send(
    target: Message | CallbackQuery,
    text: str,
    reply_markup: object,
) -> None:
    """Safely update an existing text message or send a fresh one."""
    if isinstance(target, Message):
        await target.answer(text, reply_markup=reply_markup)
        return

    msg = target.message
    if msg is None:
        await target.answer()
        return
    if msg.photo or msg.document:
        await msg.answer(text, reply_markup=reply_markup)
        await target.answer()
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await msg.answer(text, reply_markup=reply_markup)
    await target.answer()


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> None:
    await state.clear()
    text, kb = await build_main_menu(session, db_user, settings)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "menu:home")
async def cb_main_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> None:
    await state.clear()
    text, kb = await build_main_menu(session, db_user, settings)
    await safe_edit_or_send(callback, text, kb)
