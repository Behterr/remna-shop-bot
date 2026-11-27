"""Idempotent billing orchestrator connecting payment providers, subscriptions, promos, and referrals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    PaymentProviderType,
    Subscription,
    Tariff,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
)
from app.services.payments.base import (
    BasePaymentProvider,
    InsufficientBalanceError,
    PaymentCreationRequest,
    PaymentCreationResult,
    PaymentError,
)
from app.services.payments.stars import TelegramStarsProvider
from app.services.promocode_service import PromoCodeService, calculate_discounted_price
from app.services.referral_service import ReferralRewardResult, ReferralService
from app.services.remnawave import RemnawaveClient
from app.services.subscription_service import SubscriptionService


@dataclass(slots=True)
class BillingFulfillmentResult:
    """Outcome of processing a payment or internal balance purchase."""

    transaction: Transaction
    user: User
    subscription: Subscription | None = None
    referral_reward: ReferralRewardResult | None = None
    already_processed: bool = False
    credited_rub: Decimal = Decimal("0.00")


async def _lock_user(session: AsyncSession, user_id: int) -> User:
    stmt = select(User).where(User.id == user_id)
    if session.bind and session.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise PaymentError(f"User id={user_id} not found")
    return user


class BillingService:
    """Coordinates checkout creation and idempotent payment fulfillment."""

    @staticmethod
    async def get_tariff_checkout_quote(
        session: AsyncSession,
        user: User,
        tariff: Tariff,
        settings: Settings,
    ) -> tuple[Decimal, int, int | None]:
        """Return (final_rub_price, stars_price, promo_id) considering pending discount promo."""
        promo = await PromoCodeService.get_applicable_discount_promo(session, user)
        final_rub = calculate_discounted_price(tariff.price_rub, promo)

        stars_provider = TelegramStarsProvider(settings.STARS_RUB_RATE)
        if promo is not None and tariff.price_stars:
            ratio = final_rub / max(Decimal("1.00"), tariff.price_rub)
            explicit_discounted = max(1, int(round(float(tariff.price_stars) * float(ratio))))
            stars = explicit_discounted
        else:
            stars = stars_provider.calculate_stars(final_rub, tariff.price_stars)

        return final_rub, stars, (promo.id if promo else None)

    @classmethod
    async def create_tariff_checkout(
        cls,
        session: AsyncSession,
        user: User,
        tariff: Tariff,
        provider: BasePaymentProvider,
        settings: Settings,
        *,
        bot_username: str = "",
    ) -> tuple[Transaction, PaymentCreationResult]:
        """Create external payment invoice and record PENDING transaction."""
        final_rub, stars, promo_id = await cls.get_tariff_checkout_quote(session, user, tariff, settings)
        existing_sub = (
            await session.execute(select(Subscription).where(Subscription.user_id == user.id))
        ).scalar_one_or_none()
        tx_type = (
            TransactionType.SUBSCRIPTION_RENEWAL
            if existing_sub is not None
            else TransactionType.SUBSCRIPTION_PURCHASE
        )

        payment_id = f"pay_{uuid4().hex}"
        return_url = settings.YOOKASSA_RETURN_URL or (
            f"https://t.me/{bot_username}" if bot_username else "https://t.me"
        )
        req = PaymentCreationRequest(
            payment_id=payment_id,
            user_telegram_id=user.telegram_id,
            amount_rub=final_rub,
            stars_amount=stars,
            description=f"VPN тариф «{tariff.name}» ({tariff.duration_days} дн.)",
            return_url=return_url,
            metadata={"tariff_id": str(tariff.id), "telegram_id": str(user.telegram_id)},
        )
        creation = await provider.create_payment(req)

        tx = Transaction(
            user_id=user.id,
            payment_id=payment_id,
            external_id=creation.external_id,
            provider=provider.provider_type,
            tx_type=tx_type,
            status=TransactionStatus.PENDING,
            amount=creation.amount,
            currency=creation.currency,
            tariff_id=tariff.id,
            promo_code_id=promo_id,
            description=req.description,
            metadata_json={
                "rub_amount": str(final_rub),
                "tariff_name": tariff.name,
            },
        )
        session.add(tx)
        await session.flush()
        return tx, creation

    @classmethod
    async def create_deposit_checkout(
        cls,
        session: AsyncSession,
        user: User,
        amount_rub: Decimal,
        provider: BasePaymentProvider,
        settings: Settings,
        *,
        bot_username: str = "",
    ) -> tuple[Transaction, PaymentCreationResult]:
        """Create balance top-up invoice and record PENDING DEPOSIT transaction."""
        clean_amount = amount_rub.quantize(Decimal("0.01"))
        if clean_amount <= Decimal("0.00"):
            raise PaymentError("Сумма пополнения должна быть больше нуля.")

        payment_id = f"dep_{uuid4().hex}"
        return_url = settings.YOOKASSA_RETURN_URL or (
            f"https://t.me/{bot_username}" if bot_username else "https://t.me"
        )
        stars_provider = TelegramStarsProvider(settings.STARS_RUB_RATE)
        stars_amount = stars_provider.calculate_stars(clean_amount, None)

        req = PaymentCreationRequest(
            payment_id=payment_id,
            user_telegram_id=user.telegram_id,
            amount_rub=clean_amount,
            stars_amount=stars_amount,
            description=f"Пополнение баланса на {clean_amount} ₽",
            return_url=return_url,
            metadata={"deposit": "1", "telegram_id": str(user.telegram_id)},
        )
        creation = await provider.create_payment(req)

        tx = Transaction(
            user_id=user.id,
            payment_id=payment_id,
            external_id=creation.external_id,
            provider=provider.provider_type,
            tx_type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            amount=creation.amount,
            currency=creation.currency,
            description=req.description,
            metadata_json={"rub_amount": str(clean_amount)},
        )
        session.add(tx)
        await session.flush()
        return tx, creation

    @classmethod
    async def pay_tariff_with_balance(
        cls,
        session: AsyncSession,
        user: User,
        tariff: Tariff,
        remnawave: RemnawaveClient,
        settings: Settings,
        *,
        is_auto_renewal: bool = False,
        idempotent_payment_id: str | None = None,
    ) -> BillingFulfillmentResult:
        """Atomically deduct user's internal balance and activate/extend VPN subscription."""
        if idempotent_payment_id:
            existing_stmt = select(Transaction).where(Transaction.payment_id == idempotent_payment_id)
            if session.bind and session.bind.dialect.name != "sqlite":
                existing_stmt = existing_stmt.with_for_update()
            existing_tx = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing_tx is not None and existing_tx.status == TransactionStatus.SUCCEEDED:
                sub = (
                    await session.execute(
                        select(Subscription).where(Subscription.user_id == user.id)
                    )
                ).scalar_one_or_none()
                return BillingFulfillmentResult(
                    transaction=existing_tx,
                    user=user,
                    subscription=sub,
                    already_processed=True,
                )

        locked_user = await _lock_user(session, user.id)
        if is_auto_renewal:
            final_rub = tariff.price_rub.quantize(Decimal("0.01"))
            promo = None
        else:
            promo = await PromoCodeService.get_applicable_discount_promo(session, locked_user)
            final_rub = calculate_discounted_price(tariff.price_rub, promo)

        if locked_user.balance < final_rub:
            raise InsufficientBalanceError(
                f"Недостаточно средств на балансе: нужно {final_rub} ₽, доступно {locked_user.balance} ₽."
            )

        locked_user.balance = (locked_user.balance - final_rub).quantize(Decimal("0.01"))

        existing_sub = (
            await session.execute(
                select(Subscription).where(Subscription.user_id == locked_user.id)
            )
        ).scalar_one_or_none()
        if is_auto_renewal:
            tx_type = TransactionType.AUTO_RENEWAL
        elif existing_sub is not None:
            tx_type = TransactionType.SUBSCRIPTION_RENEWAL
        else:
            tx_type = TransactionType.SUBSCRIPTION_PURCHASE

        payment_id = idempotent_payment_id or f"bal_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        tx = Transaction(
            user_id=locked_user.id,
            payment_id=payment_id,
            external_id=payment_id,
            provider=PaymentProviderType.BALANCE,
            tx_type=tx_type,
            status=TransactionStatus.SUCCEEDED,
            amount=final_rub,
            currency="RUB",
            tariff_id=tariff.id,
            promo_code_id=promo.id if promo else None,
            description=f"{'Автопродление' if is_auto_renewal else 'Оплата'} тарифа «{tariff.name}» с баланса",
            metadata_json={"rub_amount": str(final_rub), "tariff_name": tariff.name},
            completed_at=now,
        )
        session.add(tx)

        if promo is not None:
            await PromoCodeService.consume_discount_promo(session, locked_user, promo.id)

        sub = await SubscriptionService.apply_tariff_purchase(
            session=session,
            user=locked_user,
            tariff=tariff,
            remnawave=remnawave,
            settings=settings,
            reset_traffic=True,
        )
        reward = await ReferralService.grant_reward_for_payment(
            session=session,
            payer=locked_user,
            paid_amount_rub=final_rub,
            source_payment_id=payment_id,
            remnawave=remnawave,
            settings=settings,
        )
        await session.flush()
        return BillingFulfillmentResult(
            transaction=tx,
            user=locked_user,
            subscription=sub,
            referral_reward=reward,
            already_processed=False,
        )

    @classmethod
    async def fulfill_payment(
        cls,
        session: AsyncSession,
        payment_id: str,
        remnawave: RemnawaveClient,
        settings: Settings,
        *,
        external_id: str | None = None,
    ) -> BillingFulfillmentResult:
        """Idempotently fulfill an external payment (CryptoBot, YooKassa, Telegram Stars)."""
        stmt = select(Transaction).where(Transaction.payment_id == payment_id)
        if session.bind and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        tx = (await session.execute(stmt)).scalar_one_or_none()

        if tx is None and external_id:
            stmt_ext = select(Transaction).where(Transaction.external_id == external_id)
            if session.bind and session.bind.dialect.name != "sqlite":
                stmt_ext = stmt_ext.with_for_update()
            tx = (await session.execute(stmt_ext)).scalar_one_or_none()

        if tx is None:
            raise PaymentError(f"Transaction not found for payment_id={payment_id}")

        locked_user = await _lock_user(session, tx.user_id)

        if tx.status == TransactionStatus.SUCCEEDED:
            sub = (
                await session.execute(
                    select(Subscription).where(Subscription.user_id == locked_user.id)
                )
            ).scalar_one_or_none()
            return BillingFulfillmentResult(
                transaction=tx,
                user=locked_user,
                subscription=sub,
                already_processed=True,
            )

        now = datetime.now(timezone.utc)
        tx.status = TransactionStatus.SUCCEEDED
        tx.completed_at = now
        if external_id and not tx.external_id:
            tx.external_id = external_id

        rub_amount = Decimal(str(tx.metadata_json.get("rub_amount") or tx.amount)).quantize(
            Decimal("0.01")
        )

        if tx.tx_type == TransactionType.DEPOSIT:
            locked_user.balance = (locked_user.balance + rub_amount).quantize(Decimal("0.01"))
            await session.flush()
            return BillingFulfillmentResult(
                transaction=tx,
                user=locked_user,
                credited_rub=rub_amount,
                already_processed=False,
            )

        if tx.tariff_id is None:
            raise PaymentError(f"Transaction {tx.payment_id} missing tariff_id")

        tariff = await session.get(Tariff, tx.tariff_id)
        if tariff is None:
            raise PaymentError(f"Tariff id={tx.tariff_id} no longer exists")

        if tx.promo_code_id is not None:
            await PromoCodeService.consume_discount_promo(session, locked_user, tx.promo_code_id)

        sub = await SubscriptionService.apply_tariff_purchase(
            session=session,
            user=locked_user,
            tariff=tariff,
            remnawave=remnawave,
            settings=settings,
            reset_traffic=True,
        )
        reward = await ReferralService.grant_reward_for_payment(
            session=session,
            payer=locked_user,
            paid_amount_rub=rub_amount,
            source_payment_id=tx.payment_id,
            remnawave=remnawave,
            settings=settings,
        )
        await session.flush()
        return BillingFulfillmentResult(
            transaction=tx,
            user=locked_user,
            subscription=sub,
            referral_reward=reward,
            already_processed=False,
        )

    @classmethod
    async def cancel_payment(cls, session: AsyncSession, payment_id: str) -> bool:
        """Mark a pending transaction as CANCELED without touching succeeded ones."""
        stmt = select(Transaction).where(Transaction.payment_id == payment_id)
        if session.bind and session.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        tx = (await session.execute(stmt)).scalar_one_or_none()
        if tx is None or tx.status == TransactionStatus.SUCCEEDED:
            return False
        tx.status = TransactionStatus.CANCELED
        await session.flush()
        return True
