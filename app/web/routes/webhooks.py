"""Payment provider webhook handlers (CryptoBot and YooKassa)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.bot.handlers.user.purchase import notify_referrer_reward
from app.bot.keyboards.user import back_to_menu_kb
from app.bot.utils.formatting import escape_html, format_dt_utc, format_money
from app.db.models import TransactionType
from app.services.billing_service import BillingService
from app.services.payments.base import PaymentError, PaymentWebhookError
from app.services.payments.cryptobot import CryptoBotProvider
from app.services.payments.yookassa import YooKassaProvider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _notify_user_after_webhook(bot: object, result: object) -> None:
    if result.already_processed:
        return
    await notify_referrer_reward(bot, result.referral_reward)
    try:
        if result.transaction.tx_type == TransactionType.DEPOSIT:
            await bot.send_message(
                result.user.telegram_id,
                (
                    f"✅ Баланс пополнен на <b>{format_money(result.credited_rub)}</b>!\n"
                    f"💰 Текущий баланс: <b>{format_money(result.user.balance)}</b>"
                ),
                reply_markup=back_to_menu_kb(),
            )
            return

        sub = result.subscription
        await bot.send_message(
            result.user.telegram_id,
            (
                "✅ <b>Оплата успешно получена!</b>\n\n"
                f"📅 <b>Подписка активна до:</b> {format_dt_utc(sub.expires_at if sub else None)}\n"
                f"🔗 <b>Ссылка подписки:</b>\n"
                f"<code>{escape_html(sub.subscription_url if sub else '')}</code>"
            ),
            reply_markup=back_to_menu_kb(),
        )
    except Exception as exc:
        logger.warning("Failed sending Telegram payment confirmation: %s", exc)


@router.post("/cryptobot")
async def cryptobot_webhook(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    if not settings.is_cryptobot_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CryptoBot disabled")

    raw_body = await request.body()
    provider = CryptoBotProvider(
        settings.CRYPTOBOT_TOKEN.get_secret_value(),
        testnet=settings.CRYPTOBOT_TESTNET,
        accepted_assets=settings.CRYPTOBOT_ACCEPTED_ASSETS,
    )
    try:
        event = await provider.verify_and_parse_webhook(raw_body, dict(request.headers))
    except PaymentWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    if event is None or event.status != "succeeded":
        return {"status": "ignored"}

    database = request.app.state.database
    remnawave = request.app.state.remnawave
    bot = request.app.state.bot

    async with database.session() as session:
        try:
            result = await BillingService.fulfill_payment(
                session=session,
                payment_id=event.payment_id,
                remnawave=remnawave,
                settings=settings,
                external_id=event.external_id,
            )
            await session.commit()
        except PaymentError as exc:
            await session.rollback()
            logger.error("CryptoBot fulfillment error for %s: %s", event.payment_id, exc)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _notify_user_after_webhook(bot, result)
    return {"status": "ok"}


@router.post("/yookassa")
async def yookassa_webhook(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    if not settings.is_yookassa_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="YooKassa disabled")

    raw_body = await request.body()
    provider = YooKassaProvider(
        settings.YOOKASSA_SHOP_ID,
        settings.YOOKASSA_SECRET_KEY.get_secret_value(),
        default_return_url=settings.YOOKASSA_RETURN_URL,
        trust_webhook_without_api_check=settings.YOOKASSA_TRUST_WEBHOOK_WITHOUT_API_CHECK,
    )
    try:
        event = await provider.verify_and_parse_webhook(raw_body, dict(request.headers))
    except PaymentWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    finally:
        await provider.aclose()

    if event is None:
        return {"status": "ignored"}

    database = request.app.state.database
    remnawave = request.app.state.remnawave
    bot = request.app.state.bot

    if event.status == "canceled":
        async with database.session() as session:
            await BillingService.cancel_payment(session, event.payment_id)
            await session.commit()
        return {"status": "canceled"}

    if event.status != "succeeded":
        return {"status": "pending"}

    async with database.session() as session:
        try:
            result = await BillingService.fulfill_payment(
                session=session,
                payment_id=event.payment_id,
                remnawave=remnawave,
                settings=settings,
                external_id=event.external_id,
            )
            await session.commit()
        except PaymentError as exc:
            await session.rollback()
            logger.error("YooKassa fulfillment error for %s: %s", event.payment_id, exc)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _notify_user_after_webhook(bot, result)
    return {"status": "ok"}
