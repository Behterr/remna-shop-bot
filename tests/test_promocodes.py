"""Unit tests for promo codes: balance bonus, bonus days, discount %, limits, and single-use."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.models import PromoCode, PromoType, Tariff, User
from app.services.billing_service import BillingService
from app.services.promocode_service import PromoCodeService, PromoError


@pytest.mark.asyncio
async def test_promo_balance_bonus_and_single_use(db, fake_remnawave, test_settings) -> None:
    async with db.session() as session:
        u1 = User(telegram_id=20001, full_name="U1")
        u2 = User(telegram_id=20002, full_name="U2")
        promo = PromoCode(
            code="BONUS150",
            promo_type=PromoType.BALANCE_BONUS,
            value=Decimal("150.00"),
            max_uses=1,
            is_active=True,
        )
        session.add_all([u1, u2, promo])
        await session.commit()

        res = await PromoCodeService.activate(session, u1, "bonus150", fake_remnawave, test_settings)
        await session.commit()

        assert u1.balance == Decimal("150.00")
        assert "150" in res.message

        with pytest.raises(PromoError):
            await PromoCodeService.activate(session, u1, "BONUS150", fake_remnawave, test_settings)

        # Max uses = 1 blocks second user as well
        with pytest.raises(PromoError):
            await PromoCodeService.activate(session, u2, "BONUS150", fake_remnawave, test_settings)


@pytest.mark.asyncio
async def test_promo_bonus_days_and_expired_guard(db, fake_remnawave, test_settings) -> None:
    async with db.session() as session:
        user = User(telegram_id=20003, full_name="U3")
        days_promo = PromoCode(
            code="DAYS7",
            promo_type=PromoType.BONUS_DAYS,
            value=Decimal("7"),
            is_active=True,
        )
        expired_promo = PromoCode(
            code="OLD10",
            promo_type=PromoType.BONUS_DAYS,
            value=Decimal("10"),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            is_active=True,
        )
        session.add_all([user, days_promo, expired_promo])
        await session.commit()

        await PromoCodeService.activate(session, user, "DAYS7", fake_remnawave, test_settings)
        await session.commit()
        assert user.remnawave_uuid is not None

        with pytest.raises(PromoError):
            await PromoCodeService.activate(session, user, "OLD10", fake_remnawave, test_settings)


@pytest.mark.asyncio
async def test_promo_discount_percent_applied_once_on_checkout(
    db, fake_remnawave, test_settings
) -> None:
    async with db.session() as session:
        user = User(telegram_id=20004, full_name="U4", balance=Decimal("500.00"))
        tariff = Tariff(
            name="Standard",
            price_rub=Decimal("200.00"),
            duration_days=30,
            traffic_limit_gb=0,
            device_limit=3,
        )
        promo = PromoCode(
            code="SALE25",
            promo_type=PromoType.DISCOUNT_PERCENT,
            value=Decimal("25"),
            max_uses=5,
            is_active=True,
        )
        session.add_all([user, tariff, promo])
        await session.commit()

        await PromoCodeService.activate(session, user, "SALE25", fake_remnawave, test_settings)
        await session.commit()
        assert user.pending_promo_id == promo.id

        res = await BillingService.pay_tariff_with_balance(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()

        # 200 RUB - 25% = 150 RUB
        assert res.transaction.amount == Decimal("150.00")
        assert user.balance == Decimal("350.00")
        assert user.pending_promo_id is None
        assert promo.used_count == 1

        # Second purchase pays full price and cannot reactivate SALE25
        res2 = await BillingService.pay_tariff_with_balance(
            session=session,
            user=user,
            tariff=tariff,
            remnawave=fake_remnawave,
            settings=test_settings,
        )
        await session.commit()
        assert res2.transaction.amount == Decimal("200.00")

        with pytest.raises(PromoError):
            await PromoCodeService.activate(session, user, "SALE25", fake_remnawave, test_settings)
