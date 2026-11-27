"""Admin dashboard and statistics with Remnawave health check."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.admin import admin_main_kb
from app.bot.utils.formatting import format_money
from app.db.models import (
    Subscription,
    SubscriptionStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
)
from app.services.remnawave import RemnawaveClient

router = Router(name="admin_menu")

_REVENUE_TYPES = (
    TransactionType.SUBSCRIPTION_PURCHASE,
    TransactionType.SUBSCRIPTION_RENEWAL,
    TransactionType.AUTO_RENEWAL,
)


async def _sum_revenue(session: AsyncSession, since: datetime | None = None) -> Decimal:
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        Transaction.status == TransactionStatus.SUCCEEDED,
        Transaction.currency == "RUB",
        Transaction.tx_type.in_(_REVENUE_TYPES),
    )
    if since is not None:
        stmt = stmt.where(Transaction.completed_at >= since)
    val = (await session.execute(stmt)).scalar_one()
    return Decimal(str(val)).quantize(Decimal("0.01"))


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🛠 <b>Панель администратора</b>\n\nВыберите раздел управления:",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "adm:home")
async def cb_admin_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_or_send(
        callback,
        "🛠 <b>Панель администратора</b>\n\nВыберите раздел управления:",
        admin_main_kb(),
    )


@router.callback_query(F.data == "adm:stats")
async def cb_admin_stats(
    callback: CallbackQuery,
    session: AsyncSession,
    remnawave: RemnawaveClient,
) -> None:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now - timedelta(days=30)

    total_users = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
    banned_users = int(
        (await session.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))).scalar_one()
        or 0
    )
    trials_used = int(
        (await session.execute(select(func.count(User.id)).where(User.trial_used.is_(True)))).scalar_one()
        or 0
    )
    active_subs = int(
        (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.expires_at > now,
                )
            )
        ).scalar_one()
        or 0
    )

    rev_day = await _sum_revenue(session, day_start)
    rev_month = await _sum_revenue(session, month_start)
    rev_total = await _sum_revenue(session, None)

    health = await remnawave.health_check()
    rw_status = (
        f"🟢 Доступна ({health.latency_ms} мс)"
        if health.healthy
        else f"🔴 Ошибка (код {health.status_code}: {health.detail})"
    )
    rw_ver = f"\n🏷 <b>Версия Remnawave:</b> {health.version}" if health.version else ""

    text = (
        "📊 <b>Статистика проекта</b>\n\n"
        f"👥 <b>Пользователей:</b> {total_users} (забанено: {banned_users})\n"
        f"🟢 <b>Активных подписок:</b> {active_subs}\n"
        f"🎁 <b>Использовано триалов:</b> {trials_used}\n\n"
        f"💵 <b>Выручка за сегодня:</b> {format_money(rev_day)}\n"
        f"📆 <b>Выручка за 30 дней:</b> {format_money(rev_month)}\n"
        f"🏦 <b>Выручка всего:</b> {format_money(rev_total)}\n\n"
        f"🌐 <b>Связь с Remnawave:</b> {rw_status}"
        f"{rw_ver}"
    )
    await safe_edit_or_send(callback, text, admin_main_kb())
