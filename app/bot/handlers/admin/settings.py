"""Admin runtime settings handlers for trial and referral configuration."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.admin import admin_settings_kb
from app.bot.states.admin import AdminSettingsStates
from app.config import Settings
from app.services.settings_service import SettingsService

router = Router(name="admin_settings")


async def _render_settings(session: AsyncSession, settings: Settings) -> tuple[str, object]:
    trial_cfg = await SettingsService.get_trial_config(session, settings)
    ref_cfg = await SettingsService.get_referral_config(session, settings)
    text = (
        "⚙️ <b>Настройки триала и реферальной программы</b>\n\n"
        f"🎁 <b>Триал:</b> {'Включён' if trial_cfg.enabled else 'Отключён'} | "
        f"{trial_cfg.duration_days} дн. | {trial_cfg.traffic_gb} ГБ | "
        f"{trial_cfg.device_limit} устр.\n"
        f"👥 <b>Рефералка:</b> {'Включена' if ref_cfg.enabled else 'Отключена'} | "
        f"Режим: {ref_cfg.reward_type} ({ref_cfg.reward_percent}% / {ref_cfg.reward_days} дн.)"
    )
    return text, admin_settings_kb(trial_cfg, ref_cfg)


@router.callback_query(F.data == "adm:settings")
async def cb_admin_settings(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await state.clear()
    text, kb = await _render_settings(session, settings)
    await safe_edit_or_send(callback, text, kb)


@router.callback_query(F.data.startswith("adm:set:"))
async def cb_admin_setting_action(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    key = (callback.data or "").split(":", maxsplit=2)[-1]
    trial_cfg = await SettingsService.get_trial_config(session, settings)
    ref_cfg = await SettingsService.get_referral_config(session, settings)

    if key == "toggle_trial":
        await SettingsService.set_value(
            session,
            SettingsService.KEY_TRIAL_ENABLED,
            "false" if trial_cfg.enabled else "true",
        )
        await session.commit()
        text, kb = await _render_settings(session, settings)
        await safe_edit_or_send(callback, text, kb)
        return

    if key == "toggle_ref":
        await SettingsService.set_value(
            session,
            SettingsService.KEY_REFERRAL_ENABLED,
            "false" if ref_cfg.enabled else "true",
        )
        await session.commit()
        text, kb = await _render_settings(session, settings)
        await safe_edit_or_send(callback, text, kb)
        return

    if key == "toggle_ref_type":
        next_type = "days" if ref_cfg.reward_type == "percent" else "percent"
        await SettingsService.set_value(
            session,
            SettingsService.KEY_REFERRAL_REWARD_TYPE,
            next_type,
        )
        await session.commit()
        text, kb = await _render_settings(session, settings)
        await safe_edit_or_send(callback, text, kb)
        return

    allowed_numeric = {
        "trial_duration_days": "Введите длительность триала (в днях, >= 1):",
        "trial_traffic_gb": "Введите лимит трафика триала в ГБ (0 = безлимит):",
        "trial_device_limit": "Введите лимит устройств триала (0 = безлимит):",
        "referral_reward_percent": "Введите процент реферального вознаграждения:",
        "referral_reward_days": "Введите количество бонусных дней для реферера:",
    }
    if key in allowed_numeric:
        await state.update_data(setting_key=key)
        await state.set_state(AdminSettingsStates.waiting_value)
        await safe_edit_or_send(callback, allowed_numeric[key], admin_settings_kb(trial_cfg, ref_cfg))


@router.message(AdminSettingsStates.waiting_value)
async def msg_admin_setting_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Введите целое неотрицательное число:")
        return

    val = int(raw)
    data = await state.get_data()
    key = data["setting_key"]
    if key == "trial_duration_days" and val < 1:
        await message.answer("⚠️ Длительность триала должна быть >= 1 дня:")
        return

    await SettingsService.set_value(session, key, str(val))
    await session.commit()
    await state.clear()

    text, kb = await _render_settings(session, settings)
    await message.answer(f"✅ Настройка сохранена.\n\n{text}", reply_markup=kb)
