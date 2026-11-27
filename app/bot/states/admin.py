"""Admin panel FSM states."""

from aiogram.fsm.state import State, StatesGroup


class AdminUserStates(StatesGroup):
    waiting_search_query = State()
    waiting_balance_delta = State()
    waiting_grant_days = State()


class AdminTariffStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_price_rub = State()
    waiting_price_stars = State()
    waiting_duration_days = State()
    waiting_traffic_gb = State()
    waiting_device_limit = State()
    waiting_squads = State()
    waiting_edit_value = State()


class AdminPromoStates(StatesGroup):
    waiting_code = State()
    waiting_value = State()
    waiting_max_uses = State()
    waiting_expiry_days = State()


class AdminSettingsStates(StatesGroup):
    waiting_value = State()


class AdminBroadcastStates(StatesGroup):
    waiting_content = State()
    waiting_button = State()
    waiting_confirm = State()
