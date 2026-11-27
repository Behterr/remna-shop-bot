"""User-facing handlers router aggregation."""

from aiogram import Router

from app.bot.handlers.user import (
    balance,
    promocode,
    purchase,
    referrals,
    start,
    subscription,
    support,
    trial,
)

user_router = Router(name="user")
user_router.include_router(start.router)
user_router.include_router(trial.router)
user_router.include_router(subscription.router)
user_router.include_router(purchase.router)
user_router.include_router(balance.router)
user_router.include_router(referrals.router)
user_router.include_router(promocode.router)
user_router.include_router(support.router)
