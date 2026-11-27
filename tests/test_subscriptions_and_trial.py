"""Unit tests for trial issuance, subscription creation/extension, flag resets, and batch traffic alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.db.models import Subscription, SubscriptionStatus, Tariff, User
from app.services.subscription_service import (
    SubscriptionService,
    TrialAlreadyUsedError,
)
from app.tasks.jobs import process_expiry_and_autorenew, sync_traffic_and_notify


@pytest.mark.asyncio
async def test_trial_issued_strictly_once(db, fake_remnawave, test_settings) -> None:
    async with db.session() as session:
        user = User(telegram_id=10001, username="alice", full_name="Alice")
        session.add(user)
        await session.commit()

        sub = await SubscriptionService.activate_trial(
            session=session,
            user=user,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()

        assert sub.is_trial is True
        assert sub.status == SubscriptionStatus.ACTIVE
        assert user.trial_used is True

        with pytest.raises(TrialAlreadyUsedError):
            await SubscriptionService.activate_trial(
                session=session,
                user=user,
                remnawave=fake_remnawave,
                settings=test_settings,
            )


@pytest.mark.asyncio
async def test_renewal_resets_expiry_and_traffic_notification_flags(
    db, fake_remnawave, test_settings
) -> None:
    async with db.session() as session:
        user = User(telegram_id=10002, username="bob", full_name="Bob")
        tariff = Tariff(
            name="Pro 30d",
            price_rub=Decimal("299.00"),
            duration_days=30,
            traffic_limit_gb=50,
            device_limit=3,
            squad_uuids=["squad-1"],
        )
        session.add_all([user, tariff])
        await session.commit()

        sub = await SubscriptionService.apply_tariff_purchase(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        sub.notified_3d = True
        sub.notified_1d = True
        sub.notified_expired = True
        sub.notified_traffic_85 = True
        sub.notified_traffic_100 = True
        await session.commit()

        first_expire = sub.expires_at
        sub = await SubscriptionService.apply_tariff_purchase(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()

        assert sub.expires_at > first_expire + timedelta(days=29)
        assert sub.notified_3d is False
        assert sub.notified_1d is False
        assert sub.notified_expired is False
        assert sub.notified_traffic_85 is False
        assert sub.notified_traffic_100 is False


@pytest.mark.asyncio
async def test_batch_traffic_sync_and_autorenew_jobs(db, fake_remnawave, test_settings) -> None:
    bot_mock = AsyncMock()

    async with db.session() as session:
        user = User(
            telegram_id=10003,
            username="carol",
            full_name="Carol",
            balance=Decimal("500.00"),
            auto_renew=True,
        )
        tariff = Tariff(
            name="Monthly",
            price_rub=Decimal("200.00"),
            duration_days=30,
            traffic_limit_gb=10,
            device_limit=2,
            squad_uuids=["squad-1"],
        )
        session.add_all([user, tariff])
        await session.commit()

        sub = await SubscriptionService.apply_tariff_purchase(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        # Simulate 90% traffic usage in Remnawave
        rw_user = fake_remnawave.users_by_uuid[sub.remnawave_uuid]
        rw_user.used_traffic_bytes = int(sub.traffic_limit_bytes * 0.90)
        await session.commit()

    await sync_traffic_and_notify(database=db, remnawave=fake_remnawave, bot=bot_mock)

    assert "iter_all_users" in fake_remnawave.calls
    assert fake_remnawave.calls.count("get_user_by_uuid") == 0
    assert bot_mock.send_message.await_count == 1
    assert "85%" in bot_mock.send_message.call_args.kwargs["text"]

    # Force subscription expiration to test auto-renewal job
    async with db.session() as session:
        sub = (await session.execute(select(Subscription))).scalar_one()
        expired_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
        sub.expires_at = expired_dt
        fake_remnawave.users_by_uuid[sub.remnawave_uuid].expire_at = expired_dt
        await session.commit()

    await process_expiry_and_autorenew(
        database=db,
        remnawave=fake_remnawave,
        bot=bot_mock,
        settings=test_settings,
    )

    async with db.session() as session:
        user = (await session.execute(select(User).where(User.telegram_id == 10003))).scalar_one()
        sub = (await session.execute(select(Subscription))).scalar_one()
        sub_exp = sub.expires_at if sub.expires_at.tzinfo else sub.expires_at.replace(tzinfo=timezone.utc)
        assert user.balance == Decimal("300.00")
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub_exp > datetime.now(timezone.utc) + timedelta(days=25)
