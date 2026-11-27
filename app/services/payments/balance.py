"""Internal balance payment provider."""

from __future__ import annotations

from app.db.models import PaymentProviderType
from app.services.payments.base import (
    BasePaymentProvider,
    PaymentCreationRequest,
    PaymentCreationResult,
    WebhookPaymentEvent,
)


class BalancePaymentProvider(BasePaymentProvider):
    """Instant payment using internal user balance stored in PostgreSQL."""

    provider_type = PaymentProviderType.BALANCE

    async def create_payment(self, request: PaymentCreationRequest) -> PaymentCreationResult:
        return PaymentCreationResult(
            provider=self.provider_type,
            payment_id=request.payment_id,
            amount=request.amount_rub,
            currency="RUB",
            external_id=request.payment_id,
            payment_url=None,
            raw_payload={"mode": "internal_balance"},
        )

    async def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookPaymentEvent | None:
        return None
