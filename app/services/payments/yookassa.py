"""YooKassa API v3 payment provider with Idempotence-Key and authoritative API status check."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx

from app.db.models import PaymentProviderType
from app.services.payments.base import (
    BasePaymentProvider,
    PaymentCreationRequest,
    PaymentCreationResult,
    PaymentError,
    PaymentWebhookError,
    WebhookPaymentEvent,
)


class YooKassaProvider(BasePaymentProvider):
    """Async YooKassa REST v3 integration via httpx."""

    provider_type = PaymentProviderType.YOOKASSA
    API_BASE = "https://api.yookassa.ru/v3"

    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        default_return_url: str = "",
        trust_webhook_without_api_check: bool = False,
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.shop_id = shop_id.strip()
        self.secret_key = secret_key.strip()
        self.default_return_url = default_return_url.strip()
        self.trust_webhook_without_api_check = trust_webhook_without_api_check
        self.timeout = timeout
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _ensure_configured(self) -> None:
        if not self.shop_id or not self.secret_key:
            raise PaymentError("YooKassa shop_id or secret_key is not configured.")

    def _auth(self) -> tuple[str, str]:
        return (self.shop_id, self.secret_key)

    async def create_payment(self, request: PaymentCreationRequest) -> PaymentCreationResult:
        self._ensure_configured()
        return_url = request.return_url or self.default_return_url or "https://t.me"
        body: dict[str, Any] = {
            "amount": {
                "value": f"{request.amount_rub:.2f}",
                "currency": "RUB",
            },
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
            "capture": True,
            "description": request.description[:128],
            "metadata": {
                "payment_id": request.payment_id,
                **request.metadata,
            },
        }

        response = await self._client.post(
            f"{self.API_BASE}/payments",
            auth=self._auth(),
            headers={"Idempotence-Key": request.payment_id},
            json=body,
        )
        if response.status_code >= 400:
            raise PaymentError(f"YooKassa HTTP {response.status_code}: {response.text}")

        data = response.json()
        confirmation = data.get("confirmation") or {}
        return PaymentCreationResult(
            provider=self.provider_type,
            payment_id=request.payment_id,
            amount=request.amount_rub,
            currency="RUB",
            external_id=str(data["id"]),
            payment_url=confirmation.get("confirmation_url"),
            raw_payload=data,
        )

    async def fetch_payment(self, external_id: str) -> dict[str, Any]:
        """Retrieve authoritative payment object from YooKassa API."""
        self._ensure_configured()
        response = await self._client.get(
            f"{self.API_BASE}/payments/{external_id}",
            auth=self._auth(),
        )
        if response.status_code >= 400:
            raise PaymentWebhookError(
                f"YooKassa verification HTTP {response.status_code}: {response.text}"
            )
        return response.json()

    async def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookPaymentEvent | None:
        self._ensure_configured()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentWebhookError(f"Invalid YooKassa JSON body: {exc}") from exc

        event_name = str(payload.get("event") or "")
        obj = payload.get("object")
        if not isinstance(obj, dict) or not obj.get("id"):
            raise PaymentWebhookError("YooKassa webhook missing payment object id.")

        external_id = str(obj["id"])

        if not self.trust_webhook_without_api_check:
            obj = await self.fetch_payment(external_id)

        raw_status = str(obj.get("status") or "").lower()
        if raw_status == "succeeded" or (
            self.trust_webhook_without_api_check and event_name == "payment.succeeded"
        ):
            status = "succeeded"
        elif raw_status == "canceled" or event_name == "payment.canceled":
            status = "canceled"
        else:
            status = "pending"

        metadata = obj.get("metadata") or {}
        internal_payment_id = str(metadata.get("payment_id") or "")
        if not internal_payment_id:
            raise PaymentWebhookError("YooKassa payment metadata missing payment_id.")

        amount_obj = obj.get("amount") or {}
        amount = Decimal(str(amount_obj.get("value") or "0.00"))
        currency = str(amount_obj.get("currency") or "RUB")

        return WebhookPaymentEvent(
            provider=self.provider_type,
            payment_id=internal_payment_id,
            external_id=external_id,
            status=status,
            amount=amount,
            currency=currency,
            metadata=metadata,
            raw_payload=obj,
        )
