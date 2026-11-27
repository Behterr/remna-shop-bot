"""Promo code validation, activation, and checkout discount application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    PaymentProviderType,
    PromoActivation,
    PromoCode,
    PromoType,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
)
from app.services.remnawave import RemnawaveClient
from app.services.subscription_service import SubscriptionService


class PromoError(Exception):
    """Domain error during promo code validation or activation."""


@dataclass(slots=True)
class PromoActivationResult:
    promo: PromoCode
    message: str


def calculate_discounted_price(base_price: Decimal, promo: PromoCode | None) -> Decimal:
    """Apply DISCOUNT_PERCENT promo to base RUB price cleanly."""
    if promo is None or promo.promo_type != PromoType.DISCOUNT_PERCENT:
        return base_price.quantize(Decimal("0.01"))
    percent = min(Decimal("99"), max(Decimal("1"), promo.value))
    multiplier = (Decimal("100") - percent) / Decimal("100")
    discounted = (base_price * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return max(Decimal("1.00"), discounted)


class PromoCodeService:
    """Handles promo code validation, redemption, and discount consumption."""

    @staticmethod
    async def validate_for_user(
        session: AsyncSession,
        user: User,
        raw_code: str,
        *,
        for_update: bool = False,
    ) -> PromoCode:
        code = raw_code.strip().upper()
        if not code:
            raise PromoError("Введите непустой промокод.")

        stmt = select(PromoCode).where(PromoCode.code == code)
        if for_update and session.bind and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        result = await session.execute(stmt)
        promo = result.scalar_one_or_none()
        if promo is None or not promo.is_active:
            raise PromoError("Промокод не найден или отключён.")

        now = datetime.now(timezone.utc)
        if promo.expires_at is not None:
            exp = promo.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= now:
                raise PromoError("Срок действия этого промокода истёк.")

        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            raise PromoError("Лимит активаций этого промокода исчерпан.")

        existing_act = await session.execute(
            select(PromoActivation).where(
                PromoActivation.promo_code_id == promo.id,
                PromoActivation.user_id == user.id,
            )
        )
        if existing_act.scalar_one_or_none() is not None:
            raise PromoError("Вы уже активировали этот промокод ранее.")

        return promo

    @classmethod
    async def activate(
        cls,
        session: AsyncSession,
        user: User,
        raw_code: str,
        remnawave: RemnawaveClient,
        settings: Settings,
    ) -> PromoActivationResult:
        """Activate a promo code for a user."""
        promo = await cls.validate_for_user(session, user, raw_code, for_update=True)
        now = datetime.now(timezone.utc)

        if promo.promo_type == PromoType.DISCOUNT_PERCENT:
            user.pending_promo_id = promo.id
            await session.flush()
            pct = int(promo.value)
            return PromoActivationResult(
                promo=promo,
                message=(
                    f"Промокод <b>{promo.code}</b> активирован!\n"
                    f"Скидка <b>{pct}%</b> автоматически применится при следующей покупке или продлении тарифа."
                ),
            )

        # Record single-use activation and increment counter atomically
        activation = PromoActivation(promo_code_id=promo.id, user_id=user.id)
        session.add(activation)
        promo.used_count += 1

        if promo.promo_type == PromoType.BALANCE_BONUS:
            bonus = promo.value.quantize(Decimal("0.01"))
            user.balance = (user.balance + bonus).quantize(Decimal("0.01"))
            session.add(
                Transaction(
                    user_id=user.id,
                    payment_id=f"promo_bal_{promo.id}_{user.id}_{uuid4().hex[:8]}",
                    provider=PaymentProviderType.PROMO,
                    tx_type=TransactionType.PROMO_BONUS,
                    status=TransactionStatus.SUCCEEDED,
                    amount=bonus,
                    currency="RUB",
                    promo_code_id=promo.id,
                    description=f"Промокод {promo.code}: +{bonus} ₽ на баланс",
                    metadata_json={"code": promo.code},
                    completed_at=now,
                )
            )
            await session.flush()
            return PromoActivationResult(
                promo=promo,
                message=(
                    f"Промокод <b>{promo.code}</b> активирован!\n"
                    f"На ваш баланс начислено <b>{bonus} ₽</b>."
                ),
            )

        if promo.promo_type == PromoType.BONUS_DAYS:
            days = max(1, int(promo.value))
            await SubscriptionService.grant_bonus_days(
                session=session,
                user=user,
                days=days,
                remnawave=remnawave,
                settings=settings,
            )
            session.add(
                Transaction(
                    user_id=user.id,
                    payment_id=f"promo_days_{promo.id}_{user.id}_{uuid4().hex[:8]}",
                    provider=PaymentProviderType.PROMO,
                    tx_type=TransactionType.PROMO_BONUS,
                    status=TransactionStatus.SUCCEEDED,
                    amount=Decimal(days),
                    currency="DAYS",
                    promo_code_id=promo.id,
                    description=f"Промокод {promo.code}: +{days} дн. подписки",
                    metadata_json={"code": promo.code, "days": days},
                    completed_at=now,
                )
            )
            await session.flush()
            return PromoActivationResult(
                promo=promo,
                message=(
                    f"Промокод <b>{promo.code}</b> активирован!\n"
                    f"К вашей подписке добавлено <b>{days} дн.</b>"
                ),
            )

        raise PromoError("Неизвестный тип промокода.")

    @classmethod
    async def get_applicable_discount_promo(
        cls,
        session: AsyncSession,
        user: User,
    ) -> PromoCode | None:
        """Return user's pending discount promo if still valid, otherwise clear it."""
        if user.pending_promo_id is None:
            return None
        promo = await session.get(PromoCode, user.pending_promo_id)
        if promo is None or promo.promo_type != PromoType.DISCOUNT_PERCENT:
            user.pending_promo_id = None
            await session.flush()
            return None
        try:
            return await cls.validate_for_user(session, user, promo.code, for_update=False)
        except PromoError:
            user.pending_promo_id = None
            await session.flush()
            return None

    @classmethod
    async def consume_discount_promo(
        cls,
        session: AsyncSession,
        user: User,
        promo_id: int | None,
    ) -> PromoCode | None:
        """Consume a DISCOUNT_PERCENT promo code upon successful payment completion."""
        if promo_id is None:
            return None

        stmt = select(PromoCode).where(PromoCode.id == promo_id)
        if session.bind and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        promo = (await session.execute(stmt)).scalar_one_or_none()
        if promo is None or not promo.is_active or promo.promo_type != PromoType.DISCOUNT_PERCENT:
            if user.pending_promo_id == promo_id:
                user.pending_promo_id = None
            return None

        now = datetime.now(timezone.utc)
        if promo.expires_at is not None:
            exp = promo.expires_at if promo.expires_at.tzinfo else promo.expires_at.replace(tzinfo=timezone.utc)
            if exp <= now:
                if user.pending_promo_id == promo_id:
                    user.pending_promo_id = None
                return None

        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            if user.pending_promo_id == promo_id:
                user.pending_promo_id = None
            return None

        existing_act = (
            await session.execute(
                select(PromoActivation).where(
                    PromoActivation.promo_code_id == promo.id,
                    PromoActivation.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if existing_act is not None:
            if user.pending_promo_id == promo_id:
                user.pending_promo_id = None
            return None

        session.add(PromoActivation(promo_code_id=promo.id, user_id=user.id))
        promo.used_count += 1
        if user.pending_promo_id == promo_id:
            user.pending_promo_id = None
        await session.flush()
        return promo
