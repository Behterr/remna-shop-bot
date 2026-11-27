"""Unit tests for referral attribution, percent/days rewards, and self-referral guard."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.models import Tariff, User
from app.services.billing_service import BillingService
from app.services.referral_service import ReferralService
from app.services.settings_service import SettingsService


@pytest.mark.asyncio
async def test_referral_percent_reward_and_self_referral_guard(
    db, fake_remnawave, test_settings
) -> None:
    async with db.session() as session:
        referrer = User(telegram_id=40001, full_name="Ref")
        friend = User(telegram_id=40002, full_name="Friend", balance=Decimal("500.00"))
        tariff = Tariff(
            name="Base",
            price_rub=Decimal("200.00"),
            duration_days=30,
            traffic_limit_gb=0,
            device_limit=2,
        )
        session.add_all([referrer, friend, tariff])
        await session.commit()

        assert await ReferralService.bind_referrer(session, referrer, 40001) is False
        assert await ReferralService.bind_referrer(session, friend, 40001) is True
        # Second bind attempt cannot overwrite existing referrer
        assert await ReferralService.bind_referrer(session, friend, 99999) is False
        await session.commit()

        res = await BillingService.pay_tariff_with_balance(
            session=session,
            user=friend,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()

        # 15% of 200 RUB = 30.00 RUB
        assert res.referral_reward is not None
        assert res.referral_reward.amount_rub == Decimal("30.00")
        assert referrer.balance == Decimal("30.00")

        stats = await ReferralService.get_stats(session, referrer.id)
        assert stats.invited_count == 1
        assert stats.total_bonus_rub == Decimal("30.00")


@pytest.mark.asyncio
async def test_referral_days_reward_mode(db, fake_remnawave, test_settings) -> None:
    async with db.session() as session:
        await SettingsService.set_value(
            session, SettingsService.KEY_REFERRAL_REWARD_TYPE, "days"
        )
        await SettingsService.set_value(
            session, SettingsService.KEY_REFERRAL_REWARD_DAYS, "5"
        )
        referrer = User(telegram_id=40010, full_name="RefDays")
        friend = User(telegram_id=40011, full_name="FriendDays", balance=Decimal("300.00"))
        tariff = Tariff(
            name="Base",
            price_rub=Decimal("100.00"),
            duration_days=30,
            traffic_limit_gb=0,
            device_limit=2,
        )
        session.add_all([referrer, friend, tariff])
        await session.commit()

        await ReferralService.bind_referrer(session, friend, 40010)
        res = await BillingService.pay_tariff_with_balance(
            session=session,
            user=friend,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()

        assert res.referral_reward is not None
        assert res.referral_reward.reward_type == "days"
        assert res.referral_reward.days == 5
        stats = await ReferralService.get_stats(session, referrer.id)
        assert stats.total_bonus_days == 5
