"""Referral program menu handler."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import back_to_menu_kb
from app.bot.utils.formatting import format_money
from app.config import Settings
from app.db.models import User
from app.services.referral_service import ReferralService
from app.services.settings_service import SettingsService

router = Router(name="user_referrals")


@router.callback_query(F.data == "menu:referrals")
async def cb_referrals(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> None:
    ref_cfg = await SettingsService.get_referral_config(session, settings)
    stats = await ReferralService.get_stats(session, db_user.id)
    me = await bot.get_me()
    bot_username = me.username or settings.BOT_USERNAME or "bot"
    ref_link = f"https://t.me/{bot_username}?start=ref_{db_user.telegram_id}"

    if not ref_cfg.enabled:
        reward_desc = "Реферальная программа временно приостановлена."
    elif ref_cfg.reward_type == "days":
        reward_desc = f"Вы получаете <b>+{ref_cfg.reward_days} дн.</b> к подписке за оплату друга."
    else:
        reward_desc = f"Вы получаете <b>{ref_cfg.reward_percent}%</b> на баланс от оплат приглашённых друзей."

    text = (
        "👥 <b>Реферальная программа</b>\n\n"
        f"{reward_desc}\n\n"
        f"🔗 <b>Ваша ссылка</b> (нажмите, чтобы скопировать):\n"
        f"<code>{ref_link}</code>\n\n"
        f"👤 <b>Приглашено пользователей:</b> {stats.invited_count}\n"
        f"💰 <b>Заработано на баланс:</b> {format_money(stats.total_bonus_rub)}\n"
        f"📅 <b>Получено бонусных дней:</b> {stats.total_bonus_days} дн."
    )
    await safe_edit_or_send(callback, text, back_to_menu_kb())
