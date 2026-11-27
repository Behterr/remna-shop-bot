"""User-facing inline keyboards in Russian."""

from __future__ import annotations

from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.utils.formatting import (
    CONNECT_APP_SCHEMES,
    build_connect_redirect_url,
    format_money,
)
from app.config import Settings
from app.db.models import Subscription, Tariff, User


def main_menu_kb(*, show_trial: bool, is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🛒 Купить / Продлить", callback_data="menu:buy"),
        InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu:sub"),
    )
    if show_trial:
        builder.row(
            InlineKeyboardButton(text="🎁 Пробный период", callback_data="menu:trial"),
        )
    builder.row(
        InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="menu:balance"),
        InlineKeyboardButton(text="👥 Рефералы", callback_data="menu:referrals"),
    )
    builder.row(
        InlineKeyboardButton(text="🎟 Промокод", callback_data="menu:promo"),
        InlineKeyboardButton(text="🆘 Поддержка", callback_data="menu:support"),
    )
    if is_admin:
        builder.row(
            InlineKeyboardButton(text="🛠 Админ-панель", callback_data="adm:home"),
        )
    return builder.as_markup()


def back_to_menu_kb(extra_buttons: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if extra_buttons:
        for btn in extra_buttons:
            builder.row(btn)
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:home"))
    return builder.as_markup()


def tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        price_str = format_money(tariff.price_rub)
        builder.row(
            InlineKeyboardButton(
                text=f"⚡ {tariff.name} — {tariff.duration_days} дн. ({price_str})",
                callback_data=f"tariff:{tariff.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="menu:promo"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:home"))
    return builder.as_markup()


def tariff_payment_kb(
    *,
    tariff: Tariff,
    final_rub: Decimal,
    stars_amount: int,
    user_balance: Decimal,
    settings: Settings,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    bal_label = f"💰 С баланса ({format_money(user_balance)})"
    builder.row(
        InlineKeyboardButton(text=bal_label, callback_data=f"pay:{tariff.id}:balance"),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"⭐ Telegram Stars ({stars_amount} XTR)",
            callback_data=f"pay:{tariff.id}:stars",
        )
    )
    if settings.is_cryptobot_enabled:
        builder.row(
            InlineKeyboardButton(
                text=f"💎 CryptoBot ({format_money(final_rub)})",
                callback_data=f"pay:{tariff.id}:cryptobot",
            )
        )
    if settings.is_yookassa_enabled:
        builder.row(
            InlineKeyboardButton(
                text=f"💳 YooKassa ({format_money(final_rub)})",
                callback_data=f"pay:{tariff.id}:yookassa",
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 К тарифам", callback_data="menu:buy"))
    return builder.as_markup()


def subscription_kb(
    *,
    sub: Subscription | None,
    user: User,
    settings: Settings,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if sub is not None and sub.subscription_url:
        if settings.WEBHOOK_DOMAIN.startswith("https://"):
            secret = settings.BOT_TOKEN.get_secret_value()
            app_buttons: list[InlineKeyboardButton] = []
            for slug, (title, _) in CONNECT_APP_SCHEMES.items():
                redirect_url = build_connect_redirect_url(
                    settings.WEBHOOK_DOMAIN,
                    secret,
                    slug,
                    sub.subscription_url,
                )
                if redirect_url:
                    app_buttons.append(
                        InlineKeyboardButton(text=f"📲 {title}", url=redirect_url)
                    )
            for i in range(0, len(app_buttons), 2):
                builder.row(*app_buttons[i : i + 2])

        builder.row(
            InlineKeyboardButton(text="📷 QR-код подписки", callback_data="sub:qr"),
            InlineKeyboardButton(text="📖 Инструкция", callback_data="sub:guides"),
        )
        auto_label = "🔄 Автопродление: ВКЛ ✅" if user.auto_renew else "🔄 Автопродление: ВЫКЛ ❌"
        builder.row(
            InlineKeyboardButton(text=auto_label, callback_data="sub:toggle_autorenew"),
        )

    builder.row(
        InlineKeyboardButton(text="🛒 Купить / Продлить", callback_data="menu:buy"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:home"),
    )
    return builder.as_markup()


def client_guides_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📱 Happ (iOS / Android)", callback_data="sub:guide:happ"),
        InlineKeyboardButton(text="⚡ v2rayTUN (iOS / Android)", callback_data="sub:guide:v2raytun"),
    )
    builder.row(
        InlineKeyboardButton(text="🛡 Hiddify (All OS)", callback_data="sub:guide:hiddify"),
        InlineKeyboardButton(text="🧭 Streisand (iOS)", callback_data="sub:guide:streisand"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Моя подписка", callback_data="menu:sub"),
    )
    return builder.as_markup()


def deposit_amounts_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="100 ₽", callback_data="dep_amt:100"),
        InlineKeyboardButton(text="300 ₽", callback_data="dep_amt:300"),
        InlineKeyboardButton(text="500 ₽", callback_data="dep_amt:500"),
    )
    builder.row(
        InlineKeyboardButton(text="1000 ₽", callback_data="dep_amt:1000"),
        InlineKeyboardButton(text="✏️ Другая сумма", callback_data="dep_amt:custom"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:home"))
    return builder.as_markup()


def deposit_methods_kb(amount_rub: Decimal, settings: Settings, stars_amount: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    amt_str = str(int(amount_rub)) if amount_rub == amount_rub.to_integral_value() else f"{amount_rub:.2f}"
    builder.row(
        InlineKeyboardButton(
            text=f"⭐ Telegram Stars ({stars_amount} XTR)",
            callback_data=f"dep_pay:{amt_str}:stars",
        )
    )
    if settings.is_cryptobot_enabled:
        builder.row(
            InlineKeyboardButton(
                text=f"💎 CryptoBot ({format_money(amount_rub)})",
                callback_data=f"dep_pay:{amt_str}:cryptobot",
            )
        )
    if settings.is_yookassa_enabled:
        builder.row(
            InlineKeyboardButton(
                text=f"💳 YooKassa ({format_money(amount_rub)})",
                callback_data=f"dep_pay:{amt_str}:yookassa",
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 Выбрать другую сумму", callback_data="menu:balance"))
    return builder.as_markup()


def payment_url_kb(pay_url: str, back_callback: str = "menu:home") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_url))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback))
    return builder.as_markup()


def support_kb(support_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if support_url.startswith("http://") or support_url.startswith("https://"):
        builder.row(InlineKeyboardButton(text="💬 Написать в поддержку", url=support_url))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu:home"))
    return builder.as_markup()
