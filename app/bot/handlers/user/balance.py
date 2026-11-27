"""Internal balance top-up handlers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import (
    back_to_menu_kb,
    deposit_amounts_kb,
    deposit_methods_kb,
    payment_url_kb,
)
from app.bot.states.user import TopUpStates
from app.bot.utils.formatting import format_money
from app.config import Settings
from app.db.models import User
from app.services.billing_service import BillingService
from app.services.payments.base import PaymentError
from app.services.payments.cryptobot import CryptoBotProvider
from app.services.payments.stars import TelegramStarsProvider
from app.services.payments.yookassa import YooKassaProvider

router = Router(name="user_balance")


@router.callback_query(F.data == "menu:balance")
async def cb_balance_menu(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
) -> None:
    await state.clear()
    text = (
        "💳 <b>Пополнение баланса</b>\n\n"
        f"💰 Текущий баланс: <b>{format_money(db_user.balance)}</b>\n\n"
        "Выберите сумму пополнения или введите свою:"
    )
    await safe_edit_or_send(callback, text, deposit_amounts_kb())


@router.callback_query(F.data.startswith("dep_amt:"))
async def cb_deposit_amount(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
) -> None:
    raw = (callback.data or "").split(":")[-1]
    if raw == "custom":
        await state.set_state(TopUpStates.waiting_amount)
        await safe_edit_or_send(
            callback,
            "✏️ Введите сумму пополнения в рублях (от 10 до 100000):",
            back_to_menu_kb(),
        )
        return

    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return

    stars_amount = TelegramStarsProvider(settings.STARS_RUB_RATE).calculate_stars(amount)
    await safe_edit_or_send(
        callback,
        f"💳 Пополнение баланса на <b>{format_money(amount)}</b>\n\nВыберите способ оплаты:",
        deposit_methods_kb(amount, settings, stars_amount),
    )


@router.message(TopUpStates.waiting_amount)
async def msg_deposit_custom_amount(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer("⚠️ Введите корректное число, например <code>350</code>:")
        return

    if amount < Decimal("10") or amount > Decimal("100000"):
        await message.answer("⚠️ Сумма должна быть от 10 до 100 000 ₽:")
        return

    await state.clear()
    stars_amount = TelegramStarsProvider(settings.STARS_RUB_RATE).calculate_stars(amount)
    await message.answer(
        f"💳 Пополнение баланса на <b>{format_money(amount)}</b>\n\nВыберите способ оплаты:",
        reply_markup=deposit_methods_kb(amount, settings, stars_amount),
    )


@router.callback_query(F.data.startswith("dep_pay:"))
async def cb_deposit_pay(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Некорректный запрос.", show_alert=True)
        return

    try:
        amount = Decimal(parts[1]).quantize(Decimal("0.01"))
    except InvalidOperation:
        await callback.answer("Некорректная сумма.", show_alert=True)
        return
    method = parts[2]

    me = await bot.get_me()
    bot_username = me.username or settings.BOT_USERNAME

    if method == "stars":
        provider = TelegramStarsProvider(settings.STARS_RUB_RATE)
        tx, creation = await BillingService.create_deposit_checkout(
            session=session,
            user=db_user,
            amount_rub=amount,
            provider=provider,
            settings=settings,
            bot_username=bot_username,
        )
        await session.commit()
        invoice_params = provider.build_invoice_params(
            chat_id=db_user.telegram_id,
            title=f"Баланс +{format_money(amount)}",
            description=f"Пополнение внутреннего баланса на {format_money(amount)}",
            payment_id=tx.payment_id,
            stars_amount=int(creation.amount),
        )
        if callback.message is not None:
            await bot.send_invoice(**invoice_params)
        await callback.answer("Счёт в Telegram Stars отправлен!")
        return

    if method == "cryptobot":
        if not settings.is_cryptobot_enabled:
            await callback.answer("CryptoBot отключён.", show_alert=True)
            return
        provider = CryptoBotProvider(
            settings.CRYPTOBOT_TOKEN.get_secret_value(),
            testnet=settings.CRYPTOBOT_TESTNET,
            accepted_assets=settings.CRYPTOBOT_ACCEPTED_ASSETS,
        )
        try:
            _, creation = await BillingService.create_deposit_checkout(
                session=session,
                user=db_user,
                amount_rub=amount,
                provider=provider,
                settings=settings,
                bot_username=bot_username,
            )
            await session.commit()
        except PaymentError as exc:
            await session.rollback()
            await callback.answer(f"Ошибка CryptoBot: {exc}", show_alert=True)
            return
        finally:
            await provider.aclose()

        await safe_edit_or_send(
            callback,
            (
                f"💎 <b>Пополнение через CryptoBot</b>\n"
                f"Сумма: <b>{format_money(amount)}</b>\n\n"
                "Нажмите кнопку ниже для перехода к оплате."
            ),
            payment_url_kb(creation.payment_url or "https://t.me", back_callback="menu:balance"),
        )
        return

    if method == "yookassa":
        if not settings.is_yookassa_enabled:
            await callback.answer("YooKassa отключена.", show_alert=True)
            return
        provider = YooKassaProvider(
            settings.YOOKASSA_SHOP_ID,
            settings.YOOKASSA_SECRET_KEY.get_secret_value(),
            default_return_url=settings.YOOKASSA_RETURN_URL,
        )
        try:
            _, creation = await BillingService.create_deposit_checkout(
                session=session,
                user=db_user,
                amount_rub=amount,
                provider=provider,
                settings=settings,
                bot_username=bot_username,
            )
            await session.commit()
        except PaymentError as exc:
            await session.rollback()
            await callback.answer(f"Ошибка YooKassa: {exc}", show_alert=True)
            return
        finally:
            await provider.aclose()

        await safe_edit_or_send(
            callback,
            (
                f"💳 <b>Пополнение через YooKassa</b>\n"
                f"Сумма: <b>{format_money(amount)}</b>\n\n"
                "Нажмите кнопку ниже для перехода к оплате."
            ),
            payment_url_kb(creation.payment_url or "https://t.me", back_callback="menu:balance"),
        )
        return

    await callback.answer("Неизвестный способ оплаты.", show_alert=True)
