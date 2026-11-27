"""User-facing FSM states."""

from aiogram.fsm.state import State, StatesGroup


class TopUpStates(StatesGroup):
    waiting_amount = State()


class PromoStates(StatesGroup):
    waiting_code = State()
