"""User subscription management, QR code generation, and client instructions."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.user import back_to_menu_kb, client_guides_kb, subscription_kb
from app.bot.utils.formatting import (
    escape_html,
    format_bytes,
    format_device_limit,
    format_dt_utc,
    format_traffic_limit,
    pluralize_days,
    remaining_days,
)
from app.bot.utils.qr import generate_qr_png
from app.config import Settings
from app.db.models import Subscription, SubscriptionStatus, User
from app.services.remnawave import RemnawaveClient, RemnawaveError
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)
router = Router(name="user_subscription")

CLIENT_GUIDES: dict[str, str] = {
    "happ": (
        "📱 <b>Подключение в Happ (iOS / Android)</b>\n\n"
        "1. Установите приложение <b>Happ</b> из App Store или Google Play.\n"
        "2. Нажмите кнопку <b>📲 Happ</b> в разделе «Моя подписка» или скопируйте ссылку подписки.\n"
        "3. В приложении нажмите <b>«+» → «Вставить из буфера обмена»</b> или отсканируйте QR-код.\n"
        "4. Нажмите кнопку подключения — готово!"
    ),
    "v2raytun": (
        "⚡ <b>Подключение в v2rayTUN (iOS / Android)</b>\n\n"
        "1. Установите <b>v2rayTUN</b> из App Store / Google Play.\n"
        "2. Скопируйте ссылку подписки (нажмите на неё в сообщении) или нажмите <b>📲 v2rayTUN</b>.\n"
        "3. В v2rayTUN выберите <b>«+» → «Импорт из буфера»</b> либо <b>«Сканировать QR»</b>.\n"
        "4. Выберите сервер и включите VPN."
    ),
    "hiddify": (
        "🛡 <b>Подключение в Hiddify (Windows / macOS / Linux / Android / iOS)</b>\n\n"
        "1. Скачайте <b>Hiddify</b> с официального сайта или магазина приложений.\n"
        "2. Нажмите <b>«Новый профиль» → «Добавить из буфера»</b> (или используйте кнопку <b>📲 Hiddify</b>).\n"
        "3. Нажмите большую кнопку <b>Подключить</b>."
    ),
    "streisand": (
        "🧭 <b>Подключение в Streisand (iOS / macOS Apple Silicon)</b>\n\n"
        "1. Установите <b>Streisand</b> из App Store.\n"
        "2. Скопируйте ссылку подписки или нажмите <b>📲 Streisand</b>.\n"
        "3. В Streisand нажмите <b>«+» → «Добавить из буфера»</b>.\n"
        "4. Включите переключатель подключения и подтвердите добавление VPN-конфигурации."
    ),
}


def _sub_status_badge(status: SubscriptionStatus) -> str:
    return {
        SubscriptionStatus.ACTIVE: "🟢 Активна",
        SubscriptionStatus.EXPIRED: "⌛ Истекла",
        SubscriptionStatus.DISABLED: "🔴 Отключена",
        SubscriptionStatus.LIMITED: "🟠 Трафик исчерпан",
    }.get(status, status.value)


async def _load_user_sub(session: AsyncSession, user_id: int) -> Subscription | None:
    return (
        await session.execute(select(Subscription).where(Subscription.user_id == user_id))
    ).scalar_one_or_none()


async def _render_subscription_view(
    session: AsyncSession,
    db_user: User,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> tuple[str, object]:
    sub = await _load_user_sub(session, db_user.id)
    if sub is None:
        text = (
            "📱 <b>Моя подписка</b>\n\n"
            "У вас пока нет активной подписки.\n"
            "Вы можете выбрать подходящий тариф в каталоге или активировать пробный период."
        )
        return text, subscription_kb(sub=None, user=db_user, settings=settings)

    try:
        sub = await SubscriptionService.sync_single_subscription(session, sub, remnawave)
        await session.commit()
    except RemnawaveError as exc:
        logger.warning("Failed to live-sync subscription %s: %s", sub.remnawave_uuid, exc)

    days_left = remaining_days(sub.expires_at)
    used_str = format_bytes(sub.used_traffic_bytes)
    limit_str = format_traffic_limit(sub.traffic_limit_bytes)
    import_hint = (
        "Нажмите кнопку вашего приложения ниже для мгновенного импорта:"
        if settings.WEBHOOK_DOMAIN.startswith("https://")
        else "Скопируйте ссылку подписки ниже и вставьте в приложение (или используйте QR-код):"
    )

    text = (
        "📱 <b>Моя подписка</b>\n\n"
        f"🛡 <b>Статус:</b> {_sub_status_badge(sub.status)}\n"
        f"📅 <b>Действует до:</b> {format_dt_utc(sub.expires_at)} ({pluralize_days(days_left)})\n"
        f"📊 <b>Трафик:</b> {used_str} / {limit_str}\n"
        f"📱 <b>Устройств:</b> до {format_device_limit(sub.device_limit)}\n"
        f"🔄 <b>Автопродление с баланса:</b> {'Включено' if db_user.auto_renew else 'Выключено'}\n\n"
        f"🔗 <b>Ссылка на подписку</b> (нажмите, чтобы скопировать):\n"
        f"<code>{escape_html(sub.subscription_url)}</code>\n\n"
        f"{import_hint}"
    )
    return text, subscription_kb(sub=sub, user=db_user, settings=settings)


@router.callback_query(F.data == "menu:sub")
async def cb_my_subscription(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    text, kb = await _render_subscription_view(session, db_user, remnawave, settings)
    await safe_edit_or_send(callback, text, kb)


@router.callback_query(F.data == "sub:toggle_autorenew")
async def cb_toggle_autorenew(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    remnawave: RemnawaveClient,
    settings: Settings,
) -> None:
    db_user.auto_renew = not db_user.auto_renew
    await session.commit()
    state_label = "включено ✅" if db_user.auto_renew else "выключено ❌"
    await callback.answer(f"Автопродление {state_label}")
    text, kb = await _render_subscription_view(session, db_user, remnawave, settings)
    await safe_edit_or_send(callback, text, kb)


@router.callback_query(F.data == "sub:qr")
async def cb_subscription_qr(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
) -> None:
    sub = await _load_user_sub(session, db_user.id)
    if sub is None or not sub.subscription_url:
        await callback.answer("Ссылка подписки пока не создана.", show_alert=True)
        return

    png_bytes = generate_qr_png(sub.subscription_url)
    photo = BufferedInputFile(png_bytes, filename="subscription_qr.png")
    if callback.message is not None:
        await callback.message.answer_photo(
            photo=photo,
            caption=(
                "📷 <b>QR-код вашей подписки</b>\n"
                "Отсканируйте его в Happ, v2rayTUN, Hiddify или Streisand."
            ),
            reply_markup=back_to_menu_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "sub:guides")
async def cb_guides_menu(callback: CallbackQuery) -> None:
    await safe_edit_or_send(
        callback,
        "📖 <b>Инструкции по подключению</b>\n\nВыберите ваше VPN-приложение:",
        client_guides_kb(),
    )


@router.callback_query(F.data.startswith("sub:guide:"))
async def cb_guide_detail(callback: CallbackQuery) -> None:
    app_slug = (callback.data or "").split(":")[-1]
    guide = CLIENT_GUIDES.get(app_slug)
    if not guide:
        await callback.answer("Инструкция не найдена.", show_alert=True)
        return
    await safe_edit_or_send(callback, guide, client_guides_kb())
