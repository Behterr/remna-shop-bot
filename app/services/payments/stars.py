"""Telegram Stars (XTR) payment provider."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from aiogram.types import LabeledPrice

from app.db.models import PaymentProviderType
from app.services.payments.base import (
    BasePaymentProvider,
    PaymentCreationRequest,
    PaymentCreationResult,
    WebhookPaymentEvent,
)


class TelegramStarsProvider(BasePaymentProvider):
    """Handles Telegram Stars (XTR) invoices and checkout payload building."""

    provider_type = PaymentProviderType.TELEGRAM_STARS

    def __init__(self, stars_rub_rate: float = 1.8) -> None:
        self.stars_rub_rate = max(0.1, float(stars_rub_rate))

    def calculate_stars(
        self,
        amount_rub: Decimal,
        explicit_stars: int | None = None,
    ) -> int:
        """Determine Stars price either from explicit value or RUB rate."""
        if explicit_stars is not None and explicit_stars > 0:
            return int(explicit_stars)
        return max(1, math.ceil(float(amount_rub) / self.stars_rub_rate))

    async def create_payment(self, request: PaymentCreationRequest) -> PaymentCreationResult:
        stars = self.calculate_stars(request.amount_rub, request.stars_amount)
        return PaymentCreationResult(
            provider=self.provider_type,
            payment_id=request.payment_id,
            amount=Decimal(stars),
            currency="XTR",
            external_id=None,
            payment_url=None,
            raw_payload={"stars": stars},
        )

    def build_invoice_params(
        self,
        *,
        chat_id: int,
        title: str,
        description: str,
        payment_id: str,
        stars_amount: int,
    ) -> dict[str, Any]:
        """Build parameters for aiogram Bot.send_invoice using XTR currency."""
        return {
            "chat_id": chat_id,
            "title": title[:32],
            "description": description[:255],
            "payload": payment_id,
            "provider_token": "",
            "currency": "XTR",
            "prices": [LabeledPrice(label=title[:32], amount=max(1, int(stars_amount)))],
        }

    async def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookPaymentEvent | None:
        return None
