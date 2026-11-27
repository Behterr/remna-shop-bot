"""Admin handlers router protected by IsAdminFilter."""

from aiogram import Router

from app.bot.filters.admin import IsAdminFilter
from app.bot.handlers.admin import (
    broadcast,
    menu,
    promocodes,
    settings,
    tariffs,
    users,
)

admin_router = Router(name="admin")
admin_router.message.filter(IsAdminFilter())
admin_router.callback_query.filter(IsAdminFilter())

admin_router.include_router(menu.router)
admin_router.include_router(users.router)
admin_router.include_router(tariffs.router)
admin_router.include_router(promocodes.router)
admin_router.include_router(settings.router)
admin_router.include_router(broadcast.router)
