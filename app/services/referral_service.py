"""Referral attribution, reward payout, and statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    PaymentProviderType,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
)
from app.services.remnawave import RemnawaveClient
from app.services.settings_service import SettingsService
from app.services.subscription_service import SubscriptionService


@dataclass(slots=True)
class ReferralStats:
    invited_count: int
    total_bonus_rub: Decimal
    total_bonus_days: int


@dataclass(slots=True)
class ReferralRewardResult:
    referrer_telegram_id: int
    reward_type: str
    amount_rub: Decimal = Decimal("0.00")
    days: int = 0


class ReferralService:
    """Manages referral attribution and idempotent referral rewards."""

    @staticmethod
    async def bind_referrer(
        session: AsyncSession,
        user: User,
        referrer_telegram_id: int,
    ) -> bool:
        """Bind referrer to a user if not already bound and not self-referral."""
        if user.referred_by_id is not None:
            return False
        if referrer_telegram_id == user.telegram_id:
            return False

        referrer = (
            await session.execute(
                select(User).where(User.telegram_id == referrer_telegram_id)
            )
        ).scalar_one_or_none()
        if referrer is None or referrer.id == user.id:
            return False

        user.referred_by_id = referrer.id
        await session.flush()
        return True

    @staticmethod
    async def grant_reward_for_payment(
        session: AsyncSession,
        payer: User,
        paid_amount_rub: Decimal,
        source_payment_id: str,
        remnawave: RemnawaveClient,
        settings: Settings,
    ) -> ReferralRewardResult | None:
        """Grant referral reward (% on balance or bonus days) idempotently."""
        if payer.referred_by_id is None or paid_amount_rub <= Decimal("0.00"):
            return None

        ref_cfg = await SettingsService.get_referral_config(session, settings)
        if not ref_cfg.enabled:
            return None

        idempotent_pid = f"ref_bonus_{source_payment_id}"
        existing_tx = (
            await session.execute(
                select(Transaction).where(Transaction.payment_id == idempotent_pid)
            )
        ).scalar_one_or_none()
        if existing_tx is not None:
            return None

        if ref_cfg.first_payment_only:
            prior_tids = (
                await session.execute(
                    select(Transaction).where(
                        Transaction.user_id == payer.referred_by_id,
                        Transaction.tx_type == TransactionType.REFERRAL_BONUS,
                        Transaction.status == TransactionStatus.SUCCEEDED,
                    )
                )
            ).scalars().all()
            if any(
                tx.metadata_json.get("source_user_id") == payer.id
                for tx in prior_tids
            ):
                return None

        stmt = select(User).where(User.id == payer.referred_by_id)
        if session.bind and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        referrer = (await session.execute(stmt)).scalar_one_or_none()
        if referrer is None:
            return None

        now = datetime.now(timezone.utc)

        if ref_cfg.reward_type == "days":
            days = ref_cfg.reward_days
            if days <= 0:
                return None
            await SubscriptionService.grant_bonus_days(
                session=session,
                user=referrer,
                days=days,
                remnawave=remnawave,
                settings=settings,
            )
            session.add(
                Transaction(
                    user_id=referrer.id,
                    payment_id=idempotent_pid,
                    provider=PaymentProviderType.REFERRAL,
                    tx_type=TransactionType.REFERRAL_BONUS,
                    status=TransactionStatus.SUCCEEDED,
                    amount=Decimal(days),
                    currency="DAYS",
                    description=f"Реферальный бонус +{days} дн. от пользователя {payer.telegram_id}",
                    metadata_json={
                        "source_user_id": payer.id,
                        "source_payment_id": source_payment_id,
                        "days": days,
                    },
                    completed_at=now,
                )
            )
            await session.flush()
            return ReferralRewardResult(
                referrer_telegram_id=referrer.telegram_id,
                reward_type="days",
                days=days,
            )

        percent = ref_cfg.reward_percent
        if percent <= 0:
            return None
        bonus_rub = (paid_amount_rub * Decimal(percent) / Decimal("100")).quantize(Decimal("0.01"))
        if bonus_rub <= Decimal("0.00"):
            return None

        referrer.balance = (referrer.balance + bonus_rub).quantize(Decimal("0.01"))
        session.add(
            Transaction(
                user_id=referrer.id,
                payment_id=idempotent_pid,
                provider=PaymentProviderType.REFERRAL,
                tx_type=TransactionType.REFERRAL_BONUS,
                status=TransactionStatus.SUCCEEDED,
                amount=bonus_rub,
                currency="RUB",
                description=(
                    f"Реферальный бонус {percent}% ({bonus_rub} ₽) от пользователя {payer.telegram_id}"
                ),
                metadata_json={
                    "source_user_id": payer.id,
                    "source_payment_id": source_payment_id,
                    "percent": percent,
                },
                completed_at=now,
            )
        )
        await session.flush()
        return ReferralRewardResult(
            referrer_telegram_id=referrer.telegram_id,
            reward_type="percent",
            amount_rub=bonus_rub,
        )

    @staticmethod
    async def get_stats(session: AsyncSession, user_id: int) -> ReferralStats:
        """Compute invited user count and cumulative referral rewards."""
        invited_count = int(
            (
                await session.execute(
                    select(func.count(User.id)).where(User.referred_by_id == user_id)
                )
            ).scalar_one()
            or 0
        )

        rub_total = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.user_id == user_id,
                    Transaction.tx_type == TransactionType.REFERRAL_BONUS,
                    Transaction.status == TransactionStatus.SUCCEEDED,
                    Transaction.currency == "RUB",
                )
            )
        ).scalar_one()

        days_total = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.user_id == user_id,
                    Transaction.tx_type == TransactionType.REFERRAL_BONUS,
                    Transaction.status == TransactionStatus.SUCCEEDED,
                    Transaction.currency == "DAYS",
                )
            )
        ).scalar_one()

        return ReferralStats(
            invited_count=invited_count,
            total_bonus_rub=Decimal(str(rub_total)).quantize(Decimal("0.01")),
            total_bonus_days=int(days_total),
        )
