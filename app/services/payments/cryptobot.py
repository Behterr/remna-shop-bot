"""CryptoBot (Crypto Pay API) payment provider with RUB fiat invoices and HMAC verification."""

from __future__ import annotations

import hashlib
import hmac
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


class CryptoBotProvider(BasePaymentProvider):
    """Crypto Pay API client for RUB-denominated fiat invoices and signed webhooks."""

    provider_type = PaymentProviderType.CRYPTOBOT

    def __init__(
        self,
        api_token: str,
        *,
        testnet: bool = False,
        accepted_assets: str = "USDT,TON,BTC,ETH,LTC,TRX,USDC",
        timeout: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_token = api_token.strip()
        self.base_url = (
            "https://testnet-pay.crypt.bot/api"
            if testnet
            else "https://pay.crypt.bot/api"
        )
        self.accepted_assets = accepted_assets.strip()
        self.timeout = timeout
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _ensure_configured(self) -> None:
        if not self.api_token:
            raise PaymentError("CryptoBot API token is not configured.")

    @staticmethod
    def verify_signature(raw_body: bytes, incoming_signature: str, api_token: str) -> bool:
        """Verify crypto-pay-api-signature header via HMAC-SHA256(SHA256(token), raw_body)."""
        if not incoming_signature or not api_token:
            return False
        secret = hashlib.sha256(api_token.encode("utf-8")).digest()
        expected = hmac.new(secret, msg=raw_body, digestmod=hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, incoming_signature.strip().lower())

    async def create_payment(self, request: PaymentCreationRequest) -> PaymentCreationResult:
        self._ensure_configured()
        body: dict[str, Any] = {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": f"{request.amount_rub:.2f}",
            "description": request.description[:1024],
            "payload": request.payment_id,
            "expires_in": 3600,
        }
        if self.accepted_assets:
            body["accepted_assets"] = self.accepted_assets

        response = await self._client.post(
            f"{self.base_url}/createInvoice",
            headers={"Crypto-Pay-API-Token": self.api_token},
            json=body,
        )
        if response.status_code >= 400:
            raise PaymentError(f"CryptoBot HTTP {response.status_code}: {response.text}")

        data = response.json()
        if not data.get("ok"):
            err = data.get("error", {})
            raise PaymentError(f"CryptoBot API error: {err}")

        result = data["result"]
        invoice_url = (
            result.get("bot_invoice_url")
            or result.get("pay_url")
            or result.get("mini_app_invoice_url")
        )
        return PaymentCreationResult(
            provider=self.provider_type,
            payment_id=request.payment_id,
            amount=request.amount_rub,
            currency="RUB",
            external_id=str(result["invoice_id"]),
            payment_url=invoice_url,
            raw_payload=result,
        )

    async def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookPaymentEvent | None:
        self._ensure_configured()
        normalized_headers = {k.lower(): v for k, v in headers.items()}
        signature = normalized_headers.get("crypto-pay-api-signature", "")
        if not self.verify_signature(raw_body, signature, self.api_token):
            raise PaymentWebhookError("Invalid CryptoBot webhook signature.")

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaymentWebhookError(f"Invalid CryptoBot JSON body: {exc}") from exc

        update_type = data.get("update_type")
        if update_type != "invoice_paid":
            return None

        payload_obj = data.get("payload") or {}
        internal_payment_id = str(payload_obj.get("payload") or "")
        external_id = str(payload_obj.get("invoice_id") or "")
        status = "succeeded" if payload_obj.get("status") == "paid" else "pending"
        amount = Decimal(str(payload_obj.get("amount") or "0.00"))
        currency = str(payload_obj.get("fiat") or payload_obj.get("asset") or "RUB")

        if not internal_payment_id:
            raise PaymentWebhookError("CryptoBot webhook missing internal payment_id payload.")

        return WebhookPaymentEvent(
            provider=self.provider_type,
            payment_id=internal_payment_id,
            external_id=external_id or None,
            status=status,
            amount=amount,
            currency=currency,
            metadata={"asset": payload_obj.get("paid_asset"), "paid_amount": payload_obj.get("paid_amount")},
            raw_payload=data,
        )
