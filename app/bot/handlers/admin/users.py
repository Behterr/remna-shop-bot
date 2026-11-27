"""Admin user search, balance adjustments, subscription grant/revoke, traffic reset, ban."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.admin import admin_user_card_kb, admin_users_menu_kb
from app.bot.states.admin import AdminUserStates
from app.bot.utils.formatting import (
    escape_html,
    format_bytes,
    format_dt_utc,
    format_money,
    format_traffic_limit,
)
from app.config import Settings
from app.db.models import (
    PaymentProviderType,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
)
from app.services.remnawave import RemnawaveClient, RemnawaveError
from app.services.subscription_service import SubscriptionError, SubscriptionService

logger = logging.getLogger(__name__)
router = Router(name="admin_users")


async def _load_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return (
        await session.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.subscription))
        )
    ).scalar_one_or_none()


async def _render_user_card(session: AsyncSession, target: User) -> tuple[str, object]:
    ref_count = int(
        (
            await session.execute(
                select(func.count(User.id)).where(User.referred_by_id == target.id)
            )
        ).scalar_one()
        or 0
    )
    sub = target.subscription
    if sub is not None:
        sub_info = (
            f"🟢 {sub.status.value} до {format_dt_utc(sub.expires_at)}\n"
            f"📊 Трафик: {format_bytes(sub.used_traffic_bytes)} / "
            f"{format_traffic_limit(sub.traffic_limit_bytes)}\n"
            f"🔑 Remnawave UUID: <code>{escape_html(sub.remnawave_uuid)}</code>"
        )
    else:
        sub_info = "⚪ Нет подписки"

    uname = f"@{escape_html(target.username)}" if target.username else "—"
    ban_str = "🔴 Забанен" if target.is_banned else "🟢 Активен"
    text = (
        f"👤 <b>Профиль пользователя #{target.id}</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{target.telegram_id}</code>\n"
        f"👤 <b>Username:</b> {uname}\n"
        f"📛 <b>Имя:</b> {escape_html(target.full_name)}\n"
        f"🛡 <b>Аккаунт:</b> {ban_str}\n"
        f"💰 <b>Баланс:</b> {format_money(target.balance)}\n"
        f"🎁 <b>Триал:</b> {'Использован' if target.trial_used else 'Доступен'}\n"
        f"👥 <b>Рефералов:</b> {ref_count}\n\n"
        f"📱 <b>Подписка:</b>\n{sub_info}"
    )
    return text, admin_user_card_kb(target, sub)


@router.callback_query(F.data == "adm:users")
async def cb_admin_users(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_or_send(
        callback,
        "👤 <b>Управление пользователями</b>\n\nНажмите поиск и введите Telegram ID или @username:",
        admin_users_menu_kb(),
    )


@router.callback_query(F.data == "adm:user_search")
async def cb_admin_user_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserStates.waiting_search_query)
    await safe_edit_or_send(
        callback,
        "🔍 Введите <b>Telegram ID</b> (например <code>123456789</code>) или <b>@username</b>:",
        admin_users_menu_kb(),
    )


@router.message(AdminUserStates.waiting_search_query)
async def msg_admin_user_search(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите Telegram ID или @username:")
        return

    user: User | None = None
    if query.isdigit():
        user = (
            await session.execute(
                select(User)
                .where(User.telegram_id == int(query))
                .options(selectinload(User.subscription))
            )
        ).scalar_one_or_none()
    else:
        clean_uname = query.lstrip("@").lower()
        user = (
            await session.execute(
                select(User)
                .where(func.lower(User.username) == clean_uname)
                .options(selectinload(User.subscription))
            )
        ).scalar_one_or_none()

    if user is None:
        await message.answer("❌ Пользователь не найден. Попробуйте другой ID или @username:")
        return

    await state.clear()
    text, kb = await _render_user_card(session, user)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("adm:u:"))
async def cb_admin_user_action(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    remnawave: RemnawaveClient,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректный запрос.", show_alert=True)
        return

    target_id = int(parts[2])
    action = parts[3]
    target = await _load_user_by_id(session, target_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    if action == "bal":
        await state.update_data(target_user_id=target.id)
        await state.set_state(AdminUserStates.waiting_balance_delta)
        await safe_edit_or_send(
            callback,
            (
                f"💰 Изменение баланса для <code>{target.telegram_id}</code> "
                f"(сейчас: {format_money(target.balance)})\n\n"
                "Отправьте сумму изменения, например <code>+250</code> или <code>-100</code>:"
            ),
            admin_users_menu_kb(),
        )
        return

    if action == "grant":
        await state.update_data(target_user_id=target.id)
        await state.set_state(AdminUserStates.waiting_grant_days)
        await safe_edit_or_send(
            callback,
            (
                f"📅 Выдача/продление подписки для <code>{target.telegram_id}</code>\n\n"
                "Отправьте количество дней (целое число, например <code>30</code>):"
            ),
            admin_users_menu_kb(),
        )
        return

    if action == "revoke":
        try:
            await SubscriptionService.revoke_subscription(session, target, remnawave)
            await session.commit()
        except RemnawaveError as exc:
            await session.rollback()
            await callback.answer(f"Ошибка Remnawave: {exc}", show_alert=True)
            return
        target = await _load_user_by_id(session, target_id)
        if target:
            text, kb = await _render_user_card(session, target)
            await safe_edit_or_send(callback, text, kb)
        return

    if action == "enable":
        try:
            await SubscriptionService.enable_subscription(session, target, remnawave)
            await session.commit()
        except RemnawaveError as exc:
            await session.rollback()
            await callback.answer(f"Ошибка Remnawave: {exc}", show_alert=True)
            return
        target = await _load_user_by_id(session, target_id)
        if target:
            text, kb = await _render_user_card(session, target)
            await safe_edit_or_send(callback, text, kb)
        return

    if action == "reset_tr":
        try:
            await SubscriptionService.reset_user_traffic(session, target, remnawave)
            await session.commit()
        except (SubscriptionError, RemnawaveError) as exc:
            await session.rollback()
            await callback.answer(f"Ошибка: {exc}", show_alert=True)
            return
        target = await _load_user_by_id(session, target_id)
        if target:
            text, kb = await _render_user_card(session, target)
            await safe_edit_or_send(callback, text, kb)
        return

    if action == "ban":
        target.is_banned = not target.is_banned
        await session.commit()
        target = await _load_user_by_id(session, target_id)
        if target:
            text, kb = await _render_user_card(session, target)
            await safe_edit_or_send(callback, text, kb)
        return


@router.message(AdminUserStates.waiting_balance_delta)
async def msg_admin_balance_delta(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        delta = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer("⚠️ Введите корректное число (например +100 или -50):")
        return

    data = await state.get_data()
    target = await _load_user_by_id(session, int(data["target_user_id"]))
    if target is None:
        await state.clear()
        await message.answer("Пользователь не найден.")
        return

    new_balance = max(Decimal("0.00"), (target.balance + delta).quantize(Decimal("0.01")))
    target.balance = new_balance
    session.add(
        Transaction(
            user_id=target.id,
            payment_id=f"adm_bal_{target.id}_{uuid4().hex[:10]}",
            provider=PaymentProviderType.ADMIN,
            tx_type=TransactionType.ADMIN_ADJUSTMENT,
            status=TransactionStatus.SUCCEEDED,
            amount=delta,
            currency="RUB",
            description=f"Корректировка баланса администратором ({delta:+} ₽)",
            metadata_json={"admin_id": message.from_user.id if message.from_user else 0},
            completed_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    await state.clear()

    target = await _load_user_by_id(session, target.id)
    if target:
        text, kb = await _render_user_card(session, target)
        await message.answer(f"✅ Баланс обновлён.\n\n{text}", reply_markup=kb)


@router.message(AdminUserStates.waiting_grant_days)
async def msg_admin_grant_days(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Введите положительное целое количество дней:")
        return

    days = int(raw)
    data = await state.get_data()
    target = await _load_user_by_id(session, int(data["target_user_id"]))
    if target is None:
        await state.clear()
        await message.answer("Пользователь не найден.")
        return

    try:
        await SubscriptionService.grant_bonus_days(
            session=session,
            user=target,
            days=days,
            remnawave=remnawave,
            settings=settings,
        )
        await session.commit()
    except (SubscriptionError, RemnawaveError) as exc:
        await session.rollback()
        await message.answer(f"❌ Ошибка при выдаче дней: {exc}")
        return

    await state.clear()
    target = await _load_user_by_id(session, target.id)
    if target:
        text, kb = await _render_user_card(session, target)
        await message.answer(f"✅ Начислено +{days} дн. подписки.\n\n{text}", reply_markup=kb)
