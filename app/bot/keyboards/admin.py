"""Admin panel inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import PromoCode, Subscription, SubscriptionStatus, Tariff, User
from app.services.settings_service import ReferralRuntimeConfig, TrialRuntimeConfig


def admin_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика и Remnawave", callback_data="adm:stats"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 Пользователи", callback_data="adm:users"),
        InlineKeyboardButton(text="📦 Тарифы", callback_data="adm:tariffs"),
    )
    builder.row(
        InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm:promos"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm:settings"),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Выйти в меню", callback_data="menu:home"),
    )
    return builder.as_markup()


def admin_users_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔍 Найти по ID / @username", callback_data="adm:user_search"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Админ-панель", callback_data="adm:home"))
    return builder.as_markup()


def admin_user_card_kb(target_user: User, sub: Subscription | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Изменить баланс", callback_data=f"adm:u:{target_user.id}:bal"),
        InlineKeyboardButton(text="📅 Выдать/продлить дни", callback_data=f"adm:u:{target_user.id}:grant"),
    )
    if sub is not None:
        if sub.status == SubscriptionStatus.DISABLED:
            builder.row(
                InlineKeyboardButton(text="✅ Включить подписку", callback_data=f"adm:u:{target_user.id}:enable"),
            )
        else:
            builder.row(
                InlineKeyboardButton(text="🚫 Отозвать подписку", callback_data=f"adm:u:{target_user.id}:revoke"),
            )
        builder.row(
            InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data=f"adm:u:{target_user.id}:reset_tr"),
        )
    ban_label = "🔓 Разбанить" if target_user.is_banned else "🔨 Забанить"
    builder.row(
        InlineKeyboardButton(text=ban_label, callback_data=f"adm:u:{target_user.id}:ban"),
    )
    builder.row(
        InlineKeyboardButton(text="🔍 Другой пользователь", callback_data="adm:user_search"),
        InlineKeyboardButton(text="🔙 Админ-панель", callback_data="adm:home"),
    )
    return builder.as_markup()


def admin_tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        state_icon = "✅" if t.is_active else "⏸"
        builder.row(
            InlineKeyboardButton(
                text=f"{state_icon} {t.name} | {t.price_rub}₽ / {t.duration_days}д",
                callback_data=f"adm:t:{t.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="➕ Создать тариф", callback_data="adm:t_new"))
    builder.row(InlineKeyboardButton(text="🔙 Админ-панель", callback_data="adm:home"))
    return builder.as_markup()


def admin_tariff_detail_kb(tariff: Tariff) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💵 Цена ₽", callback_data=f"adm:t:{tariff.id}:edit:price_rub"),
        InlineKeyboardButton(text="⭐ Цена Stars", callback_data=f"adm:t:{tariff.id}:edit:price_stars"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 Дни", callback_data=f"adm:t:{tariff.id}:edit:duration_days"),
        InlineKeyboardButton(text="📊 Трафик ГБ", callback_data=f"adm:t:{tariff.id}:edit:traffic_limit_gb"),
    )
    builder.row(
        InlineKeyboardButton(text="📱 Устройства", callback_data=f"adm:t:{tariff.id}:edit:device_limit"),
        InlineKeyboardButton(text="🌐 Сквады UUID", callback_data=f"adm:t:{tariff.id}:edit:squad_uuids"),
    )
    builder.row(
        InlineKeyboardButton(text="📝 Описание", callback_data=f"adm:t:{tariff.id}:edit:description"),
        InlineKeyboardButton(text="↕️ Порядок", callback_data=f"adm:t:{tariff.id}:edit:sort_order"),
    )
    toggle_text = "⏸ Отключить" if tariff.is_active else "✅ Включить"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=f"adm:t:{tariff.id}:toggle"),
    )
    builder.row(InlineKeyboardButton(text="🔙 К тарифам", callback_data="adm:tariffs"))
    return builder.as_markup()


def admin_promos_kb(promos: list[PromoCode]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in promos:
        icon = "✅" if p.is_active else "⛔"
        limit_str = f"{p.used_count}/{p.max_uses}" if p.max_uses else f"{p.used_count}/∞"
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} {p.code} ({p.promo_type.value}: {p.value}) [{limit_str}]",
                callback_data=f"adm:p:{p.id}:toggle",
            )
        )
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm:p_new"))
    builder.row(InlineKeyboardButton(text="🔙 Админ-панель", callback_data="adm:home"))
    return builder.as_markup()


def admin_promo_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏷 Скидка %", callback_data="adm:p_type:DISCOUNT_PERCENT"),
    )
    builder.row(
        InlineKeyboardButton(text="💰 Бонус на баланс ₽", callback_data="adm:p_type:BALANCE_BONUS"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 Бонусные дни", callback_data="adm:p_type:BONUS_DAYS"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="adm:promos"))
    return builder.as_markup()


def admin_settings_kb(
    trial_cfg: TrialRuntimeConfig,
    ref_cfg: ReferralRuntimeConfig,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"🎁 Триал: {'ВКЛ' if trial_cfg.enabled else 'ВЫКЛ'}",
            callback_data="adm:set:toggle_trial",
        ),
        InlineKeyboardButton(
            text=f"📅 Дни триала: {trial_cfg.duration_days}",
            callback_data="adm:set:trial_duration_days",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"📊 Трафик триала (ГБ): {trial_cfg.traffic_gb}",
            callback_data="adm:set:trial_traffic_gb",
        ),
        InlineKeyboardButton(
            text=f"📱 Устройства триала: {trial_cfg.device_limit}",
            callback_data="adm:set:trial_device_limit",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"👥 Рефералка: {'ВКЛ' if ref_cfg.enabled else 'ВЫКЛ'}",
            callback_data="adm:set:toggle_ref",
        ),
        InlineKeyboardButton(
            text=f"🎯 Тип: {ref_cfg.reward_type}",
            callback_data="adm:set:toggle_ref_type",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"💸 % реферала: {ref_cfg.reward_percent}%",
            callback_data="adm:set:referral_reward_percent",
        ),
        InlineKeyboardButton(
            text=f"🗓 Дни реферала: {ref_cfg.reward_days}",
            callback_data="adm:set:referral_reward_days",
        ),
    )
    builder.row(InlineKeyboardButton(text="🔙 Админ-панель", callback_data="adm:home"))
    return builder.as_markup()


def admin_broadcast_segment_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Всем пользователям", callback_data="adm:bc_seg:all"),
    )
    builder.row(
        InlineKeyboardButton(text="✅ С активной подпиской", callback_data="adm:bc_seg:active"),
    )
    builder.row(
        InlineKeyboardButton(text="🆕 Без подписки", callback_data="adm:bc_seg:no_subscription"),
    )
    builder.row(
        InlineKeyboardButton(text="⌛ С истёкшей подпиской", callback_data="adm:bc_seg:expired"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Админ-панель", callback_data="adm:home"))
    return builder.as_markup()


def admin_broadcast_button_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏭ Пропустить кнопку", callback_data="adm:bc_skip_btn"))
    builder.row(InlineKeyboardButton(text="❌ Отменить рассылку", callback_data="adm:bc_cancel"))
    return builder.as_markup()


def admin_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data="adm:bc_send"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm:bc_cancel"),
    )
    return builder.as_markup()
