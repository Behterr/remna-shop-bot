"""APScheduler background jobs for notifications, auto-renewals, batch traffic sync, and cleanup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bot.handlers.user.purchase import notify_referrer_reward
from app.bot.utils.formatting import (
    escape_html,
    format_bytes,
    format_dt_utc,
    format_money,
    format_traffic_limit,
)
from app.config import Settings
from app.db.models import Subscription, SubscriptionStatus, User
from app.db.session import Database
from app.services.billing_service import BillingService
from app.services.payments.base import InsufficientBalanceError
from app.services.remnawave import RemnawaveClient, RemnawaveError

logger = logging.getLogger(__name__)


def _renew_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Продлить подписку", callback_data="menu:buy")],
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="menu:balance")],
        ]
    )


async def _safe_send(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.debug("Skipped notification for %s: %s", chat_id, exc)
    except Exception as exc:
        logger.warning("Unexpected error notifying %s: %s", chat_id, exc)


async def process_expiry_and_autorenew(
    *,
    database: Database,
    remnawave: RemnawaveClient,
    bot: Bot,
    settings: Settings,
) -> None:
    """Send 3d/1d/expired warnings and attempt balance auto-renewal on expiration."""
    now = datetime.now(timezone.utc)
    warn_3d_cutoff = now + timedelta(days=3)
    warn_1d_cutoff = now + timedelta(days=1)

    async with database.session() as session:
        stmt = (
            select(Subscription)
            .where(
                Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED]),
                Subscription.expires_at <= warn_3d_cutoff,
            )
            .options(selectinload(Subscription.user), selectinload(Subscription.tariff))
        )
        subs = list((await session.execute(stmt)).scalars().all())

        for sub in subs:
            user = sub.user
            if user is None or user.is_banned:
                continue

            expires_at = sub.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at <= now:
                if (
                    user.auto_renew
                    and sub.tariff is not None
                    and sub.tariff.is_active
                    and not sub.is_trial
                ):
                    idempotent_pid = f"autorenew_{sub.id}_{int(expires_at.timestamp())}"
                    try:
                        res = await BillingService.pay_tariff_with_balance(
                            session=session,
                            user=user,
                            tariff=sub.tariff,
                            remnawave=remnawave,
                            settings=settings,
                            is_auto_renewal=True,
                            idempotent_payment_id=idempotent_pid,
                        )
                        await session.commit()
                        if not res.already_processed:
                            await notify_referrer_reward(bot, res.referral_reward)
                            await _safe_send(
                                bot,
                                user.telegram_id,
                                (
                                    "🔄 <b>Подписка автоматически продлена!</b>\n\n"
                                    f"📦 Тариф: <b>{escape_html(sub.tariff.name)}</b>\n"
                                    f"📅 Действует до: <b>{format_dt_utc(res.subscription.expires_at if res.subscription else None)}</b>\n"
                                    f"💰 Остаток баланса: <b>{format_money(res.user.balance)}</b>"
                                ),
                            )
                        continue
                    except InsufficientBalanceError:
                        logger.info("Auto-renew skipped for %s: insufficient balance", user.telegram_id)
                    except RemnawaveError as exc:
                        logger.warning("Auto-renew Remnawave error for %s: %s", user.telegram_id, exc)
                        await session.rollback()
                        continue

                sub.status = SubscriptionStatus.EXPIRED
                if not sub.notified_expired:
                    sub.notified_expired = True
                    await session.commit()
                    await _safe_send(
                        bot,
                        user.telegram_id,
                        (
                            "⌛ <b>Срок вашей VPN-подписки истёк.</b>\n\n"
                            "Чтобы восстановить доступ, выберите и оплатите тариф:"
                        ),
                        reply_markup=_renew_kb(),
                    )
                else:
                    await session.commit()
                continue

            if expires_at <= warn_1d_cutoff and not sub.notified_1d:
                sub.notified_1d = True
                await session.commit()
                await _safe_send(
                    bot,
                    user.telegram_id,
                    (
                        "⏰ <b>Подписка истекает менее чем через 24 часа!</b>\n\n"
                        f"📅 Окончание: <b>{format_dt_utc(expires_at)}</b>\n"
                        "Продлите подписку заранее, чтобы не потерять доступ."
                    ),
                    reply_markup=_renew_kb(),
                )
                continue

            if expires_at <= warn_3d_cutoff and not sub.notified_3d:
                sub.notified_3d = True
                await session.commit()
                await _safe_send(
                    bot,
                    user.telegram_id,
                    (
                        "🔔 <b>До окончания VPN-подписки осталось меньше 3 дней.</b>\n\n"
                        f"📅 Окончание: <b>{format_dt_utc(expires_at)}</b>"
                    ),
                    reply_markup=_renew_kb(),
                )


async def sync_traffic_and_notify(
    *,
    database: Database,
    remnawave: RemnawaveClient,
    bot: Bot,
) -> None:
    """Batch-sync user traffic from paginated GET /api/users and emit 85%/100% alerts."""
    try:
        rw_users = await remnawave.iter_all_users(batch_size=100)
    except RemnawaveError as exc:
        logger.warning("Batch traffic sync failed contacting Remnawave: %s", exc)
        return

    if not rw_users:
        return

    by_uuid = {u.uuid: u for u in rw_users if u.uuid}

    async with database.session() as session:
        stmt = (
            select(Subscription)
            .where(Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED]))
            .options(selectinload(Subscription.user))
        )
        subs = list((await session.execute(stmt)).scalars().all())
        pending_alerts: list[tuple[int, str]] = []

        for sub in subs:
            rw_user = by_uuid.get(sub.remnawave_uuid)
            if rw_user is None:
                continue

            sub.used_traffic_bytes = rw_user.used_traffic_bytes
            if rw_user.subscription_url:
                sub.subscription_url = rw_user.subscription_url
            if rw_user.status == "DISABLED":
                sub.status = SubscriptionStatus.DISABLED
            elif rw_user.status == "LIMITED":
                sub.status = SubscriptionStatus.LIMITED

            user = sub.user
            if user is None or user.is_banned or sub.traffic_limit_bytes <= 0:
                continue

            ratio = sub.used_traffic_bytes / sub.traffic_limit_bytes
            if ratio >= 1.0 and not sub.notified_traffic_100:
                sub.notified_traffic_100 = True
                sub.notified_traffic_85 = True
                sub.status = SubscriptionStatus.LIMITED
                pending_alerts.append(
                    (
                        user.telegram_id,
                        (
                            "🚫 <b>Лимит трафика исчерпан на 100%!</b>\n\n"
                            f"📊 Использовано: {format_bytes(sub.used_traffic_bytes)} / "
                            f"{format_traffic_limit(sub.traffic_limit_bytes)}\n"
                            "Обновите тариф или продлите подписку для сброса счётчика."
                        ),
                    )
                )
            elif ratio >= 0.85 and not sub.notified_traffic_85:
                sub.notified_traffic_85 = True
                pending_alerts.append(
                    (
                        user.telegram_id,
                        (
                            "⚠️ <b>Вы израсходовали 85% трафика по подписке.</b>\n\n"
                            f"📊 Использовано: {format_bytes(sub.used_traffic_bytes)} / "
                            f"{format_traffic_limit(sub.traffic_limit_bytes)}"
                        ),
                    )
                )

        await session.commit()

    for chat_id, msg_text in pending_alerts:
        await _safe_send(bot, chat_id, msg_text, reply_markup=_renew_kb())


async def cleanup_expired_users(
    *,
    database: Database,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    """Optionally delete long-expired users from Remnawave when ENABLE_EXPIRED_CLEANUP is enabled."""
    if not settings.ENABLE_EXPIRED_CLEANUP:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, settings.EXPIRED_CLEANUP_DAYS))
    async with database.session() as session:
        stmt = (
            select(Subscription)
            .where(Subscription.expires_at < cutoff)
            .options(selectinload(Subscription.user))
        )
        stale_subs = list((await session.execute(stmt)).scalars().all())
        for sub in stale_subs:
            try:
                await remnawave.delete_user(sub.remnawave_uuid)
            except RemnawaveError as exc:
                logger.warning("Cleanup failed deleting Remnawave user %s: %s", sub.remnawave_uuid, exc)
                continue
            if sub.user is not None:
                sub.user.remnawave_uuid = None
            await session.delete(sub)
        await session.commit()
