"""User promo code redemption handler."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import back_to_menu_kb
from app.bot.states.user import PromoStates
from app.config import Settings
from app.db.models import User
from app.services.promocode_service import PromoCodeService, PromoError
from app.services.remnawave import RemnawaveClient, RemnawaveError

logger = logging.getLogger(__name__)
router = Router(name="user_promocode")


@router.callback_query(F.data == "menu:promo")
async def cb_promo_prompt(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.set_state(PromoStates.waiting_code)
    await safe_edit_or_send(
        callback,
        "🎟 <b>Активация промокода</b>\n\nОтправьте промокод в чат:",
        back_to_menu_kb(),
    )


@router.message(PromoStates.waiting_code)
async def msg_promo_code(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    code = (message.text or "").strip()
    try:
        result = await PromoCodeService.activate(
            session=session,
            user=db_user,
            raw_code=code,
            remnawave=remnawave,
            settings=settings,
        )
        await session.commit()
    except PromoError as exc:
        await message.answer(
            f"❌ {exc}\n\nПопробуйте ввести другой промокод или вернитесь в меню:",
            reply_markup=back_to_menu_kb(),
        )
        return
    except RemnawaveError as exc:
        logger.exception("Remnawave error activating bonus-days promo: %s", exc)
        await session.rollback()
        await message.answer(
            "⚠️ Не удалось начислить бонусные дни из-за временной ошибки VPN-панели.",
            reply_markup=back_to_menu_kb(),
        )
        return

    await state.clear()
    await message.answer(f"✅ {result.message}", reply_markup=back_to_menu_kb())
