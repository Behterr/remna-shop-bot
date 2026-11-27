"""Admin segmented broadcast handler with TelegramRetryAfter flood-limit protection."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.admin import (
    admin_broadcast_button_kb,
    admin_broadcast_confirm_kb,
    admin_broadcast_segment_kb,
    admin_main_kb,
)
from app.bot.states.admin import AdminBroadcastStates
from app.db.models import Subscription, SubscriptionStatus, User

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")

SEGMENT_TITLES = {
    "all": "Все пользователи",
    "active": "Активная подписка",
    "no_subscription": "Без подписки",
    "expired": "Истёкшая подписка",
}


async def _fetch_segment_recipients(session: AsyncSession, segment: str) -> list[int]:
    now = datetime.now(timezone.utc)
    if segment == "all":
        stmt = select(User.telegram_id).where(User.is_banned.is_(False))
    elif segment == "active":
        stmt = (
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                User.is_banned.is_(False),
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at > now,
            )
        )
    elif segment == "no_subscription":
        stmt = (
            select(User.telegram_id)
            .outerjoin(Subscription, Subscription.user_id == User.id)
            .where(User.is_banned.is_(False), Subscription.id.is_(None))
        )
    elif segment == "expired":
        stmt = (
            select(User.telegram_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                User.is_banned.is_(False),
                (Subscription.status == SubscriptionStatus.EXPIRED)
                | (Subscription.expires_at <= now),
            )
        )
    else:
        return []

    return list((await session.execute(stmt)).scalars().all())


@router.callback_query(F.data == "adm:broadcast")
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_or_send(
        callback,
        "📢 <b>Рассылка пользователям</b>\n\nВыберите сегмент аудитории:",
        admin_broadcast_segment_kb(),
    )


@router.callback_query(F.data.startswith("adm:bc_seg:"))
async def cb_broadcast_segment(callback: CallbackQuery, state: FSMContext) -> None:
    segment = (callback.data or "").split(":")[-1]
    if segment not in SEGMENT_TITLES:
        await callback.answer("Неизвестный сегмент.", show_alert=True)
        return
    await state.update_data(segment=segment)
    await state.set_state(AdminBroadcastStates.waiting_content)
    await safe_edit_or_send(
        callback,
        (
            f"🎯 Сегмент: <b>{SEGMENT_TITLES[segment]}</b>\n\n"
            "Отправьте текст сообщения или <b>фото с подписью</b> (поддерживается HTML):"
        ),
        admin_broadcast_confirm_kb(),
    )


@router.message(AdminBroadcastStates.waiting_content)
async def msg_broadcast_content(message: Message, state: FSMContext) -> None:
    photo_id = message.photo[-1].file_id if message.photo else None
    text = message.html_text if (message.text or message.caption) else ""
    if not photo_id and not text:
        await message.answer("⚠️ Отправьте текст или фото с подписью:")
        return

    await state.update_data(photo_id=photo_id, text=text)
    await state.set_state(AdminBroadcastStates.waiting_button)
    await message.answer(
        "Добавить inline-кнопку со ссылкой?\n"
        "Отправьте в формате <code>Текст кнопки | https://example.com</code> или нажмите «Пропустить»:",
        reply_markup=admin_broadcast_button_kb(),
    )


def _build_optional_markup(btn_text: str | None, btn_url: str | None) -> InlineKeyboardMarkup | None:
    if btn_text and btn_url:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=btn_url)]]
        )
    return None


@router.callback_query(F.data == "adm:bc_skip_btn")
async def cb_broadcast_skip_button(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await state.update_data(btn_text=None, btn_url=None)
    await _show_broadcast_preview(callback, state, session)


@router.message(AdminBroadcastStates.waiting_button)
async def msg_broadcast_button(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    raw = (message.text or "").strip()
    if "|" not in raw:
        await message.answer("⚠️ Используйте формат <code>Текст | https://ссылка</code>:")
        return
    title, url = [part.strip() for part in raw.split("|", maxsplit=1)]
    if not title or not (url.startswith("https://") or url.startswith("http://")):
        await message.answer("⚠️ Укажите непустой текст и корректный http(s) URL:")
        return

    await state.update_data(btn_text=title[:64], btn_url=url)
    await _show_broadcast_preview(message, state, session)


async def _show_broadcast_preview(
    target: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    segment = data["segment"]
    recipients = await _fetch_segment_recipients(session, segment)
    await state.set_state(AdminBroadcastStates.waiting_confirm)

    preview_info = (
        f"📢 <b>Подтверждение рассылки</b>\n\n"
        f"🎯 <b>Сегмент:</b> {SEGMENT_TITLES[segment]}\n"
        f"👥 <b>Получателей:</b> {len(recipients)}\n"
        f"🔘 <b>Кнопка:</b> {data.get('btn_text') or 'Нет'}\n\n"
        f"<b>Предпросмотр текста:</b>\n{data.get('text') or '(фото без подписи)'}"
    )
    await safe_edit_or_send(target, preview_info, admin_broadcast_confirm_kb())


@router.callback_query(F.data == "adm:bc_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit_or_send(callback, "❌ Рассылка отменена.", admin_main_kb())


@router.callback_query(F.data == "adm:bc_send")
async def cb_broadcast_execute(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    segment = data.get("segment")
    if not segment:
        await callback.answer("Данные рассылки устарели.", show_alert=True)
        return

    recipients = await _fetch_segment_recipients(session, segment)
    photo_id: str | None = data.get("photo_id")
    text: str = data.get("text") or ""
    markup = _build_optional_markup(data.get("btn_text"), data.get("btn_url"))
    await state.clear()

    await safe_edit_or_send(
        callback,
        f"🚀 Рассылка запущена для {len(recipients)} пользователей...",
        admin_main_kb(),
    )

    started = time.monotonic()
    sent_count = 0
    blocked_count = 0
    failed_count = 0

    for chat_id in recipients:
        delivered = False
        for _ in range(3):
            try:
                if photo_id:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_id,
                        caption=text or None,
                        reply_markup=markup,
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=markup,
                    )
                sent_count += 1
                delivered = True
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except TelegramForbiddenError:
                blocked_count += 1
                delivered = True
                break
            except TelegramBadRequest as exc:
                logger.warning("Broadcast bad request for %s: %s", chat_id, exc)
                failed_count += 1
                delivered = True
                break
            except Exception as exc:
                logger.warning("Broadcast error for %s: %s", chat_id, exc)
                break

        if not delivered:
            failed_count += 1
        await asyncio.sleep(0.05)

    elapsed = round(time.monotonic() - started, 1)
    report = (
        "✅ <b>Рассылка завершена!</b>\n\n"
        f"👥 Всего в сегменте: <b>{len(recipients)}</b>\n"
        f"📨 Доставлено: <b>{sent_count}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked_count}</b>\n"
        f"⚠️ Ошибок доставки: <b>{failed_count}</b>\n"
        f"⏱ Время: <b>{elapsed} сек.</b>"
    )
    if callback.message is not None:
        await callback.message.answer(report, reply_markup=admin_main_kb())
