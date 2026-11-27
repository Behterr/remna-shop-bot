"""Tariff catalog, checkout, internal balance & Stars/CryptoBot/YooKassa payments."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import (
    back_to_menu_kb,
    payment_url_kb,
    tariff_payment_kb,
    tariffs_kb,
)
from app.bot.utils.formatting import (
    escape_html,
    format_device_limit,
    format_dt_utc,
    format_money,
)
from app.config import Settings
from app.db.models import Tariff, Transaction, TransactionStatus, TransactionType, User
from app.services.billing_service import BillingService
from app.services.payments.base import InsufficientBalanceError, PaymentError
from app.services.payments.cryptobot import CryptoBotProvider
from app.services.payments.stars import TelegramStarsProvider
from app.services.payments.yookassa import YooKassaProvider
from app.services.remnawave import RemnawaveClient, RemnawaveError

logger = logging.getLogger(__name__)
router = Router(name="user_purchase")


def _format_tariff_traffic(gb: int) -> str:
    return "Безлимит" if gb <= 0 else f"{gb} ГБ"


@router.callback_query(F.data == "menu:buy")
async def cb_buy_menu(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    tariffs = (
        await session.execute(
            select(Tariff)
            .where(Tariff.is_active.is_(True))
            .order_by(Tariff.sort_order.asc(), Tariff.id.asc())
        )
    ).scalars().all()

    if not tariffs:
        await safe_edit_or_send(
            callback,
            "🛒 <b>Тарифы временно отсутствуют</b>\n\nАдминистратор еще не добавил активные тарифы.",
            back_to_menu_kb(),
        )
        return

    await safe_edit_or_send(
        callback,
        "🛒 <b>Выберите тариф VPN-подписки:</b>",
        tariffs_kb(list(tariffs)),
    )


@router.callback_query(F.data.startswith("tariff:"))
async def cb_tariff_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
) -> None:
    raw_id = (callback.data or "").split(":")[-1]
    if not raw_id.isdigit():
        await callback.answer("Некорректный тариф.", show_alert=True)
        return

    tariff = await session.get(Tariff, int(raw_id))
    if tariff is None or not tariff.is_active:
        await callback.answer("Тариф не найден или отключён.", show_alert=True)
        return

    final_rub, stars, promo_id = await BillingService.get_tariff_checkout_quote(
        session, db_user, tariff, settings
    )
    price_block = f"💵 <b>Цена:</b> {format_money(final_rub)}"
    if promo_id is not None and final_rub < tariff.price_rub:
        price_block = (
            f"💵 <b>Цена со скидкой:</b> <s>{format_money(tariff.price_rub)}</s> "
            f"<b>{format_money(final_rub)}</b>"
        )

    desc = f"\n📝 {escape_html(tariff.description)}\n" if tariff.description else ""
    text = (
        f"⚡ <b>Тариф «{escape_html(tariff.name)}»</b>"
        f"{desc}\n"
        f"📅 <b>Срок:</b> {tariff.duration_days} дн.\n"
        f"📊 <b>Трафик:</b> {_format_tariff_traffic(tariff.traffic_limit_gb)}\n"
        f"📱 <b>Устройств:</b> {format_device_limit(tariff.device_limit)}\n"
        f"{price_block}\n\n"
        "Выберите способ оплаты:"
    )
    kb = tariff_payment_kb(
        tariff=tariff,
        final_rub=final_rub,
        stars_amount=stars,
        user_balance=db_user.balance,
        settings=settings,
    )
    await safe_edit_or_send(callback, text, kb)


async def notify_referrer_reward(bot: Bot, reward: object) -> None:
    """Send polite notification to referrer when bonus is credited."""
    if reward is None:
        return
    try:
        if reward.reward_type == "days":
            msg = (
                f"🎉 По вашей реферальной ссылке прошла оплата!\n"
                f"Вам начислено <b>+{reward.days} дн.</b> к подписке."
            )
        else:
            msg = (
                f"🎉 По вашей реферальной ссылке прошла оплата!\n"
                f"На ваш баланс начислен бонус <b>{format_money(reward.amount_rub)}</b>."
            )
        await bot.send_message(reward.referrer_telegram_id, msg)
    except Exception as exc:
        logger.debug("Could not notify referrer %s: %s", reward.referrer_telegram_id, exc)


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay_tariff(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer("Некорректный запрос оплаты.", show_alert=True)
        return

    tariff_id = int(parts[1])
    method = parts[2]
    tariff = await session.get(Tariff, tariff_id)
    if tariff is None or not tariff.is_active:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return

    if method == "balance":
        try:
            result = await BillingService.pay_tariff_with_balance(
                session=session,
                user=db_user,
                tariff=tariff,
                remnawave=remnawave,
                settings=settings,
            )
            await session.commit()
        except InsufficientBalanceError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        except RemnawaveError as exc:
            logger.exception("Remnawave error during balance purchase: %s", exc)
            await session.rollback()
            await callback.answer(
                "Ошибка VPN-панели при активации. Средства не списаны.",
                show_alert=True,
            )
            return

        await notify_referrer_reward(bot, result.referral_reward)
        sub = result.subscription
        text = (
            "✅ <b>Оплата прошла успешно!</b>\n\n"
            f"📦 <b>Тариф:</b> {escape_html(tariff.name)}\n"
            f"📅 <b>Активна до:</b> {format_dt_utc(sub.expires_at if sub else None)}\n"
            f"💰 <b>Остаток баланса:</b> {format_money(result.user.balance)}\n\n"
            f"🔗 <b>Ссылка подписки:</b>\n"
            f"<code>{escape_html(sub.subscription_url if sub else '')}</code>"
        )
        await safe_edit_or_send(callback, text, back_to_menu_kb())
        return

    me = await bot.get_me()
    bot_username = me.username or settings.BOT_USERNAME

    if method == "stars":
        provider = TelegramStarsProvider(settings.STARS_RUB_RATE)
        tx, creation = await BillingService.create_tariff_checkout(
            session=session,
            user=db_user,
            tariff=tariff,
            provider=provider,
            settings=settings,
            bot_username=bot_username,
        )
        await session.commit()
        invoice_params = provider.build_invoice_params(
            chat_id=db_user.telegram_id,
            title=f"VPN: {tariff.name}",
            description=f"Подписка на {tariff.duration_days} дн.",
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
            _, creation = await BillingService.create_tariff_checkout(
                session=session,
                user=db_user,
                tariff=tariff,
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

        if not creation.payment_url:
            await callback.answer("Не удалось получить ссылку оплаты.", show_alert=True)
            return
        await safe_edit_or_send(
            callback,
            (
                f"💎 <b>Счёт CryptoBot создан</b>\n\n"
                f"📦 Тариф: <b>{escape_html(tariff.name)}</b>\n"
                f"💵 Сумма: <b>{format_money(creation.amount)}</b>\n\n"
                "После оплаты подписка активируется автоматически."
            ),
            payment_url_kb(creation.payment_url, back_callback=f"tariff:{tariff.id}"),
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
            _, creation = await BillingService.create_tariff_checkout(
                session=session,
                user=db_user,
                tariff=tariff,
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

        if not creation.payment_url:
            await callback.answer("Не удалось получить ссылку оплаты.", show_alert=True)
            return
        await safe_edit_or_send(
            callback,
            (
                f"💳 <b>Счёт YooKassa создан</b>\n\n"
                f"📦 Тариф: <b>{escape_html(tariff.name)}</b>\n"
                f"💵 Сумма: <b>{format_money(creation.amount)}</b>\n\n"
                "После оплаты подписка активируется автоматически."
            ),
            payment_url_kb(creation.payment_url, back_callback=f"tariff:{tariff.id}"),
        )
        return

    await callback.answer("Неизвестный способ оплаты.", show_alert=True)


@router.pre_checkout_query()
async def on_pre_checkout(
    pre_checkout: PreCheckoutQuery,
    session: AsyncSession,
) -> None:
    tx = (
        await session.execute(
            select(Transaction).where(Transaction.payment_id == pre_checkout.invoice_payload)
        )
    ).scalar_one_or_none()
    if tx is None or tx.status == TransactionStatus.CANCELED:
        await pre_checkout.answer(
            ok=False,
            error_message="Счёт не найден или устарел. Попробуйте создать новый.",
        )
        return
    await pre_checkout.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_stars_payment(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    sp = message.successful_payment
    if sp is None:
        return

    try:
        result = await BillingService.fulfill_payment(
            session=session,
            payment_id=sp.invoice_payload,
            remnawave=remnawave,
            settings=settings,
            external_id=sp.telegram_payment_charge_id,
        )
        await session.commit()
    except Exception as exc:
        logger.exception("Failed fulfilling Stars payment %s: %s", sp.invoice_payload, exc)
        await session.rollback()
        await message.answer(
            "⚠️ Платёж получен, но возникла ошибка активации. "
            f"Обратитесь в поддержку с кодом <code>{escape_html(sp.invoice_payload)}</code>."
        )
        return

    if result.already_processed:
        return

    await notify_referrer_reward(bot, result.referral_reward)

    if result.transaction.tx_type == TransactionType.DEPOSIT:
        await message.answer(
            f"✅ Баланс пополнен на <b>{format_money(result.credited_rub)}</b>!\n"
            f"💰 Текущий баланс: <b>{format_money(result.user.balance)}</b>",
            reply_markup=back_to_menu_kb(),
        )
        return

    sub = result.subscription
    await message.answer(
        "✅ <b>Оплата Telegram Stars прошла успешно!</b>\n\n"
        f"📅 <b>Подписка активна до:</b> {format_dt_utc(sub.expires_at if sub else None)}\n"
        f"🔗 <b>Ссылка подписки:</b>\n"
        f"<code>{escape_html(sub.subscription_url if sub else '')}</code>",
        reply_markup=back_to_menu_kb(),
    )
