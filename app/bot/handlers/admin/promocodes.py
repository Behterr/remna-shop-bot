"""Admin promo code management handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.admin import admin_promo_type_kb, admin_promos_kb
from app.bot.states.admin import AdminPromoStates
from app.db.models import PromoCode, PromoType

router = Router(name="admin_promocodes")


async def _load_promos(session: AsyncSession) -> list[PromoCode]:
    return list(
        (await session.execute(select(PromoCode).order_by(PromoCode.id.desc()).limit(30))).scalars().all()
    )


@router.callback_query(F.data == "adm:promos")
async def cb_admin_promos(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await state.clear()
    promos = await _load_promos(session)
    await safe_edit_or_send(
        callback,
        "🎟 <b>Промокоды</b>\n\nНажмите на промокод для переключения активности или создайте новый:",
        admin_promos_kb(promos),
    )


@router.callback_query(F.data == "adm:p_new")
async def cb_promo_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_or_send(
        callback,
        "➕ <b>Создание промокода</b>\n\nВыберите тип промокода:",
        admin_promo_type_kb(),
    )


@router.callback_query(F.data.startswith("adm:p_type:"))
async def cb_promo_type(callback: CallbackQuery, state: FSMContext) -> None:
    raw_type = (callback.data or "").split(":")[-1]
    if raw_type not in {t.value for t in PromoType}:
        await callback.answer("Неизвестный тип.", show_alert=True)
        return
    await state.update_data(promo_type=raw_type)
    await state.set_state(AdminPromoStates.waiting_code)
    await safe_edit_or_send(
        callback,
        "Введите код промокода латиницей/цифрами (например <code>WELCOME20</code>):",
        admin_promo_type_kb(),
    )


@router.message(AdminPromoStates.waiting_code)
async def msg_promo_code(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    code = (message.text or "").strip().upper()
    if len(code) < 2 or len(code) > 64:
        await message.answer("⚠️ Длина кода должна быть от 2 до 64 символов:")
        return

    exists = (
        await session.execute(select(PromoCode).where(PromoCode.code == code))
    ).scalar_one_or_none()
    if exists is not None:
        await message.answer("⚠️ Такой промокод уже существует. Введите другой:")
        return

    data = await state.get_data()
    ptype = PromoType(data["promo_type"])
    await state.update_data(code=code)
    await state.set_state(AdminPromoStates.waiting_value)

    hint = {
        PromoType.DISCOUNT_PERCENT: "Введите процент скидки (от 1 до 99):",
        PromoType.BALANCE_BONUS: "Введите сумму бонуса на баланс в рублях:",
        PromoType.BONUS_DAYS: "Введите количество бонусных дней:",
    }[ptype]
    await message.answer(hint)


@router.message(AdminPromoStates.waiting_value)
async def msg_promo_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    data = await state.get_data()
    ptype = PromoType(data["promo_type"])
    try:
        val = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer("⚠️ Введите корректное число:")
        return

    if val <= 0:
        await message.answer("⚠️ Значение должно быть больше 0:")
        return
    if ptype == PromoType.DISCOUNT_PERCENT and val > 99:
        await message.answer("⚠️ Скидка не может превышать 99%:")
        return

    await state.update_data(value=str(val))
    await state.set_state(AdminPromoStates.waiting_max_uses)
    await message.answer("Введите лимит активаций (<code>0</code> — без ограничений):")


@router.message(AdminPromoStates.waiting_max_uses)
async def msg_promo_max_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Введите целое число (0 = безлимит):")
        return
    val = int(raw)
    await state.update_data(max_uses=val if val > 0 else None)
    await state.set_state(AdminPromoStates.waiting_expiry_days)
    await message.answer("Введите срок действия в днях (<code>0</code> — бессрочный):")


@router.message(AdminPromoStates.waiting_expiry_days)
async def msg_promo_expiry(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Введите целое число дней (0 = бессрочный):")
        return

    days = int(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(days=days) if days > 0 else None
    data = await state.get_data()

    promo = PromoCode(
        code=data["code"],
        promo_type=PromoType(data["promo_type"]),
        value=Decimal(data["value"]),
        max_uses=data["max_uses"],
        expires_at=expires_at,
        is_active=True,
    )
    session.add(promo)
    await session.commit()
    await state.clear()

    promos = await _load_promos(session)
    await message.answer(
        f"✅ Промокод <b>{promo.code}</b> создан!",
        reply_markup=admin_promos_kb(promos),
    )


@router.callback_query(F.data.startswith("adm:p:"))
async def cb_promo_toggle(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректный промокод.", show_alert=True)
        return

    promo = await session.get(PromoCode, int(parts[2]))
    if promo is None:
        await callback.answer("Промокод не найден.", show_alert=True)
        return

    promo.is_active = not promo.is_active
    await session.commit()
    promos = await _load_promos(session)
    await safe_edit_or_send(
        callback,
        "🎟 <b>Промокоды</b>\n\nНажмите на промокод для переключения активности:",
        admin_promos_kb(promos),
    )
