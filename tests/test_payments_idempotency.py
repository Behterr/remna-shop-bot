"""Unit tests for payment providers, CryptoBot HMAC, YooKassa Idempotence-Key, and idempotency."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Subscription, Tariff, Transaction, TransactionStatus, User
from app.services.billing_service import BillingService
from app.services.payments.base import (
    InsufficientBalanceError,
    PaymentCreationRequest,
    PaymentWebhookError,
)
from app.services.payments.cryptobot import CryptoBotProvider
from app.services.payments.yookassa import YooKassaProvider


@pytest.mark.asyncio
async def test_cryptobot_fiat_rub_invoice_and_hmac_verification() -> None:
    token = "12345:SECRET_CRYPTO_KEY"
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen_payloads.append(body)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "invoice_id": 777111,
                    "bot_invoice_url": "https://t.me/CryptoBot?start=IV123",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = CryptoBotProvider(token, http_client=http)
        res = await provider.create_payment(
            PaymentCreationRequest(
                payment_id="pay_abc",
                user_telegram_id=101,
                amount_rub=Decimal("349.00"),
                description="Test VPN",
            )
        )
        assert res.external_id == "777111"
        assert res.currency == "RUB"
        assert seen_payloads[0]["currency_type"] == "fiat"
        assert seen_payloads[0]["fiat"] == "RUB"
        assert seen_payloads[0]["amount"] == "349.00"

        webhook_body = json.dumps(
            {
                "update_type": "invoice_paid",
                "payload": {
                    "invoice_id": 777111,
                    "status": "paid",
                    "amount": "349.00",
                    "fiat": "RUB",
                    "payload": "pay_abc",
                },
            }
        ).encode("utf-8")
        secret = hashlib.sha256(token.encode("utf-8")).digest()
        sig = hmac.new(secret, webhook_body, hashlib.sha256).hexdigest()

        event = await provider.verify_and_parse_webhook(
            webhook_body, {"Crypto-Pay-API-Signature": sig}
        )
        assert event is not None
        assert event.payment_id == "pay_abc"
        assert event.status == "succeeded"

        with pytest.raises(PaymentWebhookError):
            await provider.verify_and_parse_webhook(
                webhook_body, {"Crypto-Pay-API-Signature": "deadbeef"}
            )


@pytest.mark.asyncio
async def test_yookassa_idempotence_key_and_api_verification() -> None:
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v3/payments":
            seen_headers.append(request.headers.get("Idempotence-Key", ""))
            return httpx.Response(
                200,
                json={
                    "id": "yk-ext-999",
                    "status": "pending",
                    "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/999"},
                },
            )
        if request.method == "GET" and request.url.path == "/v3/payments/yk-ext-999":
            return httpx.Response(
                200,
                json={
                    "id": "yk-ext-999",
                    "status": "succeeded",
                    "amount": {"value": "250.00", "currency": "RUB"},
                    "metadata": {"payment_id": "pay_yk_1"},
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = YooKassaProvider("1001", "sec", http_client=http)
        created = await provider.create_payment(
            PaymentCreationRequest(
                payment_id="pay_yk_1",
                user_telegram_id=101,
                amount_rub=Decimal("250.00"),
                description="VPN order",
            )
        )
        assert seen_headers == ["pay_yk_1"]
        assert created.external_id == "yk-ext-999"

        raw_hook = json.dumps(
            {
                "event": "payment.succeeded",
                "object": {"id": "yk-ext-999", "status": "succeeded"},
            }
        ).encode()
        event = await provider.verify_and_parse_webhook(raw_hook, {})
        assert event is not None
        assert event.payment_id == "pay_yk_1"
        assert event.status == "succeeded"


@pytest.mark.asyncio
async def test_double_fulfillment_and_balance_idempotency(
    db, fake_remnawave, test_settings
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "invoice_id": 555000,
                    "bot_invoice_url": "https://t.me/CryptoBot?start=IV555",
                },
            },
        )

    async with db.session() as session:
        user = User(telegram_id=30001, full_name="Payer", balance=Decimal("50.00"))
        tariff = Tariff(
            name="1 Month",
            price_rub=Decimal("199.00"),
            duration_days=30,
            traffic_limit_gb=50,
            device_limit=3,
        )
        session.add_all([user, tariff])
        await session.commit()

        # Insufficient balance check
        with pytest.raises(InsufficientBalanceError):
            await BillingService.pay_tariff_with_balance(
                session=session,
                user=user,
                tariff=tariff,
                remnawave=fake_remnawave,
                settings=test_settings,
            )

        # Deposit checkout + duplicate fulfillment idempotency
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            cb_provider = CryptoBotProvider("tok", http_client=http)
            dep_tx, _ = await BillingService.create_deposit_checkout(
                session=session,
                user=user,
                amount_rub=Decimal("300.00"),
                provider=cb_provider,
                settings=test_settings,
            )
            await session.commit()

        first = await BillingService.fulfill_payment(
            session=session,
            payment_id=dep_tx.payment_id,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()
        assert first.already_processed is False
        assert user.balance == Decimal("350.00")

        second = await BillingService.fulfill_payment(
            session=session,
            payment_id=dep_tx.payment_id,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()
        assert second.already_processed is True
        assert user.balance == Decimal("350.00")

        # Balance purchase idempotency with explicit payment_id
        b1 = await BillingService.pay_tariff_with_balance(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
            idempotent_payment_id="bal_fixed_1",
        )
        await session.commit()
        b2 = await BillingService.pay_tariff_with_balance(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
            idempotent_payment_id="bal_fixed_1",
        )
        await session.commit()

        assert b1.already_processed is False
        assert b2.already_processed is True
        assert user.balance == Decimal("151.00")
        sub = (await session.execute(select(Subscription))).scalar_one()
        assert sub.status.value == "ACTIVE"
        tx = (
            await session.execute(
                select(Transaction).where(Transaction.payment_id == "bal_fixed_1")
            )
        ).scalar_one()
        assert tx.status == TransactionStatus.SUCCEEDED
