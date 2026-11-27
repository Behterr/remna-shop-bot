"""Integration tests for FastAPI routes (/health, /connect/{app}, CryptoBot webhook) and QR."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Dispatcher

from app.bot.utils.formatting import build_connect_redirect_url
from app.bot.utils.qr import generate_qr_png
from app.db.models import PaymentProviderType, Transaction, TransactionStatus, TransactionType, User
from app.web.app import create_web_app


def test_qr_png_generation_in_memory() -> None:
    png = generate_qr_png("https://sub.example.com/sub/test-uuid")
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 200


@pytest.mark.asyncio
async def test_web_health_connect_redirect_and_cryptobot_webhook(
    db, fake_remnawave, test_settings
) -> None:
    bot_mock = AsyncMock()
    dp_mock = Dispatcher()
    app = create_web_app(
        settings=test_settings,
        database=db,
        redis=None,
        remnawave=fake_remnawave,
        bot=bot_mock,
        dispatcher=dp_mock,
    )

    async with db.session() as session:
        user = User(telegram_id=50001, full_name="HookUser")
        session.add(user)
        await session.flush()
        tx = Transaction(
            user_id=user.id,
            payment_id="dep_hook_1",
            external_id="88888",
            provider=PaymentProviderType.CRYPTOBOT,
            tx_type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            amount=Decimal("400.00"),
            currency="RUB",
            metadata_json={"rub_amount": "400.00"},
        )
        session.add(tx)
        await session.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. /health
        h = await client.get("/health")
        assert h.status_code == 200
        assert h.json()["postgres"] == "ok"

        # 2. /connect/{app} valid signed redirect
        sub_url = "https://sub.example.com/sub/abc123"
        full_redirect_url = build_connect_redirect_url(
            test_settings.WEBHOOK_DOMAIN,
            test_settings.BOT_TOKEN.get_secret_value(),
            "happ",
            sub_url,
        )
        assert full_redirect_url is not None
        path_with_query = full_redirect_url.replace("https://bot.example.com", "")
        r_ok = await client.get(path_with_query, follow_redirects=False)
        assert r_ok.status_code == 302
        assert r_ok.headers["location"] == f"happ://add/{sub_url}"

        # 3. /connect/{app} blocks invalid signature, unsupported app, and unsafe schemes
        r_bad_sig = await client.get(
            "/connect/happ",
            params={"url": sub_url, "sig": "0" * 24},
            follow_redirects=False,
        )
        assert r_bad_sig.status_code == 403

        r_bad_scheme = await client.get(
            "/connect/happ",
            params={"url": "javascript:alert(1)", "sig": "0" * 24},
            follow_redirects=False,
        )
        assert r_bad_scheme.status_code == 400

        r_bad_app = await client.get(
            "/connect/evilapp",
            params={"url": sub_url, "sig": "0" * 24},
            follow_redirects=False,
        )
        assert r_bad_app.status_code == 404

        # 4. Signed CryptoBot webhook + duplicate delivery idempotency
        raw_body = json.dumps(
            {
                "update_type": "invoice_paid",
                "payload": {
                    "invoice_id": 88888,
                    "status": "paid",
                    "amount": "400.00",
                    "fiat": "RUB",
                    "payload": "dep_hook_1",
                },
            }
        ).encode("utf-8")
        secret = hashlib.sha256(
            test_settings.CRYPTOBOT_TOKEN.get_secret_value().encode("utf-8")
        ).digest()
        sig = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

        w1 = await client.post(
            "/webhooks/cryptobot",
            content=raw_body,
            headers={"crypto-pay-api-signature": sig},
        )
        assert w1.status_code == 200
        w2 = await client.post(
            "/webhooks/cryptobot",
            content=raw_body,
            headers={"crypto-pay-api-signature": sig},
        )
        assert w2.status_code == 200

    async with db.session() as session:
        refreshed = await session.get(User, 1)
        assert refreshed is not None
        assert refreshed.balance == Decimal("400.00")
        assert bot_mock.send_message.await_count == 1
