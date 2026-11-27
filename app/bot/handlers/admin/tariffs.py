"""Admin tariff CRUD handlers."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.user.start import safe_edit_or_send
from app.bot.keyboards.admin import admin_tariff_detail_kb, admin_tariffs_kb
from app.bot.states.admin import AdminTariffStates
from app.bot.utils.formatting import escape_html, format_device_limit, format_money
from app.config import Settings
from app.db.models import Tariff
from app.services.remnawave import RemnawaveClient, RemnawaveError

logger = logging.getLogger(__name__)
router = Router(name="admin_tariffs")


def _format_tariff_card(tariff: Tariff) -> str:
    traffic = "Безлимит" if tariff.traffic_limit_gb <= 0 else f"{tariff.traffic_limit_gb} ГБ"
    stars = str(tariff.price_stars) if tariff.price_stars else "Авто"
    squads = ", ".join(tariff.squad_uuids) if tariff.squad_uuids else "По умолчанию (.env)"
    state_str = "✅ Активен" if tariff.is_active else "⏸ Отключён"
    return (
        f"📦 <b>Тариф #{tariff.id}: {escape_html(tariff.name)}</b>\n\n"
        f"📝 <b>Описание:</b> {escape_html(tariff.description) or '—'}\n"
        f"🔘 <b>Статус:</b> {state_str}\n"
        f"💵 <b>Цена RUB:</b> {format_money(tariff.price_rub)}\n"
        f"⭐ <b>Цена Stars:</b> {stars}\n"
        f"📅 <b>Длительность:</b> {tariff.duration_days} дн.\n"
        f"📊 <b>Трафик:</b> {traffic} ({tariff.traffic_limit_strategy})\n"
        f"📱 <b>Устройств:</b> {format_device_limit(tariff.device_limit)}\n"
        f"🌐 <b>Сквады:</b> <code>{escape_html(squads)}</code>\n"
        f"↕️ <b>Сортировка:</b> {tariff.sort_order}"
    )


@router.callback_query(F.data == "adm:tariffs")
async def cb_admin_tariffs(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await state.clear()
    tariffs = (
        await session.execute(select(Tariff).order_by(Tariff.sort_order.asc(), Tariff.id.asc()))
    ).scalars().all()
    await safe_edit_or_send(
        callback,
        "📦 <b>Управление тарифами</b>\n\nВыберите тариф для редактирования или создайте новый:",
        admin_tariffs_kb(list(tariffs)),
    )


@router.callback_query(F.data == "adm:t_new")
async def cb_tariff_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminTariffStates.waiting_name)
    await safe_edit_or_send(
        callback,
        "➕ <b>Создание тарифа (шаг 1/8)</b>\n\nВведите название тарифа (например <code>1 месяц</code>):",
        admin_tariffs_kb([]),
    )


@router.message(AdminTariffStates.waiting_name)
async def msg_tariff_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("⚠️ Название слишком короткое. Введите заново:")
        return
    await state.update_data(name=name[:120])
    await state.set_state(AdminTariffStates.waiting_description)
    await message.answer("Шаг 2/8. Введите описание тарифа (или <code>-</code> чтобы пропустить):")


@router.message(AdminTariffStates.waiting_description)
async def msg_tariff_desc(message: Message, state: FSMContext) -> None:
    desc = (message.text or "").strip()
    await state.update_data(description="" if desc == "-" else desc)
    await state.set_state(AdminTariffStates.waiting_price_rub)
    await message.answer("Шаг 3/8. Введите цену в рублях (например <code>199</code>):")


@router.message(AdminTariffStates.waiting_price_rub)
async def msg_tariff_price_rub(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer("⚠️ Введите корректную цену в рублях:")
        return
    if price <= Decimal("0.00"):
        await message.answer("⚠️ Цена должна быть больше 0:")
        return
    await state.update_data(price_rub=str(price))
    await state.set_state(AdminTariffStates.waiting_price_stars)
    await message.answer(
        "Шаг 4/8. Введите цену в Telegram Stars (целое число, или <code>0</code> для авторасчёта):"
    )


@router.message(AdminTariffStates.waiting_price_stars)
async def msg_tariff_price_stars(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Введите целое число (или 0 для авторасчёта):")
        return
    stars = int(raw)
    await state.update_data(price_stars=stars if stars > 0 else None)
    await state.set_state(AdminTariffStates.waiting_duration_days)
    await message.answer("Шаг 5/8. Введите длительность тарифа в днях (например <code>30</code>):")


@router.message(AdminTariffStates.waiting_duration_days)
async def msg_tariff_days(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Введите положительное целое число дней:")
        return
    await state.update_data(duration_days=int(raw))
    await state.set_state(AdminTariffStates.waiting_traffic_gb)
    await message.answer(
        "Шаг 6/8. Введите лимит трафика в ГБ (<code>0</code> — безлимит):"
    )


@router.message(AdminTariffStates.waiting_traffic_gb)
async def msg_tariff_traffic(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Введите целое число (0 для безлимита):")
        return
    await state.update_data(traffic_limit_gb=int(raw))
    await state.set_state(AdminTariffStates.waiting_device_limit)
    await message.answer(
        "Шаг 7/8. Введите лимит устройств HWID (<code>0</code> — безлимит):"
    )


@router.message(AdminTariffStates.waiting_device_limit)
async def msg_tariff_devices(
    message: Message,
    state: FSMContext,
    remnawave: RemnawaveClient,
) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Введите целое число (0 для безлимита):")
        return
    await state.update_data(device_limit=int(raw))
    await state.set_state(AdminTariffStates.waiting_squads)

    squads_hint = ""
    try:
        squads = await remnawave.get_internal_squads()
        if squads:
            lines = [f"• <b>{escape_html(s.name)}</b>: <code>{escape_html(s.uuid)}</code>" for s in squads]
            squads_hint = "\n\nДоступные сквады в Remnawave:\n" + "\n".join(lines)
    except RemnawaveError as exc:
        logger.debug("Could not fetch squads hint: %s", exc)

    await message.answer(
        "Шаг 8/8. Введите UUID внутренних сквадов через запятую (или <code>-</code> для сквадов из .env):"
        f"{squads_hint}"
    )


@router.message(AdminTariffStates.waiting_squads)
async def msg_tariff_squads(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    raw = (message.text or "").strip()
    squads = [] if raw in {"-", ""} else [s.strip() for s in raw.split(",") if s.strip()]
    data = await state.get_data()

    tariff = Tariff(
        name=data["name"],
        description=data["description"],
        price_rub=Decimal(data["price_rub"]),
        price_stars=data["price_stars"],
        duration_days=int(data["duration_days"]),
        traffic_limit_gb=int(data["traffic_limit_gb"]),
        traffic_limit_strategy=settings.REMNAWAVE_TRAFFIC_STRATEGY,
        device_limit=int(data["device_limit"]),
        squad_uuids=squads,
        is_active=True,
    )
    session.add(tariff)
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Тариф создан!\n\n{_format_tariff_card(tariff)}",
        reply_markup=admin_tariff_detail_kb(tariff),
    )


@router.callback_query(F.data.startswith("adm:t:"))
async def cb_tariff_action(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Некорректный ID тарифа.", show_alert=True)
        return

    tariff = await session.get(Tariff, int(parts[2]))
    if tariff is None:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    if len(parts) == 3:
        await safe_edit_or_send(
            callback,
            _format_tariff_card(tariff),
            admin_tariff_detail_kb(tariff),
        )
        return

    action = parts[3]
    if action == "toggle":
        tariff.is_active = not tariff.is_active
        await session.commit()
        await safe_edit_or_send(
            callback,
            _format_tariff_card(tariff),
            admin_tariff_detail_kb(tariff),
        )
        return

    if action == "edit" and len(parts) == 5:
        field = parts[4]
        await state.update_data(edit_tariff_id=tariff.id, edit_field=field)
        await state.set_state(AdminTariffStates.waiting_edit_value)
        prompts = {
            "price_rub": "Введите новую цену в рублях:",
            "price_stars": "Введите цену в Stars (0 = авторасчёт):",
            "duration_days": "Введите длительность в днях:",
            "traffic_limit_gb": "Введите лимит трафика в ГБ (0 = безлимит):",
            "device_limit": "Введите лимит устройств (0 = безлимит):",
            "squad_uuids": "Введите UUID сквадов через запятую (или - для сброса):",
            "description": "Введите новое описание (или - для очистки):",
            "sort_order": "Введите число порядка сортировки:",
        }
        await safe_edit_or_send(
            callback,
            f"✏️ {prompts.get(field, 'Введите новое значение:')}",
            admin_tariff_detail_kb(tariff),
        )


@router.message(AdminTariffStates.waiting_edit_value)
async def msg_tariff_edit_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    tariff = await session.get(Tariff, int(data["edit_tariff_id"]))
    if tariff is None:
        await state.clear()
        await message.answer("Тариф не найден.")
        return

    field = data["edit_field"]
    raw = (message.text or "").strip()

    try:
        if field == "price_rub":
            val = Decimal(raw.replace(",", ".")).quantize(Decimal("0.01"))
            if val <= 0:
                raise ValueError
            tariff.price_rub = val
        elif field == "price_stars":
            val_int = int(raw)
            tariff.price_stars = val_int if val_int > 0 else None
        elif field == "duration_days":
            val_int = int(raw)
            if val_int <= 0:
                raise ValueError
            tariff.duration_days = val_int
        elif field in {"traffic_limit_gb", "device_limit"}:
            val_int = int(raw)
            if val_int < 0:
                raise ValueError
            setattr(tariff, field, val_int)
        elif field == "sort_order":
            tariff.sort_order = int(raw)
        elif field == "description":
            tariff.description = "" if raw == "-" else raw
        elif field == "squad_uuids":
            tariff.squad_uuids = [] if raw == "-" else [s.strip() for s in raw.split(",") if s.strip()]
    except (ValueError, InvalidOperation):
        await message.answer("⚠️ Некорректное значение. Попробуйте ещё раз:")
        return

    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Параметр обновлён.\n\n{_format_tariff_card(tariff)}",
        reply_markup=admin_tariff_detail_kb(tariff),
    )
