"""Optional Telegram Bot webhook receiver endpoint."""

from __future__ import annotations

import hmac

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status

router = APIRouter(prefix="/webhooks", tags=["telegram"])


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    settings = request.app.state.settings
    expected_secret = settings.BOT_WEBHOOK_SECRET.strip()
    if expected_secret:
        if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            expected_secret,
            x_telegram_bot_api_secret_token,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret token")

    payload = await request.json()
    update = Update.model_validate(payload)
    dp = request.app.state.dispatcher
    bot = request.app.state.bot
    await dp.feed_update(bot, update)
    return {"ok": True}
